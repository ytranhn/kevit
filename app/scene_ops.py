"""Thao tác quản lý scene/chương: xoá, xoá video, gộp, đánh số lại. Không phụ thuộc giao diện.
Mọi thao tác xoá file đều đi qua thùng rác (trash.py)."""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from . import trash
from .models import Chapter, Project, Scene

FILE_ATTRS = ("raw_clip", "clip", "audio")
MAX_CHARS_PER_SCENE = 3


def scene_files(s: Scene) -> list[str]:
    return [getattr(s, a) for a in FILE_ATTRS if getattr(s, a)]


def _renamed(path: str, new_idx: int) -> Path:
    p = Path(path)
    return p.with_name(re.sub(r"^scene_\d+", f"scene_{new_idx:02d}", p.name))


def renumber(p: Project, ch: Chapter) -> None:
    """Đánh số lại scene 1..n theo thứ tự và đổi tên file clip/audio tương ứng (scene_NN_*.mp4), cập nhật đường dẫn.
    Đổi tên 2 pha (qua tên tạm) nên không bao giờ đè lên nhau."""
    pending = []
    for i, s in enumerate(ch.scenes, 1):
        if s.index == i:
            continue
        for attr in FILE_ATTRS:
            v = getattr(s, attr)
            if v:
                pending.append((s, attr, Path(v), _renamed(v, i)))
    staged = []
    for s, attr, src, dst in pending:
        if src.exists():
            tmp = src.with_name(f"__renum_{uuid.uuid4().hex[:8]}_{src.name}")
            src.rename(tmp)
            staged.append((s, attr, tmp, dst))
        else:
            setattr(s, attr, str(dst))          # file đã mất: chỉ cập nhật tên trong dữ liệu
    for s, attr, tmp, dst in staged:
        if dst.exists():                         # file lạ trùng tên (không thuộc scene nào) -> thùng rác, không đè
            trash.move_to_trash(p.dir, [dst])
        tmp.rename(dst)
        setattr(s, attr, str(dst))
    for i, s in enumerate(ch.scenes, 1):
        s.index = i
    export = p.chapter_dir(ch) / "flow"          # thư mục xuất thủ công đặt theo số scene cũ: tạo lại được, bỏ đi
    if export.exists():
        shutil.rmtree(export, ignore_errors=True)


def delete_scenes(p: Project, ch: Chapter, scenes: list[Scene]) -> int:
    """Xoá scene khỏi chương, file video/giọng của chúng vào thùng rác. Trả về số file đã chuyển."""
    ids = {id(s) for s in scenes}
    moved = trash.move_to_trash(p.dir, [f for s in scenes for f in scene_files(s)])
    ch.scenes = [s for s in ch.scenes if id(s) not in ids]
    renumber(p, ch)
    return moved


def clear_videos(p: Project, ch: Chapter, scenes: list[Scene], keep_raw: bool) -> int:
    """Xoá video của scene (scene vẫn còn). keep_raw=True: chỉ bỏ bản có giọng, giữ clip Flow gốc để tạo lại giọng
    miễn phí; False: xoá cả clip gốc (phải gen lại trên Flow, tốn credit)."""
    paths = []
    for s in scenes:
        paths += [s.clip, s.audio] + ([] if keep_raw else [s.raw_clip])
    moved = trash.move_to_trash(p.dir, paths)
    for s in scenes:
        s.clip = s.audio = ""
        if not keep_raw:
            s.raw_clip = ""
        s.error = ""
        s.status = "raw" if (keep_raw and s.raw_clip and Path(s.raw_clip).exists()) else "pending"
    return moved


def delete_chapter(p: Project, ch: Chapter, delete_files: bool) -> int:
    moved = trash.move_to_trash(p.dir, [p.chapter_dir(ch)]) if delete_files else 0
    p.chapters.remove(ch)
    return moved


# ---------------- gộp scene ----------------
def is_contiguous(ch: Chapter, group: list[Scene]) -> bool:
    pos = sorted(next(i for i, x in enumerate(ch.scenes) if x is s) for s in group)
    return len(pos) >= 2 and pos == list(range(pos[0], pos[0] + len(pos)))


def merge_heuristic(group: list[Scene]) -> dict:
    """Gộp không cần LLM: nối thuyết minh theo thứ tự, visual nối bằng 'Then', nhân vật theo số lần xuất hiện."""
    count: dict[str, int] = {}
    for s in group:
        for n in s.characters:
            count[n] = count.get(n, 0) + 1
    order = {n: i for i, n in enumerate(dict.fromkeys(n for s in group for n in s.characters))}
    chars = sorted(count, key=lambda n: (-count[n], order[n]))[:MAX_CHARS_PER_SCENE]
    visuals = [s.visual.strip().rstrip(".") for s in group if s.visual.strip()]
    visual = ". Then ".join([visuals[0]] + [v[0].lower() + v[1:] for v in visuals[1:]]) + "." if visuals else ""
    return {
        "title": " · ".join(dict.fromkeys(s.title.strip() for s in group if s.title.strip()))[:90],
        "narration": " ".join(s.narration.strip() for s in group if s.narration.strip()),
        "visual": visual,
        "characters": chars,
    }


def merge_group(p: Project, ch: Chapter, group: list[Scene], merged: dict) -> Scene:
    """Thay các scene liền kề bằng 1 scene gộp. Clip của các scene cũ vào thùng rác (nội dung đã đổi nên phải gen lại)."""
    if not is_contiguous(ch, group):
        raise ValueError("Chỉ gộp được các scene liền kề nhau.")
    pos = sorted(next(i for i, x in enumerate(ch.scenes) if x is s) for s in group)
    members = [ch.scenes[i] for i in pos]
    trash.move_to_trash(p.dir, [f for s in members for f in scene_files(s)])
    new = Scene(index=members[0].index, title=merged["title"], visual=merged["visual"],
                source_text="\n".join(s.source_text for s in members if s.source_text),
                narration=merged["narration"], characters=list(merged["characters"])[:MAX_CHARS_PER_SCENE], duration=8)
    ch.scenes[pos[0]:pos[-1] + 1] = [new]
    renumber(p, ch)
    return new


def words(s: Scene) -> int:
    return len(s.narration.split())


def suggest_merges(ch: Chapter, short: int = 14, budget: int = 32, include_done: bool = False) -> list[list[Scene]]:
    """Gợi ý nhóm scene liền kề NGẮN có thể gộp thành 1 clip (tiết kiệm credit Flow): từng scene <= `short` từ, tổng <= `budget` từ.
    Mặc định bỏ qua scene đã có clip vì gộp sẽ bỏ clip đó."""
    out, cur, total = [], [], 0

    def flush():
        nonlocal cur, total
        if len(cur) >= 2:
            out.append(cur)
        cur, total = [], 0
    for s in ch.scenes:
        usable = words(s) <= short and (include_done or s.status not in ("done", "raw", "generating"))
        if not usable:
            flush()
            continue
        if cur and total + words(s) > budget:
            flush()
        cur.append(s)
        total += words(s)
    flush()
    return out

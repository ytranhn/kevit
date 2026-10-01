"""Chế độ Google Flow (không có API): xuất prompt + ảnh nhân vật, nhập lại clip đã gen."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .models import Chapter, Character, Project, Scene
from .veo_client import build_prompt


def export_scene(p: Project, ch: Chapter, s: Scene, chars: dict[str, Character]) -> Path:
    d = p.chapter_dir(ch) / "flow" / f"scene_{s.index:02d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt.txt").write_text(build_prompt(p, s, chars), encoding="utf-8")
    for n in s.characters[:3]:
        img = chars.get(n) and chars[n].image
        if img and Path(img).exists():
            shutil.copy2(img, d / f"{n}{Path(img).suffix}")  # ảnh = "Ingredients" trong Flow
    return d


def export_all(p: Project, ch: Chapter, chars: dict[str, Character]) -> Path:
    for s in ch.scenes:
        export_scene(p, ch, s, chars)
    return p.chapter_dir(ch) / "flow"


def reveal(path: Path) -> None:
    cmd = ["open", str(path)] if sys.platform == "darwin" else (
        ["explorer", str(path)] if sys.platform == "win32" else ["xdg-open", str(path)])
    subprocess.Popen(cmd)


def reveal_file(path: Path) -> None:
    """Mở thư mục chứa file và CHỌN sẵn file đó (Finder / Explorer); Linux chỉ mở thư mục cha."""
    path = Path(path)
    if sys.platform == "darwin":
        cmd = ["open", "-R", str(path)]
    elif sys.platform == "win32":
        cmd = ["explorer", f"/select,{path}"]
    else:
        cmd = ["xdg-open", str(path.parent)]
    subprocess.Popen(cmd)


def _natkey(f: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(f).name)]


def import_clips(p: Project, ch: Chapter, files: list[str], start_row: int = 0) -> list[Scene]:
    """Gán clip lần lượt (theo tên file, thứ tự tự nhiên) vào các scene từ start_row."""
    out = []
    for s, f in zip(ch.scenes[start_row:], sorted(files, key=_natkey)):
        dst = p.chapter_dir(ch) / "clips" / f"scene_{s.index:02d}_raw.mp4"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        s.raw_clip, s.status, s.error = str(dst), "raw", ""
        out.append(s)
    return out

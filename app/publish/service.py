"""Quy trình tự động: ghép video -> viết mô tả/hashtag -> đăng lên các nền tảng đã chọn. Mỗi bước tự bỏ qua nếu đã có kết quả còn mới."""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

from .. import merger
from ..models import Chapter, Project
from . import account_name, describe, get
from .base import Post, PublishError, Result

PRIVACY_LABELS = {"private": "Riêng tư", "unlisted": "Không công khai", "public": "Công khai"}


@dataclass
class Target:
    key: str                 # id chương, hoặc "project" cho video cả dự án
    label: str
    chapter: Chapter | None  # None = cả dự án


def targets(p: Project) -> list[Target]:
    if p.publish_scope == "project":
        return [Target("project", f"{p.name} (cả dự án)", None)]
    return [Target(c.id, c.name, c) for c in p.chapters]


def clips_of(p: Project, t: Target) -> list[Path]:
    chs = [t.chapter] if t.chapter else p.chapters
    return [Path(s.clip) for c in chs for s in c.scenes if s.status == "done" and s.clip and Path(s.clip).exists()]


def scene_count(p: Project, t: Target) -> int:
    return sum(len(c.scenes) for c in ([t.chapter] if t.chapter else p.chapters))


def is_ready(p: Project, t: Target) -> bool:
    n = scene_count(p, t)
    return n > 0 and len(clips_of(p, t)) == n


def video_path(p: Project, t: Target) -> Path:
    return p.merged_path(t.chapter) if t.chapter else p.full_path


def is_stale(p: Project, t: Target) -> bool:
    """Video ghép chưa có, hoặc cũ hơn một clip nào đó (đã gen lại / đổi giọng sau lần ghép)."""
    out = video_path(p, t)
    if not out.exists() or out.stat().st_size == 0:
        return True
    return any(c.stat().st_mtime > out.stat().st_mtime for c in clips_of(p, t))


def meta_of(p: Project, t: Target) -> dict:
    return (t.chapter.post_meta if t.chapter else p.post_meta) or {}


def set_meta(p: Project, t: Target, meta: dict) -> None:
    if t.chapter:
        t.chapter.post_meta = meta
    else:
        p.post_meta = meta


def probe(video: Path) -> tuple[bool, float]:
    """(là video dọc?, thời lượng giây) đọc từ ffmpeg."""
    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(video)], **merger.RUN).stderr
    d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    secs = int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else 0.0
    m = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", out)
    return (int(m[2]) > int(m[1]) if m else True), secs


def ensure_video(p: Project, t: Target, log=print) -> Path:
    if not is_ready(p, t):
        raise PublishError(f"{t.label}: chưa gen xong tất cả scene nên chưa ghép được.")
    out = video_path(p, t)
    if is_stale(p, t):
        log(f"[{t.label}] Đang ghép video…")
        merger.merge(clips_of(p, t), out)
    else:
        log(f"[{t.label}] Dùng lại video đã ghép.")
    return out


def ensure_meta(p: Project, t: Target, log=print, regenerate: bool = False) -> dict:
    meta = meta_of(p, t)
    if meta.get("title") and not regenerate:
        return meta
    meta = describe.generate(p, t.chapter, log)
    set_meta(p, t, meta)
    p.save()
    return meta


def history_ok(p: Project, t: Target, account_id: str) -> dict | None:
    return next((h for h in reversed(p.publish_history) if h.get("chapter") == t.key and h.get("account") == account_id and h.get("ok")), None)


def _record(p: Project, t: Target, r: Result) -> None:
    p.publish_history.append(dict(chapter=t.key, platform=r.platform, account=r.account, ok=r.ok, url=r.url, post_id=r.post_id,
                                  privacy=r.privacy, message=r.message, time=time.strftime("%Y-%m-%d %H:%M:%S")))
    del p.publish_history[:-300]
    p.save()


def run(p: Project, tgts: list[Target], accounts: list[str], privacy: str | None = None, log=print, cancel=None,
        force: bool = False, regenerate_meta: bool = False) -> list[Result]:
    """Ghép + viết mô tả + đăng cho từng đích lên từng TÀI KHOẢN đã chọn. Lỗi ở một đích/tài khoản không làm dừng các phần còn lại."""
    privacy = privacy or p.publish_privacy
    results: list[Result] = []
    if not accounts:
        raise PublishError("Chưa chọn tài khoản nào để đăng.")
    for t in tgts:
        if cancel and cancel.is_set():
            log("Đã dừng theo yêu cầu.")
            break
        todo = [a for a in accounts if force or not history_ok(p, t, a)]
        for a in accounts:
            if a not in todo:
                log(f"[{t.label}] {account_name(a)}: đã đăng ({history_ok(p, t, a).get('time', '')}), bỏ qua. Chọn “Đăng lại” nếu muốn đăng lần nữa.")
        if not todo:
            continue
        try:
            video = ensure_video(p, t, log)
            meta = ensure_meta(p, t, log)
            vertical, secs = probe(video)
        except Exception as e:  # noqa: BLE001 - lỗi chuẩn bị video áp cho mọi tài khoản của đích này
            log(f"[{t.label}] LỖI: {e}")
            results += [Result(a.split(":", 1)[0], False, message=str(e), account=a) for a in todo]
            continue
        post = Post(meta.get("title", ""), meta.get("description", ""), list(meta.get("hashtags", [])), privacy, vertical, secs)
        for a in todo:
            if cancel and cancel.is_set():
                log("Đã dừng theo yêu cầu.")
                return results
            name = account_name(a)
            try:
                plat = get(a)
                if not plat.is_connected():
                    raise PublishError(f"{name}: tài khoản chưa kết nối hoặc đã bị xoá (Cài đặt → Đăng video).")
                log(f"[{t.label}] Đang đăng lên {name}…")
                r = plat.upload(video, post, log)
                r.account = r.account or a
                log(f"[{t.label}] Đã đăng lên {name}" + (f": {r.url}" if r.url else "") + (f" — {r.message}" if r.message else ""))
            except Exception as e:  # noqa: BLE001
                r = Result(a.split(":", 1)[0], False, message=str(e)[:600], account=a)
                log(f"[{t.label}] {name} lỗi: {e}")
            results.append(r)
            _record(p, t, r)
    return results


def thumbnail(p: Project, t: Target, size: int = 360) -> Path | None:
    """Ảnh đại diện của video (khung hình đầu của clip đầu tiên), lưu đệm trong thư mục dự án; tạo lại khi clip mới hơn ảnh."""
    clips = clips_of(p, t)
    if not clips:
        return None
    out = p.dir / "publish_thumbs" / f"{t.key}.jpg"
    if out.exists() and out.stat().st_size and out.stat().st_mtime >= clips[0].stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss", "0.3", "-i", str(clips[0]), "-frames:v", "1",
                        "-vf", f"scale={size}:-2", "-q:v", "4", str(out)], **merger.RUN)
    if r.returncode or not out.exists():          # clip quá ngắn cho -ss 0.3: thử lại từ đầu
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(clips[0]), "-frames:v", "1",
                            "-vf", f"scale={size}:-2", "-q:v", "4", str(out)], **merger.RUN)
    return out if r.returncode == 0 and out.exists() else None


def video_seconds(p: Project, t: Target) -> float:
    """Thời lượng video: đo từ file đã ghép nếu có, chưa ghép thì cộng thời lượng các clip."""
    f = video_path(p, t)
    if f.exists() and not is_stale(p, t):
        return probe(f)[1]
    return sum(probe(c)[1] for c in clips_of(p, t))


def created_at(p: Project, t: Target) -> float:
    """Thời điểm video được tạo: file ghép nếu có, không thì clip mới nhất; 0 nếu chưa có clip."""
    f = video_path(p, t)
    if f.exists():
        return f.stat().st_mtime
    cl = clips_of(p, t)
    return max((c.stat().st_mtime for c in cl), default=0.0)

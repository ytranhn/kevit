"""Thùng rác của dự án: xoá = chuyển vào <dự án>/.trash/<thời gian>/..., giữ nguyên cấu trúc để khôi phục thủ công.
Chỉ 'Dọn thùng rác' mới xoá hẳn."""
from __future__ import annotations

import shutil
import time
from pathlib import Path


def trash_root(project_dir: Path) -> Path:
    return project_dir / ".trash"


def move_to_trash(project_dir: Path, paths) -> int:
    """Chuyển file/thư mục vào thùng rác. Trả về số mục đã chuyển. Bỏ qua mục không tồn tại."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved = 0
    for raw in paths:
        if not raw:
            continue
        src = Path(raw)
        if not src.exists():
            continue
        try:
            rel = src.resolve().relative_to(project_dir.resolve())
        except ValueError:                       # nằm ngoài thư mục dự án: chỉ giữ tên
            rel = Path(src.name)
        dst = trash_root(project_dir) / stamp / rel
        n = 1
        while dst.exists():                      # tránh ghi đè khi xoá trùng tên trong cùng giây
            dst = dst.with_name(f"{dst.stem}__{n}{dst.suffix}")
            n += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
    return moved


def trash_stats(project_dir: Path) -> tuple[int, int]:
    """(số file, tổng byte) đang nằm trong thùng rác."""
    root = trash_root(project_dir)
    files = [f for f in root.rglob("*") if f.is_file()] if root.exists() else []
    return len(files), sum(f.stat().st_size for f in files)


def empty_trash(project_dir: Path) -> None:
    root = trash_root(project_dir)
    if root.exists():
        shutil.rmtree(root)

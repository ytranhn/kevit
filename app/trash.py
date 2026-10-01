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


# ---- thùng rác cấp DỰ ÁN: xoá cả dự án = chuyển nguyên thư mục vào <data>/.trash-projects/<thời gian>__<tên> ----
def projects_trash_root() -> Path:
    from . import models
    return models.PROJ_DIR.parent / ".trash-projects"


def dir_size(path: Path) -> tuple[int, int]:
    """(số file, tổng byte) của một thư mục."""
    files = [f for f in Path(path).rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def trash_project(name: str) -> Path:
    """Chuyển cả thư mục dự án vào thùng rác dự án (khôi phục được). Trả về đường dẫn mới."""
    from . import models
    src = models.PROJ_DIR / name
    if not (src / "project.json").exists():
        raise FileNotFoundError(f"Không thấy dự án '{name}'.")
    root = projects_trash_root()
    root.mkdir(parents=True, exist_ok=True)
    base = f"{time.strftime('%Y%m%d-%H%M%S')}__{name}"
    dst, n = root / base, 1
    while dst.exists():
        dst = root / f"{base}__{n}"
        n += 1
    shutil.move(str(src), str(dst))
    return dst


def list_trashed_projects() -> list[dict]:
    """Các dự án đã xoá, mới nhất trước: [{path, name, when, files, bytes}]."""
    root = projects_trash_root()
    out = []
    if root.exists():
        for d in sorted(root.iterdir(), reverse=True):
            if not (d / "project.json").exists():
                continue
            stamp, _, rest = d.name.partition("__")
            files, size = dir_size(d)
            out.append({"path": d, "name": rest.rsplit("__", 1)[0] if rest.rsplit("__", 1)[-1].isdigit() else rest,
                        "when": stamp, "files": files, "bytes": size})
    return out


def restore_project(path: Path) -> str:
    """Khôi phục dự án từ thùng rác. Trùng tên với dự án đang có thì thêm hậu tố ' (khôi phục)'. Trả về tên dự án sau khôi phục."""
    from . import models
    info = next((x for x in list_trashed_projects() if x["path"] == Path(path)), None)
    name = info["name"] if info else Path(path).name.partition("__")[2]
    new, n = name, 1
    while (models.PROJ_DIR / new).exists():
        new = f"{name} (khôi phục{'' if n == 1 else f' {n}'})"
        n += 1
    models.PROJ_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(models.PROJ_DIR / new))
    if new != name:                         # đổi cả tên lưu trong project.json cho khớp tên thư mục
        import json
        f = models.PROJ_DIR / new / "project.json"
        d = json.loads(f.read_text(encoding="utf-8"))
        d["name"] = new
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return new


def purge_project(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)

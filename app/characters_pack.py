"""Nhập 'gói nhân vật': mỗi nhân vật 1 thư mục NN_Tên/ (ảnh NN_Tên.png + description.md) và character_index.csv.
Ghép theo TÊN với nhân vật đang có của dự án; chỉ cập nhật ảnh + dữ liệu của gói (vai trò, tên Hán, mô tả gốc).
Mô tả tiếng Anh dùng làm prompt và tên gọi khác được GIỮ NGUYÊN (vì chúng khớp theo ảnh, người dùng có thể đã chỉnh)."""
from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg

from . import merger

from . import models
from .models import Character

CAPTION_CROP = range(41, 51)   # poster 41-50 dính dải chữ tên ở đáy + viền đen phía trên -> cắt bỏ


def _appearance(md: Path) -> str:
    """Lấy nội dung mục '## Mô tả ngoại hình' trong description.md."""
    if not md.exists():
        return ""
    lines, take = [], False
    for ln in md.read_text(encoding="utf-8").splitlines():
        if ln.startswith("## "):
            take = ln.strip() == "## Mô tả ngoại hình"
            continue
        if take and ln.strip():
            lines.append(ln.strip())
    return " ".join(lines)


def parse_pack(folder: Path) -> list[dict]:
    index = folder / "character_index.csv"
    if not index.exists():
        raise FileNotFoundError(f"Không thấy character_index.csv trong {folder}")
    out = []
    with index.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            img = folder / row["Folder"]
            out.append({
                "no": int(row["No"]), "name": models.nfc(row["Tên"].strip()), "zh": row.get("Tên Trung", "").strip(),
                "role": row.get("Vai trò", "").strip(), "image": img,
                "desc_vi": _appearance(img.parent / "description.md"),
            })
    return out


def _place_image(project: str, item: dict) -> str:
    dst_dir = models.char_dir(project)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{item['name']}.png"
    if item["no"] in CAPTION_CROP:
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(item["image"]),
                            "-vf", "crop=512:392:0:28", "-frames:v", "1", str(dst)], **merger.RUN)
        if r.returncode:
            raise RuntimeError(f"Cắt ảnh {item['name']} lỗi: {r.stderr[-200:]}")
    else:
        shutil.copy2(item["image"], dst)
    return str(dst)


def import_pack(project: str, folder: Path) -> list[str]:
    items = parse_pack(Path(folder))
    chars = models.load_characters(project)
    by_name = {models.nfc(c.name): c for c in chars}
    report, seen = [], set()
    for it in items:
        if not it["image"].exists():
            report.append(f"BỎ QUA {it['name']}: thiếu ảnh {it['image'].name}")
            continue
        old_img = by_name[it["name"]].image if it["name"] in by_name else ""
        new_img = _place_image(project, it)
        c = by_name.get(it["name"])
        if c is None:      # nhân vật mới: chưa có mô tả theo ảnh nên tạm dùng mô tả của gói
            c = Character(it["name"], it["desc_vi"])
            chars.append(c)
            report.append(f"THÊM MỚI {it['name']}")
        c.image, c.role, c.zh, c.description_vi = new_img, it["role"], it["zh"], it["desc_vi"]
        seen.add(it["name"])
        base = models.char_dir(project)
        if old_img and Path(old_img) != Path(new_img) and Path(old_img).parent == base:
            Path(old_img).unlink(missing_ok=True)      # ảnh .jpg cũ do chính tool tạo ra, đã được thay thế
    missing = [c.name for c in chars if models.nfc(c.name) not in seen]
    if missing:
        report.append(f"Không có trong gói (giữ nguyên): {', '.join(missing)}")
    models.save_characters(project, chars)
    report.insert(0, f"Đã cập nhật {len(seen)}/{len(items)} nhân vật từ gói.")
    return report

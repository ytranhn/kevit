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


SAMPLE = [("Nhân vật mẫu A", "主角", "Nhân vật chính", "Nam, thanh niên, tóc đen dài, áo choàng xanh lục viền vàng."),
          ("Nhân vật mẫu B", "师姐", "Sư tỷ", "Nữ, tóc búi cao, váy trắng thêu hoa, ánh mắt dịu dàng.")]


def write_sample(dest: Path) -> Path:
    """Tạo một gói mẫu (2 nhân vật, ảnh giữ chỗ) để người dùng xem cấu trúc rồi thay bằng dữ liệu thật."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    root = Path(dest) / "goi-nhan-vat-mau"
    root.mkdir(parents=True, exist_ok=True)
    rows = ["No,Tên,Tên Trung,Vai trò,Folder"]
    for i, (name, zh, role, desc) in enumerate(SAMPLE, 1):
        sub = root / f"{i:02d}_{name}"
        sub.mkdir(exist_ok=True)
        img = QImage(512, 768, QImage.Format_RGB32)
        img.fill(QColor("#6B6FF2" if i == 1 else "#4CC38A"))
        p = QPainter(img)
        p.setPen(QColor("white"))
        p.setFont(QFont("Helvetica", 34))
        p.drawText(img.rect(), Qt.AlignCenter | Qt.TextWordWrap, f"{name}\n(thay bằng ảnh thật)")
        p.end()
        img.save(str(sub / f"{i:02d}_{name}.png"))
        (sub / "description.md").write_text(f"# {name}\n\n## Mô tả ngoại hình\n{desc}\n", encoding="utf-8")
        rows.append(f"{i},{name},{zh},{role},{sub.name}/{sub.name}.png")
    (root / "character_index.csv").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    return root


def preview_pack(project: str, folder: Path) -> dict:
    """Đọc gói mà chưa ghi gì: bao nhiêu nhân vật sẽ được cập nhật / thêm mới / bị bỏ qua vì thiếu ảnh."""
    items = parse_pack(Path(folder))
    have = {models.nfc(c.name) for c in models.load_characters(project)}
    ok = [it for it in items if it["image"].exists()]
    return {"total": len(items), "update": [it["name"] for it in ok if it["name"] in have],
            "new": [it["name"] for it in ok if it["name"] not in have],
            "no_image": [it["name"] for it in items if not it["image"].exists()],
            "no_desc": [it["name"] for it in ok if not it["desc_vi"]]}

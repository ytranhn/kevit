"""Nhập nhân vật theo lô, chịu được nhiều kiểu gói khác nhau và không để một dòng lỗi làm hỏng cả lô.

Nguồn nhập: thư mục hoặc file .zip. Hai cách chuẩn bị:
  1. Chỉ cần ảnh: mỗi ảnh một nhân vật, tên nhân vật lấy từ tên file (bỏ số thứ tự đầu như "01_"). Mô tả (tuỳ chọn) nằm trong
     file .txt/.md cùng tên ảnh hoặc description.md cạnh ảnh.
  2. Có bảng CSV (bất kỳ tên file nào, dấu phẩy / chấm phẩy / tab, mã hoá UTF-8 hoặc Excel) với các cột như Tên, Ảnh, Vai trò,
     Tên Trung, Mô tả, Tên khác. Tên cột linh hoạt (tiếng Việt hoặc Anh), thiếu cột nào cũng được trừ tên.
Ghép theo TÊN với nhân vật đang có: cập nhật ảnh + dữ liệu của gói; mô tả prompt tiếng Anh và tên gọi khác đã chỉnh được GIỮ NGUYÊN."""
from __future__ import annotations

import csv
import io
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import imageio_ffmpeg

from . import merger, models
from .models import Character, safe_dirname

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_SIDE = 2048                       # ảnh lớn hơn được thu nhỏ để dự án nhẹ và Flow nhận nhanh
LEGACY_CROP = range(41, 51)           # chỉ dùng khi bật tuỳ chọn cắt dải chữ (bộ poster cũ)

# tên cột (đã bỏ dấu, chữ thường) -> trường
COLS = {
    "name": {"ten", "ten chuan", "ten nhan vat", "nhan vat", "name", "character", "character name"},
    "zh": {"ten trung", "ten han", "han", "zh", "chinese", "ten tieng trung"},
    "role": {"vai tro", "role", "chuc vu", "vai"},
    "image": {"folder", "anh", "hinh", "hinh anh", "image", "file", "path", "duong dan", "tep", "img", "picture", "photo"},
    "no": {"no", "stt", "so", "so thu tu", "id", "#"},
    "desc": {"mo ta", "description", "mo ta ngoai hinh", "ngoai hinh", "desc", "prompt"},
    "aliases": {"ten khac", "ten goi khac", "alias", "aliases", "biet danh", "ten goi"},
}


def _fold(x: str) -> str:
    from .scene_planner import _fold as f
    return re.sub(r"\s+", " ", f(x)).strip()


# ---------------------------------------------------------------- nguồn: thư mục hoặc zip
def open_source(source: Path) -> tuple[Path, "tempfile.TemporaryDirectory | None"]:
    """Trả về (thư mục gốc của gói, thư mục tạm cần dọn nếu là zip)."""
    source = Path(source)
    if source.is_file() and source.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="kevit-pack-")
        root = Path(tmp.name)
        try:
            with zipfile.ZipFile(source) as z:
                for info in z.infolist():
                    name = info.filename
                    if not info.flag_bits & 0x800:           # zip cũ ghi tên theo cp437: thử đọc lại thành UTF-8 cho đúng tiếng Việt
                        try:
                            name = name.encode("cp437").decode("utf-8")
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            pass
                    if name.startswith("__MACOSX/") or name.endswith(".DS_Store"):
                        continue
                    dest = (root / name).resolve()
                    if not str(dest).startswith(str(root.resolve())):
                        raise ValueError(f"File zip chứa đường dẫn không an toàn: {name}")
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(z.read(info))
        except zipfile.BadZipFile as e:
            tmp.cleanup()
            raise ValueError("File .zip bị hỏng hoặc không phải file zip.") from e
        # zip thường bọc thêm một thư mục ngoài cùng: đi vào nếu chỉ có một thư mục con
        kids = [k for k in root.iterdir() if not k.name.startswith(".")]
        if len(kids) == 1 and kids[0].is_dir():
            root = kids[0]
        return root, tmp
    if source.is_dir():
        return source, None
    raise FileNotFoundError(f"Không thấy thư mục hoặc file zip: {source}")


# ---------------------------------------------------------------- đọc CSV linh hoạt
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_table(path: Path) -> list[dict]:
    text = _read_text(path)
    head = text.splitlines()[0] if text.strip() else ""
    delim = max(",;\t|", key=lambda d: head.count(d))
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [_fold(h) for h in rows[0]]
    col = {}
    for field, names in COLS.items():
        for i, h in enumerate(head):
            if h in names and field not in col:
                col[field] = i
    if "name" not in col:
        raise ValueError("Bảng CSV cần có cột tên nhân vật (tên cột: Tên, Name hoặc Nhân vật). "
                         f"Các cột tool thấy: {', '.join(h or '(trống)' for h in rows[0])}")
    out = []
    for r in rows[1:]:
        get = lambda f: (r[col[f]].strip() if f in col and col[f] < len(r) else "")
        out.append({k: get(k) for k in COLS})
    return out


# ---------------------------------------------------------------- tìm ảnh / mô tả
def _images(root: Path) -> list[Path]:
    return sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in IMG_EXT and not f.name.startswith("."))


def _strip_no(stem: str) -> str:
    return re.sub(r"^\s*\d+\s*[_\-.)\s]\s*", "", stem).strip() or stem


def _find_image(root: Path, hint: str, name: str, imgs: list[Path], extra_base: Path | None = None) -> Path | None:
    """Ảnh theo cột đường dẫn (không phân biệt hoa thường/dấu, chấp nhận thiếu đuôi), không có thì dò theo tên nhân vật."""
    by_rel = {_fold(str(f.relative_to(root))): f for f in imgs}
    by_stem = {}
    for f in imgs:
        by_stem.setdefault(_fold(_strip_no(f.stem)), f)
        by_stem.setdefault(_fold(f.stem), f)
    if hint:
        h = hint.replace("\\", "/").lstrip("./")
        for base in (extra_base, root):
            if base and (base / h).is_file():
                return base / h
        for k in (_fold(h), _fold(Path(h).with_suffix("").as_posix())):
            if k in by_rel:
                return by_rel[k]
        stem = _fold(_strip_no(Path(h).stem))
        if stem in by_stem:
            return by_stem[stem]
        if not Path(h).suffix:                       # chỉ ghi tên file không đuôi / tên thư mục
            for f in imgs:
                if _fold(f.parent.name) == _fold(Path(h).name) or _fold(f.stem) == _fold(Path(h).name):
                    return f
    return by_stem.get(_fold(name))


def _desc_near(img: Path) -> str:
    for cand in (img.with_suffix(".txt"), img.with_suffix(".md"), img.parent / "description.md", img.parent / "description.txt"):
        if cand.exists():
            return _appearance(cand)
    return ""


def _appearance(md: Path) -> str:
    """Nội dung mục '## Mô tả ngoại hình' nếu có, không thì toàn bộ văn bản (bỏ dòng tiêu đề #)."""
    if not md.exists():
        return ""
    text = _read_text(md)
    lines, take, has_sec = [], False, False
    for ln in text.splitlines():
        if ln.startswith("## "):
            take = ln.strip().lower().replace("á", "a") in ("## mô tả ngoại hình", "## mo ta ngoai hinh")
            has_sec = has_sec or take
            continue
        if take and ln.strip():
            lines.append(ln.strip())
    if has_sec:
        return " ".join(lines)
    return " ".join(ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


# ---------------------------------------------------------------- phân tích gói
def parse_pack(source: Path) -> tuple[list[dict], list[str], "tempfile.TemporaryDirectory | None"]:
    """Trả về (các nhân vật, các cảnh báo từng dòng, thư mục tạm cần dọn). Mỗi nhân vật: name, zh, role, image (Path|None),
    desc, aliases (list), no, problem (str: lý do bị bỏ qua, '' nếu ổn)."""
    root, tmp = open_source(Path(source))
    try:
        imgs = _images(root)
        csvs = sorted(root.glob("*.csv")) or sorted(root.rglob("*.csv"))
        csvs = sorted(csvs, key=lambda f: (f.name != "character_index.csv", len(f.parts)))
        items: list[dict] = []
        if csvs:
            for i, r in enumerate(read_table(csvs[0]), 1):
                name = models.nfc(r["name"])
                if not name:
                    continue
                img = _find_image(root, r["image"], name, imgs, csvs[0].parent)
                no = int(re.sub(r"\D", "", r["no"]) or 0) or i
                items.append({"no": no, "name": name, "zh": r["zh"], "role": r["role"], "image": img,
                              "desc": r["desc"] or (_desc_near(img) if img else ""),
                              "aliases": [models.nfc(a.strip()) for a in re.split(r"[,;|/]", r["aliases"]) if a.strip()], "problem": ""})
            used = {it["image"] for it in items if it["image"]}
            have = {it["name"] for it in items}
            for img in imgs:             # ảnh không có trong bảng: vẫn nhập theo tên file (xem trước sẽ ghi rõ để bạn kiểm tra)
                nm = models.nfc(_strip_no(img.stem))
                if img not in used and nm not in have:
                    items.append({"no": len(items) + 1, "name": nm, "zh": "", "role": "", "image": img, "desc": _desc_near(img),
                                  "aliases": [], "problem": "", "extra": True})
                    have.add(nm)
        else:
            for i, img in enumerate(imgs, 1):
                items.append({"no": i, "name": models.nfc(_strip_no(img.stem)), "zh": "", "role": "", "image": img,
                              "desc": _desc_near(img), "aliases": [], "problem": ""})
        if not items:
            raise ValueError("Không tìm thấy nhân vật nào trong gói. Cần ít nhất một ảnh (.png/.jpg/.webp), hoặc bảng CSV có cột Tên.")
        warns, seen = [], set()
        for it in items:
            if not it["image"]:
                it["problem"] = "không tìm thấy ảnh (cột ảnh/đường dẫn không khớp file nào trong gói)"
            elif it["name"] in seen:
                it["problem"] = "trùng tên với nhân vật phía trên"
            seen.add(it["name"])
            if it["problem"]:
                warns.append(f"{it['name']}: {it['problem']}")
        return items, warns, tmp
    except Exception:
        if tmp:
            tmp.cleanup()
        raise


# ---------------------------------------------------------------- đặt ảnh vào dự án
def _place_image(project: str, item: dict, crop_captions: bool = False) -> str:
    """Chuẩn hoá ảnh về PNG trong thư mục nhân vật của dự án (đọc được mọi định dạng Qt hỗ trợ, thu nhỏ nếu quá lớn)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    dst_dir = models.char_dir(project)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{safe_dirname(item['name'])}.png"
    img = QImage(str(item["image"]))
    if img.isNull():
        raise ValueError(f"không đọc được ảnh '{Path(item['image']).name}' (định dạng không hỗ trợ hoặc file hỏng; dùng PNG/JPG/WEBP)")
    if crop_captions and item["no"] in LEGACY_CROP and img.width() >= 512 and img.height() >= 420:
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(item["image"]), "-vf", "crop=512:392:0:28",
                            "-frames:v", "1", str(dst)], **merger.RUN)
        if r.returncode == 0:
            return str(dst)
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if not img.save(str(dst), "PNG"):
        raise ValueError(f"không ghi được ảnh vào {dst}")
    return str(dst)


def preview_pack(project: str, source: Path) -> dict:
    """Đọc gói mà chưa ghi gì: cập nhật / thêm mới / bỏ qua kèm lý do."""
    items, warns, tmp = parse_pack(source)
    try:
        have = {models.nfc(c.name) for c in models.load_characters(project)}
        ok = [it for it in items if not it["problem"]]
        return {"total": len(items), "update": [it["name"] for it in ok if it["name"] in have],
                "new": [it["name"] for it in ok if it["name"] not in have],
                "no_image": [it["name"] for it in items if not it["image"]],
                "problems": warns, "no_desc": [it["name"] for it in ok if not it["desc"]],
                "extra": [it["name"] for it in ok if it.get("extra")]}
    finally:
        if tmp:
            tmp.cleanup()


def import_pack(project: str, source: Path, crop_captions: bool = False) -> list[str]:
    items, warns, tmp = parse_pack(source)
    try:
        chars = models.load_characters(project)
        by_name = {models.nfc(c.name): c for c in chars}
        report, done, failed = [], 0, []
        for it in items:
            if it["problem"]:
                failed.append(f"BỎ QUA {it['name']}: {it['problem']}")
                continue
            try:
                old_img = by_name[it["name"]].image if it["name"] in by_name else ""
                new_img = _place_image(project, it, crop_captions)
            except Exception as e:  # noqa: BLE001 - một nhân vật lỗi không làm hỏng cả lô
                failed.append(f"LỖI {it['name']}: {e}")
                continue
            c = by_name.get(it["name"])
            if c is None:
                c = Character(it["name"], it["desc"])      # nhân vật mới: tạm lấy mô tả của gói làm mô tả prompt
                chars.append(c)
                by_name[it["name"]] = c
                report.append(f"THÊM MỚI {it['name']}")
            c.image = new_img
            if it["role"]:
                c.role = it["role"]
            if it["zh"]:
                c.zh = it["zh"]
            if it["desc"]:
                c.description_vi = it["desc"]
            if it["aliases"] and not c.aliases:
                c.aliases = it["aliases"]
            done += 1
            base = models.char_dir(project)
            if old_img and Path(old_img) != Path(new_img) and Path(old_img).parent == base:
                Path(old_img).unlink(missing_ok=True)       # ảnh cũ do chính tool tạo ra, đã được thay thế
        models.save_characters(project, chars)
        report.insert(0, f"Đã nhập {done}/{len(items)} nhân vật." + (f" {len(failed)} nhân vật bị bỏ qua, xem bên dưới." if failed else ""))
        return report + failed
    finally:
        if tmp:
            tmp.cleanup()


# ---------------------------------------------------------------- gói mẫu
SAMPLE = [("Nhân vật mẫu A", "主角", "Nhân vật chính", "Nam, thanh niên, tóc đen dài, áo choàng xanh lục viền vàng."),
          ("Nhân vật mẫu B", "师姐", "Sư tỷ", "Nữ, tóc búi cao, váy trắng thêu hoa, ánh mắt dịu dàng.")]


def write_sample(dest: Path) -> Path:
    """Gói mẫu kiểu đơn giản nhất: mỗi nhân vật một ảnh + file mô tả cùng tên, kèm bảng CSV để xem cách điền (tuỳ chọn)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    root = Path(dest) / "goi-nhan-vat-mau"
    root.mkdir(parents=True, exist_ok=True)
    rows = ["Tên,Tên Trung,Vai trò,Ảnh,Mô tả"]
    for i, (name, zh, role, desc) in enumerate(SAMPLE, 1):
        img = QImage(512, 768, QImage.Format_RGB32)
        img.fill(QColor("#6B6FF2" if i == 1 else "#4CC38A"))
        p = QPainter(img)
        p.setPen(QColor("white"))
        p.setFont(QFont("Helvetica", 34))
        p.drawText(img.rect(), Qt.AlignCenter | Qt.TextWordWrap, f"{name}\n(thay bằng ảnh thật)")
        p.end()
        img.save(str(root / f"{name}.png"))
        (root / f"{name}.txt").write_text(desc + "\n", encoding="utf-8")
        rows.append(f"{name},{zh},{role},{name}.png,{desc}")
    (root / "danh-sach-nhan-vat.csv").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    return root

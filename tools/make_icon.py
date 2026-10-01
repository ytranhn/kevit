"""Vẽ logo ứng dụng (tam giác phát + sóng giọng đọc trên nền chàm) và xuất assets/icon.{png,ico,icns}.
Chạy lại khi muốn đổi thiết kế: .venv/bin/python tools/make_icon.py"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # console Windows (cp1252) không in được tiếng Việt

import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

OUT = Path(__file__).resolve().parent.parent / "assets"


def draw(size: int = 1024) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    u = size / 1024
    m = 60 * u                                    # lề trong suốt theo chuẩn biểu tượng macOS
    body = QRectF(m, m, size - 2 * m, size - 2 * m)
    g = QLinearGradient(body.topLeft(), body.bottomRight())
    g.setColorAt(0, QColor("#7C7FF5")); g.setColorAt(1, QColor("#3B33C9"))
    p.setPen(Qt.NoPen); p.setBrush(g)
    p.drawRoundedRect(body, 230 * u, 230 * u)
    # ánh sáng nhẹ phía trên
    hl = QLinearGradient(body.topLeft(), QPointF(body.left(), body.center().y()))
    hl.setColorAt(0, QColor(255, 255, 255, 60)); hl.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(hl); p.drawRoundedRect(body, 230 * u, 230 * u)
    # tam giác phát bo góc
    cx, cy, r = 340 * u, 512 * u, 190 * u
    tri = QPainterPath()
    pts = [QPointF(cx - r * 0.62, cy - r), QPointF(cx - r * 0.62, cy + r), QPointF(cx + r * 1.05, cy)]
    pen = QPen(QColor("white"), 70 * u, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    tri.addPolygon(pts + [pts[0]])
    p.setPen(pen); p.setBrush(QColor("white")); p.drawPath(tri)
    # sóng giọng đọc (người dẫn truyện)
    p.setBrush(Qt.NoBrush)
    for k, (rad, a) in enumerate(((125, 255), (225, 190))):
        pen = QPen(QColor(255, 255, 255, a), 52 * u, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        rect = QRectF(cx + 235 * u - rad * u, cy - rad * u, 2 * rad * u, 2 * rad * u)
        p.drawArc(rect, -42 * 16, 84 * 16)
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    OUT.mkdir(exist_ok=True)
    big = draw(1024)
    big.save(str(OUT / "icon.png"))
    sizes = (16, 32, 48, 64, 128, 256)
    # .ico nhiều kích thước (Windows)
    from PySide6.QtCore import QBuffer, QIODevice
    ico = bytearray(b"\x00\x00\x01\x00" + len(sizes).to_bytes(2, "little"))
    blobs, off = [], 6 + 16 * len(sizes)
    for s in sizes:
        buf = QBuffer(); buf.open(QIODevice.WriteOnly)
        big.scaled(s, s, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(buf, "PNG")
        data = bytes(buf.data()); blobs.append(data)
        ico += bytes([s % 256, s % 256, 0, 0, 1, 0, 32, 0]) + len(data).to_bytes(4, "little") + off.to_bytes(4, "little")
        off += len(data)
    (OUT / "icon.ico").write_bytes(bytes(ico) + b"".join(blobs))
    # .icns (macOS) nếu có iconutil
    if shutil.which("iconutil"):
        iset = OUT / "icon.iconset"
        shutil.rmtree(iset, ignore_errors=True); iset.mkdir()
        for s in (16, 32, 128, 256, 512):
            big.scaled(s, s, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(str(iset / f"icon_{s}x{s}.png"))
            big.scaled(s * 2, s * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(str(iset / f"icon_{s}x{s}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iset), "-o", str(OUT / "icon.icns")], check=True)
        shutil.rmtree(iset)
    print("Đã tạo:", ", ".join(sorted(f.name for f in OUT.iterdir())))


if __name__ == "__main__":
    main()

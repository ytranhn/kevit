"""Bộ icon vector tự vẽ (không phụ thuộc phông chữ): nét dày đều, bo tròn, nét rõ trên màn hình thường lẫn Retina.
Màu lấy từ theme và tự đổi theo trạng thái nút: bình thường (màu chữ), nút chính (chữ trắng trên nền chàm), nút nguy hiểm (đỏ),
bị khoá (nhạt). `attach(widget, tên)` gắn icon vào QPushButton/QLabel và tự cập nhật khi đổi sáng/tối hoặc khi nút đổi `primary`."""
from __future__ import annotations

import math
import weakref

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QLabel, QWidget

from . import theme

DEFAULT_SIZE = 20          # px: lớn hơn ký tự chữ (~11-13px) nên dễ nhìn
GRID = 24.0                # các hàm vẽ làm việc trên lưới 24x24


def _poly(p: QPainter, pts):
    path = QPainterPath(QPointF(*pts[0]))
    for x, y in pts[1:]:
        path.lineTo(x, y)
    p.drawPath(path)


def _arrowhead(p: QPainter, tip, back: float, size: float = 5.0, spread: float = 0.8):
    """Đầu mũi tên chữ V tại `tip`; `back` là góc (rad) hướng NGƯỢC chiều mũi tên."""
    for d in (spread, -spread):
        p.drawLine(QPointF(*tip), QPointF(tip[0] + size * math.cos(back + d), tip[1] + size * math.sin(back + d)))


def _refresh(p: QPainter, fill: QColor):
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 75 * 16, 285 * 16)                # cung gần kín, hở ở phía trên bên phải
    _arrowhead(p, (19.5, 12.0), math.radians(90), 5.2)                    # đầu cung ở bên phải, mũi tên hướng lên


def _gear(p: QPainter, fill: QColor):
    path = QPainterPath()
    first = True
    for k in range(8):                                                     # bánh răng 8 răng: đường viền liền, không phải tia nắng
        th = k * 45.0
        for dr, dth in ((6.7, -14), (9.0, -8), (9.0, 8), (6.7, 14)):
            a = math.radians(th + dth)
            pt = QPointF(12 + dr * math.cos(a), 12 + dr * math.sin(a))
            if first:
                path.moveTo(pt)
                first = False
            else:
                path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 3.0, 3.0)


def _chev(dx: float, dy: float):
    def draw(p: QPainter, fill: QColor):
        # mũi tên chữ V quay theo hướng (dx, dy): điểm nhọn ở phía (dx, dy)
        tip = (12 + dx * 3.2, 12 + dy * 3.2)
        px, py = -dy, dx
        _poly(p, [(tip[0] - dx * 6.4 + px * 6.4, tip[1] - dy * 6.4 + py * 6.4), tip, (tip[0] - dx * 6.4 - px * 6.4, tip[1] - dy * 6.4 - py * 6.4)])
    return draw


def _folder(p: QPainter, fill: QColor):
    path = QPainterPath()
    path.moveTo(3.5, 7.5)
    path.lineTo(3.5, 18.0)
    path.quadTo(3.5, 19.5, 5.0, 19.5)
    path.lineTo(19.0, 19.5)
    path.quadTo(20.5, 19.5, 20.5, 18.0)
    path.lineTo(20.5, 9.5)
    path.quadTo(20.5, 8.0, 19.0, 8.0)
    path.lineTo(11.5, 8.0)
    path.lineTo(9.5, 5.5)
    path.lineTo(5.0, 5.5)
    path.quadTo(3.5, 5.5, 3.5, 7.0)
    path.closeSubpath()
    p.drawPath(path)


def _play(p: QPainter, fill: QColor):
    p.setBrush(fill)
    p.drawPolygon(QPolygonF([QPointF(8, 5.5), QPointF(19, 12), QPointF(8, 18.5)]))


def _pause(p: QPainter, fill: QColor):
    p.setBrush(fill)
    p.drawRoundedRect(QRectF(6.5, 5.5, 3.8, 13), 1.2, 1.2)
    p.drawRoundedRect(QRectF(13.7, 5.5, 3.8, 13), 1.2, 1.2)


def _plus(p: QPainter, fill: QColor):
    p.drawLine(QPointF(12, 5), QPointF(12, 19))
    p.drawLine(QPointF(5, 12), QPointF(19, 12))


def _check(p: QPainter, fill: QColor):
    _poly(p, [(5, 12.5), (10, 17.5), (19, 7)])


def _trash(p: QPainter, fill: QColor):
    p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
    p.drawLine(QPointF(9.5, 7), QPointF(9.5, 4.5))
    p.drawLine(QPointF(14.5, 7), QPointF(14.5, 4.5))
    p.drawLine(QPointF(9.5, 4.5), QPointF(14.5, 4.5))
    _poly(p, [(6.5, 7), (7.5, 19.5), (16.5, 19.5), (17.5, 7)])
    p.drawLine(QPointF(10.2, 10.5), QPointF(10.4, 16.5))
    p.drawLine(QPointF(13.8, 10.5), QPointF(13.6, 16.5))


def _copy(p: QPainter, fill: QColor):
    p.drawRoundedRect(QRectF(8.5, 8.5, 11, 11), 2.2, 2.2)
    path = QPainterPath(QPointF(15.5, 8.5))
    path.lineTo(15.5, 6.5)
    path.quadTo(15.5, 4.5, 13.5, 4.5)
    path.lineTo(6.5, 4.5)
    path.quadTo(4.5, 4.5, 4.5, 6.5)
    path.lineTo(4.5, 13.5)
    path.quadTo(4.5, 15.5, 6.5, 15.5)
    path.lineTo(8.5, 15.5)
    p.drawPath(path)


def _sparkle(p: QPainter, fill: QColor):
    path = QPainterPath(QPointF(12, 3.5))
    for x, y in ((13.8, 10.2), (20.5, 12), (13.8, 13.8), (12, 20.5), (10.2, 13.8), (3.5, 12), (10.2, 10.2)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _link_out(p: QPainter, fill: QColor):
    _poly(p, [(14, 4.5), (19.5, 4.5), (19.5, 10)])
    p.drawLine(QPointF(19.5, 4.5), QPointF(11, 13))
    path = QPainterPath(QPointF(16.5, 14.5))
    path.lineTo(16.5, 17.5)
    path.quadTo(16.5, 19.5, 14.5, 19.5)
    path.lineTo(6.5, 19.5)
    path.quadTo(4.5, 19.5, 4.5, 17.5)
    path.lineTo(4.5, 9.5)
    path.quadTo(4.5, 7.5, 6.5, 7.5)
    path.lineTo(9.5, 7.5)
    p.drawPath(path)


def _step(n: int):
    def draw(p: QPainter, fill: QColor):
        p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(fill))
        p.drawText(QRectF(0, 0.5, 24, 24), Qt.AlignCenter, str(n))
    return draw


_DRAW = {
    "refresh": _refresh, "gear": _gear, "folder": _folder, "play": _play, "pause": _pause, "plus": _plus, "check": _check,
    "trash": _trash, "copy": _copy, "sparkle": _sparkle, "open": _link_out,
    "left": _chev(-1, 0), "right": _chev(1, 0), "down": _chev(0, 1), "up": _chev(0, -1),
    "step1": _step(1), "step2": _step(2), "step3": _step(3),
}
_FILLED = {"play", "pause"}
_DOWN_CARET_NAMES = ("down", "up")


def names() -> list[str]:
    return sorted(_DRAW)


def pixmap(name: str, size: int, color: str | QColor, dpr: float | None = None) -> QPixmap:
    dpr = dpr or max(2.0, (QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 2.0))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / GRID, size / GRID)
    col = QColor(color)
    pen = QPen(col, 2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if name in _FILLED:
        p.setPen(Qt.NoPen)
    _DRAW[name](p, col)
    p.end()
    return pm


# ---- gắn vào điều khiển, tự đổi màu theo theme/trạng thái ----
_reg: "dict[int, tuple[weakref.ref, str, int, str | None]]" = {}


def _colors(w: QWidget, role: str | None) -> tuple[str, str]:
    """(màu bình thường, màu khi bị khoá) cho điều khiển theo trạng thái hiện tại."""
    T = theme.T
    if role == "muted":
        return T["muted"], T["faint"]
    if w.property("primary"):
        dis = QColor(T["on_accent"])
        dis.setAlpha(150)
        return T["on_accent"], dis.name(QColor.HexArgb)
    if w.property("danger") or role == "danger":
        return T["err"], T["faint"]
    return T["text"], T["faint"]


def _apply(w: QWidget, name: str, size: int, role: str | None) -> None:
    normal, dis = _colors(w, role)
    if isinstance(w, QLabel):
        w.setPixmap(pixmap(name, size, normal))
        return
    ic = QIcon()
    ic.addPixmap(_padded(pixmap(name, size, normal), name), QIcon.Normal)
    ic.addPixmap(_padded(pixmap(name, size, dis), name), QIcon.Disabled)
    w.setIcon(ic)
    w.setIconSize(QSize(size + GAP, size))


GAP = 5                    # px đệm giữa icon và chữ (Qt để icon sát chữ)


def _padded(pm: QPixmap, name: str) -> QPixmap:
    """Thêm GAP px trống vào phía đối diện với chữ: mũi tên xổ (đứng bên phải chữ) đệm bên trái, icon khác đệm bên phải."""
    dpr = pm.devicePixelRatio()
    out = QPixmap(pm.width() + int(GAP * dpr), pm.height())
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(int(GAP * dpr) if name in _DOWN_CARET_NAMES else 0, 0, pm)
    p.end()
    return out


def attach(w: QWidget, name: str, size: int = DEFAULT_SIZE, role: str | None = None) -> None:
    """Gắn icon `name` vào nút/nhãn. Gọi lại với tên khác để đổi icon (vd. play <-> pause)."""
    if name not in _DRAW:
        raise KeyError(f"không có icon '{name}'")
    key = id(w)
    fresh = key not in _reg
    _reg[key] = (weakref.ref(w, lambda _r, k=key: _reg.pop(k, None)), name, size, role)
    if fresh:
        w.destroyed.connect(lambda *_a, k=key: _reg.pop(k, None))      # đối tượng C++ bị huỷ (vd. đóng hộp thoại): gỡ khỏi bảng
    _apply(w, name, size, role)


def refresh(w: QWidget) -> None:
    """Vẽ lại icon của điều khiển `w` (sau khi đổi property primary/danger)."""
    item = _reg.get(id(w))
    if item:
        try:
            _apply(w, item[1], item[2], item[3])
        except RuntimeError:
            _reg.pop(id(w), None)


def refresh_all() -> None:
    for key, (ref, name, size, role) in list(_reg.items()):
        w = ref()
        if w is None:
            continue
        try:
            _apply(w, name, size, role)
        except RuntimeError:                         # đối tượng C++ đã bị huỷ nhưng wrapper Python còn sống
            _reg.pop(key, None)


theme.on_change(refresh_all)

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


def _users(p: QPainter, fill: QColor):
    p.drawEllipse(QPointF(9, 8.2), 3.4, 3.4)
    p.drawArc(QRectF(2.8, 13.6, 12.4, 12), 0, 180 * 16)
    p.drawEllipse(QPointF(17.2, 9.2), 2.6, 2.6)
    p.drawArc(QRectF(13.6, 14.4, 8.6, 9.4), 20 * 16, 140 * 16)


def _layers(p: QPainter, fill: QColor):
    _poly(p, [(12, 3.8), (20.8, 8.6), (12, 13.4), (3.2, 8.6), (12, 3.8)])
    _poly(p, [(3.2, 12.6), (12, 17.4), (20.8, 12.6)])
    _poly(p, [(3.2, 16.4), (12, 21.2), (20.8, 16.4)])


def _lock(p: QPainter, fill: QColor):
    p.drawRoundedRect(QRectF(5.5, 10.5, 13, 9.5), 2.4, 2.4)
    path = QPainterPath(QPointF(8.5, 10.5))
    path.lineTo(8.5, 8)
    path.arcTo(QRectF(8.5, 4, 7, 7), 180, -180)
    path.lineTo(15.5, 10.5)
    p.drawPath(path)


def _more(p: QPainter, fill: QColor):
    p.setBrush(fill)
    for x in (5.5, 12, 18.5):
        p.drawEllipse(QPointF(x, 12), 1.7, 1.7)


def _send(p: QPainter, fill: QColor):
    _poly(p, [(3.5, 11.2), (20.5, 3.8), (14.2, 20.5), (11, 13.2), (3.5, 11.2)])
    p.drawLine(QPointF(11, 13.2), QPointF(20.5, 3.8))


def _link(p: QPainter, fill: QColor):
    p.save()
    p.translate(12, 12)
    p.rotate(-45)
    p.drawRoundedRect(QRectF(-9, -3.3, 9.5, 6.6), 3.3, 3.3)
    p.drawRoundedRect(QRectF(-0.5, -3.3, 9.5, 6.6), 3.3, 3.3)
    p.restore()


def _eye(p: QPainter, fill: QColor):
    path = QPainterPath(QPointF(2.8, 12))
    path.quadTo(12, 3.2, 21.2, 12)
    path.quadTo(12, 20.8, 2.8, 12)
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 3.1, 3.1)


def _ring(p: QPainter, fill: QColor):
    p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
    p.setBrush(fill)
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)


def _search(p: QPainter, fill: QColor):
    p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
    p.drawLine(QPointF(15, 15), QPointF(20, 20))


def _filter(p: QPainter, fill: QColor):
    _poly(p, [(4, 5.5), (20, 5.5), (14, 13), (14, 19), (10, 17), (10, 13), (4, 5.5)])


def _volume(p: QPainter, fill: QColor):
    p.setBrush(fill)
    p.drawPolygon(QPolygonF([QPointF(4, 9.5), QPointF(8, 9.5), QPointF(13, 5), QPointF(13, 19), QPointF(8, 14.5), QPointF(4, 14.5)]))
    p.setBrush(Qt.NoBrush)
    pen = QPen(fill, 2.2)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(10, 7.5, 7, 9), -60 * 16, 120 * 16)
    p.drawArc(QRectF(8, 4.5, 12, 15), -60 * 16, 120 * 16)


def _volume_off(p: QPainter, fill: QColor):
    p.setBrush(fill)
    p.drawPolygon(QPolygonF([QPointF(4, 9.5), QPointF(8, 9.5), QPointF(13, 5), QPointF(13, 19), QPointF(8, 14.5), QPointF(4, 14.5)]))
    p.setBrush(Qt.NoBrush)
    pen = QPen(fill, 2.2)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(16.5, 9), QPointF(21, 15))
    p.drawLine(QPointF(21, 9), QPointF(16.5, 15))


def _expand(p: QPainter, fill: QColor):
    for (x, y, dx, dy) in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
        _poly(p, [(x + dx * 6, y), (x, y), (x, y + dy * 6)])


def _doc(p: QPainter, fill: QColor):
    path = QPainterPath(QPointF(6.5, 3.5))
    path.lineTo(14, 3.5)
    path.lineTo(18.5, 8)
    path.lineTo(18.5, 19)
    path.quadTo(18.5, 20.5, 17, 20.5)
    path.lineTo(6.5, 20.5)
    path.quadTo(5, 20.5, 5, 19)
    path.lineTo(5, 5)
    path.quadTo(5, 3.5, 6.5, 3.5)
    p.drawPath(path)
    _poly(p, [(14, 3.5), (14, 8), (18.5, 8)])
    p.drawLine(QPointF(8.5, 12.5), QPointF(15, 12.5))
    p.drawLine(QPointF(8.5, 16), QPointF(13, 16))


def _edit(p: QPainter, fill: QColor):
    _poly(p, [(4.5, 19.5), (5.2, 15.4), (15.8, 4.8), (19.2, 8.2), (8.6, 18.8), (4.5, 19.5)])
    p.drawLine(QPointF(13.6, 7), QPointF(17, 10.4))


def _image(p: QPainter, fill: QColor):
    p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.4, 2.4)
    p.drawEllipse(QPointF(9, 10), 1.8, 1.8)
    _poly(p, [(4.5, 18), (10, 12.8), (14, 16.2), (16.5, 14), (19.5, 17.5)])


def _save(p: QPainter, fill: QColor):
    path = QPainterPath(QPointF(5.5, 3.5))
    path.lineTo(16.5, 3.5)
    path.lineTo(20.5, 7.5)
    path.lineTo(20.5, 19)
    path.quadTo(20.5, 20.5, 19, 20.5)
    path.lineTo(5.5, 20.5)
    path.quadTo(3.5, 20.5, 3.5, 18.5)
    path.lineTo(3.5, 5.5)
    path.quadTo(3.5, 3.5, 5.5, 3.5)
    p.drawPath(path)
    p.drawRect(QRectF(7.5, 3.5, 7, 5))
    p.drawRoundedRect(QRectF(7, 13, 10, 7.5), 1, 1)


def _info(p: QPainter, fill: QColor):
    p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
    p.setBrush(fill)
    p.drawEllipse(QPointF(12, 7.8), 1.1, 1.1)


def _sort(p: QPainter, fill: QColor):
    p.drawLine(QPointF(6, 5), QPointF(6, 19))
    _poly(p, [(3.2, 8), (6, 5), (8.8, 8)])
    p.drawLine(QPointF(14, 8), QPointF(21, 8))
    p.drawLine(QPointF(14, 12.5), QPointF(19, 12.5))
    p.drawLine(QPointF(14, 17), QPointF(17, 17))


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
    "users": _users, "layers": _layers, "lock": _lock, "more": _more, "eye": _eye, "ring": _ring,
    "search": _search, "filter": _filter, "volume": _volume, "volume_off": _volume_off, "expand": _expand, "doc": _doc, "edit": _edit,
    "image": _image, "save": _save, "info": _info, "sort": _sort, "send": _send, "link": _link,
}
_FILLED = {"play", "pause", "more", "volume", "volume_off"}
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
    if not w.text():                        # nút chỉ có icon: không đệm, để icon nằm chính giữa nút
        ic.addPixmap(pixmap(name, size, normal), QIcon.Normal)
        ic.addPixmap(pixmap(name, size, dis), QIcon.Disabled)
        w.setIcon(ic)
        w.setIconSize(QSize(size, size))
        return
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

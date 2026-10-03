"""Thành phần cơ bản: làm mới style, avatar, ảnh bo góc, nhãn cắt chữ, combo."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QComboBox, QLabel, QSizePolicy, QStyledItemDelegate, QWidget

from .. import icons


_AV: dict[tuple, QPixmap] = {}


def repolish(w: QWidget) -> None:
    """Áp lại stylesheet sau khi đổi property (Qt không tự làm)."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()
    icons.refresh(w)             # icon theo màu chữ của trạng thái mới (vd. nút đổi sang/khỏi 'primary')


def avatar(path: str, size: int) -> QPixmap:
    """Ảnh tròn lấy từ phần ĐẦU ảnh chân dung (mặt nằm ở phía trên), có bộ nhớ đệm theo (đường dẫn, kích thước, mtime)."""
    p = Path(path) if path else None
    mt = p.stat().st_mtime if p and p.exists() else 0
    key = (path, size, mt)
    if key in _AV:
        return _AV[key]
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    pm = QPixmap(path) if mt else QPixmap()
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    painter.setClipPath(clip)
    if pm.isNull():
        painter.fillRect(0, 0, size, size, QColor(128, 128, 128, 110))
    else:
        sc = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter.drawPixmap(0, 0, sc, (sc.width() - size) // 2, 0, size, size)
    painter.end()
    _AV[key] = out
    return out


def rounded_pixmap(path: str, size: int, radius: int = 16) -> QPixmap:
    """Ảnh vuông bo góc, lấy phần ĐẦU ảnh chân dung (khuôn mặt)."""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    pm = QPixmap(path) if path and Path(path).exists() else QPixmap()
    if pm.isNull():
        return out
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(clip)
    sc = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    painter.drawPixmap(0, 0, sc, (sc.width() - size) // 2, 0, size, size)
    painter.end()
    return out


class ElidedLabel(QLabel):
    """Nhãn 1 dòng, tự cắt '…' theo bề rộng: nội dung dài không bao giờ làm đổi chiều cao (tránh lệch bố cục)."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def set_full(self, text: str):
        self._full = " ".join(text.split())
        self._refit()

    def _refit(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width() - 4, 10)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def minimumSizeHint(self):
        return QSize(10, super().minimumSizeHint().height())


# ---------------------------------------------------------------- ô chọn có danh sách xổ ra theo thiết kế thẻ
class Combo(QComboBox):
    """QComboBox với danh sách xổ ra bo góc, có đệm, dòng chọn tô nhạt, xổ ngay bên dưới ô.
    Cần QStyledItemDelegate (bộ vẽ mặc định của Fusion bỏ qua stylesheet) và khung chứa trong suốt để bo góc hiện ra."""

    def __init__(self):
        super().__init__()
        self.setItemDelegate(QStyledItemDelegate(self))
        box = self.view().window()                      # khung chứa danh sách (QComboBoxPrivateContainer)
        box.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        box.setAttribute(Qt.WA_TranslucentBackground)
        self.setMaxVisibleItems(10)

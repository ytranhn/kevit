"""Giao diện thống nhất cho mọi hộp thoại thông báo/xác nhận (QMessageBox): biểu tượng rõ theo mức, chữ thường (không đậm hết),
nút tiếng Việt, nút mặc định nổi bật. Cài một lần ở mức ứng dụng nên áp cho cả các chỗ gọi QMessageBox.question/warning/... sẵn có."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QMessageBox

from . import icons, theme
from .theme import SP

SB = QMessageBox.StandardButton
LABELS = {SB.Yes: "Đồng ý", SB.No: "Không", SB.Ok: "Đóng", SB.Cancel: "Huỷ", SB.Close: "Đóng", SB.Save: "Lưu", SB.Discard: "Không lưu",
          SB.Retry: "Thử lại", SB.Open: "Mở", SB.Apply: "Áp dụng", SB.Abort: "Dừng", SB.Ignore: "Bỏ qua", SB.YesToAll: "Đồng ý tất cả",
          SB.NoToAll: "Không với tất cả", SB.Help: "Trợ giúp", SB.Reset: "Đặt lại", SB.RestoreDefaults: "Khôi phục mặc định"}
# icon theo mức: (tên icon, token màu)
KINDS = {QMessageBox.Icon.Question: ("question", "accent"), QMessageBox.Icon.Information: ("info", "accent"),
         QMessageBox.Icon.Warning: ("warn", "warn"), QMessageBox.Icon.Critical: ("warn", "err")}
ICON_SIZE = 40


def style_box(box: QMessageBox) -> None:
    """Áp kiểu thống nhất cho một hộp thoại; an toàn khi gọi nhiều lần."""
    if box.property("kevit_styled"):
        return
    box.setProperty("kevit_styled", True)
    kind = KINDS.get(box.icon())
    if kind:
        name, token = kind
        box.setIconPixmap(icons.pixmap(name, ICON_SIZE, QColor(theme.T[token])))
    standard = box.standardButtons()
    for b in box.buttons():
        sb = box.standardButton(b)
        if sb in LABELS:
            b.setText(LABELS[sb])
    # nút mặc định là nút chính (màu nhấn); chưa có nút mặc định thì lấy Đồng ý/Đóng
    default = box.defaultButton() or next((box.button(x) for x in (SB.Yes, SB.Ok, SB.Save, SB.Retry) if standard & x and box.button(x)), None)
    if default is not None:
        default.setProperty("primary", True)
        default.setFocus()
        icons.refresh(default)
        default.style().unpolish(default)
        default.style().polish(default)
    lay = box.layout()
    if isinstance(lay, QGridLayout):
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.l)
        lay.setHorizontalSpacing(SP.l)
        lay.setVerticalSpacing(SP.m)


class _Styler(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QMessageBox):
            style_box(obj)
        return False


_styler: _Styler | None = None


def install(app) -> None:
    global _styler
    if _styler is None:
        _styler = _Styler(app)
        app.installEventFilter(_styler)

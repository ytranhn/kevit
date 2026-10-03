"""Thẻ (card) 3 phần, nút điều hướng bên."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .. import icons
from ..theme import SP
from .basic import repolish, ElidedLabel


HEADER_H, FOOTER_H = 56, 68


def card_bar(layout, height: int) -> QWidget:
    """Thanh đầu/chân thẻ: chiều cao cố định (để mọi thẻ cùng hàng luôn thẳng hàng) và lề ngang thống nhất."""
    w = QWidget()
    w.setFixedHeight(height)
    layout.setContentsMargins(SP.l, 0, SP.l, 0)
    layout.setSpacing(SP.s)
    w.setLayout(layout)
    return w


def card_body(widget: QWidget, margins=(SP.l, SP.l, SP.l, SP.l)) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(*margins)
    v.setSpacing(0)
    v.addWidget(widget, 1)
    return w


def card_sep() -> QFrame:
    s = QFrame()
    s.setProperty("cardsep", True)
    s.setFixedHeight(1)
    return s


def make_card(header: QWidget, body: QWidget, footer: QWidget | None = None) -> QFrame:
    card = QFrame()
    card.setProperty("card", True)
    v = QVBoxLayout(card)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    v.addWidget(header)
    v.addWidget(card_sep())
    v.addWidget(body, 1)
    if footer is not None:
        v.addWidget(card_sep())
        v.addWidget(footer)
    return card


# ---------------------------------------------------------------- nút điều hướng breadcrumb
class NavButton(QPushButton):
    """Nút chuyển dự án/chương: tên (tự cắt '…') + nhãn tiến độ tuỳ chọn + mũi tên; bấm để mở danh sách chuyển nhanh."""

    def __init__(self, with_pill: bool = False, icon: str = "", show_pill: bool = True):
        super().__init__()
        self.setProperty("navbtn", True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._show_pill = show_pill
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 0, SP.m, 0)
        row.setSpacing(SP.s)
        if icon:
            lead = QLabel()
            icons.attach(lead, icon, 20, role="muted")
            lead.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(lead, 0, Qt.AlignVCenter)
        self.title = ElidedLabel()
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.title, 1)
        self.pill = QLabel("")
        self.pill.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pill.setVisible(False)
        self._with_pill = with_pill
        row.addWidget(self.pill, 0, Qt.AlignVCenter)        # canh giữa theo chiều dọc: không bị kéo giãn hết chiều cao nút
        caret = QLabel()
        icons.attach(caret, "down", 18, role="muted")
        caret.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(caret, 0, Qt.AlignVCenter)

    def set_title(self, text: str) -> None:
        self.title.set_full(text)

    def set_pill(self, text: str, kind: str = "info") -> None:
        if not self._with_pill:
            return
        self.pill.setText(text)
        self.pill.setVisible(bool(text) and self._show_pill)
        if self.pill.property("pill") != kind:
            self.pill.setProperty("pill", kind)
            repolish(self.pill)

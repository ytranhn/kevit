"""Hộp thoại xem lỗi của một scene: tiêu đề + ngữ cảnh, nội dung lỗi chọn/copy được (cao theo nội dung, không phình), nút hành động."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from . import icons
from .theme import SP

MIN_H, MAX_H = 72, 320


class ErrorDialog(QDialog):
    """sync_cb: nếu lỗi nhắc 'Đồng bộ Flow' thì có nút chạy thẳng thao tác đó (đóng hộp thoại rồi gọi sync_cb)."""

    def __init__(self, parent, heading: str, context: str, text: str, copy_text: str | None = None, sync_cb=None):
        super().__init__(parent)
        self.setWindowTitle("Chi tiết lỗi")
        self.setMinimumWidth(520)
        self.setMaximumWidth(760)
        self.resize(600, 10)
        self._copy = copy_text if copy_text is not None else text
        self._sync = sync_cb

        title = QLabel(heading)
        title.setProperty("heading", True)
        title.setWordWrap(True)
        ctx = QLabel(context)
        ctx.setProperty("caption", True)
        ctx.setWordWrap(True)
        self.box = QPlainTextEdit(text)
        self.box.setReadOnly(True)
        self.box.setFrameShape(QPlainTextEdit.NoFrame)
        self.box.setProperty("errorbox", True)
        self.box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.box.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.btn_copy = QPushButton("Copy lỗi")
        icons.attach(self.btn_copy, "copy", 18)
        self.btn_copy.clicked.connect(self.copy)
        self.btn_sync = QPushButton("Đồng bộ Flow")
        icons.attach(self.btn_sync, "refresh", 18)
        self.btn_sync.setVisible(bool(sync_cb) and "Đồng bộ Flow" in text)
        self.btn_sync.clicked.connect(self.run_sync)
        self.btn_close = QPushButton("Đóng")
        self.btn_close.setProperty("primary", True)
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.setSpacing(SP.s)
        foot.addStretch()
        foot.addWidget(self.btn_copy)
        foot.addWidget(self.btn_sync)
        foot.addWidget(self.btn_close)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(title)
        lay.addWidget(ctx)
        lay.addWidget(self.box, 1)
        lay.addLayout(foot)
        self.fit()

    def fit(self) -> None:
        """Cao vừa đủ nội dung (tối thiểu MIN_H, tối đa MAX_H rồi cuộn), thay vì một khung trống rất lớn."""
        self.box.document().setTextWidth(max(self.width() - 2 * SP.xl - 24, 200))
        lines = max(1, int(round(self.box.document().size().height())))   # QPlainTextEdit: kích thước tài liệu tính theo SỐ DÒNG
        h = lines * self.box.fontMetrics().lineSpacing() + 2 * int(self.box.document().documentMargin()) + 16
        self.box.setFixedHeight(max(MIN_H, min(h, MAX_H)))
        self.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        self.fit()

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self._copy)
        self.btn_copy.setText("Đã copy")
        icons.attach(self.btn_copy, "check", 18)

    def run_sync(self) -> None:
        self.accept()
        if self._sync:
            self._sync()

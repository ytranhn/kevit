"""Hộp thoại 'Dự án đã xoá': xem thùng rác dự án, khôi phục hoặc xoá vĩnh viễn."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from . import trash
from .theme import SP


def _when(stamp: str) -> str:
    return f"{stamp[6:8]}/{stamp[4:6]}/{stamp[0:4]} {stamp[9:11]}:{stamp[11:13]}" if len(stamp) >= 13 else stamp


class TrashedProjectsDialog(QDialog):
    """self.restored = tên dự án vừa khôi phục (nếu có) để cửa sổ chính mở lên."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.restored = ""
        self.setWindowTitle("Dự án đã xoá")
        self.resize(560, 460)
        head = QLabel("Dự án đã xoá")
        head.setProperty("heading", True)
        sub = QLabel("Dự án bị xoá được giữ ở đây, kèm nhân vật, clip và giọng đọc. Khôi phục để dùng lại, hoặc xoá vĩnh viễn để giải phóng ổ đĩa.")
        sub.setProperty("caption", True)
        sub.setWordWrap(True)
        self.list = QListWidget()
        self.btn_restore, self.btn_purge, btn_close = QPushButton("Khôi phục"), QPushButton("Xoá vĩnh viễn…"), QPushButton("Đóng")
        self.btn_restore.setProperty("primary", True)
        self.btn_purge.setProperty("danger", True)
        self.btn_restore.clicked.connect(self.restore)
        self.btn_purge.clicked.connect(self.purge)
        btn_close.clicked.connect(self.reject)
        self.list.currentRowChanged.connect(self._sel)
        foot = QHBoxLayout()
        foot.addWidget(self.btn_purge)
        foot.addStretch()
        foot.addWidget(btn_close)
        foot.addWidget(self.btn_restore)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        for w in (head, sub):
            lay.addWidget(w)
        lay.addWidget(self.list, 1)
        lay.addLayout(foot)
        self.fill()

    def fill(self):
        self.items = trash.list_trashed_projects()
        self.list.clear()
        for it in self.items:
            self.list.addItem(QListWidgetItem(f"{it['name']}\n{_when(it['when'])}  ·  {it['files']} file  ·  {it['bytes'] / 1_048_576:.1f} MB"))
        if self.items:
            self.list.setCurrentRow(0)
        self._sel()

    def _sel(self, *_):
        on = 0 <= self.list.currentRow() < len(self.items)
        self.btn_restore.setEnabled(on)
        self.btn_purge.setEnabled(on)

    def current(self):
        r = self.list.currentRow()
        return self.items[r] if 0 <= r < len(self.items) else None

    def restore(self):
        it = self.current()
        if not it:
            return
        self.restored = trash.restore_project(it["path"])
        self.accept()

    def purge(self):
        it = self.current()
        if not it:
            return
        if QMessageBox.warning(self, "Xoá vĩnh viễn", f"Xoá hẳn '{it['name']}' ({it['files']} file, {it['bytes'] / 1_048_576:.1f} MB)? "
                               "Không thể khôi phục sau đó.", QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            trash.purge_project(it["path"])
            self.fill()

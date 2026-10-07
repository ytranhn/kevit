"""Hộp thoại 'Tạo scene nhiều chương': chọn các chương đã có truyện để AI tách scene lần lượt."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import icons, llm
from .models import Project
from .theme import SP


class SceneBatchDialog(QDialog):
    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.p = project
        self.setWindowTitle("Tạo scene nhiều chương")
        self.setMinimumSize(680, 480)
        self.resize(780, 600)
        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(44, 44)
        tile.setAlignment(Qt.AlignCenter)
        icons.attach(tile, "layers", 24)
        title = QLabel("Tạo scene nhiều chương")
        title.setProperty("heading", True)
        sub = QLabel(f"{project.name}  ·  dùng {llm.describe()}")
        sub.setProperty("caption", True)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title)
        col.addWidget(sub)
        head = QHBoxLayout()
        head.setSpacing(SP.m)
        head.addWidget(tile)
        head.addLayout(col, 1)

        self.table = QTableWidget(len(project.chapters), 4)
        self.table.setHorizontalHeaderLabels(["", "Chương", "Truyện", "Scene hiện có"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 64)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.checks: list[QCheckBox] = []
        for r, ch in enumerate(project.chapters):
            words = len(ch.story.split())
            cells = [ch.name, f"{words:,} từ" if words else "chưa có truyện",
                     f"{len(ch.scenes)} scene  ·  {ch.done} đã gen" if ch.scenes else "chưa có"]
            for c, text in enumerate(cells, 1):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                if not words:
                    it.setForeground(self.palette().color(self.palette().ColorRole.PlaceholderText))
                self.table.setItem(r, c, it)
            cb = QCheckBox()
            cb.setEnabled(bool(words))
            cb.toggled.connect(self.refresh)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(cb, 0, Qt.AlignCenter)
            self.table.setCellWidget(r, 0, holder)
            self.table.setRowHeight(r, 42)
            self.checks.append(cb)

        b_new, b_none = QPushButton("Chọn chương chưa có scene"), QPushButton("Bỏ chọn tất cả")
        b_new.clicked.connect(lambda: self.set_default())
        b_none.clicked.connect(lambda: [cb.setChecked(False) for cb in self.checks])
        tools = QHBoxLayout()
        tools.addWidget(b_new)
        tools.addWidget(b_none)
        tools.addStretch(1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600; background: transparent;")
        note = QLabel("Các chương được tách lần lượt, mỗi chương một lượt gọi AI. Lỗi ở một chương không chặn các chương sau. "
                      "Chỉ tốn lượt gọi LLM, không tốn credit Flow.")
        note.setProperty("caption", True)
        note.setWordWrap(True)

        self.ok = QPushButton("Tạo scene")
        self.ok.setProperty("primary", True)
        cancel = QPushButton("Huỷ")
        for b in (self.ok, cancel):
            b.setFixedHeight(40)
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.l)
        lay.setSpacing(SP.m)
        lay.addLayout(head)
        lay.addLayout(tools)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.summary)
        lay.addWidget(note)
        lay.addLayout(foot)
        self.set_default()

    def set_default(self) -> None:
        """Mặc định chỉ chọn chương đã có truyện nhưng chưa có scene (không đụng tới scene/clip đã làm)."""
        for ch, cb in zip(self.p.chapters, self.checks):
            cb.setChecked(bool(ch.story.strip()) and not ch.scenes)
        self.refresh()

    def chosen(self) -> list:
        return [ch for ch, cb in zip(self.p.chapters, self.checks) if cb.isChecked()]

    def replaced(self) -> list:
        return [ch for ch in self.chosen() if ch.scenes]

    def refresh(self, *_) -> None:
        n, rep = len(self.chosen()), self.replaced()
        text = f"Sẽ tạo scene cho {n} chương." if n else "Chưa chọn chương nào."
        if rep:
            text += f"\n⚠ {len(rep)} chương đã có scene sẽ bị thay bằng scene mới (clip đã gen của các scene đó không còn gắn với chương)."
        self.summary.setText(text)
        self.ok.setEnabled(bool(n))

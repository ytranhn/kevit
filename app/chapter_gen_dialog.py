"""Hộp thoại 'Gen nhiều chương': chọn các chương cần gen, xem số scene và ước tính credit, bật/tắt tự ghép video và tự đồng bộ Flow khi lỗi."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import accounts, credits, icons, theme
from .models import Project
from .theme import SP


class ChapterGenDialog(QDialog):
    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.p = project
        self.setWindowTitle("Gen nhiều chương")
        self.setMinimumSize(720, 540)
        self.resize(820, 640)

        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(44, 44)
        tile.setAlignment(Qt.AlignCenter)
        icons.attach(tile, "layers", 24)
        title = QLabel("Gen nhiều chương")
        title.setProperty("heading", True)
        acc = accounts.get(project.account_id)
        cfg = project.flow_model + (f" {project.flow_resolution}" if project.flow_model == credits.OMNI else "")
        sub = QLabel(f"{project.name}  ·  {cfg}  ·  Flow «{acc.name}»: {accounts.describe_credits(acc)}")
        sub.setProperty("caption", True)
        sub.setWordWrap(True)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title)
        col.addWidget(sub)
        head = QHBoxLayout()
        head.setSpacing(SP.m)
        head.addWidget(tile)
        head.addLayout(col, 1)

        self.table = QTableWidget(len(project.chapters), 5)
        self.table.setHorizontalHeaderLabels(["", "Chương", "Scene xong", "Cần gen", "Ước tính"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 44)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.checks: list[QCheckBox] = []
        self.pending: list[list] = []
        for r, ch in enumerate(project.chapters):
            todo = [s for s in ch.scenes if s.status != "done"]
            self.pending.append(todo)
            est = credits.estimate(project.flow_model, project.flow_resolution, todo, project.flow_auto_duration, project.narration_lang) if todo else 0
            done = len(ch.scenes) - len(todo)
            video = "  ·  đã ghép" if (done == len(ch.scenes) and ch.scenes and project.merged_path(ch).exists()) else ""
            cells = [ch.name, f"{done}/{len(ch.scenes)}{video}" if ch.scenes else "chưa có scene", f"{len(todo)} scene" if todo else "—",
                     f"≈ {est} credit" if todo else "—"]
            for c, text in enumerate(cells, 1):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                if not todo:
                    it.setForeground(self.palette().color(self.palette().ColorRole.PlaceholderText))
                self.table.setItem(r, c, it)
            cb = QCheckBox()
            cb.setEnabled(bool(todo))
            cb.setChecked(False)
            cb.toggled.connect(self.refresh)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(SP.m, 0, 0, 0)
            hl.addWidget(cb)
            self.table.setCellWidget(r, 0, holder)
            self.table.setRowHeight(r, 42)
            self.checks.append(cb)

        b_pend, b_none = QPushButton("Chọn mọi chương chưa xong"), QPushButton("Bỏ chọn tất cả")
        b_pend.clicked.connect(lambda: self.set_all(True))
        b_none.clicked.connect(lambda: self.set_all(False))
        tools = QHBoxLayout()
        tools.addWidget(b_pend)
        tools.addWidget(b_none)
        tools.addStretch(1)

        self.auto_merge = QCheckBox("Tự ghép video khi chương gen xong (đủ mọi scene)")
        self.auto_merge.setChecked(project.gen_auto_merge)
        self.auto_sync = QCheckBox("Tự đồng bộ với Flow khi có scene lỗi (không tốn credit)")
        self.auto_sync.setChecked(project.gen_auto_sync)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600; background: transparent;")
        note = QLabel("Các chương được gen lần lượt, từng chương một. Lỗi ở một chương không chặn các chương sau; hết credit hoặc bấm Dừng thì cả lô dừng.")
        note.setProperty("caption", True)
        note.setWordWrap(True)

        self.ok = QPushButton("Bắt đầu gen")
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
        lay.addWidget(self.auto_merge)
        lay.addWidget(self.auto_sync)
        lay.addWidget(self.summary)
        lay.addWidget(note)
        lay.addLayout(foot)
        self.set_all(True)

    def set_all(self, on: bool) -> None:
        for cb, todo in zip(self.checks, self.pending):
            cb.setChecked(on and bool(todo))
        self.refresh()

    def chosen(self) -> list:
        return [ch for ch, cb in zip(self.p.chapters, self.checks) if cb.isChecked()]

    def refresh(self, *_) -> None:
        scenes = [s for todo, cb in zip(self.pending, self.checks) if cb.isChecked() for s in todo]
        est = credits.estimate(self.p.flow_model, self.p.flow_resolution, scenes, self.p.flow_auto_duration, self.p.narration_lang) if scenes else 0
        n = len(self.chosen())
        text = f"Sẽ gen {len(scenes)} scene của {n} chương  ·  ước tính ≈ {est} credit" if scenes else "Chưa chọn chương nào."
        acc = accounts.get(self.p.account_id)
        over = bool(scenes) and acc.credits is not None and est > acc.credits
        if over:
            text += (f"\n⚠ Vượt credit hiện có của «{acc.name}» ({accounts.fmt_credits(acc.credits)}). "
                     + ("Kevit sẽ tự chuyển tài khoản khi hết credit." if accounts.auto_switch() and len(accounts.all_accounts()) > 1
                        else "Nạp thêm credit, bật tự chuyển tài khoản, hoặc chọn ít chương hơn."))
        self.summary.setText(text)
        self.summary.setStyleSheet(f"font-weight: 600; background: transparent; color: {theme.T['warn'] if over else theme.T['text']};")
        self.ok.setEnabled(bool(scenes))

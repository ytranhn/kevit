"""Màn hình chào mừng cho lần mở đầu tiên (chưa có dự án nào): danh sách bước thiết lập có trạng thái thật + nút hành động."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import flow_auto, llm, models
from .theme import SP
from .widgets import repolish


class Step(QFrame):
    def __init__(self, no: int, title: str, hint: str, action: str, callback):
        super().__init__()
        self.setProperty("stepRow", True)
        self.no = no
        self.setMinimumHeight(80)
        self.badge = QLabel(str(no))
        self.badge.setProperty("stepnum", True)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedSize(28, 28)
        t = QLabel(title)
        t.setProperty("subheading", True)
        h = QLabel(hint)
        h.setProperty("caption", True)
        h.setWordWrap(True)
        col = QVBoxLayout()
        col.setSpacing(SP.xs)
        col.addWidget(t)
        col.addWidget(h)
        self.state = QLabel("")
        self.btn = QPushButton(action)
        self.btn.clicked.connect(callback)
        self.btn.setMinimumWidth(132)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
        lay.setSpacing(SP.m)
        lay.addWidget(self.badge, 0, Qt.AlignTop)
        lay.addLayout(col, 1)
        lay.addWidget(self.state, 0, Qt.AlignVCenter)
        lay.addWidget(self.btn, 0, Qt.AlignVCenter)

    def set_state(self, done: bool, text: str, enabled: bool = True) -> None:
        self.badge.setText("✓" if done else str(self.no))
        self.badge.setProperty("done", done)
        self.state.setText(text)
        self.state.setProperty("pill", "ok" if done else "info")
        self.btn.setEnabled(enabled)
        self.btn.setProperty("primary", not done and enabled)
        for w in (self.badge, self.state, self.btn):
            repolish(w)


class Welcome(QWidget):
    """Phủ kín ProjectTab khi chưa có dự án. Các nút gọi lại hành động do cửa sổ chính cung cấp."""

    def __init__(self, parent, on_settings, on_new_project, on_flow):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("welcome", True)
        logo = QLabel()
        pm = QPixmap(str(models.resource_path("assets/icon.png")))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        title = QLabel("Chào mừng đến Kevit")
        title.setProperty("heading", True)
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Biến truyện chữ thành video kể chuyện (9:16, 16:9 hoặc theo Flow) có người dẫn truyện.\nLàm lần lượt các bước dưới đây, chỉ cần một lần.")
        sub.setProperty("caption", True)
        sub.setAlignment(Qt.AlignCenter)
        self.s_llm = Step(1, "Kết nối mô hình AI", "Claude (qua proxy) hoặc Gemini, dùng để tách truyện thành scene.", "Mở Cài đặt", on_settings)
        self.s_flow = Step(2, "Đăng nhập Google Flow", "Mở Chrome riêng và đăng nhập một lần, tool sẽ tự thao tác để tạo clip.", "Mở Chrome Flow", on_flow)
        self.s_proj = Step(3, "Tạo dự án đầu tiên", "Mỗi bộ truyện là một dự án, gồm nhiều chương và scene.", "Tạo dự án", on_new_project)
        steps = QVBoxLayout()
        steps.setSpacing(SP.s)
        for s in (self.s_llm, self.s_flow, self.s_proj):
            steps.addWidget(s)
        nxt = QLabel("Sau đó: thêm nhân vật ở tab Nhân vật → dán truyện vào tab Truyện → bấm ① Tạo scene → ② Gen video → ③ Ghép video.")
        nxt.setProperty("caption", True)
        nxt.setWordWrap(True)
        nxt.setAlignment(Qt.AlignCenter)
        panel = QWidget()
        panel.setMinimumWidth(640)
        panel.setMaximumWidth(700)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(SP.m)
        pv.addWidget(logo)
        pv.addWidget(title)
        pv.addWidget(sub)
        pv.addSpacing(SP.m)
        pv.addLayout(steps)
        pv.addSpacing(SP.s)
        pv.addWidget(nxt)
        root = QVBoxLayout(self)
        root.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        root.addStretch(1)
        root.addWidget(panel, 0, Qt.AlignHCenter)
        root.addStretch(2)
        self.refresh()

    def refresh(self) -> None:
        ok, _ = llm.is_configured()
        self.s_llm.set_state(ok, f"Đã kết nối · {llm.short_name()}" if ok else "Chưa kết nối")
        names = models.Project.list_names()
        self.s_proj.set_state(bool(names), f"{len(names)} dự án" if names else "Chưa có")
        up = flow_auto.cdp_state()
        self.s_flow.set_state(up, "Chrome đang mở" if up else "Chưa mở")

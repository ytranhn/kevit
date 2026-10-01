"""Tab Cài đặt chung, chia mục: Mô hình AI (nhiều LLM) · Gemini · Google Flow (nhiều tài khoản) · Dữ liệu.
Mỗi mục là một trang riêng (cuộn được, canh giữa một cột) để không phải kéo qua một danh sách dài."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import flow, models, settings
from .accounts_ui import AccountsPanel
from .llm_panel import LLMPanel, card, field, secret
from .theme import SP
from .widgets import Segmented, repolish

SECTIONS = (("llm", "Mô hình AI"), ("gemini", "Gemini"), ("flow", "Google Flow"), ("data", "Dữ liệu"))


def _page(*cards: QWidget) -> QScrollArea:
    """Một trang: các thẻ xếp một cột, canh giữa, rộng tối đa 680px; dài hơn cửa sổ thì cuộn."""
    col = QVBoxLayout()
    col.setSpacing(SP.l)
    for c in cards:
        col.addWidget(c)
    col.addStretch()
    holder = QWidget()
    holder.setMaximumWidth(680)
    holder.setLayout(col)
    row = QHBoxLayout()
    row.setContentsMargins(0, SP.s, SP.m, SP.l)
    row.addStretch(1)
    row.addWidget(holder, 100)
    row.addStretch(1)
    page = QWidget()
    page.setLayout(row)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(page)
    return scroll


class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        # ---- mục 1: mô hình AI ----
        self.llm_panel = LLMPanel()
        self.llm_changed = self.llm_panel.changed

        # ---- mục 2: Gemini ----
        self.gemini_key = secret(settings.get_api_key(), "AIza…")
        self.image_model = QLineEdit(settings.image_model())
        self.image_model.setPlaceholderText(settings.IMAGE_DEFAULT_MODEL)
        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        btn_save = QPushButton("Lưu")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self.save)
        gem_card = card("Gemini API", "Khoá dùng chung cho các việc chạy trực tiếp qua Gemini: gen video (Veo), giọng đọc Gemini TTS, tạo ảnh "
                        "nhân vật. Mô hình Gemini làm “mô hình AI” có thể dùng khoá riêng ở mục Mô hình AI (để trống thì dùng khoá này).")
        gem_card.layout().addWidget(field("API key", "Lấy từ Google AI Studio.", self.gemini_key))
        gem_card.layout().addWidget(field("Model tạo ảnh nhân vật", "Dùng cho “Tạo nhân vật từ truyện”. Ví dụ gemini-2.5-flash-image "
                                          "hoặc gemini-3.1-flash-image (giá khác nhau theo bảng giá Gemini API).", self.image_model))
        foot = QHBoxLayout()
        foot.addWidget(self.status, 0, Qt.AlignVCenter)
        foot.addStretch(1)
        foot.addWidget(btn_save)
        gem_card.layout().addLayout(foot)

        # ---- mục 3: Google Flow (nhiều tài khoản) ----
        acc_card = card("Tài khoản Google Flow")
        self.accounts_panel = AccountsPanel()
        acc_card.layout().addWidget(self.accounts_panel)
        self.accounts_changed = self.accounts_panel.changed

        # ---- mục 4: dữ liệu ----
        data_card = card("Dữ liệu", "Dự án, nhân vật, clip và đăng nhập Chrome Flow. Dữ liệu nằm ngoài ứng dụng nên "
                                    "cập nhật hay build lại app không làm mất.")
        self.data_path = QLabel(str(models.DATA_DIR))
        self.data_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.data_path.setWordWrap(True)
        self.data_path.setProperty("mono", True)
        b_change, b_open = QPushButton("Đổi thư mục…"), QPushButton("Mở thư mục")
        b_change.clicked.connect(self.change_data_dir)
        b_open.clicked.connect(lambda: (models.DATA_DIR.mkdir(parents=True, exist_ok=True), flow.reveal(models.DATA_DIR)))
        data_card.layout().addWidget(self.data_path)
        drow = QHBoxLayout()
        drow.addStretch()
        drow.addWidget(b_open)
        drow.addWidget(b_change)
        data_card.layout().addLayout(drow)

        # ---- khung: tiêu đề + bộ chọn mục + trang ----
        head = QLabel("Cài đặt")
        head.setProperty("heading", True)
        sub = QLabel("Cấu hình dùng chung cho mọi dự án.")
        sub.setProperty("caption", True)
        self.seg = Segmented()
        for key, label in SECTIONS:
            self.seg.addItem(label, key)
        self.stack = QStackedWidget()
        self.stack.addWidget(_page(self.llm_panel))
        self.stack.addWidget(_page(gem_card))
        self.stack.addWidget(_page(acc_card))
        self.stack.addWidget(_page(data_card))
        self.seg.currentIndexChanged.connect(self.stack.setCurrentIndex)
        top = QVBoxLayout()
        top.setSpacing(SP.xs)
        top.addWidget(head)
        top.addWidget(sub)
        bar = QHBoxLayout()
        bar.addWidget(self.seg)
        bar.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SP.m)
        outer.addLayout(top)
        outer.addLayout(bar)
        outer.addWidget(self.stack, 1)
        self.seg.setCurrentIndex(0)

    def select_section(self, key: str) -> None:
        i = self.seg.findData(key)
        if i >= 0:
            self.seg.setCurrentIndex(i)

    def show_status(self, text: str, kind: str) -> None:
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)

    def save(self) -> None:
        settings.set_api_key(self.gemini_key.edit.text())
        settings.set_image_model(self.image_model.text())
        self.show_status("Đã lưu.", "ok")

    def change_data_dir(self):
        start = str(models.DATA_DIR if models.DATA_DIR.exists() else Path.home())
        f = QFileDialog.getExistingDirectory(self, "Chọn thư mục dữ liệu (thư mục chứa “projects”)", start)
        if not f or Path(f) == models.DATA_DIR:
            return
        has = models.has_projects(Path(f))
        models.set_data_dir(Path(f))
        self.data_path.setText(str(models.DATA_DIR))
        QMessageBox.information(
            self, "Đã đổi thư mục dữ liệu",
            ("Tìm thấy dự án trong thư mục này. " if has else "Thư mục này chưa có dự án nào (sẽ tạo mới khi bạn tạo dự án). ")
            + "\n\nHãy đóng và mở lại ứng dụng để áp dụng. Dữ liệu ở thư mục cũ không bị xoá hay di chuyển.")

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
from .publish_panel import PublishSettingsPanel
from .shell import PageHeader, SideNav, info_banner
from .theme import SP
from .widgets import repolish

SECTIONS = (("llm", "Mô hình AI", "Quản lý các mô hình AI", "layers"),
            ("gemini", "Gemini", "Cấu hình Gemini API", "sparkle"),
            ("flow", "Google Flow", "Quản lý tài khoản và credit", "ring"),
            ("publish", "Đăng video", "Kết nối YouTube, TikTok, Facebook, Instagram", "open"),
            ("data", "Dữ liệu", "Thư mục lưu dự án, nhân vật…", "folder"))
LOCAL_NOTE = ("Lưu trữ cục bộ", "API key và cấu hình được lưu trên máy của bạn, không đồng bộ lên server. Hãy bảo mật thiết bị của bạn.")


def _page(header: QWidget, *blocks: QWidget, wide: bool = False) -> QScrollArea:
    """Một trang cài đặt: tiêu đề lớn + các thẻ xếp một cột (rộng tối đa 1000px); dài hơn cửa sổ thì cuộn."""
    col = QVBoxLayout()
    col.setSpacing(SP.l)
    col.addWidget(header)
    col.addSpacing(SP.xs)
    for b in blocks:
        col.addWidget(b)
    col.addStretch()
    holder = QWidget()
    holder.setMaximumWidth(1400 if wide else 1000)
    holder.setLayout(col)
    row = QHBoxLayout()
    row.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
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
        btn_save.setFixedHeight(40)
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
        self.accounts_panel = AccountsPanel()
        self.accounts_changed = self.accounts_panel.changed

        # ---- mục 4: đăng video lên các nền tảng ----
        self.publish_panel = PublishSettingsPanel()
        self.publish_changed = self.publish_panel.changed

        # ---- mục 5: dữ liệu ----
        data_card = card("Thư mục dữ liệu", "Dự án, nhân vật, clip và đăng nhập Chrome Flow. Dữ liệu nằm ngoài ứng dụng nên "
                                            "cập nhật hay build lại app không làm mất.")
        self.data_path = QLabel(str(models.DATA_DIR))
        self.data_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.data_path.setWordWrap(True)
        self.data_path.setProperty("mono", True)
        b_change, b_open = QPushButton("Đổi thư mục…"), QPushButton("Mở thư mục")
        for b in (b_change, b_open):
            b.setFixedHeight(40)
        b_change.clicked.connect(self.change_data_dir)
        b_open.clicked.connect(lambda: (models.DATA_DIR.mkdir(parents=True, exist_ok=True), flow.reveal(models.DATA_DIR)))
        data_card.layout().addWidget(self.data_path)
        drow = QHBoxLayout()
        drow.addStretch()
        drow.addWidget(b_open)
        drow.addWidget(b_change)
        data_card.layout().addLayout(drow)

        # ---- khung: thanh điều hướng bên + các trang ----
        self.seg = SideNav("Cài đặt", "Cấu hình dùng chung cho mọi dự án.")
        for key, label, sub, icon in SECTIONS:
            self.seg.addItem(label, key, sub, icon)
        self.stack = QStackedWidget()
        self.stack.addWidget(_page(PageHeader("Mô hình AI", "Các mô hình dùng để tách scene, viết lại thuyết minh, dịch và tạo nhân vật. "
                                              "Thêm bao nhiêu tuỳ ý (Claude, Gemini, OpenAI và các dịch vụ tương thích), chọn một cái đang dùng; "
                                              "đổi nhanh ở chip LLM dưới cùng.", self.llm_panel.b_add),
                                   self.llm_panel, info_banner("lock", *LOCAL_NOTE)))
        self.stack.addWidget(_page(PageHeader("Gemini", "Khoá Gemini API cho gen video (Veo), giọng đọc Gemini TTS và tạo ảnh nhân vật."),
                                   gem_card, info_banner("lock", *LOCAL_NOTE)))
        self.stack.addWidget(_page(PageHeader("Google Flow", "Quản lý tài khoản Google Flow, theo dõi credit và tự động chuyển tài khoản khi hết credit.",
                                              self.accounts_panel.b_add, self.accounts_panel.b_open),
                                   self.accounts_panel, wide=True))
        self.stack.addWidget(_page(PageHeader("Đăng video", "Kết nối tài khoản để Kevit tự đăng video lên YouTube, TikTok, Facebook và Instagram bằng API chính thức. "
                                              "Mỗi nền tảng cần một ứng dụng bạn tự đăng ký (miễn phí) để lấy khoá."),
                                   self.publish_panel, info_banner("lock", *LOCAL_NOTE)))
        self.stack.addWidget(_page(PageHeader("Dữ liệu", "Nơi lưu dự án, nhân vật, clip và đăng nhập Chrome Flow."), data_card))
        self.seg.currentIndexChanged.connect(self.stack.setCurrentIndex)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.seg)
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

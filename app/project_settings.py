"""Hộp thoại 'Cài đặt dự án': các nhóm thẻ có nhãn rõ ràng thay cho lưới ô nhập chen chúc."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import credits
from .theme import SP


def _row(title: str, hint: str, control: QWidget) -> QWidget:
    """Dòng thiết lập: tên + mô tả ở bên trái, điều khiển ngắn ở bên phải."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(SP.l)
    col = QVBoxLayout()
    col.setSpacing(SP.xs)
    t = QLabel(title)
    col.addWidget(t)
    if hint:
        c = QLabel(hint)
        c.setProperty("caption", True)
        c.setWordWrap(True)
        col.addWidget(c)
    h.addLayout(col, 1)
    h.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
    return w


def _field(title: str, hint: str, control: QWidget) -> QWidget:
    """Ô nhập dài: nhãn phía trên, ô nhập chiếm hết chiều ngang."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(SP.xs)
    v.addWidget(QLabel(title))
    v.addWidget(control)
    if hint:
        c = QLabel(hint)
        c.setProperty("caption", True)
        c.setWordWrap(True)
        v.addWidget(c)
    return w


def _card(title: str, subtitle: str, *rows: QWidget) -> QFrame:
    card = QFrame()
    card.setProperty("card", True)
    v = QVBoxLayout(card)
    v.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
    v.setSpacing(SP.m)
    head = QLabel(title)
    head.setProperty("subheading", True)
    v.addWidget(head)
    if subtitle:
        s = QLabel(subtitle)
        s.setProperty("caption", True)
        s.setWordWrap(True)
        v.addWidget(s)
    for r in rows:
        v.addWidget(r)
    return card


class ProjectSettingsDialog(QDialog):
    """Dùng lại các ô nhập đang nằm trên ProjectTab (tab.style, tab.aspect...) — dialog chỉ sắp xếp lại giao diện.
    Huỷ thì khôi phục giá trị cũ; Lưu thì gọi tab.save_edits()."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setWindowTitle("Cài đặt dự án")
        self.resize(660, 720)
        self._snap: dict = {}

        self.cost = QLabel("")
        self.cost.setProperty("pill", "ok")
        tab.style.setPlaceholderText("Ví dụ: cinematic, soft lighting, 35mm film look")
        tab.voice_style.setPlaceholderText("Chỉ áp dụng cho Gemini TTS")

        visual = _card(
            "Hình ảnh & video", "Áp dụng cho mọi prompt gửi Flow trong dự án này.",
            _field("Phong cách hình ảnh", "Được gắn vào cuối mọi prompt. Viết bằng tiếng Anh sẽ cho kết quả ổn định nhất.", tab.style),
            _row("Khổ video", "9:16 cho điện thoại, 16:9 cho màn ngang; “Theo Flow” giữ nguyên khổ đang chọn trong Flow (tool không đổi).", tab.aspect))
        scenes = _card(
            "Tách scene", "",
            _row("Cảnh báo khi chương vượt số scene", "Chỉ để cảnh báo chi phí. Tool luôn tách đủ scene để thuyết minh giữ ≥70% nội dung truyện.", tab.max_scenes))
        flow = _card(
            "Google Flow", "Model và độ dài ảnh hưởng trực tiếp đến credit.",
            _row("Model", "Veo 3.1 Lite rẻ nhất ở 720p; Omni linh hoạt thời lượng.", tab.flow_model),
            _row("Độ phân giải", "Chỉ Omni có 360p (rẻ, hợp bản nháp).", tab.flow_res),
            tab.flow_auto_dur,
            _row("Ước tính", "Cho mỗi scene dài 8 giây.", self.cost))
        voice = _card(
            "Giọng đọc", "Một giọng duy nhất cho cả dự án để người nghe không thấy lệch giữa các scene.",
            _row("Nguồn giọng", "Edge miễn phí, Gemini cần API key.", tab.provider),
            _row("Giọng", "", tab.voice),
            _field("Phong cách đọc", "Edge TTS không nhận chỉ dẫn phong cách.", tab.voice_style))

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, SP.m, 0)
        bv.setSpacing(SP.l)
        self.sub = QLabel("")
        self.sub.setProperty("caption", True)
        head = QLabel("Cài đặt dự án")
        head.setProperty("heading", True)
        bv.addWidget(head)
        bv.addWidget(self.sub)
        for c in (visual, scenes, flow, voice):
            bv.addWidget(c)
        bv.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)

        cancel = QPushButton("Huỷ")
        save = QPushButton("Lưu")
        save.setProperty("primary", True)
        save.setDefault(True)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(cancel)
        foot.addWidget(save)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.l)
        lay.addWidget(scroll, 1)
        lay.addLayout(foot)

        tab.flow_model.currentIndexChanged.connect(self.refresh_flow)
        tab.flow_res.currentIndexChanged.connect(self.refresh_flow)
        tab.flow_auto_dur.toggled.connect(self.refresh_flow)
        tab.provider.currentIndexChanged.connect(self.refresh_voice)

    # ---- trạng thái điều khiển phụ thuộc nhau ----
    def refresh_flow(self) -> None:
        omni = self.tab.flow_model.currentText() == credits.OMNI
        self.tab.flow_res.setEnabled(omni)
        self.tab.flow_auto_dur.setEnabled(omni)
        res = self.tab.flow_res.currentText()
        auto = omni and self.tab.flow_auto_dur.isChecked()
        per = credits.scene_cost(self.tab.flow_model.currentText(), res, 8)
        self.cost.setText(f"≈ {per} credit / scene" + ("  ·  ngắn hơn nếu thuyết minh ngắn" if auto else ""))

    def refresh_voice(self) -> None:
        self.tab.voice_style.setEnabled(self.tab.provider.currentData() == "gemini")

    # ---- mở / đóng ----
    def snapshot(self) -> dict:
        t = self.tab
        return dict(style=t.style.text(), aspect=t.aspect.currentIndex(), scenes=t.max_scenes.value(),
                    model=t.flow_model.currentText(), res=t.flow_res.currentText(), auto=t.flow_auto_dur.isChecked(),
                    provider=t.provider.currentIndex(), voice=t.voice.currentText(), vstyle=t.voice_style.text())

    def restore(self, s: dict) -> None:
        t = self.tab
        t.style.setText(s["style"]); t.aspect.setCurrentIndex(s["aspect"]); t.max_scenes.setValue(s["scenes"])
        t.flow_model.setCurrentText(s["model"]); t.flow_res.setCurrentText(s["res"]); t.flow_auto_dur.setChecked(s["auto"])
        t.provider.setCurrentIndex(s["provider"]); t.voice.setCurrentText(s["voice"]); t.voice_style.setText(s["vstyle"])

    def open_for_project(self) -> bool:
        """Hiện hộp thoại; trả True nếu người dùng bấm Lưu."""
        self.sub.setText(self.tab.project.name if self.tab.project else "")
        self.refresh_flow()
        self.refresh_voice()
        self._snap = self.snapshot()
        if self.exec() == QDialog.Accepted:
            self.tab.save_edits()
            return True
        self.restore(self._snap)
        return False

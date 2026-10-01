"""Quản lý nhiều mô hình LLM (tab Cài đặt → Mô hình AI): danh sách, thêm từ mẫu (Claude, Gemini, OpenAI-compatible...), sửa, nhân bản, xoá,
chọn mô hình đang dùng và thử kết nối từng mô hình."""
from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from . import icons, llm, settings
from .theme import SP
from .widgets import repolish
from .workers import Worker


def card(title: str, subtitle: str = "") -> QFrame:
    c = QFrame()
    c.setProperty("card", True)
    v = QVBoxLayout(c)
    v.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
    v.setSpacing(SP.m)
    t = QLabel(title)
    t.setProperty("subheading", True)
    v.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setProperty("caption", True)
        s.setWordWrap(True)
        v.addWidget(s)
    return c


def field(title: str, hint: str, control: QWidget) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(SP.xs)
    v.addWidget(QLabel(title))
    v.addWidget(control)
    w.hint = QLabel(hint)
    w.hint.setProperty("caption", True)
    w.hint.setWordWrap(True)
    w.hint.setVisible(bool(hint))
    v.addWidget(w.hint)
    return w


def secret(value: str = "", placeholder: str = "") -> QWidget:
    """Ô nhập khoá có nút Hiện/Ẩn. Dùng .edit để đọc giá trị."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(SP.s)
    e = QLineEdit(value)
    e.setEchoMode(QLineEdit.Password)
    e.setPlaceholderText(placeholder)
    b = QPushButton("Hiện")
    b.setProperty("ghost", True)
    b.setCheckable(True)

    def flip(on):
        e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        b.setText("Ẩn" if on else "Hiện")
    b.toggled.connect(flip)
    h.addWidget(e, 1)
    h.addWidget(b)
    w.edit = e
    return w


class LLMPanel(QWidget):
    changed = Signal()            # danh sách mô hình hoặc mô hình đang dùng vừa đổi

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles: list[settings.LLMProfile] = settings.llm_profiles()
        self.active_id = settings.active_llm_id()
        self._row = -1
        self.worker = None

        # ---- thẻ danh sách ----
        self.list = QListWidget()
        self.list.setFixedHeight(190)
        self.list.setSpacing(2)
        self.b_add = QPushButton("Thêm mô hình")
        icons.attach(self.b_add, "plus", 18)
        self.add_menu = QMenu(self.b_add)
        for label, kind, base, model in settings.LLM_PRESETS:
            self.add_menu.addAction(label, lambda l=label, k=kind, b=base, m=model: self.add_preset(l, k, b, m))
        self.b_add.setMenu(self.add_menu)
        self.b_dup = QPushButton("Nhân bản")
        self.b_del = QPushButton("Xoá")
        self.b_del.setProperty("danger", True)
        self.b_use = QPushButton("Dùng mô hình này")
        self.b_use.setProperty("primary", True)
        self.list_card = card("Mô hình AI", "Các mô hình dùng để tách scene, viết lại thuyết minh, dịch và tạo nhân vật. Thêm bao nhiêu tuỳ ý "
                              "(Claude, Gemini, OpenAI và các dịch vụ tương thích), chọn một cái đang dùng; đổi nhanh ở chip LLM dưới cùng.")
        lv = self.list_card.layout()
        lv.addWidget(self.list)
        r = QHBoxLayout()
        r.setSpacing(SP.s)
        r.addWidget(self.b_add)
        r.addWidget(self.b_dup)
        r.addWidget(self.b_del)
        r.addStretch()
        r.addWidget(self.b_use)
        lv.addLayout(r)

        # ---- thẻ chỉnh sửa ----
        self.name = QLineEdit()
        self.kind_label = QLabel("")
        self.kind_label.setProperty("caption", True)
        self.key = secret()
        self.base = QLineEdit()
        self.model = QLineEdit()
        self.proxy = QLineEdit()
        self.proxy.setPlaceholderText("http://127.0.0.1:7890")
        self.f_name = field("Tên hiển thị", "", self.name)
        self.f_key = field("API key", "Key của bạn hoặc do nhà cung cấp/proxy cấp. Được lưu trên máy này.", self.key)
        self.f_base = field("Địa chỉ API", "", self.base)
        self.f_model = field("Model", "", self.model)
        self.f_proxy = field("HTTP proxy mạng (tuỳ chọn)", "Chỉ cần khi máy phải đi qua proxy mạng.", self.proxy)
        self.edit_card = card("Cấu hình mô hình đang chọn")
        self.edit_card.layout().addWidget(self.kind_label)
        for f in (self.f_name, self.f_key, self.f_base, self.f_model, self.f_proxy):
            self.edit_card.layout().addWidget(f)
        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        self.btn_test = QPushButton("Lưu và thử kết nối")
        self.btn_save = QPushButton("Lưu")
        self.btn_save.setProperty("primary", True)
        foot = QHBoxLayout()
        foot.setSpacing(SP.s)
        foot.addWidget(self.status, 0, Qt.AlignVCenter)
        foot.addStretch(1)
        foot.addWidget(self.btn_test)
        foot.addWidget(self.btn_save)
        self.edit_card.layout().addLayout(foot)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SP.l)
        v.addWidget(self.list_card)
        v.addWidget(self.edit_card)

        self.list.currentRowChanged.connect(self.on_select)
        self.b_dup.clicked.connect(self.duplicate)
        self.b_del.clicked.connect(self.delete)
        self.b_use.clicked.connect(self.use)
        self.btn_save.clicked.connect(self.save)
        self.btn_test.clicked.connect(self.test)
        self.refresh_list(self.active_id)

    # ---- hiển thị ----
    def current(self) -> settings.LLMProfile | None:
        return self.profiles[self._row] if 0 <= self._row < len(self.profiles) else None

    def refresh_list(self, select_id: str | None = None) -> None:
        keep = select_id or (self.current().id if self.current() else self.active_id)
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.profiles:
            tag = "  ·  đang dùng" if p.id == self.active_id else ""
            it = QListWidgetItem(f"{p.name}{tag}\n{p.kind_label.split(" (")[0]}  ·  {p.effective_model}")
            it.setData(Qt.UserRole, p.id)
            self.list.addItem(it)
        self._row = next((i for i, p in enumerate(self.profiles) if p.id == keep), 0)
        self.list.setCurrentRow(self._row)
        self.list.blockSignals(False)
        self.load_form()
        self.update_buttons()

    def load_form(self) -> None:
        p = self.current()
        if p is None:
            return
        meta = settings.LLM_KINDS[p.kind]
        self.kind_label.setText(f"Loại: {p.kind_label}")
        self.name.setText(p.name)
        self.key.edit.setText(p.api_key)
        self.key.edit.setPlaceholderText(meta["key_hint"])
        self.base.setText(p.base_url)
        self.base.setPlaceholderText(meta["base_hint"])
        self.f_base.hint.setText(meta["base_note"])
        self.f_base.hint.setVisible(bool(meta["base_note"]))
        self.f_base.setVisible(p.kind != "gemini")
        self.f_proxy.setVisible(p.kind != "gemini")
        self.model.setText(p.model)
        self.model.setPlaceholderText(meta["model"])
        self.f_model.hint.setText({"claude": "Tên model theo nhà cung cấp/proxy của bạn.", "gemini": "Vd. gemini-3.8-flash.",
                                   "openai": "Tên model theo dịch vụ (vd. gpt-4o-mini, deepseek-chat, llama3.1)."}[p.kind])
        self.f_model.hint.setVisible(True)
        self.status.setText("")

    def update_buttons(self) -> None:
        p = self.current()
        self.b_use.setEnabled(p is not None and p.id != self.active_id)
        self.b_del.setEnabled(len(self.profiles) > 1)
        self.b_dup.setEnabled(p is not None)

    def show_status(self, text: str, kind: str) -> None:
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)

    # ---- chỉnh dữ liệu ----
    def commit_form(self) -> None:
        """Ghi các ô nhập vào mô hình đang chọn."""
        p = self.current()
        if p is None:
            return
        p.name = self.name.text().strip() or p.name
        p.api_key = self.key.edit.text().strip()
        p.base_url = (settings.normalize_base_url(self.base.text()) if p.kind == "claude"
                      else settings.normalize_openai_base(self.base.text()) if p.kind == "openai" else "")
        p.model = self.model.text().strip()
        p.proxy = self.proxy.text().strip() if p.kind != "gemini" else ""

    def persist(self) -> None:
        settings.save_llm_profiles(self.profiles, self.active_id)
        self.changed.emit()

    def on_select(self, row: int) -> None:
        if row < 0:
            return
        self.commit_form()                                # sửa dở ở mô hình trước được giữ lại, không mất khi chuyển mô hình
        self.persist()
        self._row = row
        self.load_form()
        self.refresh_list_text()
        self.update_buttons()

    def refresh_list_text(self) -> None:
        for i, p in enumerate(self.profiles):
            it = self.list.item(i)
            if it:
                tag = "  ·  đang dùng" if p.id == self.active_id else ""
                it.setText(f"{p.name}{tag}\n{p.kind_label.split(" (")[0]}  ·  {p.effective_model}")

    def add_preset(self, label: str, kind: str, base: str, model: str) -> None:
        self.commit_form()
        names = {p.name.casefold() for p in self.profiles}
        name, n = label, 1
        while name.casefold() in names:
            n += 1
            name = f"{label} {n}"
        p = settings.LLMProfile(settings.new_profile_id(), name, kind, "", base if base != "https://" else "", model)
        self.profiles.append(p)
        self.persist()
        self.refresh_list(p.id)
        self.show_status("Đã thêm. Nhập API key rồi bấm “Lưu và thử kết nối”.", "info")
        self.key.edit.setFocus()

    def duplicate(self) -> None:
        self.commit_form()
        p = self.current()
        if p is None:
            return
        q = dataclasses.replace(p, id=settings.new_profile_id(), name=f"{p.name} (bản sao)")
        self.profiles.append(q)
        self.persist()
        self.refresh_list(q.id)

    def delete(self) -> None:
        p = self.current()
        if p is None or len(self.profiles) <= 1:
            return
        if QMessageBox.question(self, "Xoá mô hình", f"Xoá “{p.name}”? API key đã nhập cho mô hình này cũng bị xoá.") != QMessageBox.Yes:
            return
        self.profiles = [x for x in self.profiles if x.id != p.id]
        if self.active_id == p.id:
            self.active_id = self.profiles[0].id
        self._row = -1
        self.persist()
        self.refresh_list(self.active_id)

    def use(self) -> None:
        self.commit_form()
        p = self.current()
        if p is None:
            return
        self.active_id = p.id
        self.persist()
        self.refresh_list_text()
        self.update_buttons()
        self.show_status(f"Đang dùng “{p.name}”.", "ok")

    def save(self) -> None:
        self.commit_form()
        self.persist()
        self.refresh_list_text()
        self.show_status("Đã lưu.", "ok")

    def test(self) -> None:
        self.save()
        p = self.current()
        if p is None or self.worker is not None:
            return
        self.show_status("Đang thử kết nối…", "warn")
        self.btn_test.setEnabled(False)
        prof = dataclasses.replace(p)
        self.worker = Worker(lambda log: llm.ping(prof))
        self.worker.done.connect(lambda m: self.show_status(str(m), "ok"))
        self.worker.failed.connect(lambda e: self.show_status(f"Lỗi: {e}", "err"))
        self.worker.finished.connect(self._test_done)
        self.worker.start()

    def _test_done(self) -> None:
        self.worker = None
        self.btn_test.setEnabled(True)

"""Cài đặt → Đăng video: khoá ứng dụng và kết nối tài khoản cho từng nền tảng (YouTube, TikTok, Facebook + Instagram)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .llm_panel import card, field, secret
from .publish import FacebookReels, TikTok, YouTube, store
from .theme import SP
from .widgets import repolish
from .workers import Worker

# nhóm kết nối: (khoá lưu, tiêu đề thẻ, lớp dùng để kết nối, các nền tảng dùng chung kết nối này)
GROUPS = (("youtube", "YouTube", YouTube, "Shorts và video thường"),
          ("tiktok", "TikTok", TikTok, "Đăng thẳng từ máy qua Content Posting API"),
          ("meta", "Facebook và Instagram", FacebookReels, "Reels lên Trang Facebook và tài khoản Instagram liên kết, dùng chung một ứng dụng Meta"))


class ConnectionCard(QWidget):
    changed = Signal()

    def __init__(self, store_key: str, title: str, cls, subtitle: str):
        super().__init__()
        self.store_key, self.cls = store_key, cls
        self.plat = cls()
        self.worker: Worker | None = None
        self.box = card(title, subtitle)
        v = self.box.layout()
        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(self.status)
        v.insertLayout(1, head)

        note = QLabel(f'{self.plat.setup_note} <a href="{self.plat.setup_url}">Mở trang nhà phát triển</a>')
        note.setProperty("caption", True)
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        v.addWidget(note)

        c = store.get_creds(store_key)
        labels = {k: (lbl, hint, sec) for k, lbl, hint, sec in self.plat.fields}
        self.edits: dict[str, QWidget] = {}
        for k, (lbl, hint, sec) in labels.items():
            w = secret(c.get(k, ""), hint) if sec else QLineEdit(c.get(k, ""))
            self.edits[k] = w
            v.addWidget(field(lbl, "", w))
        self.redirect = QLineEdit(c.get("redirect_uri") or self.plat.default_redirect)
        v.addWidget(field("Địa chỉ chuyển hướng (Redirect URI)",
                          "Đăng ký đúng địa chỉ này trên trang nhà phát triển. Phải là địa chỉ trên máy bạn (127.0.0.1 hoặc localhost).", self.redirect))

        self.page_row = QWidget()
        pr = QVBoxLayout(self.page_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.pages = QComboBox()
        self.pages.activated.connect(self.pick_page)
        pr.addWidget(field("Trang Facebook đang dùng", "Instagram sẽ đăng lên tài khoản liên kết với Trang này.", self.pages))
        v.addWidget(self.page_row)
        self.page_row.setVisible(store_key == "meta")

        self.b_save = QPushButton("Lưu khoá")
        self.b_connect = QPushButton("Kết nối")
        self.b_connect.setProperty("primary", True)
        self.b_off = QPushButton("Ngắt kết nối")
        for b in (self.b_save, self.b_connect, self.b_off):
            b.setFixedHeight(40)
        self.b_save.clicked.connect(self.save)
        self.b_connect.clicked.connect(self.connect)
        self.b_off.clicked.connect(self.disconnect)
        row = QHBoxLayout()
        row.addStretch(1)
        for b in (self.b_off, self.b_save, self.b_connect):
            row.addWidget(b)
        v.addLayout(row)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.box)
        self.refresh()

    def values(self) -> dict:
        out = {k: (w.edit.text() if hasattr(w, "edit") else w.text()) for k, w in self.edits.items()}
        out["redirect_uri"] = self.redirect.text()
        return out

    def set_status(self, text: str, kind: str) -> None:
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)

    def refresh(self) -> None:
        connected = self.plat.is_connected()
        if connected:
            self.set_status(f"Đã kết nối: {self.plat.account_label() or 'tài khoản'}", "ok")
        else:
            self.set_status("Chưa kết nối", "info")
        self.b_connect.setText("Kết nối lại" if connected else "Kết nối")
        self.b_off.setEnabled(connected)
        if self.store_key == "meta":
            self.pages.blockSignals(True)
            self.pages.clear()
            cur = self.plat.token().get("page_id")
            for p in self.plat.pages():
                self.pages.addItem(p["name"] + (f"  ·  Instagram @{p['ig_username']}" if p.get("ig_username") else "  ·  chưa liên kết Instagram"), p["id"])
                if p["id"] == cur:
                    self.pages.setCurrentIndex(self.pages.count() - 1)
            self.pages.blockSignals(False)
            self.page_row.setVisible(connected and self.pages.count() > 0)

    def save(self) -> None:
        store.set_creds(self.store_key, self.values())
        self.set_status("Đã lưu khoá", "ok")

    def connect(self) -> None:
        self.save()
        self.b_connect.setEnabled(False)
        self.b_connect.setText("Đang chờ đăng nhập trên trình duyệt…")
        self.worker = Worker(lambda log: self.cls().connect(log))
        self.worker.done.connect(lambda name: (self.refresh(), self.changed.emit(), self.set_status(f"Đã kết nối: {name}", "ok")))
        self.worker.failed.connect(lambda e: self.set_status(e[:300], "err"))
        self.worker.finished.connect(lambda: (self.b_connect.setEnabled(True), self.refresh()))
        self.worker.start()

    def disconnect(self) -> None:
        self.plat.disconnect()
        self.refresh()
        self.changed.emit()

    def pick_page(self, i: int) -> None:
        try:
            self.plat.set_page(self.pages.itemData(i))
            self.changed.emit()
        except Exception as e:  # noqa: BLE001
            self.set_status(str(e), "err")


class PublishSettingsPanel(QWidget):
    changed = Signal()          # kết nối vừa đổi: tab Đăng video cập nhật trạng thái

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP.l)
        self.cards = [ConnectionCard(*g) for g in GROUPS]
        for c in self.cards:
            c.changed.connect(self.changed.emit)
            lay.addWidget(c)

    def refresh(self) -> None:
        for c in self.cards:
            c.refresh()

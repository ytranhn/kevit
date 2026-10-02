"""Cài đặt → Đăng video: khoá ứng dụng cho từng nền tảng và danh sách TÀI KHOẢN đã kết nối (thêm bao nhiêu tài khoản tuỳ ý)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from . import publish
from .llm_panel import card, field, secret
from .publish import FacebookReels, TikTok, YouTube, store
from .theme import SP
from .widgets import repolish
from .workers import Worker

# nhóm khoá ứng dụng: (khoá lưu, tiêu đề thẻ, lớp dùng để kết nối, các nền tảng của nhóm, mô tả)
GROUPS = (("youtube", "YouTube", YouTube, ("youtube",), "Shorts và video thường. Mỗi tài khoản Google là một kênh."),
          ("tiktok", "TikTok", TikTok, ("tiktok",), "Đăng thẳng từ máy qua Content Posting API."),
          ("meta", "Facebook và Instagram", FacebookReels, ("facebook", "instagram"),
           "Một lần đăng nhập Meta thêm mọi Trang bạn quản lý, và Instagram liên kết với từng Trang."))


class AccountRow(QFrame):
    removed = Signal(str)

    def __init__(self, acc: dict):
        super().__init__()
        self.setProperty("card", True)
        tag = QLabel(publish.PLATFORMS[acc["platform"]].label)
        tag.setProperty("pill", "info")
        name = QLabel(acc.get("label", acc["id"]))
        name.setStyleSheet("font-weight: 600; background: transparent;")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sub = acc.get("page_name")
        hint = QLabel(f"Trang liên kết: {sub}" if sub else "")
        hint.setProperty("caption", True)
        hint.setVisible(bool(sub))
        b = QPushButton("Xoá")
        b.setProperty("ghost", True)
        b.setToolTip("Gỡ tài khoản này khỏi Kevit (không xoá gì trên nền tảng). Dự án đang chọn tài khoản này sẽ bỏ qua nó.")
        b.clicked.connect(lambda: self.removed.emit(acc["id"]))
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, SP.s, SP.s, SP.s)
        row.addWidget(tag)
        row.addWidget(name)
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(b)


class ConnectionCard(QWidget):
    changed = Signal()

    def __init__(self, group: str, title: str, cls, platforms: tuple[str, ...], subtitle: str):
        super().__init__()
        self.group, self.cls, self.platforms = group, cls, platforms
        self.plat = cls()
        self.worker: Worker | None = None
        self.box = card(title, subtitle)
        v = self.box.layout()

        note = QLabel(f'{self.plat.setup_note} <a href="{self.plat.setup_url}">Mở trang nhà phát triển</a>')
        note.setProperty("caption", True)
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        v.addWidget(note)

        c = store.get_creds(group)
        self.edits: dict[str, QWidget] = {}
        for k, lbl, hint, sec in self.plat.fields:
            w = secret(c.get(k, ""), hint) if sec else QLineEdit(c.get(k, ""))
            self.edits[k] = w
            v.addWidget(field(lbl, "", w))
        self.redirect = QLineEdit(c.get("redirect_uri") or self.plat.default_redirect)
        v.addWidget(field("Địa chỉ chuyển hướng (Redirect URI)",
                          "Đăng ký đúng địa chỉ này trên trang nhà phát triển. Phải là địa chỉ trên máy bạn (127.0.0.1 hoặc localhost).", self.redirect))

        head = QLabel("Tài khoản đã kết nối")
        head.setProperty("subheading", True)
        v.addWidget(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(SP.s)
        v.addLayout(self.rows)
        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.b_save = QPushButton("Lưu khoá")
        self.b_add = QPushButton("Thêm tài khoản")
        self.b_add.setProperty("primary", True)
        for b in (self.b_save, self.b_add):
            b.setFixedHeight(40)
        self.b_save.clicked.connect(self.save)
        self.b_add.clicked.connect(self.add_account)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.b_save)
        row.addWidget(self.b_add)
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
        self.status.setVisible(bool(text))
        repolish(self.status)

    def refresh(self) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        accs = [a for pl in self.platforms for a in store.list_accounts(pl)]
        for a in accs:
            r = AccountRow(a)
            r.removed.connect(self.remove)
            self.rows.addWidget(r)
        if not accs:
            empty = QLabel("Chưa có tài khoản nào. Nhập khoá rồi bấm “Thêm tài khoản”.")
            empty.setProperty("caption", True)
            self.rows.addWidget(empty)

    def save(self) -> None:
        store.set_creds(self.group, self.values())
        self.set_status("Đã lưu khoá.", "ok")

    def add_account(self) -> None:
        store.set_creds(self.group, self.values())
        self.b_add.setEnabled(False)
        self.b_add.setText("Đang chờ đăng nhập trên trình duyệt…")
        self.set_status("", "info")
        self.worker = Worker(lambda log: self.cls().connect(log))
        self.worker.done.connect(self.on_connected)
        self.worker.failed.connect(lambda e: self.set_status(e[:400], "err"))
        self.worker.finished.connect(lambda: (self.b_add.setEnabled(True), self.b_add.setText("Thêm tài khoản"), self.refresh()))
        self.worker.start()

    def on_connected(self, accs: list) -> None:
        self.set_status("Đã kết nối: " + ", ".join(a["label"] for a in accs), "ok")
        self.changed.emit()

    def remove(self, account_id: str) -> None:
        name = publish.account_name(account_id)
        if QMessageBox.question(self, "Xoá tài khoản", f"Gỡ “{name}” khỏi Kevit?\nLịch sử đăng được giữ nguyên.") != QMessageBox.Yes:
            return
        store.remove_account(account_id)
        self.refresh()
        self.changed.emit()


class PublishSettingsPanel(QWidget):
    changed = Signal()          # danh sách tài khoản vừa đổi: tab Đăng video cập nhật

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

"""Cài đặt → Đăng video: chọn nền tảng, nhập khoá ứng dụng, thêm/xoá/kiểm tra các TÀI KHOẢN đã kết nối (nhiều tài khoản mỗi nền tảng)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import icons, publish, theme
from .publish import FacebookReels, InstagramReels, TikTok, YouTube, store
from .shell import AdaptiveRow, platform_pixmap, platform_tile
from .theme import SP
from .widgets import ElidedLabel, repolish
from .workers import Worker

# (khoá nền tảng, nhãn, lớp, nhóm khoá ứng dụng, mô tả ngắn)
TABS = (("youtube", "YouTube", YouTube, "youtube", "Kết nối YouTube Data API để tự động đăng Shorts và video thường."),
        ("tiktok", "TikTok", TikTok, "tiktok", "Kết nối TikTok Content Posting API để đăng video tự động."),
        ("facebook", "Facebook", FacebookReels, "meta", "Kết nối Facebook Graph API để đăng Reels lên các Trang bạn quản lý."),
        ("instagram", "Instagram", InstagramReels, "meta", "Kết nối Instagram Graph API để đăng Reels. Dùng chung ứng dụng Meta với Facebook."))

STEPS = {
    "youtube": ("Vào Google Cloud Console, tạo (hoặc chọn) một dự án.",
                "Bật “YouTube Data API v3” cho dự án.",
                "Tạo thông tin xác thực OAuth loại “Desktop app”, sao chép Client ID và Client secret vào bên trên.",
                "Bấm “Thêm tài khoản”, đăng nhập tài khoản Google và cấp quyền. Lặp lại để thêm kênh khác.",
                "Lưu ý: dự án API chưa được Google kiểm duyệt thì video luôn ở chế độ riêng tư; mỗi lượt tải lên tốn 1600/10.000 đơn vị hạn mức mỗi ngày (khoảng 6 video/ngày)."),
    "tiktok": ("Vào developers.tiktok.com, tạo một app.",
               "Thêm sản phẩm “Login Kit” và “Content Posting API” (bật Direct Post).",
               "Đăng ký ĐÚNG địa chỉ chuyển hướng ở trên vào phần Redirect URI của app.",
               "Dán Client key và Client secret vào bên trên, bấm “Thêm tài khoản” và đăng nhập TikTok.",
               "Lưu ý: app chưa được TikTok duyệt chỉ đăng được ở chế độ riêng tư (SELF_ONLY) và tài khoản phải được thêm làm Target User."),
    "meta": ("Vào developers.facebook.com, tạo app loại “Business”.",
             "Thêm “Facebook Login for Business” và thêm địa chỉ chuyển hướng ở trên vào “Valid OAuth Redirect URIs”.",
             "Dán App ID và App secret vào bên trên, bấm “Thêm tài khoản” và đăng nhập Facebook; tích chọn các Trang muốn dùng.",
             "Mỗi Trang là một tài khoản Facebook; Trang có Instagram Professional liên kết sẽ có thêm một tài khoản Instagram.",
             "Lưu ý: ở chế độ Development chỉ tài khoản Admin/Tester của app đăng được. Instagram không có chế độ riêng tư."),
}
SECURITY = ("Khoá và token chỉ lưu trên máy bạn (trong cấu hình của Kevit) và chỉ được gửi tới chính nền tảng tương ứng khi kết nối hoặc đăng bài. "
            "Kevit không gửi chúng đi đâu khác. Hãy bảo mật thiết bị của bạn.")


def _qicon(name: str) -> QIcon:
    return QIcon(icons.pixmap(name, 18, theme.T["muted"]))


def _label(text: str, required: bool = False) -> QLabel:
    lab = QLabel(text + (f"  <span style='color:{theme.T['err']}'>*</span>" if required else ""))
    lab.setTextFormat(Qt.RichText)
    return lab


def _input(value: str, placeholder: str, secret: bool = False, copy: bool = False) -> QLineEdit:
    """Ô nhập có nút nằm trong ô: sao chép (Client ID, Redirect URI) hoặc hiện/ẩn (khoá bí mật)."""
    e = QLineEdit(value)
    e.setPlaceholderText(placeholder)
    if secret:
        e.setEchoMode(QLineEdit.Password)
        act = e.addAction(_qicon("eye"), QLineEdit.TrailingPosition)
        act.setToolTip("Hiện / ẩn")
        act.triggered.connect(lambda: e.setEchoMode(QLineEdit.Normal if e.echoMode() == QLineEdit.Password else QLineEdit.Password))
    if copy:
        act = e.addAction(_qicon("copy"), QLineEdit.TrailingPosition)
        act.setToolTip("Sao chép")
        act.triggered.connect(lambda: QApplication.clipboard().setText(e.text()))
    return e


def _field(label: QLabel, edit: QWidget, hint: str = "") -> QVBoxLayout:
    v = QVBoxLayout()
    v.setSpacing(SP.xs)
    v.addWidget(label)
    v.addWidget(edit)
    if hint:
        h = QLabel(hint)
        h.setProperty("caption", True)
        h.setWordWrap(True)
        v.addWidget(h)
    return v


class AccountRow(QFrame):
    removed = Signal(str)

    def __init__(self, acc: dict):
        super().__init__()
        self.setProperty("provrow", True)
        self.setFixedHeight(52)
        name = QLabel(acc.get("label", acc["id"]))
        name.setStyleSheet("font-weight: 600; background: transparent;")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sub = acc.get("page_name")
        hint = QLabel(f"Trang liên kết: {sub}" if sub else "")
        hint.setProperty("caption", True)
        hint.setVisible(bool(sub))
        self.result = QLabel("")
        self.result.setProperty("caption", True)
        b = QPushButton("Xoá")
        b.setProperty("ghost", True)
        b.setToolTip("Gỡ tài khoản này khỏi Kevit (không xoá gì trên nền tảng). Dự án đang chọn tài khoản này sẽ bỏ qua nó.")
        b.clicked.connect(lambda: self.removed.emit(acc["id"]))
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, SP.s, SP.s, SP.s)
        row.setSpacing(SP.m)
        row.addWidget(name)
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(self.result)
        row.addWidget(b)

    def show_result(self, ok: bool, text: str) -> None:
        self.result.setText(("✓ " if ok else "✗ ") + text)
        self.result.setStyleSheet(f"color: {theme.T['ok'] if ok else theme.T['err']}; background: transparent;")


class GuideBox(QFrame):
    """Hướng dẫn kết nối: bấm để mở/thu gọn danh sách các bước."""

    def __init__(self, steps: tuple[str, ...], where: str):
        super().__init__()
        self.setProperty("guide", True)
        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(40, 40)
        tile.setAlignment(Qt.AlignCenter)
        icons.attach(tile, "doc", 22)
        t = QLabel("Hướng dẫn kết nối")
        t.setStyleSheet("font-weight: 600; background: transparent;")
        d = QLabel(f"Xem các bước cấu hình chi tiết trên {where}.")
        d.setProperty("caption", True)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(t)
        col.addWidget(d)
        self.caret = QPushButton()
        self.caret.setProperty("ghost", True)
        self.caret.setFixedSize(34, 34)
        icons.attach(self.caret, "down", 18, role="muted")
        head = QHBoxLayout()
        head.setSpacing(SP.m)
        head.addWidget(tile)
        head.addLayout(col, 1)
        head.addWidget(self.caret)
        self.body = QLabel("<ol style='margin-left:-18px'>" + "".join(f"<li style='margin-bottom:6px'>{s}</li>" for s in steps[:-1])
                           + f"</ol><p style='color:{theme.T['muted']}'>{steps[-1]}</p>")
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setVisible(False)
        v = QVBoxLayout(self)
        v.setContentsMargins(SP.m, SP.m, SP.m, SP.m)
        v.setSpacing(SP.s)
        v.addLayout(head)
        v.addWidget(self.body)
        self.caret.clicked.connect(self.toggle)

    def toggle(self) -> None:
        on = not self.body.isVisible()
        self.body.setVisible(on)
        icons.attach(self.caret, "up" if on else "down", 18, role="muted")

    def mousePressEvent(self, e):
        if e.position().y() < 70:
            self.toggle()
        super().mousePressEvent(e)


def secure_banner() -> QFrame:
    f = QFrame()
    f.setProperty("secure", True)
    tile = QLabel()
    tile.setProperty("navtile", True)
    tile.setFixedSize(40, 40)
    tile.setAlignment(Qt.AlignCenter)
    icons.attach(tile, "lock", 22)
    t = QLabel("Bảo mật")
    t.setStyleSheet(f"font-weight: 600; color: {theme.T['accent']}; background: transparent;")
    d = QLabel(SECURITY)
    d.setProperty("caption", True)
    d.setWordWrap(True)
    col = QVBoxLayout()
    col.setSpacing(0)
    col.addWidget(t)
    col.addWidget(d)
    row = QHBoxLayout(f)
    row.setContentsMargins(SP.m, SP.m, SP.m, SP.m)
    row.setSpacing(SP.m)
    row.addWidget(tile, 0, Qt.AlignTop)
    row.addLayout(col, 1)
    return f


class PlatformCard(QFrame):
    """Thẻ chi tiết của nền tảng đang chọn: logo + trạng thái, khoá ứng dụng, hướng dẫn, tài khoản đã kết nối, các nút thao tác."""
    changed = Signal()

    def __init__(self, key: str, label: str, cls, group: str, desc: str):
        super().__init__()
        self.setProperty("card", True)
        self.key, self.label, self.cls, self.group = key, label, cls, group
        self.plat = cls()
        self.worker: Worker | None = None
        self.rows: dict[str, AccountRow] = {}

        self.status = QLabel("")
        title = QLabel(label)
        title.setStyleSheet("font-size: 20px; font-weight: 600; background: transparent;")
        d = QLabel(desc)
        d.setProperty("caption", True)
        d.setWordWrap(True)
        b_doc = QPushButton("Xem tài liệu")
        icons.attach(b_doc, "open", 16)
        b_doc.setLayoutDirection(Qt.RightToLeft)
        b_doc.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.plat.setup_url)))
        trow = QHBoxLayout()
        trow.setSpacing(SP.m)
        trow.addWidget(title)
        trow.addWidget(self.status)
        trow.addStretch(1)
        tcol = QVBoxLayout()
        tcol.setSpacing(SP.xs)
        tcol.addLayout(trow)
        tcol.addWidget(d)
        head = QHBoxLayout()
        head.setSpacing(SP.l)
        head.addWidget(platform_tile(key, 56), 0, Qt.AlignTop)
        head.addLayout(tcol, 1)
        head.addWidget(b_doc, 0, Qt.AlignTop)

        c = store.get_creds(group)
        labels = {k: (lbl, hint, sec) for k, lbl, hint, sec in self.plat.fields}
        (k1, (l1, h1, _)), (k2, (l2, h2, _)) = list(labels.items())[:2]
        self.id_key, self.secret_key = k1, k2
        self.id_edit = _input(c.get(k1, ""), f"Nhập {l1}", copy=True)
        self.secret_edit = _input(c.get(k2, ""), f"Nhập {l2}", secret=True)
        self.redirect = _input(c.get("redirect_uri") or self.plat.default_redirect, "", copy=True)
        two = AdaptiveRow(640)
        two.addLayout(_field(_label(l1, True), self.id_edit), 1)
        two.addLayout(_field(_label(l2, True), self.secret_edit), 1)
        redirect_hint = ("Đăng ký đúng địa chỉ này khi tạo ứng dụng trên trang nhà phát triển. Phải là địa chỉ trên máy bạn (127.0.0.1 hoặc localhost)."
                         + (" Facebook và Instagram dùng chung địa chỉ này." if group == "meta" else ""))

        self.guide = GuideBox(STEPS[group], {"youtube": "Google Cloud Console", "tiktok": "TikTok for Developers", "meta": "Meta for Developers"}[group])

        acc_head = QLabel("Tài khoản đã kết nối")
        acc_head.setProperty("subheading", True)
        self.acc_box = QVBoxLayout()
        self.acc_box.setSpacing(SP.s)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.b_check = QPushButton("Kiểm tra kết nối")
        icons.attach(self.b_check, "check", 18)
        self.b_check.clicked.connect(self.check)
        self.b_save = QPushButton("Lưu khoá")
        icons.attach(self.b_save, "save", 18)
        self.b_save.clicked.connect(self.save)
        self.b_add = QPushButton(f"Thêm tài khoản {label}")
        self.b_add.setProperty("primary", True)
        icons.attach(self.b_add, "plus", 18)
        self.b_add.clicked.connect(self.add_account)
        for b in (self.b_check, self.b_save, self.b_add):
            b.setFixedHeight(40)
        btns = QHBoxLayout()
        btns.setSpacing(SP.m)
        btns.addStretch(1)
        for b in (self.b_check, self.b_save, self.b_add):
            btns.addWidget(b)

        v = QVBoxLayout(self)
        v.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        v.setSpacing(SP.l)
        v.addLayout(head)
        v.addWidget(two)
        v.addLayout(_field(_label("Địa chỉ chuyển hướng (Redirect URI)", True), self.redirect, redirect_hint))
        v.addWidget(self.guide)
        v.addWidget(secure_banner())
        v.addWidget(acc_head)
        v.addLayout(self.acc_box)
        v.addWidget(self.msg)
        v.addLayout(btns)
        self.refresh()

    # ---- dữ liệu ----
    def accounts(self) -> list[dict]:
        return store.list_accounts(self.key)

    def values(self) -> dict:
        return {self.id_key: self.id_edit.text(), self.secret_key: self.secret_edit.text(), "redirect_uri": self.redirect.text()}

    def reload_creds(self) -> None:
        """Nạp lại khoá từ kho (Facebook và Instagram dùng chung một bộ khoá nên thẻ này có thể vừa được thẻ kia sửa)."""
        c = store.get_creds(self.group)
        self.id_edit.setText(c.get(self.id_key, ""))
        self.secret_edit.setText(c.get(self.secret_key, ""))
        self.redirect.setText(c.get("redirect_uri") or self.plat.default_redirect)

    def set_msg(self, text: str, kind: str = "info") -> None:
        self.msg.setText(text)
        self.msg.setProperty("pill", kind)
        self.msg.setVisible(bool(text))
        repolish(self.msg)

    def pill_text(self) -> tuple[str, str]:
        n = len(self.accounts())
        return (f"{n} tài khoản đã kết nối", "ok") if n else ("Chưa kết nối", "info")

    def refresh(self) -> None:
        text, kind = self.pill_text()
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)
        while self.acc_box.count():
            w = self.acc_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.rows = {}
        accs = self.accounts()
        for a in accs:
            r = AccountRow(a)
            r.removed.connect(self.remove)
            self.acc_box.addWidget(r)
            self.rows[a["id"]] = r
        if not accs:
            empty = QLabel("Chưa có tài khoản nào. Nhập khoá rồi bấm “Thêm tài khoản”.")
            empty.setProperty("caption", True)
            self.acc_box.addWidget(empty)
        self.b_check.setEnabled(True)
        self.set_msg("", "info")

    # ---- thao tác ----
    def save(self) -> None:
        store.set_creds(self.group, self.values())
        self.set_msg("Đã lưu khoá.", "ok")
        self.changed.emit()

    def add_account(self) -> None:
        store.set_creds(self.group, self.values())
        self.b_add.setEnabled(False)
        self.b_add.setText("Đang chờ đăng nhập trên trình duyệt…")
        self.set_msg("", "info")
        self.worker = Worker(lambda log: self.cls().connect(log))
        self.worker.done.connect(self.on_connected)
        self.worker.failed.connect(lambda e: self.set_msg(e[:400], "err"))
        self.worker.finished.connect(lambda: (self.b_add.setEnabled(True), self.b_add.setText(f"Thêm tài khoản {self.label}"), self.refresh_keep_msg()))
        self.worker.start()

    def refresh_keep_msg(self) -> None:
        text, kind = self.msg.text(), self.msg.property("pill")
        self.refresh()
        if text:
            self.set_msg(text, kind or "info")

    def on_connected(self, accs: list) -> None:
        self.set_msg("Đã kết nối: " + ", ".join(a["label"] for a in accs), "ok")
        self.changed.emit()

    def remove(self, account_id: str) -> None:
        name = publish.account_name(account_id)
        if QMessageBox.question(self, "Xoá tài khoản", f"Gỡ “{name}” khỏi Kevit?\nLịch sử đăng được giữ nguyên.") != QMessageBox.Yes:
            return
        store.remove_account(account_id)
        self.refresh()
        self.changed.emit()

    def check(self) -> None:
        """Kiểm tra từng tài khoản đã kết nối (làm mới token nếu cần rồi gọi thử API)."""
        accs = self.accounts()
        if not accs:
            c = self.values()
            ok = bool(c[self.id_key].strip() and c[self.secret_key].strip())
            self.set_msg("Khoá đã đủ, chưa có tài khoản nào để kiểm tra." if ok else "Chưa nhập đủ Client ID / Client secret.", "info" if ok else "warn")
            return
        self.b_check.setEnabled(False)
        self.set_msg("Đang kiểm tra…", "info")

        def job(log):
            out = {}
            for a in accs:
                try:
                    out[a["id"]] = (True, publish.get(a["id"]).check())
                except Exception as e:  # noqa: BLE001
                    out[a["id"]] = (False, str(e)[:160])
            return out

        def done(res):
            for aid, (ok, text) in res.items():
                if aid in self.rows:
                    self.rows[aid].show_result(ok, text)
            bad = sum(1 for ok, _ in res.values() if not ok)
            self.set_msg(f"Đã kiểm tra {len(res)} tài khoản" + (f", {bad} lỗi." if bad else ": tất cả hoạt động."), "warn" if bad else "ok")
        self.worker = Worker(job)
        self.worker.done.connect(done)
        self.worker.failed.connect(lambda e: self.set_msg(e[:300], "err"))
        self.worker.finished.connect(lambda: self.b_check.setEnabled(True))
        self.worker.start()


class PlatformRow(QFrame):
    """Nền tảng đang thu gọn: logo · tên · mô tả · trạng thái · nút Kết nối · mũi tên mở rộng."""
    picked = Signal(str)

    def __init__(self, key: str, label: str, desc: str):
        super().__init__()
        self.key = key
        self.setProperty("provrow", True)
        self.setFixedHeight(68)
        self.setCursor(Qt.PointingHandCursor)
        name = QLabel(label)
        name.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        name.setMinimumWidth(90)
        d = ElidedLabel()                                   # mô tả dài tự cắt “…” để hàng không bao giờ rộng hơn trang
        d.setProperty("caption", True)
        d.set_full(desc)
        d.setToolTip(desc)
        self.status = QLabel("")
        self.btn = QPushButton("Kết nối")
        icons.attach(self.btn, "link", 16)
        self.btn.clicked.connect(lambda: self.picked.emit(key))
        arrow = QPushButton()
        arrow.setProperty("ghost", True)
        arrow.setFixedSize(34, 34)
        icons.attach(arrow, "right", 18, role="muted")
        arrow.clicked.connect(lambda: self.picked.emit(key))
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 0, SP.s, 0)
        row.setSpacing(SP.m)
        row.addWidget(platform_tile(key, 36))
        row.addWidget(name)
        row.addWidget(d, 1)
        row.addWidget(self.status, 0, Qt.AlignVCenter)
        row.addWidget(self.btn)
        row.addWidget(arrow)

    def set_state(self, text: str, kind: str) -> None:
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)

    def mousePressEvent(self, e):
        self.picked.emit(self.key)
        super().mousePressEvent(e)


class PublishSettingsPanel(QWidget):
    changed = Signal()          # danh sách tài khoản vừa đổi: tab Đăng video cập nhật

    def __init__(self):
        super().__init__()
        self.cards: dict[str, PlatformCard] = {}
        self.rows: dict[str, PlatformRow] = {}
        self.tab_btns: dict[str, QPushButton] = {}
        bar = QFrame()
        bar.setProperty("card", True)
        br = QHBoxLayout(bar)
        br.setContentsMargins(SP.s, SP.s, SP.s, SP.s)
        br.setSpacing(SP.xs)
        for key, label, cls, group, desc in TABS:
            b = QPushButton("  " + label)               # chừa khoảng cách giữa logo và chữ
            b.setProperty("platab", True)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(QIcon(platform_pixmap(key, 22)))
            b.setIconSize(QSize(22, 22))
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            br.addWidget(b, 1)
            self.tab_btns[key] = b
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP.l)
        lay.addWidget(bar)
        for key, label, cls, group, desc in TABS:
            card = PlatformCard(key, label, cls, group, desc)
            card.changed.connect(self.on_card_changed)
            row = PlatformRow(key, label, desc)
            row.picked.connect(self.select)
            self.cards[key], self.rows[key] = card, row
            lay.addWidget(card)
            lay.addWidget(row)
        self.current = "youtube"
        self.select("youtube")

    def select(self, key: str) -> None:
        self.current = key
        for k in self.cards:
            self.cards[k].setVisible(k == key)
            self.rows[k].setVisible(k != key)
            self.tab_btns[k].setChecked(k == key)
        self.cards[key].reload_creds()
        self.sync_rows()

    def sync_rows(self) -> None:
        for k, row in self.rows.items():
            text, kind = self.cards[k].pill_text()
            row.set_state(text, kind)
            row.btn.setText("Quản lý" if self.cards[k].accounts() else "Kết nối")

    def on_card_changed(self) -> None:
        for c in self.cards.values():          # Facebook và Instagram dùng chung khoá và cùng đăng nhập Meta: làm mới cả hai thẻ
            c.reload_creds()
            c.refresh_keep_msg()
        self.sync_rows()
        self.changed.emit()

    def refresh(self) -> None:
        for c in self.cards.values():
            c.refresh()
        self.sync_rows()

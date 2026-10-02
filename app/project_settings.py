"""Hộp thoại 'Cài đặt dự án': thanh điều hướng bên trái + các thẻ cuộn được bên phải (Hình ảnh & video · Tách scene · Google Flow · Giọng đọc · Đăng video).
Dùng lại các ô nhập đang nằm trên ProjectTab (tab.style, tab.aspect...) — hộp thoại chỉ sắp xếp lại giao diện.
Huỷ thì khôi phục giá trị cũ; Lưu thì gọi tab.save_edits()."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from . import accounts, credits, flow_auto, icons, models, publish, theme
from .publish import service
from .publish_tab import PRIVACY, account_tile
from .shell import AdaptiveRow, NavItem, section
from .theme import SP
from .widgets import Combo, rounded_pixmap
from .workers import Worker

DEFAULT_STYLE = "cinematic, soft lighting, 35mm film look"
DEFAULT_VOICE_STYLE = "Đọc bằng giọng kể chuyện ấm, rõ ràng, tốc độ vừa phải"
SECTIONS = (("visual", "Hình ảnh & video", "Phong cách, tỉ lệ, chất lượng", "image"),
            ("scenes", "Tách scene", "Cảnh báo và chia cảnh", "layers"),
            ("flow", "Google Flow", "Tài khoản, model, credit", "sparkle"),
            ("voice", "Giọng đọc", "Ngôn ngữ, giọng và phong cách", "volume"),
            ("publish", "Đăng video", "Chọn tài khoản và chế độ đăng", "open"))


def _caption(text: str) -> QLabel:
    c = QLabel(text)
    c.setProperty("caption", True)
    c.setWordWrap(True)
    return c


def _labeled(title: str, control: QWidget, hint: str = "") -> QVBoxLayout:
    v = QVBoxLayout()
    v.setSpacing(SP.xs)
    v.addWidget(QLabel(title))
    v.addWidget(control)
    if hint:
        v.addWidget(_caption(hint))
    v.addStretch(1)                      # cột thấp hơn không bị giãn khoảng cách khi cột bên cạnh cao hơn
    return v


def _note(icon: str, text: str) -> QFrame:
    f = QFrame()
    f.setProperty("banner", True)
    ic = QLabel()
    ic.setFixedSize(22, 22)
    icons.attach(ic, icon, 18, role="accent")
    t = _caption(text)
    row = QHBoxLayout(f)
    row.setContentsMargins(SP.m, SP.s, SP.m, SP.s)
    row.setSpacing(SP.s)
    row.addWidget(ic, 0, Qt.AlignTop)
    row.addWidget(t, 1)
    return f


def _aspect_icon(kind: str, color: str, size: int = 26) -> QPixmap:
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 2.0)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    if kind == "9:16":
        p.drawRoundedRect(QRectF(size * 0.28, size * 0.1, size * 0.44, size * 0.8), 3, 3)
    elif kind == "16:9":
        p.drawRoundedRect(QRectF(size * 0.1, size * 0.28, size * 0.8, size * 0.44), 3, 3)
    p.end()
    return pm


class AspectCard(QPushButton):
    """Một lựa chọn tỉ lệ khung hình: icon · tỉ lệ · hướng · nơi dùng."""

    def __init__(self, ratio: str, big: str, name: str, where: str):
        super().__init__()
        self.ratio = ratio
        self.setProperty("aspcard", True)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(104)
        self.icon_lab = QLabel()
        self.icon_lab.setFixedSize(26, 26)
        self.icon_lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        b = QLabel(big)
        b.setStyleSheet("font-weight: 700; font-size: 15px; background: transparent;")
        n = QLabel(name)
        n.setProperty("caption", True)
        w = QLabel(where)
        w.setProperty("caption", True)
        w.setWordWrap(True)
        w.setAlignment(Qt.AlignCenter)
        for x in (b, n, w):
            x.setAlignment(Qt.AlignCenter)
            x.setAttribute(Qt.WA_TransparentForMouseEvents)
        col = QVBoxLayout(self)
        col.setContentsMargins(SP.s, SP.s, SP.s, SP.s)
        col.setSpacing(0)
        col.addWidget(self.icon_lab, 0, Qt.AlignHCenter)
        col.addWidget(b)
        col.addWidget(n)
        col.addWidget(w)
        self.paint_icon()

    def paint_icon(self) -> None:
        if self.ratio == "flow":
            icons.attach(self.icon_lab, "sparkle", 22, role="accent")
        else:
            self.icon_lab.setPixmap(_aspect_icon(self.ratio, theme.T["text"]))


class ProjectSettingsDialog(QDialog):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setWindowTitle("Cài đặt dự án")
        from PySide6.QtGui import QGuiApplication
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1240, geo.width() - 80), min(860, geo.height() - 100))
        self.setMinimumSize(820, 560)
        self._snap: dict = {}
        self._cards: dict[str, QWidget] = {}
        self._navs: dict[str, NavItem] = {}
        self._worker: Worker | None = None

        tab.style.setPlaceholderText("Ví dụ: cinematic, soft lighting, 35mm film look")
        tab.voice_style.setPlaceholderText("Chỉ áp dụng cho Gemini TTS")
        self.cost = QLabel("")

        # ---------- đầu hộp thoại ----------
        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(48, 48)
        tile.setAlignment(Qt.AlignCenter)
        icons.attach(tile, "gear", 26)
        head = QLabel("Cài đặt dự án")
        head.setProperty("heading", True)
        self.sub = QLabel("")
        self.sub.setProperty("caption", True)
        hcol = QVBoxLayout()
        hcol.setSpacing(0)
        hcol.addWidget(head)
        hcol.addWidget(self.sub)
        top = QHBoxLayout()
        top.setSpacing(SP.m)
        top.addWidget(tile)
        top.addLayout(hcol, 1)

        # ---------- các thẻ ----------
        visual = self._build_visual()
        scenes = self._build_scenes()
        flow = self._build_flow()
        voice = self._build_voice()
        post = self._build_publish()
        self._cards = {"visual": visual, "scenes": scenes, "flow": flow, "voice": voice, "publish": post}

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, SP.m, 0)
        bv.setSpacing(SP.l)
        for c in self._cards.values():
            bv.addWidget(c)
        bv.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(body)
        self.scroll.verticalScrollBar().valueChanged.connect(self.sync_nav)

        # ---------- điều hướng bên trái ----------
        nav = QVBoxLayout()
        nav.setSpacing(SP.s)
        for key, title, sub, icon in SECTIONS:
            n = NavItem(title, sub, icon)
            n.clicked.connect(lambda _=False, k=key: self.go(k))
            nav.addWidget(n)
            self._navs[key] = n
        nav.addStretch(1)
        nav_box = QWidget()
        nav_box.setFixedWidth(250)
        nav_box.setLayout(nav)

        # ---------- chân hộp thoại ----------
        reset = QPushButton("Khôi phục mặc định")
        icons.attach(reset, "refresh", 18)
        reset.setToolTip("Đưa phong cách hình ảnh, tỉ lệ, số scene, cấu hình Flow và chế độ đăng về mặc định (không đổi tài khoản, giọng đọc hay ngôn ngữ)")
        reset.clicked.connect(self.reset_defaults)
        cancel = QPushButton("Huỷ")
        save = QPushButton("Lưu cài đặt")
        save.setProperty("primary", True)
        save.setDefault(True)
        for b in (reset, cancel, save):
            b.setFixedHeight(40)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addWidget(reset)
        foot.addStretch()
        foot.addWidget(cancel)
        foot.addWidget(save)

        main = QHBoxLayout()
        main.setSpacing(SP.l)
        main.addWidget(nav_box)
        main.addWidget(self.scroll, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.l, SP.xl, SP.l)
        lay.setSpacing(SP.l)
        lay.addLayout(top)
        lay.addLayout(main, 1)
        lay.addLayout(foot)

        tab.flow_model.currentIndexChanged.connect(self.refresh_flow)
        tab.flow_res.currentIndexChanged.connect(self.refresh_flow)
        tab.flow_auto_dur.toggled.connect(self.refresh_flow)
        tab.provider.currentIndexChanged.connect(self.refresh_voice)
        tab.flow_account.activated.connect(lambda *_: self.refresh_credit_card())
        tab.aspect.currentIndexChanged.connect(self.sync_aspect)

    # ================= dựng các thẻ =================
    def _build_visual(self) -> QFrame:
        box, body = section("image", "Hình ảnh & video", "Áp dụng cho mọi prompt gửi Flow trong dự án này.")
        left = QVBoxLayout()
        left.setSpacing(SP.xs)
        left.addLayout(_labeled("Phong cách hình ảnh", self.tab.style, "Được gắn vào cuối mọi prompt. Viết bằng tiếng Anh sẽ cho kết quả ổn định nhất."))
        left.addStretch(1)
        self.preview = QLabel()
        self.preview.setFixedSize(176, 176)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: transparent;")
        cards = QHBoxLayout()
        cards.setSpacing(SP.s)
        self.aspect_cards: list[AspectCard] = []
        for ratio, big, name, where in (("9:16", "9:16", "Dọc", "(TikTok, Reels)"), ("16:9", "16:9", "Ngang", "(YouTube)"),
                                        ("flow", "Theo Flow", "Tự động", "Dùng tỉ lệ gợi ý của Flow")):
            c = AspectCard(ratio, big, name, where)
            c.clicked.connect(lambda _=False, r=ratio: self.pick_aspect(r))
            cards.addWidget(c, 1)
            self.aspect_cards.append(c)
        right = QVBoxLayout()
        right.setSpacing(SP.s)
        right.addWidget(QLabel("Tỉ lệ khung hình"))
        right.addLayout(cards)
        right.addWidget(_note("info", "9:16 cho điện thoại, 16:9 cho màn ngang. “Theo Flow” giữ nguyên tỉ lệ mặc định của tool (không đổi)."))
        row = AdaptiveRow(900)
        row.addLayout(left, 5)
        row.addWidget(self.preview, 0, Qt.AlignTop)
        row.addLayout(right, 6)
        body.addWidget(row)
        return box

    def _build_scenes(self) -> QFrame:
        box, body = section("layers", "Tách scene", "Cảnh báo khi chương vượt số scene để đảm bảo chất lượng.")
        row = AdaptiveRow(640, SP.xl)
        self.tab.max_scenes.setFixedWidth(180)
        row.addLayout(_labeled("Số scene tối đa / chương", self.tab.max_scenes), 0)
        row.addWidget(_note("info", "Chỉ để cảnh báo chi phí. Tool luôn tách đủ scene để thuyết minh giữ ≥70% nội dung truyện."), 1)
        body.addWidget(row)
        return box

    def _build_flow(self) -> QFrame:
        box, body = section("sparkle", "Google Flow", "Model và độ dài ảnh hưởng trực tiếp đến credit.")
        manage = QPushButton("Quản lý tài khoản")
        icons.attach(manage, "open", 16)
        manage.setLayoutDirection(Qt.RightToLeft)
        manage.setToolTip("Lưu cài đặt dự án rồi mở Cài đặt → Google Flow")
        manage.clicked.connect(self.manage_accounts)
        acc_row = QHBoxLayout()
        acc_row.setSpacing(SP.s)
        self.tab.flow_account.setMinimumWidth(200)
        acc_row.addWidget(self.tab.flow_account, 1)
        acc_row.addWidget(manage)
        acc_col = QVBoxLayout()
        acc_col.setSpacing(SP.xs)
        acc_col.addWidget(QLabel("Tài khoản Flow"))
        acc_col.addLayout(acc_row)
        acc_col.addWidget(_caption("Đổi tài khoản thì mỗi chương có project Flow mới trên tài khoản đó; clip đã tải về máy không bị ảnh hưởng."))

        # thẻ credit của tài khoản đang chọn
        self.credit_card = QFrame()
        self.credit_card.setProperty("creditcard", True)
        coin = QLabel()
        coin.setProperty("navtile", True)
        coin.setFixedSize(40, 40)
        coin.setAlignment(Qt.AlignCenter)
        icons.attach(coin, "ring", 22)
        cap = QLabel("Credit hiện tại")
        cap.setProperty("caption", True)
        self.credit_big = QLabel("—")
        self.credit_big.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {theme.T['ok']}; background: transparent;")
        self.credit_detail = _caption("")
        self.b_credit = QPushButton()
        self.b_credit.setProperty("iconbtn", True)
        icons.attach(self.b_credit, "refresh", 18)
        self.b_credit.setToolTip("Đọc lại credit từ Flow (mở Chrome của tài khoản nếu chưa mở)")
        self.b_credit.clicked.connect(self.read_credit)
        ccol = QVBoxLayout()
        ccol.setSpacing(0)
        ccol.addWidget(cap)
        ccol.addWidget(self.credit_big)
        ccol.addWidget(self.credit_detail)
        crow = QHBoxLayout(self.credit_card)
        crow.setContentsMargins(SP.m, SP.m, SP.m, SP.m)
        crow.setSpacing(SP.m)
        crow.addWidget(coin, 0, Qt.AlignTop)
        crow.addLayout(ccol, 1)
        crow.addWidget(self.b_credit, 0, Qt.AlignTop)

        top = AdaptiveRow(820)
        top.addLayout(acc_col, 5)
        top.addWidget(self.credit_card, 5)
        body.addWidget(top)

        self.tab.flow_model.setMinimumWidth(0)
        self.tab.flow_account.setMinimumWidth(0)
        # hai cặp, mỗi cặp 2 cột: hàng không quá dày và co giãn theo bề rộng
        pair1 = AdaptiveRow(560)
        pair1.addLayout(_labeled("Model", self.tab.flow_model, "Veo 3.1 Lite rẻ nhất ở 720p; Omni linh hoạt thời lượng."), 1)
        pair1.addLayout(_labeled("Độ phân giải", self.tab.flow_res, "Chỉ Omni có 360p (rẻ, hợp bản nháp)."), 1)
        pair2 = AdaptiveRow(560)
        auto = QVBoxLayout()
        auto.setSpacing(SP.xs)
        auto.addSpacing(SP.l)                               # canh ngang hàng với ô nhập của cột bên cạnh
        auto.addWidget(self.tab.flow_auto_dur)
        auto.addWidget(_caption("Chỉ áp dụng cho model Omni."))
        auto.addStretch(1)
        pair2.addLayout(auto, 1)
        pair2.addLayout(_labeled("Số scene gửi cùng lúc", self.tab.flow_parallel, "Credit không đổi. Flow báo lỗi hoặc giới hạn thì giảm xuống."), 1)
        body.addWidget(pair1)
        body.addWidget(pair2)

        est = QFrame()
        est.setProperty("estimate", True)
        coin2 = QLabel()
        coin2.setFixedSize(22, 22)
        icons.attach(coin2, "ring", 18)
        self.cost.setStyleSheet(f"color: {theme.T['warn']}; font-weight: 600; background: transparent;")
        er = QHBoxLayout(est)
        er.setContentsMargins(SP.m, SP.s, SP.m, SP.s)
        er.setSpacing(SP.s)
        er.addWidget(coin2)
        er.addWidget(self.cost, 1)
        body.addWidget(est)
        return box

    def _build_voice(self) -> QFrame:
        box, body = section("volume", "Giọng đọc", "Một giọng duy nhất cho cả dự án để người nghe không thấy lệch giữa các scene.")
        self.tab.narr_lang.setMinimumWidth(0)
        self.tab.voice.setMinimumWidth(0)
        row = AdaptiveRow(820)
        row.addLayout(_labeled("Ngôn ngữ thuyết minh", self.tab.narr_lang, "Khác tiếng Việt thì thuyết minh được DỊCH từ truyện gốc."), 4)
        row.addLayout(_labeled("Nguồn giọng", self.tab.provider, "Edge miễn phí (hơn 300 giọng, 75 ngôn ngữ), Gemini cần API key."), 4)
        row.addLayout(_labeled("Giọng", self.tab.voice, "Giọng “Đa ngữ” đọc tốt nhiều ngôn ngữ, hợp khi đổi ngôn ngữ mà vẫn muốn giữ một chất giọng."), 4)
        body.addWidget(row)
        body.addLayout(_labeled("Phong cách đọc", self.tab.voice_style, "Edge TTS không nhận chỉ dẫn phong cách."))
        return box

    def _build_publish(self) -> QFrame:
        box, body = section("open", "Đăng video", "Chọn tài khoản mà dự án này sẽ đăng lên (nhiều tài khoản, nhiều nền tảng). Thêm tài khoản ở Cài đặt → Đăng video.")
        self.acc_checks: dict[str, QCheckBox] = {}
        self.tiles_row = QGridLayout()
        self.tiles_row.setSpacing(SP.m)
        body.addLayout(self.tiles_row)
        self.pub_privacy = Combo()
        for k, label in PRIVACY:
            self.pub_privacy.addItem(label, k)
        self.pub_auto = QCheckBox("Tự động đăng khi một chương gen xong")
        bottom = AdaptiveRow(620, SP.xl)
        bottom.addLayout(_labeled("Chế độ hiển thị", self.pub_privacy, "Nên thử ở “Riêng tư” trước."), 1)
        auto = QVBoxLayout()
        auto.addSpacing(SP.l)
        auto.addWidget(self.pub_auto)
        auto.addStretch(1)
        bottom.addLayout(auto, 1)
        body.addWidget(bottom)
        return box

    # ================= điều hướng =================
    def go(self, key: str) -> None:
        self.scroll.verticalScrollBar().setValue(self._cards[key].y())
        self.set_nav(key)

    def set_nav(self, key: str) -> None:
        for k, n in self._navs.items():
            n.set_active(k == key)

    def sync_nav(self, value: int) -> None:
        """Mục điều hướng sáng theo thẻ đang nằm ở đầu vùng cuộn (cuộn tới đáy thì mục cuối sáng)."""
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() and value >= bar.maximum() - 4:
            return self.set_nav(next(reversed(self._cards)))
        cur = next(iter(self._cards))
        for k, c in self._cards.items():
            if c.y() <= value + 40:
                cur = k
        self.set_nav(cur)

    # ================= tỉ lệ khung hình =================
    def pick_aspect(self, ratio: str) -> None:
        self.tab.aspect.setCurrentIndex(max(0, self.tab.aspect.findData(ratio)))

    def sync_aspect(self, *_) -> None:
        cur = self.tab.aspect.currentData()
        for c in self.aspect_cards:
            c.setChecked(c.ratio == cur)

    # ================= credit / Flow =================
    def refresh_credit_card(self) -> None:
        acc = accounts.get(self.tab.project.account_id if self.tab.project else accounts.DEFAULT_ID)
        cur = self.tab.flow_account.currentData()
        if cur:
            acc = accounts.get(cur)
        if acc.credits is None:
            self.credit_big.setText("Chưa đọc")
            self.credit_detail.setText("Bấm nút làm mới để đọc credit từ Flow.")
            return
        self.credit_big.setText(f"{accounts.fmt_credits(acc.credits)} credit")
        parts = ([f"Còn {acc.daily} credit ngày"] if acc.daily is not None else []) + ([f"Gia hạn: {acc.renew}"] if acc.renew else []) \
            + [f"Cập nhật {accounts.fmt_age(acc.checked_at)}"]
        self.credit_detail.setText(" · ".join(parts) + ("\nTự chuyển tài khoản: BẬT" if accounts.auto_switch() else ""))

    def read_credit(self) -> None:
        """Đọc lại credit của tài khoản đang chọn (mở Chrome của tài khoản nếu cần) rồi cập nhật thẻ."""
        if self._worker is not None:
            return
        acc = accounts.get(self.tab.flow_account.currentData() or accounts.DEFAULT_ID)
        self.b_credit.setEnabled(False)
        self.credit_detail.setText("Đang đọc credit… (có thể mở Chrome của tài khoản)")

        def job(log):
            with flow_auto.FlowAuto(log, acc=acc) as f:
                return f.read_credits(deep=True)

        def done(info):
            if info:
                accounts.save_credits(acc.id, info["credits"], info.get("daily"), info.get("renew", ""), info.get("email", ""),
                                      info.get("plan_total"), info.get("daily_grant"))
            self.tab.fill_accounts(acc.id)
            self.refresh_credit_card()
            self.refresh_flow()

        def failed(msg):
            self.refresh_credit_card()
            self.credit_detail.setText(f"Không đọc được credit: {msg[:120]}")
        self._worker = Worker(job)
        self._worker.done.connect(done)
        self._worker.failed.connect(failed)
        self._worker.finished.connect(lambda: (setattr(self, "_worker", None), self.b_credit.setEnabled(True)))
        self._worker.start()

    def manage_accounts(self) -> None:
        self.accept()                              # lưu cài đặt rồi mở Cài đặt → Google Flow
        QTimer.singleShot(0, lambda: self.tab.open_settings_section.emit("flow"))

    def refresh_flow(self) -> None:
        omni = self.tab.flow_model.currentText() == credits.OMNI
        self.tab.flow_res.setEnabled(omni)
        self.tab.flow_auto_dur.setEnabled(omni)
        res = self.tab.flow_res.currentText()
        auto = omni and self.tab.flow_auto_dur.isChecked()
        per = credits.scene_cost(self.tab.flow_model.currentText(), res, 8)
        self.cost.setText(f"Ước tính: ≈ {per} credit / scene (8 giây)" + ("  ·  ngắn hơn nếu thuyết minh ngắn" if auto else ""))

    def refresh_voice(self) -> None:
        self.tab.voice_style.setEnabled(self.tab.provider.currentData() == "gemini")

    # ================= đăng video =================
    def fill_accounts(self) -> None:
        """Các thẻ nền tảng với tài khoản đã kết nối; tích sẵn những tài khoản dự án đã chọn."""
        p = self.tab.project
        chosen = set(p.publish_accounts) if p else set()
        while self.tiles_row.count():
            w = self.tiles_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.acc_checks = {}
        by_plat: dict[str, list[dict]] = {}
        for a in publish.accounts():
            by_plat.setdefault(a["platform"], []).append(a)
        for i, (key, cls) in enumerate(publish.PLATFORMS.items()):       # lưới 2 cột: mỗi thẻ đủ rộng để tên tài khoản không bị cắt
            tile, boxes = account_tile(key, cls.label, by_plat.get(key, []), chosen, lambda: self.go_connect())
            self.tiles_row.addWidget(tile, i // 2, i % 2)
            self.acc_checks.update(boxes)
        self.tiles_row.setColumnStretch(0, 1)
        self.tiles_row.setColumnStretch(1, 1)
        self.pub_privacy.setCurrentIndex(max(0, self.pub_privacy.findData(p.publish_privacy if p else "private")))
        self.pub_auto.setChecked(bool(p and p.publish_auto))

    def go_connect(self) -> None:
        self.accept()
        QTimer.singleShot(0, lambda: self.tab.open_settings_section.emit("publish"))

    def apply_publish(self) -> None:
        p = self.tab.project
        if not p:
            return
        p.publish_accounts = [k for k, cb in self.acc_checks.items() if cb.isChecked()] + [a for a in p.publish_accounts if a not in self.acc_checks]
        p.publish_privacy = self.pub_privacy.currentData() or "private"
        p.publish_auto = self.pub_auto.isChecked()

    # ================= ảnh xem trước =================
    def load_preview(self) -> None:
        """Ảnh minh hoạ: khung hình đầu của video đầu tiên có clip; chưa có thì ảnh nhân vật đầu tiên; không có gì thì ẩn."""
        p = self.tab.project
        pm = None
        if p:
            try:
                for t in service.targets(p) if p.publish_scope != "project" else [service.Target(c.id, c.name, c) for c in p.chapters]:
                    img = service.thumbnail(p, t)
                    if img:
                        src = QPixmap(str(img))
                        pm = src.scaled(176, 176, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        x, y = (pm.width() - 176) // 2, (pm.height() - 176) // 2
                        pm = pm.copy(x, y, 176, 176)
                        break
            except Exception:  # noqa: BLE001 - ảnh chỉ để minh hoạ
                pm = None
            if pm is None:
                for c in models.load_characters(p.name):
                    if c.image:
                        pm = rounded_pixmap(c.image, 176, 12)
                        if not pm.isNull():
                            break
                        pm = None
        self.preview.setVisible(pm is not None)
        if pm is not None:
            self.preview.setPixmap(self._rounded(pm, 14))

    @staticmethod
    def _rounded(pm: QPixmap, radius: int) -> QPixmap:
        from PySide6.QtGui import QPainterPath
        out = QPixmap(pm.size())
        out.setDevicePixelRatio(pm.devicePixelRatio())
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio(), radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, pm)
        p.end()
        return out

    # ================= khôi phục / mở / đóng =================
    def reset_defaults(self) -> None:
        if QMessageBox.question(self, "Khôi phục mặc định", "Đưa phong cách hình ảnh, tỉ lệ, số scene tối đa, cấu hình Flow và chế độ đăng về mặc định?\n"
                                "Tài khoản Flow, giọng đọc, ngôn ngữ và tài khoản đăng được giữ nguyên. Bấm Huỷ ở cuối để bỏ thay đổi.") != QMessageBox.Yes:
            return
        t = self.tab
        t.style.setText(DEFAULT_STYLE)
        t.aspect.setCurrentIndex(max(0, t.aspect.findData("9:16")))
        t.max_scenes.setValue(16)
        t.flow_model.setCurrentText("Veo 3.1 - Fast")
        t.flow_res.setCurrentText("720p")
        t.flow_auto_dur.setChecked(True)
        t.flow_parallel.setValue(1)
        t.voice_style.setText(DEFAULT_VOICE_STYLE)
        self.pub_privacy.setCurrentIndex(max(0, self.pub_privacy.findData("private")))
        self.pub_auto.setChecked(False)
        self.refresh_flow()

    def snapshot(self) -> dict:
        t = self.tab
        return dict(style=t.style.text(), aspect=t.aspect.currentIndex(), scenes=t.max_scenes.value(),
                    model=t.flow_model.currentText(), res=t.flow_res.currentText(), auto=t.flow_auto_dur.isChecked(), parallel=t.flow_parallel.value(),
                    acct=t.project.account_id if t.project else 'default', provider=t.provider.currentIndex(), voice=t.current_voice(), lang=t.narr_lang.currentIndex(), vstyle=t.voice_style.text())

    def restore(self, s: dict) -> None:
        t = self.tab
        t.style.setText(s["style"]); t.aspect.setCurrentIndex(s["aspect"]); t.max_scenes.setValue(s["scenes"])
        t.flow_model.setCurrentText(s["model"]); t.flow_res.setCurrentText(s["res"]); t.flow_auto_dur.setChecked(s["auto"]); t.flow_parallel.setValue(s["parallel"])
        t.narr_lang.blockSignals(True); t.narr_lang.setCurrentIndex(s["lang"]); t.narr_lang.blockSignals(False)
        t.set_account(s["acct"]); t.provider.setCurrentIndex(s["provider"]); t.fill_voices(s["voice"]); t.voice_style.setText(s["vstyle"])

    def open_for_project(self) -> bool:
        """Hiện hộp thoại; trả True nếu người dùng bấm Lưu."""
        self.sub.setText(self.tab.project.name if self.tab.project else "")
        self.fill_accounts()
        self.refresh_flow()
        self.refresh_voice()
        self.refresh_credit_card()
        self.sync_aspect()
        self.load_preview()
        self.scroll.verticalScrollBar().setValue(0)
        self.set_nav("visual")
        self._snap = self.snapshot()
        if self.exec() == QDialog.Accepted:
            self.apply_publish()
            self.tab.save_edits()
            return True
        self.restore(self._snap)
        return False

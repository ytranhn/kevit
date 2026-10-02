"""Quản lý tài khoản Google Flow (Cài đặt → Google Flow): danh sách thẻ tài khoản bên trái (credit, thanh tiến độ), thông tin chi tiết bên phải
(thống kê credit, gia hạn, thao tác), công tắc tự chuyển tài khoản và cập nhật credit."""
from __future__ import annotations

import threading

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from . import accounts, flow_auto, icons, theme
from .theme import SP
from .widgets import ElidedLabel, repolish
from .workers import Worker

AVATAR_COLORS = ("#6B6FF2", "#2E9B6F", "#D97757", "#4F7BF2", "#B85CB8", "#C9A227")


def avatar_circle(name: str, acc_id: str, size: int) -> QLabel:
    a = QLabel((name or "?").strip()[:1].upper() or "?")
    a.setAlignment(Qt.AlignCenter)
    a.setFixedSize(size, size)
    color = AVATAR_COLORS[sum(map(ord, acc_id)) % len(AVATAR_COLORS)]
    a.setStyleSheet(f"background: {color}; color: #FFFFFF; border-radius: {size // 2}px; font-weight: 700; font-size: {size // 2 - 1}px;")
    return a


def status_dot(up: bool | None) -> QFrame:
    d = QFrame()                                  # không dùng QLabel rỗng: Qt coi chiều cao tối thiểu của nó là 16px
    d.setFixedSize(9, 9)
    color = {True: theme.T["ok"], False: theme.T["faint"], None: theme.T["border_strong"]}[up]
    d.setStyleSheet(f"background: {color}; border-radius: 4px;")
    return d


class ToggleSwitch(QCheckBox):
    """Công tắc bật/tắt (kế thừa QCheckBox: giữ nguyên setChecked/isChecked/toggled)."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(48, 28)
        self.setCursor(Qt.PointingHandCursor)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        on = self.isChecked()
        track = QColor(theme.T["accent"] if on else theme.T["border_strong"])
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), self.height() / 2, self.height() / 2)
        p.setBrush(QColor("#FFFFFF"))
        d = self.height() - 8
        p.drawEllipse(QRectF(self.width() - d - 4 if on else 4, 4, d, d))


def progress(percent: int | None) -> QProgressBar:
    b = QProgressBar()
    b.setProperty("acctbar", True)
    b.setRange(0, 100)
    b.setValue(percent or 0)
    b.setTextVisible(False)
    b.setFixedHeight(8)
    return b


class AccountCard(QFrame):
    """Một tài khoản trong danh sách: avatar, tên, trạng thái Chrome, nhãn 'Đang dùng', menu '···', credit + thanh tiến độ."""
    selected = Signal()
    menu_action = Signal(str)

    def __init__(self, a: accounts.Account, active: bool, default_new: bool, up: bool | None, n_projects: int):
        super().__init__()
        self.setProperty("provrow", True)
        self.setFixedHeight(122)
        name = ElidedLabel()
        name.setStyleSheet("font-weight: 600; font-size: 15px; background: transparent;")
        name.set_full(a.name)
        state = {True: "Đang mở", False: "Chưa mở", None: "Đang kiểm tra…"}[up]          # ngắn hơn bản chi tiết: thẻ hẹp
        info = ElidedLabel()
        info.setProperty("caption", True)
        info.set_full("" if active else f"Chrome {state.lower()}  ·  Cổng {a.port}  ·  {n_projects} dự án")   # có nhãn 'Đang dùng' thì không đủ chỗ cho chữ: chấm màu + tooltip nói trạng thái Chrome
        info.setToolTip(f"Chrome {state.lower()} · Cổng {a.port} · {n_projects} dự án")
        srow = QHBoxLayout()
        srow.setSpacing(SP.s)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.addWidget(status_dot(up), 0, Qt.AlignVCenter)
        srow.addWidget(info, 1)
        col = QVBoxLayout()
        col.setSpacing(SP.xs)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(name)
        col.addLayout(srow)
        if active:                                     # nhãn nằm ở dòng trạng thái để tên tài khoản được rộng tối đa
            p1 = QLabel("Đang dùng")
            p1.setProperty("pill", "ok")
            p1.setFixedHeight(24)
            srow.addWidget(p1, 0, Qt.AlignVCenter)
        more = QPushButton()
        more.setProperty("iconbtn", True)
        icons.attach(more, "more", 20)
        menu = QMenu(more)
        for key, label in (("default", "Đặt mặc định cho dự án mới"), ("rename", "Đổi tên…"), ("chrome", "Mở Chrome để đăng nhập"), ("remove", "Gỡ khỏi danh sách")):
            act = menu.addAction(label, lambda k=key: self.menu_action.emit(k))
            if key == "remove":
                act.setEnabled(a.id != accounts.DEFAULT_ID)
            if key == "default":
                act.setEnabled(not default_new)
        more.setMenu(menu)
        top = QHBoxLayout()
        top.setSpacing(SP.m)
        top.addWidget(avatar_circle(a.name, a.id, 44), 0, Qt.AlignTop)
        top.addLayout(col, 1)
        top.addWidget(more, 0, Qt.AlignTop)
        # ---- credit + thanh tiến độ ----
        pct = accounts.used_percent(a)
        credit = QLabel(f"<b style='font-size:17px'>{accounts.fmt_credits(a.credits)}</b>" + (f"<span style='color:{theme.T['muted']}'> credit</span>" if a.credits is not None else "")
                        if a.credits is not None else f"<span style='color:{theme.T['muted']}'>Chưa đọc credit</span>")
        credit.setStyleSheet("background: transparent;")
        bottom = QHBoxLayout()
        bottom.setSpacing(SP.m)
        bottom.addWidget(credit)
        if pct is not None:
            bottom.addWidget(progress(pct), 1)
            pc = QLabel(f"{pct}%")
            pc.setProperty("caption", True)
            bottom.addWidget(pc)
        else:
            bottom.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.l, SP.m, SP.m, SP.m)
        lay.setSpacing(SP.s)
        lay.addLayout(top)
        lay.addStretch(1)
        lay.addLayout(bottom)

    def mousePressEvent(self, e):
        self.selected.emit()
        super().mousePressEvent(e)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        repolish(self)


def stat_box(title: str, value: str, unit: str = "") -> QWidget:
    w = QWidget()
    t = QLabel(title)
    t.setProperty("caption", True)
    v = QLabel(f"<span style='font-size:26px;font-weight:700'>{value}</span>" + (f"<span style='color:{theme.T['muted']}'>  {unit}</span>" if unit else ""))
    v.setStyleSheet("background: transparent;")
    c = QVBoxLayout(w)
    c.setContentsMargins(0, 0, 0, 0)
    c.setSpacing(SP.xs)
    c.addWidget(t)
    c.addWidget(v)
    return w


def info_tile(icon: str, title: str, value: str) -> QFrame:
    f = QFrame()
    f.setProperty("banner", True)
    tile = QLabel()
    tile.setProperty("navtile", True)
    tile.setFixedSize(40, 40)
    tile.setAlignment(Qt.AlignCenter)
    icons.attach(tile, icon, 20)
    t = QLabel(title)
    t.setProperty("caption", True)
    v = ElidedLabel()
    v.setStyleSheet("font-weight: 500; background: transparent;")
    v.set_full(value)
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    col.addStretch(1)
    col.addWidget(t)
    col.addWidget(v)
    col.addStretch(1)
    row = QHBoxLayout(f)
    row.setContentsMargins(SP.m, SP.m, SP.m, SP.m)
    row.setSpacing(SP.m)
    row.addWidget(tile)
    row.addLayout(col, 1)
    return f


class AccountsPanel(QWidget):
    changed = Signal()                 # danh sách tài khoản vừa đổi (thêm/đổi tên/gỡ/đổi mặc định/bật tự chuyển)
    _status = Signal(str, bool)        # (id tài khoản, Chrome đang mở?) từ luồng kiểm tra nền

    def __init__(self, parent=None):
        super().__init__(parent)
        self._up: dict[str, bool] = {}
        self._busy = False
        self.worker = None
        self._row = -1

        # ---- nút đặt ở tiêu đề trang (do SettingsTab gắn vào) ----
        self.b_add = QPushButton("Thêm tài khoản…")
        self.b_add.setProperty("primary", True)
        self.b_add.setFixedHeight(40)
        icons.attach(self.b_add, "plus", 18)
        self.b_open = QPushButton("Mở Chrome để đăng nhập")
        self.b_open.setFixedHeight(40)
        icons.attach(self.b_open, "open", 18)

        # ---- cột trái: danh sách thẻ ----
        self.list = QListWidget()
        self.list.setObjectName("provList")
        self.list.setSpacing(SP.s)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self.on_select)
        self.list.itemDoubleClicked.connect(lambda *_: self.open_chrome())
        self.left_title = QLabel("Danh sách tài khoản")
        self.left_title.setProperty("subheading", True)
        left = QFrame()
        left.setProperty("card", True)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        lv.setSpacing(SP.m)
        lv.addWidget(self.left_title)
        lv.addWidget(self.list, 1)

        # ---- cột phải: chi tiết ----
        self.b_rename = QPushButton("Đổi tên…")
        self.b_default = QPushButton("Đặt mặc định")
        self.b_default.setToolTip("Dự án tạo mới sẽ dùng tài khoản này")
        self.b_remove = QPushButton("Gỡ")
        self.b_remove.setProperty("danger", True)
        self.b_credit = QPushButton("Cập nhật credit")
        icons.attach(self.b_credit, "refresh", 18)
        self.b_credit_all = QPushButton("Cập nhật tất cả")
        for b in (self.b_rename, self.b_default, self.b_remove, self.b_credit, self.b_credit_all):
            b.setFixedHeight(40)
        self.auto = ToggleSwitch()
        self.auto.setChecked(accounts.auto_switch())
        self.auto.setToolTip("Khi gen mà tài khoản của dự án không đủ credit, tự chuyển phần còn lại sang tài khoản khác còn credit "
                             "(mở sẵn Chrome của tài khoản dự phòng để không phải chờ). Tắt: tiến trình bị chặn và báo cảnh báo.")
        self.auto.toggled.connect(lambda on: (accounts.set_auto_switch(on), self.changed.emit()))
        self.msg = QLabel("")
        self.msg.setProperty("caption", True)
        self.msg.setWordWrap(True)
        self.detail = QFrame()
        self.detail.setProperty("card", True)
        self.dv = QVBoxLayout(self.detail)
        self.dv.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        self.dv.setSpacing(SP.m)
        self.dyn = QVBoxLayout()                       # phần thay đổi theo tài khoản đang chọn
        self.dyn.setSpacing(SP.m)
        self.dyn.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Thông tin tài khoản")
        title.setProperty("subheading", True)
        self.detail_more = QPushButton()
        self.detail_more.setProperty("iconbtn", True)
        icons.attach(self.detail_more, "more", 20)
        self.detail_menu = QMenu(self.detail_more)
        self.detail_more.setMenu(self.detail_menu)
        head = QHBoxLayout()
        head.addWidget(title, 1)
        head.addWidget(self.detail_more)
        self.dv.addLayout(head)
        self.dv.addLayout(self.dyn)
        acts = QGridLayout()
        acts.setHorizontalSpacing(SP.s)
        for c in range(3):
            acts.setColumnStretch(c, 1)
        acts.addWidget(self.b_rename, 0, 0)
        acts.addWidget(self.b_default, 0, 1)
        acts.addWidget(self.b_remove, 0, 2)
        self.dv.addLayout(acts)
        sw_title = QLabel("Tự động chuyển tài khoản")
        sw_title.setStyleSheet("font-weight: 600; background: transparent;")
        sw_sub = QLabel("Khi credit của tài khoản hiện tại không đủ, tự chuyển phần còn lại sang tài khoản khác còn credit để phiên làm việc không bị gián đoạn.")
        sw_sub.setProperty("caption", True)
        sw_sub.setWordWrap(True)
        sw_col = QVBoxLayout()
        sw_col.setSpacing(SP.xs)
        sw_col.addWidget(sw_title)
        sw_col.addWidget(sw_sub)
        sw = QFrame()
        sw.setProperty("banner", True)
        swr = QHBoxLayout(sw)
        swr.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
        swr.setSpacing(SP.l)
        swr.addLayout(sw_col, 1)
        swr.addWidget(self.auto, 0, Qt.AlignVCenter)
        self.dv.addWidget(sw)
        refresh_row = QGridLayout()
        refresh_row.setHorizontalSpacing(SP.s)
        refresh_row.setColumnStretch(0, 1)
        refresh_row.setColumnStretch(1, 1)
        refresh_row.addWidget(self.b_credit, 0, 0)
        refresh_row.addWidget(self.b_credit_all, 0, 1)
        self.dv.addLayout(refresh_row)
        self.dv.addWidget(self.msg)
        self.dv.addStretch(1)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SP.l)
        left.setMinimumWidth(340)                     # thẻ tài khoản đủ rộng để không bị cắt tên/nhãn/credit
        root.addWidget(left, 4)
        root.addWidget(self.detail, 6)

        self.b_add.clicked.connect(self.add)
        self.b_open.clicked.connect(self.open_chrome)
        self.b_rename.clicked.connect(self.rename)
        self.b_default.clicked.connect(self.make_default)
        self.b_remove.clicked.connect(self.remove)
        self.b_credit.clicked.connect(lambda: self.update_credits(False))
        self.b_credit_all.clicked.connect(lambda: self.update_credits(True))
        self._status.connect(self._on_status)
        self.refresh()

    # ---- danh sách ----
    def current(self) -> accounts.Account | None:
        it = self.list.currentItem()
        return accounts.get(it.data(Qt.UserRole)) if it else None

    def item_text(self, i: int) -> str:
        """Mô tả văn bản của thẻ thứ i (tên + thẻ nhãn, trạng thái Chrome, credit) — dùng cho kiểm thử/trợ năng."""
        a = accounts.get(self.list.item(i).data(Qt.UserRole))
        tags = ("  ·  mặc định cho dự án mới" if a.id == accounts.default_new_id() else "") + ("  ·  đang dùng" if a.id == accounts.active().id else "")
        up = self._up.get(a.id)
        state = "Chrome đang mở" if up else ("Chrome chưa mở" if up is False else "…")
        return f"{a.name}{tags}\n{state}  ·  cổng {a.port}  ·  {len(accounts.usage(a.id))} dự án\n{accounts.describe_credits(a)}"

    def refresh(self, keep: str | None = None) -> None:
        keep = keep or (self.current().id if self.current() else None)
        accs, dflt, act = accounts.all_accounts(), accounts.default_new_id(), accounts.active().id
        self.list.blockSignals(True)
        self.list.clear()
        for a in accs:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, a.id)
            it.setSizeHint(QSize(0, 122 + SP.s))
            self.list.addItem(it)
            card = AccountCard(a, a.id == act, a.id == dflt, self._up.get(a.id), len(accounts.usage(a.id)))
            card.selected.connect(lambda i=a.id: self.select_id(i))
            card.menu_action.connect(lambda k, i=a.id: self.act_on(i, k))
            self.list.setItemWidget(it, card)
        self._row = next((i for i, a in enumerate(accs) if a.id == keep), 0)
        self.list.setCurrentRow(self._row)
        self.list.blockSignals(False)
        self.left_title.setText(f"Danh sách tài khoản  ({len(accs)})")
        self.mark_selected()
        self.show_detail()
        self.update_buttons()
        for a in accs:                                             # kiểm tra Chrome từng tài khoản ở luồng nền (không chặn giao diện)
            threading.Thread(target=lambda a=a: self._status.emit(a.id, flow_auto._cdp_up(a.cdp_url)), daemon=True).start()

    def mark_selected(self) -> None:
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if w is not None:
                w.set_selected(i == self._row)

    def select_id(self, acc_id: str) -> None:
        row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole) == acc_id), -1)
        if row >= 0 and row != self._row:
            self.list.setCurrentRow(row)

    def on_select(self, row: int) -> None:
        if row < 0:
            return
        self._row = row
        self.mark_selected()
        self.show_detail()
        self.update_buttons()

    def act_on(self, acc_id: str, key: str) -> None:
        """Thao tác từ menu '···' của một thẻ: chọn tài khoản đó rồi chạy."""
        self.select_id(acc_id)
        QTimer.singleShot(0, {"default": self.make_default, "rename": self.rename, "chrome": self.open_chrome, "remove": self.remove}[key])

    # ---- chi tiết ----
    def _clear_dyn(self) -> None:
        while self.dyn.count():
            it = self.dyn.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().setParent(None)
                        sub.widget().deleteLater()

    def show_detail(self) -> None:
        a = self.current()
        self._clear_dyn()
        self.detail_menu.clear()
        if a is None:
            return
        dflt, act, up = accounts.default_new_id(), accounts.active().id, self._up.get(a.id)
        for key, label in (("default", "Đặt mặc định cho dự án mới"), ("rename", "Đổi tên…"), ("chrome", "Mở Chrome để đăng nhập"), ("remove", "Gỡ khỏi danh sách")):
            act_ = self.detail_menu.addAction(label, lambda k=key: self.act_on(a.id, k))
            act_.setEnabled(not (key == "remove" and a.id == accounts.DEFAULT_ID) and not (key == "default" and a.id == dflt))
        # tiêu đề tài khoản
        name = QLabel(a.name)
        name.setStyleSheet("font-weight: 600; font-size: 17px; background: transparent;")
        state = {True: "Chrome đang mở", False: "Chrome chưa mở", None: "Đang kiểm tra…"}[up]
        info_text = f"{state}  ·  Cổng {a.port}  ·  {len(accounts.usage(a.id))} dự án" + (f"  ·  {a.email}" if a.email and a.email != a.name else "")
        info = ElidedLabel()                           # cắt '…' khi hẹp, không làm bề rộng tối thiểu của cả trang phình ra
        info.setProperty("caption", True)
        info.set_full(info_text)
        info.setToolTip(info_text)
        srow = QHBoxLayout()
        srow.setSpacing(SP.s)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.addWidget(status_dot(up), 0, Qt.AlignVCenter)
        srow.addWidget(info, 1)
        col = QVBoxLayout()
        col.setSpacing(SP.xs)
        col.addWidget(name)
        col.addLayout(srow)
        pills = QHBoxLayout()
        pills.setSpacing(SP.xs)
        if a.id == act:
            p1 = QLabel("Đang dùng")
            p1.setProperty("pill", "ok")
            p1.setFixedHeight(24)
            pills.addWidget(p1, 0, Qt.AlignTop)
        if a.id == dflt:
            p2 = QLabel("Mặc định")
            p2.setProperty("pill", "info")
            p2.setFixedHeight(24)
            pills.addWidget(p2, 0, Qt.AlignTop)
        head = QWidget()
        hr = QHBoxLayout(head)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(SP.m)
        hr.addWidget(avatar_circle(a.name, a.id, 56), 0, Qt.AlignTop)
        hr.addLayout(col, 1)
        hr.addLayout(pills)
        self.dyn.addWidget(head)
        # thống kê credit
        pct = accounts.used_percent(a)
        cap = accounts.capacity(a)
        stats = QFrame()
        stats.setProperty("banner", True)
        row = QHBoxLayout()
        row.setSpacing(SP.xl)
        row.addWidget(stat_box("Credit hiện có", accounts.fmt_credits(a.credits), "credit" if a.credits is not None else ""), 1)
        daily_txt = "chưa rõ" if a.daily is None else str(a.daily)
        row.addWidget(stat_box("Credit ngày còn lại", daily_txt, ("/ " + str(a.daily_grant) if a.daily_grant else "credit") if a.daily is not None else ""), 1)
        row.addWidget(stat_box("Đã dùng", f"{pct}%" if pct is not None else "—"), 1)
        sv = QVBoxLayout(stats)
        sv.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
        sv.setSpacing(SP.s)
        sv.addLayout(row)
        if pct is not None:
            bar = QHBoxLayout()
            bar.setSpacing(SP.m)
            bar.addWidget(progress(pct), 1)
            cap_lab = QLabel(f"{accounts.fmt_credits(a.credits)} / {accounts.fmt_credits(cap)} credit")
            cap_lab.setProperty("caption", True)
            bar.addWidget(cap_lab)
            sv.addLayout(bar)
        self.dyn.addWidget(stats)
        # gia hạn + cập nhật
        tiles = QHBoxLayout()
        tiles.setSpacing(SP.m)
        tiles.addWidget(info_tile("refresh", "Gia hạn", a.renew or "Chưa rõ (bấm Cập nhật credit)"), 1)
        tiles.addWidget(info_tile("check", "Cập nhật lần cuối", accounts.fmt_age(a.checked_at)), 1)
        trow = QWidget()
        trow.setLayout(tiles)
        tiles.setContentsMargins(0, 0, 0, 0)
        self.dyn.addWidget(trow)

    def _on_status(self, acc_id: str, up: bool) -> None:
        if self._up.get(acc_id) == up:
            return
        self._up[acc_id] = up
        cur = self.current().id if self.current() else None
        QTimer.singleShot(0, lambda: self.refresh(cur))

    def set_busy(self, busy: bool) -> None:
        """Đang có tác vụ Flow chạy nền: không mở/đọc credit (sẽ giành tab Flow của tác vụ đó)."""
        self._busy = busy
        self.update_buttons()

    def update_buttons(self, *_):
        a = self.current()
        free = self.worker is None and not self._busy
        self.b_credit.setEnabled(a is not None and free)
        self.b_credit_all.setEnabled(free)
        self.b_open.setEnabled(a is not None and free)
        self.b_rename.setEnabled(a is not None)
        self.b_default.setEnabled(a is not None and a.id != accounts.default_new_id())
        self.b_remove.setEnabled(a is not None and a.id != accounts.DEFAULT_ID)

    # ---- thao tác ----
    def update_credits(self, all_accounts: bool) -> None:
        """Đọc credit (và ngày gia hạn) của tài khoản đang chọn hoặc tất cả, mở Chrome của tài khoản nếu chưa mở."""
        if self.worker is not None or self._busy:
            return
        accs = accounts.all_accounts() if all_accounts else ([self.current()] if self.current() else [])
        if not accs:
            return
        self.msg.setText("Đang đọc credit" + (" của tất cả tài khoản" if all_accounts else f" của “{accs[0].name}”") + "… (có thể mở Chrome của tài khoản)")

        def job(log):
            res = []
            for a in accs:
                try:
                    with flow_auto.FlowAuto(log, acc=a) as f:
                        info = f.read_credits(deep=True)
                except Exception as e:  # noqa: BLE001
                    res.append(f"“{a.name}”: không đọc được ({str(e)[:80]})")
                    continue
                if info:
                    accounts.save_credits(a.id, info["credits"], info.get("daily"), info.get("renew", ""), info.get("email", ""),
                                          info.get("plan_total"), info.get("daily_grant"))
                    res.append(f"“{a.name}”: {accounts.fmt_credits(info['credits'])} credit")
                else:
                    res.append(f"“{a.name}”: không đọc được credit (đã đăng nhập Google Flow chưa?)")
            return res
        self.worker = Worker(job)
        self.worker.done.connect(lambda res: self._credits_done("; ".join(res)))
        self.worker.failed.connect(lambda e: self._credits_done(f"Lỗi: {e}"))
        self.update_buttons()
        self.worker.start()

    def _credits_done(self, text: str) -> None:
        self.worker = None
        self.refresh()
        self.msg.setText(text)
        self.changed.emit()

    def add(self) -> None:
        name, ok = QInputDialog.getText(self, "Thêm tài khoản Flow", "Tên gợi nhớ (vd. email của tài khoản):")
        if not ok or not name.strip():
            return
        try:
            a = accounts.add(name)
        except ValueError as e:
            QMessageBox.warning(self, "Không thêm được", str(e))
            return
        self.refresh(a.id)
        self.msg.setText(f"Đã thêm “{a.name}”. Bấm “Mở Chrome để đăng nhập” rồi đăng nhập Google trong cửa sổ Chrome vừa mở.")
        self.changed.emit()

    def rename(self) -> None:
        a = self.current()
        if not a:
            return
        name, ok = QInputDialog.getText(self, "Đổi tên tài khoản", "Tên mới:", text=a.name)
        if not ok or not name.strip():
            return
        try:
            accounts.rename(a.id, name)
        except ValueError as e:
            QMessageBox.warning(self, "Không đổi được", str(e))
            return
        self.refresh(a.id)
        self.changed.emit()

    def make_default(self) -> None:
        a = self.current()
        if a:
            accounts.set_default_new(a.id)
            self.refresh(a.id)
            self.msg.setText(f"Dự án tạo mới sẽ dùng tài khoản “{a.name}”.")
            self.changed.emit()

    def remove(self) -> None:
        a = self.current()
        if not a or a.id == accounts.DEFAULT_ID:
            return
        used = accounts.usage(a.id)
        extra = (f"\n\n{len(used)} dự án đang dùng tài khoản này ({', '.join(used[:5])}{'…' if len(used) > 5 else ''}) "
                 "sẽ chuyển về tài khoản chính; clip đã tải về máy không bị ảnh hưởng.") if used else ""
        if QMessageBox.question(self, "Gỡ tài khoản", f"Gỡ “{a.name}” khỏi danh sách?\nĐăng nhập Chrome của tài khoản vẫn nằm trên ổ đĩa "
                                f"({a.profile}), thêm lại cùng tên là dùng tiếp được.{extra}") != QMessageBox.Yes:
            return
        try:
            moved = accounts.remove(a.id)
        except ValueError as e:
            QMessageBox.warning(self, "Không gỡ được", str(e))
            return
        self.refresh()
        self.msg.setText(f"Đã gỡ “{a.name}”." + (f" {len(moved)} dự án đã chuyển về tài khoản chính." if moved else ""))
        self.changed.emit()

    def open_chrome(self) -> None:
        a = self.current()
        if not a or self.worker is not None:
            return
        self.msg.setText(f"Đang mở Chrome cho “{a.name}”…")
        self.worker = Worker(lambda log: flow_auto.launch_chrome(a))
        self.worker.done.connect(lambda _: self._opened(a, None))
        self.worker.failed.connect(lambda e: self._opened(a, e))
        self.update_buttons()
        self.worker.start()

    def _opened(self, a: accounts.Account, err: str | None) -> None:
        self.worker = None
        if err:
            self.msg.setText(f"Không mở được Chrome: {err}")
        else:
            self.msg.setText(f"Chrome của “{a.name}” đã mở. Đăng nhập Google Flow trong cửa sổ đó (chỉ cần một lần).")
            self._up[a.id] = True
        self.refresh(a.id)

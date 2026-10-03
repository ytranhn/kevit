"""Tab Đăng video: chọn video (chương), soạn tiêu đề/mô tả/hashtag (AI hỗ trợ), chọn tài khoản đăng rồi ghép + đăng qua API."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import flow, icons, publish, theme
from .publish import base, describe, schedule, service
from .schedule_dialog import ScheduleDialog, ask_datetime
from .shell import platform_tile, section
from .theme import SP
from .widgets import Combo, ElidedLabel, repolish
from .workers import Worker

PRIVACY = (("private", "Riêng tư (an toàn để thử)"), ("unlisted", "Không công khai (có link)"), ("public", "Công khai"))
SCOPES = (("chapters", "Mỗi chương một video"), ("project", "Một video cho cả dự án"))
SORTS = (("newest", "Mới nhất"), ("oldest", "Cũ nhất"), ("order", "Theo thứ tự chương"))
FILTERS = (("all", "Tất cả"), ("ready", "Sẵn sàng"), ("posted", "Đã đăng"))
BRAND = {"youtube": ("▶", "#E62117"), "tiktok": ("♪", "#111111"), "facebook": ("f", "#1877F2"), "instagram": ("◎", "#C13584")}
MAX_TAGS = 30


def _fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _fmt_date(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def _qicon(name: str) -> QIcon:
    return QIcon(icons.pixmap(name, 18, theme.T["muted"]))


# ================================================================ thành phần nhỏ
class FlowLayout(QLayout):
    """Xếp các phần tử từ trái sang phải, tự xuống dòng khi hết chỗ (dùng cho chip hashtag)."""

    def __init__(self, parent=None, spacing: int = SP.s):
        super().__init__(parent)
        self._items = []
        self._sp = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        m = self.contentsMargins()
        return self._layout(QRect(0, 0, w - m.left() - m.right(), 0), True) + m.top() + m.bottom()

    def setGeometry(self, r):
        super().setGeometry(r)
        self._layout(r.adjusted(self.contentsMargins().left(), self.contentsMargins().top(),
                                -self.contentsMargins().right(), -self.contentsMargins().bottom()), False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize(0, 0)
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._sp
            line_h = max(line_h, h)
        return y + line_h - rect.y()


class Thumb(QWidget):
    """Ảnh đại diện bo góc (cắt đầy khung) kèm nhãn thời lượng; có thể gắn một nút nổi ở góc trên phải."""

    def __init__(self, w: int, h: int):
        super().__init__()
        self.setFixedSize(w, h)
        self._pm: QPixmap | None = None
        self._dur = ""

    def set(self, pm: QPixmap | None, seconds: float = 0.0) -> None:
        self._pm = pm if pm is not None and not pm.isNull() else None
        self._dur = _fmt_dur(seconds) if seconds else ""
        self.update()

    def add_overlay(self, btn: QPushButton) -> None:
        btn.setParent(self)
        btn.setFixedSize(36, 36)                       # khớp kích thước của kiểu iconbtn trong theme
        btn.move(self.width() - 36 - 10, 10)           # cách mép phải và mép trên 10px

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, self.width(), self.height(), 10, 10)
        p.setClipPath(clip)
        p.fillRect(self.rect(), QColor(theme.T["video"]))
        if self._pm:
            sc = self._pm.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(0, 0, sc, (sc.width() - self.width()) // 2, (sc.height() - self.height()) // 2, self.width(), self.height())
        else:
            p.setPen(QColor(theme.T["faint"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Chưa có ảnh")
        if self._dur:
            f = QFont(p.font())
            f.setPointSizeF(max(f.pointSizeF() - 1.5, 8))
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(self._dur) + 12
            r = QRect(self.width() - tw - 6, self.height() - 24, tw, 18)
            p.setClipping(False)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(r, Qt.AlignCenter, self._dur)


class VideoRow(QFrame):
    """Một video trong danh sách: ô chọn · ảnh · tiêu đề · trạng thái · ngày tạo · menu '···'."""
    clicked = Signal()
    checked = Signal()
    action = Signal(str)

    def __init__(self, info: dict):
        super().__init__()
        self.setProperty("vidrow", True)
        self.setFixedHeight(96)
        self.setCursor(Qt.PointingHandCursor)
        self.check = QCheckBox()
        self.check.toggled.connect(lambda *_: self.checked.emit())
        self.thumb = Thumb(124, 72)
        self.title = ElidedLabel()
        self.title.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        self.pill = QLabel()
        self.date = ElidedLabel()
        self.date.setProperty("caption", True)
        more = QPushButton()
        more.setProperty("iconbtn", True)
        icons.attach(more, "more", 20)
        menu = QMenu(more)
        menu.addAction("Hiện file video", lambda: self.action.emit("reveal"))
        menu.addAction("Ghép lại video", lambda: self.action.emit("remerge"))
        menu.addAction("Xoá nội dung đã soạn", lambda: self.action.emit("clear"))
        more.setMenu(menu)
        more.setFixedSize(30, 30)
        col = QVBoxLayout()
        col.setSpacing(SP.xs)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        col.addWidget(self.title)
        pr = QHBoxLayout()
        pr.addWidget(self.pill)
        pr.addStretch(1)
        col.addLayout(pr)
        col.addWidget(self.date)
        col.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, SP.s, SP.s, SP.s)
        row.setSpacing(SP.m)
        row.addWidget(self.check, 0, Qt.AlignVCenter)
        row.addWidget(self.thumb, 0, Qt.AlignVCenter)
        row.addLayout(col, 1)
        row.addWidget(more, 0, Qt.AlignTop)
        self.update_info(info)

    def update_info(self, info: dict) -> None:
        self.info = info
        self.title.set_full(info["title"])
        self.title.setToolTip(info["title"])
        self.pill.setText(info["status"])
        self.pill.setProperty("pill", info["kind"])
        repolish(self.pill)
        self.date.set_full(info["date"])

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        repolish(self)


class HashChip(QFrame):
    removed = Signal(str)

    def __init__(self, tag: str):
        super().__init__()
        self.setProperty("hashtag", True)
        x = QPushButton("×")
        x.setProperty("tagx", True)
        x.setCursor(Qt.PointingHandCursor)
        x.setToolTip("Bỏ hashtag này")
        x.clicked.connect(lambda: self.removed.emit(tag))
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 3, SP.xs, 3)
        row.setSpacing(SP.xs)
        row.addWidget(QLabel("#" + tag))
        row.addWidget(x)


class Stepper(QWidget):
    """Ba bước ở đầu trang: Nội dung · Nền tảng · Đăng. Bước xong hiện dấu ✓, bước đang làm được tô màu chủ đạo."""
    STEPS = (("Nội dung", "Tiêu đề, mô tả, hashtag"), ("Nền tảng", "Chọn tài khoản đăng"), ("Đăng", "Cài đặt và xuất bản"))

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP.m)
        self.dots: list[QLabel] = []
        for i, (t, sub) in enumerate(self.STEPS):
            dot = QLabel(str(i + 1))
            dot.setProperty("stepdot", True)
            dot.setAlignment(Qt.AlignCenter)
            dot.setFixedSize(34, 34)
            tt = QLabel(t)
            tt.setStyleSheet("font-weight: 600; background: transparent;")
            ss = QLabel(sub)
            ss.setProperty("caption", True)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(tt)
            col.addWidget(ss)
            row.addWidget(dot)
            row.addLayout(col)
            if i < len(self.STEPS) - 1:
                line = QFrame()
                line.setProperty("stepline", True)
                line.setFixedWidth(48)
                row.addWidget(line)
            self.dots.append(dot)

    def set_done(self, done: list[bool]) -> None:
        current = next((i for i, d in enumerate(done) if not d), len(done) - 1)
        for i, dot in enumerate(self.dots):
            state = "done" if done[i] else ("active" if i == current else "")
            dot.setText("✓" if done[i] else str(i + 1))
            dot.setProperty("state", state)
            repolish(dot)


class AccountPick(QFrame):
    """Một tài khoản đăng được: logo nền tảng · tên · nền tảng · ô tích. Bấm vào cả dòng để tích/bỏ tích. Dùng ở tab Đăng video và Cài đặt dự án."""

    def __init__(self, acc: dict, checked: bool):
        super().__init__()
        self.setProperty("provrow", True)
        self.setFixedHeight(54)
        self.setCursor(Qt.PointingHandCursor)
        name = QLabel(acc.get("label", acc["id"]) + (f"  ·  {acc['page_name']}" if acc.get("page_name") else ""))
        name.setStyleSheet("font-weight: 600; background: transparent;")
        name.setAttribute(Qt.WA_TransparentForMouseEvents)
        plat = QLabel(publish.PLATFORMS[acc["platform"]].label)
        plat.setProperty("caption", True)
        plat.setAttribute(Qt.WA_TransparentForMouseEvents)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addStretch(1)
        col.addWidget(name)
        col.addWidget(plat)
        col.addStretch(1)
        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.check.toggled.connect(self._mark)
        logo = platform_tile(acc["platform"], 30)
        logo.setAttribute(Qt.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 0, SP.m, 0)
        row.setSpacing(SP.m)
        row.addWidget(logo)
        row.addLayout(col, 1)
        row.addWidget(self.check)
        self._mark(checked)

    def _mark(self, on: bool) -> None:
        self.setProperty("selected", bool(on))
        repolish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.check.toggle()
        super().mousePressEvent(e)


# ================================================================ tab chính
class PublishTab(QWidget):
    activity = Signal(str, str)           # (nội dung, mức) -> thanh trạng thái
    open_settings_requested = Signal()    # bấm “Kết nối” ở thẻ nền tảng chưa có tài khoản

    def __init__(self, get_project, log=print):
        super().__init__()
        self.get_project, self.log_sink = get_project, log
        self._worker: Worker | None = None
        self._thumb_worker: Worker | None = None
        self._cancel = threading.Event()
        self._shown: service.Target | None = None
        self._project_name = ""
        self._targets: list[service.Target] = []
        self._rows: dict[str, VideoRow] = {}
        self._thumbs: dict[str, tuple[QPixmap | None, float]] = {}
        self._tags: list[str] = []
        self._loading = False
        self._filter = "ready"
        self.acc_checks: dict[str, QCheckBox] = {}

        # ---------- đầu trang ----------
        self.stepper = Stepper()
        title = QLabel("Đăng video")
        title.setProperty("pagetitle", True)
        desc = QLabel("Tự ghép video, viết mô tả và hashtag rồi đăng lên YouTube, TikTok, Facebook, Instagram qua API chính thức.")
        desc.setProperty("caption", True)
        desc.setWordWrap(True)
        tcol = QVBoxLayout()
        tcol.setSpacing(SP.xs)
        tcol.addWidget(title)
        tcol.addWidget(desc)
        top = QHBoxLayout()
        top.setSpacing(SP.xl)
        top.addLayout(tcol, 1)
        top.addWidget(self.stepper, 0, Qt.AlignVCenter)

        # ---------- cột trái: danh sách video ----------
        self.lbl_count = QLabel("Video sẽ đăng (0)")
        self.lbl_count.setProperty("subheading", True)
        self.scope = Combo()
        for k, label in SCOPES:
            self.scope.addItem(label, k)
        self.scope.activated.connect(self.on_scope)
        self.tab_btns: dict[str, QPushButton] = {}
        seg = QFrame()
        seg.setProperty("seg", True)
        sr = QHBoxLayout(seg)
        sr.setContentsMargins(SP.xs, SP.xs, SP.xs, SP.xs)
        sr.setSpacing(SP.xs)
        for k, label in FILTERS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("segbtn", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, key=k: self.set_filter(key))
            sr.addWidget(b)
            self.tab_btns[k] = b
        self.tab_btns["ready"].setChecked(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm video…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(_qicon("search"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda *_: self.apply_filter())
        self.sort = Combo()
        for k, label in SORTS:
            self.sort.addItem(label, k)
        self.sort.activated.connect(lambda *_: self.apply_filter())
        self.all_check = QCheckBox("Chọn tất cả video đang hiện")
        self.all_check.clicked.connect(lambda on: self.check_all(on))
        self.empty = QLabel("")
        self.empty.setProperty("caption", True)
        self.empty.setWordWrap(True)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(SP.s)
        self.list_box.setContentsMargins(0, 0, SP.xs, 0)
        self.list_box.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.list_box)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_scroll.setWidget(holder)
        self.list_scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        left = QFrame()
        left.setProperty("card", True)
        left.setMinimumWidth(420)
        left.setMaximumWidth(520)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        lv.setSpacing(SP.m)
        head = QHBoxLayout()
        head.addWidget(self.lbl_count, 1)
        head.addWidget(self.scope)
        lv.addLayout(head)
        lv.addWidget(seg, 0, Qt.AlignLeft)
        srow = QHBoxLayout()
        srow.setSpacing(SP.s)
        srow.addWidget(self.search, 1)
        srow.addWidget(self.sort)
        lv.addLayout(srow)
        lv.addWidget(self.all_check)
        lv.addWidget(self.list_scroll, 1)
        lv.addWidget(self.empty)

        # ---------- cột phải ----------
        # 1. Nội dung
        self.b_ai = QPushButton("Viết bằng AI")
        self.b_ai.setProperty("primary", True)
        icons.attach(self.b_ai, "sparkle", 18)
        self.b_ai.clicked.connect(self.ai_for_current)
        self.b_save_meta = QPushButton("Lưu nháp")
        icons.attach(self.b_save_meta, "save", 18)
        self.b_save_meta.setToolTip("Lưu tiêu đề, mô tả, hashtag của video đang chọn (tự lưu khi chuyển video)")
        self.b_save_meta.clicked.connect(lambda: self.commit_editor())
        self.b_more = QPushButton()
        self.b_more.setProperty("iconbtn", True)
        icons.attach(self.b_more, "more", 20)
        mm = QMenu(self.b_more)
        mm.addAction("Hiện file video", lambda: self.row_action(self.shown_key(), "reveal"))
        mm.addAction("Ghép lại video", lambda: self.row_action(self.shown_key(), "remerge"))
        mm.addAction("Xoá nội dung đã soạn", lambda: self.row_action(self.shown_key(), "clear"))
        self.b_more.setMenu(mm)
        content, cb = section("doc", "Nội dung đăng", "Tiêu đề, mô tả và hashtag của video đang chọn.", self.b_ai, self.b_save_meta, self.b_more)
        self.big_thumb = Thumb(196, 196)
        self.b_open_video = QPushButton()
        self.b_open_video.setProperty("iconbtn", True)
        icons.attach(self.b_open_video, "open", 16)
        self.b_open_video.setToolTip("Hiện file video trong thư mục")
        self.b_open_video.clicked.connect(lambda: self.row_action(self.shown_key(), "reveal"))
        self.big_thumb.add_overlay(self.b_open_video)
        self.edit_title = QLineEdit()
        self.edit_title.textChanged.connect(self.on_text_changed)
        self.edit_desc = QPlainTextEdit()
        self.edit_desc.setFixedHeight(112)
        self.edit_desc.textChanged.connect(self.on_text_changed)
        self.cnt_title, self.cnt_desc, self.cnt_tags = (self._counter() for _ in range(3))
        form = QVBoxLayout()
        form.setSpacing(SP.xs)
        form.addLayout(self._label_row("Tiêu đề *", self.cnt_title))
        form.addWidget(self.edit_title)
        form.addSpacing(SP.xs)
        form.addLayout(self._label_row("Mô tả", self.cnt_desc))
        form.addWidget(self.edit_desc)
        form.addStretch(1)
        trow = QHBoxLayout()
        trow.setSpacing(SP.l)
        trow.addWidget(self.big_thumb, 0, Qt.AlignTop)
        trow.addLayout(form, 1)
        cb.addLayout(trow)
        self.tag_area = QFrame()
        self.tag_area.setProperty("tagbox", True)
        self.tag_flow = FlowLayout(self.tag_area)
        self.tag_flow.setContentsMargins(SP.s, SP.s, SP.s, SP.s)
        self.tag_input = QLineEdit()
        self.tag_input.setProperty("taginput", True)
        self.tag_input.setPlaceholderText("+ Thêm hashtag, Enter để lưu")
        self.tag_input.setFixedWidth(190)
        self.tag_input.returnPressed.connect(self.add_tag_from_input)
        cb.addLayout(self._label_row("Hashtag", self.cnt_tags))
        cb.addWidget(self.tag_area)

        # 2. Nền tảng / tài khoản
        self.b_refresh = QPushButton("Làm mới")
        icons.attach(self.b_refresh, "refresh", 16)
        self.b_refresh.clicked.connect(self.refresh_accounts)
        plats, pb = section("users", "Đăng lên", "Chọn nền tảng và tài khoản. Mỗi dự án nhớ lựa chọn riêng.", self.b_refresh)
        self.tiles_row = QGridLayout()
        self.tiles_row.setSpacing(SP.s)
        self._tiles: list[QFrame] = []
        pb.addLayout(self.tiles_row)
        self.not_connected = QLabel("")
        self.not_connected.setProperty("caption", True)
        self.not_connected.setWordWrap(True)
        self.b_connect = QPushButton("Thêm tài khoản…")
        icons.attach(self.b_connect, "plus", 16)
        self.b_connect.setToolTip("Mở Cài đặt → Đăng video")
        self.b_connect.clicked.connect(self.open_settings_requested.emit)
        crow = QHBoxLayout()
        crow.setSpacing(SP.m)
        crow.addWidget(self.not_connected, 1)
        crow.addWidget(self.b_connect)
        pb.addLayout(crow)

        # 3. Cài đặt đăng
        self.privacy = Combo()
        for k, label in PRIVACY:
            self.privacy.addItem(label, k)
        self.privacy.activated.connect(self.save_options)
        self.auto = QCheckBox("Tự động đăng khi một chương gen xong")
        self.auto.setToolTip("Sau khi gen clip xong cả chương, tự ghép, viết mô tả và đăng không cần xác nhận lại.")
        self.auto.toggled.connect(self.save_options)
        self.force = QCheckBox("Đăng lại cả những mục đã đăng")
        sets, sb = section("gear", "Cài đặt đăng", "Chế độ hiển thị và hành vi tự động.")
        cap = QLabel("Chế độ hiển thị")
        cap.setProperty("caption", True)
        hint = QLabel("YouTube/TikTok chưa được duyệt ứng dụng có thể tự ép về riêng tư. Facebook lưu bản nháp nếu chọn riêng tư; Instagram chỉ đăng công khai.")
        hint.setProperty("caption", True)
        hint.setWordWrap(True)
        lcol = QVBoxLayout()
        lcol.setSpacing(SP.xs)
        lcol.addWidget(cap)
        lcol.addWidget(self.privacy)
        lcol.addWidget(hint)
        rcol = QVBoxLayout()
        rcol.setSpacing(SP.m)
        rcol.addWidget(self.auto)
        rcol.addWidget(self.force)
        rcol.addStretch(1)
        srow2 = QHBoxLayout()
        srow2.setSpacing(SP.xl)
        srow2.addLayout(lcol, 1)
        srow2.addLayout(rcol, 1)
        sb.addLayout(srow2)

        # 3b. Lịch đăng (hẹn giờ)
        self.b_job_now = QPushButton("Đăng ngay")
        self.b_job_time = QPushButton("Đổi giờ…")
        self.b_job_del = QPushButton("Huỷ lịch")
        self.b_job_clear = QPushButton("Dọn mục đã xong")
        for b in (self.b_job_now, self.b_job_time, self.b_job_del, self.b_job_clear):
            b.setEnabled(False)
        self.b_job_now.clicked.connect(self.job_now)
        self.b_job_time.clicked.connect(self.job_retime)
        self.b_job_del.clicked.connect(self.job_remove)
        self.b_job_clear.clicked.connect(self.job_clear)
        self.sched_table = QTableWidget(0, 4)
        self.sched_table.setHorizontalHeaderLabels(["Giờ đăng", "Video", "Tài khoản", "Trạng thái"])
        self.sched_table.verticalHeader().hide()
        self.sched_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sched_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sched_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sched_table.setShowGrid(False)
        self.sched_table.setMinimumHeight(150)
        sh = self.sched_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        sh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.sched_table.itemSelectionChanged.connect(self.update_job_buttons)
        self.sched_empty = QLabel("Chưa có lượt hẹn giờ nào. Chọn video và tài khoản rồi bấm “Hẹn giờ…” ở thanh dưới.")
        self.sched_empty.setProperty("caption", True)
        self.sched_empty.setWordWrap(True)
        self.sched_next = QLabel("")
        self.sched_next.setProperty("caption", True)
        self.sched_next.setWordWrap(True)
        sched, sb2 = section("clock", "Lịch đăng", "Các lượt đăng đã hẹn giờ của dự án này. Kevit phải đang mở và máy không ngủ đúng giờ hẹn.")
        sb2.addWidget(self.sched_next)
        sb2.addWidget(self.sched_empty)
        sb2.addWidget(self.sched_table)
        jr = QHBoxLayout()
        jr.setSpacing(SP.s)
        jr.addWidget(self.b_job_clear)
        jr.addStretch(1)
        for b in (self.b_job_now, self.b_job_time, self.b_job_del):
            jr.addWidget(b)
        sb2.addLayout(jr)

        # 4. Lịch sử (thu gọn được)
        self.hist_toggle = QPushButton()
        self.hist_toggle.setProperty("ghost", True)
        self.hist_toggle.setCheckable(True)
        self.hist_toggle.setChecked(True)
        self.hist_toggle.setCursor(Qt.PointingHandCursor)
        self.hist_toggle.setFixedHeight(36)
        ht = QLabel("Lịch sử đăng")
        ht.setProperty("subheading", True)
        ht.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hist_caret = QLabel()
        self.hist_caret.setAttribute(Qt.WA_TransparentForMouseEvents)
        icons.attach(self.hist_caret, "up", 18, role="muted")
        hrow = QHBoxLayout(self.hist_toggle)
        hrow.setContentsMargins(SP.xs, 0, SP.xs, 0)
        hrow.addWidget(ht, 1)
        hrow.addWidget(self.hist_caret)
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(["Lúc", "Mục · tài khoản", "Kết quả", "Liên kết"])
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history.setShowGrid(False)
        self.history.setMinimumHeight(150)
        hh = self.history.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.history.cellClicked.connect(self.open_history_link)
        self.hist_empty = QLabel("Chưa có lượt đăng nào.")
        self.hist_empty.setProperty("caption", True)
        hist = QFrame()
        hist.setProperty("card", True)
        hv = QVBoxLayout(hist)
        hv.setContentsMargins(SP.l, SP.m, SP.l, SP.l)
        hv.setSpacing(SP.s)
        hv.addWidget(self.hist_toggle)
        hv.addWidget(self.hist_empty)
        hv.addWidget(self.history)
        self.hist_toggle.toggled.connect(self.toggle_history)

        col = QVBoxLayout()
        col.setSpacing(SP.l)
        col.setContentsMargins(0, 0, SP.s, 0)
        for w in (content, plats, sets, sched, hist):
            col.addWidget(w)
        col.addStretch(1)
        rholder = QWidget()
        rholder.setLayout(col)
        rscroll = QScrollArea()
        rscroll.setWidgetResizable(True)
        rscroll.setFrameShape(QFrame.NoFrame)
        rscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rscroll.setWidget(rholder)
        self.right_scroll = rscroll
        rscroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")

        body = QHBoxLayout()
        body.setSpacing(SP.l)
        body.addWidget(left, 4)
        body.addWidget(rscroll, 7)

        # ---------- thanh thao tác dưới cùng ----------
        self.status = QLabel("")
        self.status.setProperty("statusline", True)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.hide()
        self.b_prepare = QPushButton("Ghép + viết mô tả")
        icons.attach(self.b_prepare, "link", 18)
        self.b_prepare.clicked.connect(self.prepare)
        self.b_schedule = QPushButton("Hẹn giờ…")
        icons.attach(self.b_schedule, "clock", 18)
        self.b_schedule.setToolTip("Hẹn giờ đăng các video đã chọn: mỗi video một giờ riêng, hoặc giờ cố định mỗi lần đăng 1 video")
        self.b_schedule.clicked.connect(self.schedule_selected)
        self.b_publish = QPushButton("Đăng ngay")
        self.b_publish.setProperty("primary", True)
        icons.attach(self.b_publish, "send", 18)
        self.b_publish.clicked.connect(self.publish)
        self.b_stop = QPushButton("Dừng")
        self.b_stop.clicked.connect(self._cancel.set)
        self.b_stop.hide()
        for b in (self.b_prepare, self.b_schedule, self.b_publish, self.b_stop):
            b.setFixedHeight(40)
        status_box = QWidget()                      # luôn giữ chỗ co giãn, kể cả khi thanh trạng thái đang ẩn (nút không bị giãn ra)
        sbl = QHBoxLayout(status_box)
        sbl.setContentsMargins(0, 0, 0, 0)
        sbl.addWidget(self.status)
        bar = QHBoxLayout()
        bar.setSpacing(SP.m)
        bar.addWidget(status_box, 1)
        bar.addWidget(self.b_stop)
        bar.addWidget(self.b_prepare)
        bar.addWidget(self.b_schedule)
        bar.addWidget(self.b_publish)

        root = QVBoxLayout(self)
        root.setContentsMargins(SP.xl, SP.l, SP.xl, SP.l)
        root.setSpacing(SP.l)
        root.addLayout(top)
        root.addLayout(body, 1)
        root.addLayout(bar)
        self.update_buttons()

    # ---------- dựng nhỏ ----------
    def _counter(self) -> QLabel:
        c = QLabel("0/0")
        c.setProperty("counter", True)
        return c

    def _label_row(self, text: str, counter: QLabel) -> QHBoxLayout:
        r = QHBoxLayout()
        r.addWidget(QLabel(text), 1)
        r.addWidget(counter)
        return r

    def _set_counter(self, c: QLabel, n: int, limit: int) -> None:
        c.setText(f"{n}/{limit}")
        c.setProperty("over", n > limit)
        repolish(c)

    def shown_key(self) -> str:
        return self._shown.key if self._shown else ""

    # ================= nạp dữ liệu =================
    def showEvent(self, e):
        super().showEvent(e)
        self.reload()

    def reload(self) -> None:
        p = self.get_project()
        self._loading = True
        try:
            self.setEnabled(bool(p))
            if not p:
                self._clear_rows()
                self._targets, self._project_name = [], ""
                self.empty.setText("Hãy mở một dự án ở tab Dự án.")
                self.lbl_count.setText("Video sẽ đăng (0)")
                self.show_target(None)
                self.history.setRowCount(0)
                return
            if p.name != self._project_name:             # đổi dự án: bỏ lựa chọn/mục đang xem/ảnh của dự án trước
                self._clear_rows()
                self._project_name, self._targets, self._shown, self._thumbs = p.name, [], None, {}
            self.scope.setCurrentIndex(max(0, self.scope.findData(p.publish_scope)))
            self.privacy.setCurrentIndex(max(0, self.privacy.findData(p.publish_privacy)))
            self.auto.setChecked(p.publish_auto)
            self.refresh_accounts()
            self.rebuild_list()
            self.fill_history()
            self.refresh_schedule()
        finally:
            self._loading = False
        self.update_buttons()
        self.update_steps()

    # ---------- danh sách video ----------
    def _clear_rows(self) -> None:
        for r in self._rows.values():
            self.list_box.removeWidget(r)
            r.deleteLater()
        self._rows = {}

    def row_info(self, p, t: service.Target) -> dict:
        n, done = service.scene_count(p, t), len(service.clips_of(p, t))
        accounts = [a["id"] for a in publish.accounts()]
        target_accs = [a for a in p.publish_accounts if a in accounts] or accounts
        posted_any = any(service.history_ok(p, t, a) for a in accounts)
        posted_all = bool(target_accs) and all(service.history_ok(p, t, a) for a in target_accs)
        ready = service.is_ready(p, t)
        if posted_all:
            status, kind = "Đã đăng", "info"
        elif ready:
            status, kind = "Sẵn sàng", "ok"
        elif not n:
            status, kind = "Chưa có scene", "warn"
        else:
            status, kind = f"Gen {done}/{n} scene", "warn"
        name = service.meta_of(p, t).get("title") or t.label
        created = service.created_at(p, t)
        return dict(title=f"{t.key} - {name}" if t.chapter else name, status=status, kind=kind, ready=ready, posted_any=posted_any,
                    posted_all=posted_all, created=created, date=f"Tạo: {_fmt_date(created)}" if created else "Chưa có clip")

    def rebuild_list(self) -> None:
        p = self.get_project()
        keep_checked = {k for k, r in self._rows.items() if r.check.isChecked()}
        first = not self._rows
        keep_shown = self._shown.key if self._shown else None
        self._clear_rows()
        self._targets = service.targets(p)
        for t in self._targets:
            info = self.row_info(p, t)
            row = VideoRow(info)
            row.check.setChecked(not info["posted_all"] and (t.key in keep_checked or (first and info["ready"])))
            row.check.setEnabled(not info["posted_all"])
            row.clicked.connect(lambda k=t.key: self.select_key(k))
            row.checked.connect(self.on_checks_changed)
            row.action.connect(lambda a, k=t.key: self.row_action(k, a))
            pm, secs = self._thumbs.get(t.key, (None, 0.0))
            row.thumb.set(pm, secs)
            self._rows[t.key] = row
            self.list_box.insertWidget(self.list_box.count() - 1, row)
        self.apply_filter()
        self.select_key(keep_shown if keep_shown in self._rows else (self._targets[0].key if self._targets else None))
        self.load_thumbs()

    def apply_filter(self) -> None:
        p = self.get_project()
        if not p:
            return
        q = self.search.text().strip().lower()
        by_key = {t.key: t for t in self._targets}
        infos = {k: r.info for k, r in self._rows.items()}
        counts = dict(all=len(infos), ready=sum(1 for i in infos.values() if i["ready"] and not i["posted_all"]),
                      posted=sum(1 for i in infos.values() if i["posted_any"]))
        for k, label in FILTERS:
            self.tab_btns[k].setText(f"{label}  {counts[k]}")

        def show(k: str) -> bool:
            i = infos[k]
            ok = {"all": True, "ready": i["ready"] and not i["posted_all"], "posted": i["posted_any"]}[self._filter]
            return ok and (not q or q in i["title"].lower() or q in by_key[k].label.lower())
        order = self.sort.currentData() or "newest"
        keys = list(self._rows)
        if order == "newest":
            keys.sort(key=lambda k: -infos[k]["created"])
        elif order == "oldest":
            keys.sort(key=lambda k: infos[k]["created"])
        for r in self._rows.values():
            self.list_box.removeWidget(r)
        for i, k in enumerate(keys):
            self.list_box.insertWidget(i, self._rows[k])
            self._rows[k].setVisible(show(k))
        if not self._targets:
            self.empty.setText("Dự án chưa có chương nào.")
        else:
            self.empty.setText("" if any(show(k) for k in keys) else "Không có video nào khớp bộ lọc.")
        self.on_checks_changed()

    def set_filter(self, key: str) -> None:
        self._filter = key
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        self.apply_filter()

    def on_checks_changed(self, *_) -> None:
        vis = [r for r in self._rows.values() if not r.isHidden()]
        self.lbl_count.setText(f"Video sẽ đăng ({len(self.checked_targets())})")
        self.all_check.blockSignals(True)
        self.all_check.setChecked(bool(vis) and all(r.check.isChecked() for r in vis if not r.info["posted_all"]))
        self.all_check.blockSignals(False)
        self.update_buttons()

    def check_all(self, on: bool) -> None:
        for r in self._rows.values():
            if not r.isHidden() and not r.info["posted_all"]:
                r.check.setChecked(on)

    def checked_targets(self) -> list[service.Target]:
        return [t for t in self._targets if (r := self._rows.get(t.key)) and r.check.isChecked() and not r.isHidden() and not r.info["posted_all"]]

    def select_key(self, key: str | None) -> None:
        if key is not None and key not in self._rows:
            key = None
        self.commit_editor(quiet=True)
        for k, r in self._rows.items():
            r.set_selected(k == key)
        self.show_target(next((t for t in self._targets if t.key == key), None))

    # ---------- ảnh đại diện (tạo ở luồng nền) ----------
    def load_thumbs(self) -> None:
        p = self.get_project()
        if not p or self._thumb_worker is not None:
            return
        todo = [t for t in self._targets if service.clips_of(p, t) and t.key not in self._thumbs]
        if not todo:
            return

        def job(log):
            out = {}
            for t in todo:
                try:
                    out[t.key] = (service.thumbnail(p, t), service.video_seconds(p, t))
                except Exception:  # noqa: BLE001 - thiếu ảnh không phải lỗi nghiêm trọng
                    out[t.key] = (None, 0.0)
            return out

        def done(res):
            for k, (path, secs) in res.items():
                pm = QPixmap(str(path)) if path else None
                self._thumbs[k] = (pm, secs)
                if k in self._rows:
                    self._rows[k].thumb.set(pm, secs)
                if self._shown and self._shown.key == k:
                    self.big_thumb.set(pm, secs)
        self._thumb_worker = w = Worker(job)
        w.done.connect(done)
        w.finished.connect(lambda: setattr(self, "_thumb_worker", None))
        w.start()

    def row_action(self, key: str, action: str) -> None:
        p = self.get_project()
        t = next((x for x in self._targets if x.key == key), None)
        if not (p and t):
            return
        if action == "reveal":
            f = service.video_path(p, t)
            if f.exists():
                flow.reveal_file(f)
            else:
                QMessageBox.information(self, "Chưa có video ghép", "Video chưa được ghép. Bấm “Ghép + viết mô tả” trước.")
        elif action == "remerge":
            service.video_path(p, t).unlink(missing_ok=True)
            self.set_status("Đã xoá video ghép cũ, lần đăng sau sẽ ghép lại.", "info")
            self.rebuild_list()
        elif action == "clear":
            service.set_meta(p, t, {})
            p.save()
            self.rebuild_list()
            self.show_target(self._shown)

    # ---------- lịch sử ----------
    def fill_history(self) -> None:
        p = self.get_project()
        names = {t.key: t.label for t in service.targets(p)} if p else {}
        rows = list(reversed(p.publish_history[-100:])) if p else []
        expanded = self.hist_toggle.isChecked()
        self.history.setRowCount(len(rows))
        self.hist_empty.setVisible(not rows and expanded)
        self.history.setVisible(bool(rows) and expanded)
        for r, h in enumerate(rows):
            who = publish.account_name(h["account"]) if h.get("account") else h.get("platform", "")
            if h.get("ok"):
                result = "✓ " + (service.PRIVACY_LABELS.get(h.get("privacy"), h.get("privacy", "")) or "Thành công") + (f" — {h['message']}" if h.get("message") else "")
                color = theme.T["ok"]
            else:
                result, color = "✗ " + (h.get("message") or "Lỗi"), theme.T["err"]
            link = f"Xem trên {publish.PLATFORMS[h['platform']].label}" if h.get("url") and h.get("platform") in publish.PLATFORMS else ""
            cells = [h.get("time", ""), f"{names.get(h.get('chapter'), h.get('chapter', ''))} · {who}", result, link]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(h.get("message") or text)
                if c == 2:
                    it.setForeground(QColor(color))
                if c == 3 and link:
                    it.setData(Qt.UserRole, h["url"])
                    it.setForeground(QColor(theme.T["info"]))
                self.history.setItem(r, c, it)

    def toggle_history(self, on: bool) -> None:
        icons.attach(self.hist_caret, "up" if on else "down", 18, role="muted")
        self.fill_history()

    def open_history_link(self, row: int, col: int) -> None:
        it = self.history.item(row, 3)
        url = it.data(Qt.UserRole) if it else None
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ================= tài khoản / nền tảng =================
    def refresh_accounts(self) -> None:
        """Dựng lại các thẻ nền tảng từ tài khoản đã kết nối; tích sẵn những tài khoản dự án đã chọn."""
        p = self.get_project()
        chosen = set(p.publish_accounts) if p else set()
        was = self._loading
        self._loading = True
        try:
            while self.tiles_row.count():
                w = self.tiles_row.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self._tiles = []
            self.acc_checks = {}
            accs = publish.accounts()
            for a in accs:
                row = AccountPick(a, a["id"] in chosen)
                row.check.toggled.connect(self.save_options)
                self.acc_checks[a["id"]] = row.check
                self._tiles.append(row)
            if not accs:
                empty = QLabel("Chưa kết nối tài khoản nào.")
                empty.setProperty("caption", True)
                self._tiles.append(empty)
            missing = [cls.label for key, cls in publish.PLATFORMS.items() if not any(a["platform"] == key for a in accs)]
            self.not_connected.setText(("Chưa kết nối: " + ", ".join(missing) + ".") if missing and accs else "")
            self.not_connected.setVisible(bool(self.not_connected.text()))
            self.reflow_tiles()
        finally:
            self._loading = was
        self.update_buttons()
        self.update_steps()
        self.update_counters()

    def reflow_tiles(self) -> None:
        """Danh sách tài khoản: 2 cột khi đủ rộng, hẹp thì 1 cột."""
        cols = 2 if self.right_scroll.viewport().width() >= 760 else 1
        while self.tiles_row.count():
            self.tiles_row.takeAt(0)
        for i, t in enumerate(self._tiles):
            self.tiles_row.addWidget(t, i // cols, i % cols)
        for c in range(2):
            self.tiles_row.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._tiles:
            self.reflow_tiles()

    def chosen_accounts(self) -> list[str]:
        return [k for k, cb in self.acc_checks.items() if cb.isChecked()]

    # ================= nội dung đang soạn =================
    def limits(self) -> tuple[int, int, int]:
        """(tiêu đề, mô tả, hashtag) tối đa theo các nền tảng đang chọn: lấy mức chặt nhất."""
        plats = {a.split(":", 1)[0] for a in self.chosen_accounts()}
        return (100 if (plats & {"youtube", "facebook"} or not plats) else 200,
                2200 if plats & {"tiktok", "facebook", "instagram"} else 5000, MAX_TAGS)

    def update_counters(self) -> None:
        lt, ld, lh = self.limits()
        self._set_counter(self.cnt_title, len(self.edit_title.text()), lt)
        self._set_counter(self.cnt_desc, len(self.edit_desc.toPlainText()), ld)
        self._set_counter(self.cnt_tags, len(self._tags), lh)

    def on_text_changed(self) -> None:
        self.update_counters()
        self.update_steps()

    def show_target(self, t: service.Target | None) -> None:
        p = self.get_project()
        self._shown = t
        meta = service.meta_of(p, t) if (p and t) else {}
        self.edit_title.blockSignals(True)
        self.edit_desc.blockSignals(True)
        self.edit_title.setText(meta.get("title", ""))
        self.edit_desc.setPlainText(meta.get("description", ""))
        self.edit_title.blockSignals(False)
        self.edit_desc.blockSignals(False)
        self._tags = list(meta.get("hashtags", []))
        self.render_tags()
        pm, secs = self._thumbs.get(t.key, (None, 0.0)) if t else (None, 0.0)
        self.big_thumb.set(pm, secs)
        for w in (self.edit_title, self.edit_desc, self.tag_input, self.b_ai, self.b_save_meta, self.b_more, self.b_open_video):
            w.setEnabled(t is not None and self._worker is None)
        self.update_counters()
        self.update_steps()

    def render_tags(self) -> None:
        while self.tag_flow.count():
            it = self.tag_flow.takeAt(0)
            w = it.widget()
            if w is self.tag_input:
                w.setParent(None)
            elif w:
                w.deleteLater()
        for t in self._tags:
            chip = HashChip(t)
            chip.removed.connect(self.remove_tag)
            self.tag_flow.addWidget(chip)
        self.tag_flow.addWidget(self.tag_input)
        self.tag_input.show()
        self.tag_area.updateGeometry()
        self.update_counters()

    def add_tag_from_input(self) -> None:
        new = base.clean_tags(self.tag_input.text(), MAX_TAGS)
        self.tag_input.clear()
        if not new:
            return
        self._tags = base.clean_tags(self._tags + new, MAX_TAGS)
        self.render_tags()
        self.commit_editor(quiet=True)

    def remove_tag(self, tag: str) -> None:
        self._tags = [t for t in self._tags if t != tag]
        self.render_tags()
        self.commit_editor(quiet=True)

    def commit_editor(self, quiet: bool = False) -> None:
        p, t = self.get_project(), self._shown
        if not p or not t:
            return
        meta = dict(title=self.edit_title.text().strip(), description=self.edit_desc.toPlainText().strip(), hashtags=list(self._tags))
        old = service.meta_of(p, t)
        if meta != {k: old.get(k, [] if k == "hashtags" else "") for k in meta}:
            service.set_meta(p, t, meta)
            p.save()
            if not quiet:
                self.set_status("Đã lưu nội dung.", "ok")
            if t.key in self._rows:
                self._rows[t.key].update_info(self.row_info(p, t))
        elif not quiet:
            self.set_status("Nội dung không thay đổi.", "info")

    def ai_for_current(self) -> None:
        t, p = self._shown, self.get_project()
        if not (t and p):
            return
        self.commit_editor(quiet=True)

        def job(log):
            return describe.generate(p, t.chapter, log)

        def done(meta):
            service.set_meta(p, t, meta)
            p.save()
            if self._shown and self._shown.key == t.key:
                self.show_target(t)
            if t.key in self._rows:
                self._rows[t.key].update_info(self.row_info(p, t))
            self.set_status("AI đã viết xong, hãy xem lại rồi lưu/đăng.", "ok")
        self.start(job, done, "Đang nhờ AI viết mô tả…")

    # ================= tuỳ chọn =================
    def save_options(self, *_) -> None:
        p = self.get_project()
        if not p or self._loading:
            return
        p.publish_accounts = self.chosen_accounts() + [a for a in p.publish_accounts if a not in self.acc_checks]    # giữ lựa chọn của tài khoản đang ẩn
        p.publish_privacy = self.privacy.currentData() or "private"
        p.publish_auto = self.auto.isChecked()
        p.save()
        self.update_buttons()
        self.update_steps()
        self.update_counters()
        for t in self._targets:
            if t.key in self._rows:
                self._rows[t.key].update_info(self.row_info(p, t))

    def on_scope(self, *_) -> None:
        p = self.get_project()
        if not p:
            return
        self.commit_editor(quiet=True)
        p.publish_scope = self.scope.currentData()
        p.save()
        self._clear_rows()
        self._targets, self._shown = [], None
        self.rebuild_list()
        self.update_buttons()

    def update_steps(self) -> None:
        has_content = bool(self.edit_title.text().strip())
        has_accounts = bool(self.chosen_accounts())
        self.stepper.set_done([has_content, has_content and has_accounts, False])

    def update_buttons(self) -> None:
        idle = self._worker is None
        any_t = bool(self.checked_targets())
        self.b_prepare.setEnabled(idle and any_t)
        self.b_publish.setEnabled(idle and any_t and bool(self.chosen_accounts()))
        self.b_schedule.setEnabled(idle and any_t and bool(self.chosen_accounts()))
        self.b_stop.setVisible(not idle)

    # ================= chạy nền =================
    def set_status(self, text: str, kind: str = "info") -> None:
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.setVisible(bool(text))
        repolish(self.status)
        self.activity.emit(text, {"ok": "ok", "err": "error", "warn": "warn"}.get(kind, "info"))

    def log(self, msg: str) -> None:
        self.log_sink(msg)
        low = msg.lower()
        self.activity.emit(msg.splitlines()[0][:300] if msg else "", "error" if "lỗi" in low else "info")

    def _lockables(self) -> list[QWidget]:
        return [self.scope, self.sort, self.search, self.privacy, self.auto, self.force, self.edit_title, self.edit_desc, self.tag_input,
                self.b_ai, self.b_save_meta, self.b_more, self.b_refresh, self.all_check,
                *self.acc_checks.values(), *(r.check for r in self._rows.values())]

    def start(self, fn, on_done, message: str) -> None:
        self._cancel.clear()
        self._worker = w = Worker(fn)
        self.set_status(message, "info")
        self.b_stop.setEnabled(True)
        w.log.connect(self.log)
        w.done.connect(on_done)
        w.failed.connect(lambda e: (self.log(f"LỖI: {e}"), self.set_status(f"Lỗi: {e[:240]}", "err")))
        w.finished.connect(self._finished)
        self.update_buttons()
        w.start()
        for wd in self._lockables():
            wd.setEnabled(False)

    def _finished(self) -> None:
        self._worker = None
        status, kind = self.status.text(), self.status.property("kind")
        self.reload()
        for wd in self._lockables():
            wd.setEnabled(True)
        self.set_status(status, kind or "info")

    def prepare(self) -> None:
        p, tgts = self.get_project(), self.checked_targets()
        if not (p and tgts):
            return
        self.commit_editor(quiet=True)

        def job(log):
            out = []
            for t in tgts:
                if self._cancel.is_set():
                    break
                try:
                    service.ensure_video(p, t, log)
                    service.ensure_meta(p, t, log)
                    out.append((t.label, ""))
                except Exception as e:  # noqa: BLE001
                    log(f"[{t.label}] LỖI: {e}")
                    out.append((t.label, str(e)))
            return out

        def done(out):
            bad = [f"{n}: {e}" for n, e in out if e]
            self.set_status(f"Đã chuẩn bị {len(out) - len(bad)}/{len(out)} video." + (f" Lỗi: {bad[0][:200]}" if bad else ""), "warn" if bad else "ok")
        self.start(job, done, "Đang ghép video và viết mô tả…")

    def publish(self) -> None:
        p, tgts = self.get_project(), self.checked_targets()
        accs = self.chosen_accounts()
        if not (p and tgts and accs):
            return
        self.commit_editor(quiet=True)
        priv = self.privacy.currentData()
        lines = [f"• {len(tgts)} video × {len(accs)} tài khoản:\n   " + "\n   ".join(publish.account_name(a) for a in accs),
                 f"• Chế độ: {service.PRIVACY_LABELS[priv]}"]
        if priv == "public":
            lines.append("\n⚠ Công khai: bài sẽ hiện ngay với mọi người và không thể thu hồi từ Kevit.")
        if self.force.isChecked():
            lines.append("\n⚠ Sẽ đăng LẠI cả những mục đã đăng (có thể trùng bài).")
        if QMessageBox.question(self, "Xác nhận đăng", "\n".join(lines) + "\n\nTiếp tục?") != QMessageBox.Yes:
            return
        self.run_publish(p, tgts, accs, priv, self.force.isChecked())

    def run_publish(self, p, tgts, accs, priv, force=False) -> None:
        def job(log):
            return service.run(p, tgts, accs, priv, log, self._cancel, force)

        def done(results):
            ok = sum(1 for r in results if r.ok)
            bad = [r for r in results if not r.ok]
            text = f"Đã đăng {ok}/{len(results)} bài." + (f" Lỗi {publish.account_name(bad[0].account)}: {bad[0].message[:200]}" if bad else "")
            self.set_status(text, "warn" if bad else "ok")
        self.start(job, done, "Đang đăng…")


    # ================= hẹn giờ =================
    scheduler = None

    def set_scheduler(self, scheduler) -> None:
        self.scheduler = scheduler
        scheduler.changed.connect(self.on_schedule_changed)

    def on_schedule_changed(self, name: str) -> None:
        p = self.get_project()
        if p and p.name == name:
            self.refresh_schedule()
            self.fill_history()
            self.rebuild_list_statuses()

    def rebuild_list_statuses(self) -> None:
        p = self.get_project()
        if p:
            for t in self._targets:
                if t.key in self._rows:
                    self._rows[t.key].update_info(self.row_info(p, t))

    def schedule_selected(self) -> None:
        p, tgts, accs = self.get_project(), self.checked_targets(), self.chosen_accounts()
        if not (p and tgts and accs):
            return
        self.commit_editor(quiet=True)
        priv = self.privacy.currentData()
        summary = (f"{len(tgts)} video → " + ", ".join(publish.account_name(a) for a in accs)
                   + f"  ·  Chế độ: {service.PRIVACY_LABELS[priv]}")
        dlg = ScheduleDialog(self, [(t.key, t.label) for t in tgts], summary)
        if dlg.exec() != ScheduleDialog.Accepted:
            return
        jobs = [schedule.new_job(key, at, accs, priv) for key, at in dlg.result()]
        schedule.add_jobs(p, jobs)
        self.refresh_schedule()
        first = min(j["at"] for j in jobs)
        self.set_status(f"Đã hẹn giờ {len(jobs)} video, lượt đầu lúc {schedule.pretty(first)}. Hãy để Kevit mở đúng giờ.", "ok")

    def refresh_schedule(self) -> None:
        p = self.get_project()
        jobs = list(p.publish_queue) if p else []
        names = {t.key: t.label for t in service.targets(p)} if p else {}
        if p:
            names.setdefault("project", f"{p.name} (cả dự án)")
            for c in p.chapters:
                names.setdefault(c.id, c.name)
        keep = self.selected_job_id()
        self.sched_table.setRowCount(len(jobs))
        colors = {"done": theme.T["ok"], "error": theme.T["err"], "missed": theme.T["warn"], "running": theme.T["info"], "pending": theme.T["text"]}
        for r, j in enumerate(jobs):
            accs = ", ".join(publish.account_name(a) for a in j["accounts"])
            status = schedule.STATUS_LABELS.get(j["status"], j["status"])
            cells = [schedule.pretty(j["at"]), names.get(j["key"], j["key"]), f"{len(j['accounts'])} tài khoản", status]
            tips = [schedule.pretty(j["at"]), names.get(j["key"], j["key"]), accs, j.get("message") or status]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(tips[c])
                if c == 0:
                    it.setData(Qt.UserRole, j["id"])
                if c == 3:
                    it.setForeground(QColor(colors.get(j["status"], theme.T["text"])))
                    if j.get("message"):
                        it.setText(f"{status}: {j['message'][:60]}")
                self.sched_table.setItem(r, c, it)
        if keep:
            for r in range(self.sched_table.rowCount()):
                if self.sched_table.item(r, 0).data(Qt.UserRole) == keep:
                    self.sched_table.selectRow(r)
        self.sched_empty.setVisible(not jobs)
        self.sched_table.setVisible(bool(jobs))
        nxt = schedule.next_pending(p) if p else None
        self.sched_next.setText(f"Lượt kế tiếp: {schedule.pretty(nxt['at'])} · {names.get(nxt['key'], nxt['key'])}" if nxt else "")
        self.sched_next.setVisible(bool(nxt))
        self.update_job_buttons()

    def selected_job_id(self) -> str | None:
        r = self.sched_table.currentRow()
        it = self.sched_table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def selected_job(self) -> dict | None:
        p, jid = self.get_project(), self.selected_job_id()
        return schedule.get_job(p, jid) if (p and jid) else None

    def update_job_buttons(self) -> None:
        j = self.selected_job()
        idle = j is not None and j["status"] != "running"
        busy = bool(self.scheduler and self.scheduler.busy) or self._worker is not None
        self.b_job_now.setEnabled(idle and j["status"] != "done" and not busy)
        self.b_job_time.setEnabled(idle and j["status"] != "done")
        self.b_job_del.setEnabled(idle)
        p = self.get_project()
        self.b_job_clear.setEnabled(bool(p and any(x["status"] == "done" for x in p.publish_queue)))

    def job_now(self) -> None:
        p, j = self.get_project(), self.selected_job()
        if not (p and j and self.scheduler):
            return
        if not self.scheduler.run_now(p, j["id"]):
            self.set_status("Đang đăng một lượt khác, hãy thử lại sau ít phút.", "warn")
        self.refresh_schedule()

    def job_retime(self) -> None:
        p, j = self.get_project(), self.selected_job()
        if not (p and j):
            return
        at = ask_datetime(self, "Đổi giờ đăng", schedule.parse(j["at"]))
        if at:
            schedule.reschedule(p, j["id"], at)
            self.refresh_schedule()
            self.set_status(f"Đã đổi giờ đăng sang {schedule.pretty(schedule.fmt(at))}.", "ok")

    def job_remove(self) -> None:
        p, j = self.get_project(), self.selected_job()
        if p and j and schedule.remove_job(p, j["id"]):
            self.refresh_schedule()
            self.set_status("Đã huỷ lượt hẹn giờ.", "info")

    def job_clear(self) -> None:
        p = self.get_project()
        if p:
            n = schedule.clear_finished(p)
            self.refresh_schedule()
            self.set_status(f"Đã dọn {n} mục đã xong.", "info")

    # ================= tự động =================
    def on_generation_done(self) -> None:
        """Một đợt gen clip vừa xong: nếu bật tự động thì ghép + viết mô tả + đăng các chương đã đủ clip và chưa đăng."""
        p = self.get_project()
        if not p or not p.publish_auto or self._worker is not None:
            return
        accs = [a for a in p.publish_accounts if publish.store.get_account(a).get("access_token")]
        for a in set(p.publish_accounts) - set(accs):
            self.log(f"Tự động đăng: bỏ qua {publish.account_name(a)} vì tài khoản chưa kết nối hoặc đã bị xoá.")
        todo = [t for t in service.targets(p) if service.is_ready(p, t) and any(not service.history_ok(p, t, a) for a in accs)]
        if not (accs and todo):
            return
        self.log(f"Tự động đăng: {len(todo)} video lên {', '.join(publish.account_name(a) for a in accs)} ({service.PRIVACY_LABELS[p.publish_privacy]}).")
        self.run_publish(p, todo, accs, p.publish_privacy)

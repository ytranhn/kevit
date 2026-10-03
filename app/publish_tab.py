"""Tab Đăng video: chọn video (chương), soạn tiêu đề/mô tả/hashtag (AI hỗ trợ), chọn tài khoản đăng rồi ghép + đăng qua API."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit,
    QPushButton, QScrollArea, QTableWidget, QVBoxLayout, QWidget)

from . import icons
from .publish import service
from .shell import section
from .theme import SP
from .widgets import Combo, repolish
from .workers import Worker
from .publish_parts.widgets import _qicon, FlowLayout, Thumb, VideoRow, Stepper, PRIVACY, SCOPES, SORTS, FILTERS
from .publish_parts.listing import ListingMixin
from .publish_parts.editor import EditorMixin
from .publish_parts.schedule_ui import ScheduleMixin
from .publish_parts.running import RunMixin


class PublishTab(ListingMixin, EditorMixin, ScheduleMixin, RunMixin, QWidget):
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

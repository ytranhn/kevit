"""Tab Dự án: danh sách scene (trái) | xem video + chi tiết scene (phải) | thanh thao tác theo thứ tự bước (dưới)."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QVBoxLayout, QWidget)

from . import langs, flow_selectors, tts
from . import icons, theme
from .theme import SP
from .project_settings import ProjectSettingsDialog
from .widgets import (
    CharPicker, Combo, ElidedLabel, NavButton, SegStack, Segmented, card_bar, card_body, make_card, popover_button)
from .widgets import FOOTER_H, HEADER_H
from .project_parts.scene_widgets import SceneDelegate, PreviewPanel, PROVIDER_LABELS
from .project_parts.chapters import ChaptersMixin
from .project_parts.scene_table import SceneTableMixin
from .project_parts.popovers import PopoverMixin
from .project_parts.runtime import RuntimeMixin
from .project_parts.planning import PlanningMixin
from .project_parts.flow_gen import FlowGenMixin
from .project_parts.batch import BatchMixin


class ProjectTab(ChaptersMixin, SceneTableMixin, PopoverMixin, RuntimeMixin, PlanningMixin, FlowGenMixin, BatchMixin, QWidget):
    opened = Signal(str)
    open_settings_section = Signal(str)  # yêu cầu mở tab Cài đặt ở một mục (vd. 'flow', 'publish')
    publish_settings_changed = Signal()  # cài đặt đăng video của dự án vừa được lưu trong Cài đặt dự án
    generation_done = Signal()           # một đợt gen clip vừa chạy xong (tab Đăng video dùng để tự động đăng)
    account_changed = Signal(str)        # id tài khoản Flow đang dùng vừa đổi
    voices_ready = Signal()              # danh mục giọng Edge vừa tải xong ở luồng nền
    activity = Signal(str, str)          # (nội dung, mức: info/ok/warn/error) -> thanh trạng thái
    busy_changed = Signal(bool, bool)    # (đang chạy, có thể dừng)
    progress_changed = Signal(int, int)  # (xong, tổng); tổng 0 = chưa biết

    def __init__(self, chars_tab, log):
        super().__init__()
        self.chars_tab, self._log_sink, self.project, self.worker = chars_tab, log, None, None
        self._busy = self._cancelable = False
        self._cancel = threading.Event()
        self._prog = (0, 0)
        self._queue_prev: dict = {}
        self._gen_t0: dict = {}
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(700)
        self._tick_timer.timeout.connect(self.tick)
        self._chap_idx = -1
        self._row = -1
        self._keep_preview = self._skip_preview = False
        self._busy_widgets: list[QWidget] = []

        # ---- thanh trên: breadcrumb Dự án › Chương (trái) | tiện ích của dự án (phải) ----
        # combo ẩn chỉ giữ trạng thái và tín hiệu cho phần còn lại của mã; người dùng chuyển qua danh sách nhanh (popover)
        self.combo = Combo()
        self.chap_combo = Combo()
        self.nav_project = NavButton(icon="folder")
        self.nav_project.setMinimumWidth(200)
        self.nav_chapter = NavButton(with_pill=True, icon="doc", show_pill=False)      # tiến độ hiện ở ô riêng bên cạnh (chap_prog)
        self.nav_chapter.setMinimumWidth(200)
        self.chap_bar = QProgressBar()
        self.chap_bar.setProperty("acctbar", True)
        self.chap_bar.setProperty("greenbar", True)
        self.chap_bar.setTextVisible(False)
        self.chap_bar.setFixedHeight(8)
        self.chap_count = QLabel("")
        self.chap_count.setStyleSheet("font-weight: 600; background: transparent;")
        self.chap_check = QLabel()
        self.chap_prog = QFrame()
        self.chap_prog.setProperty("banner", True)
        self.chap_prog.setFixedHeight(40)
        cpl = QHBoxLayout(self.chap_prog)
        cpl.setContentsMargins(SP.m, 0, SP.m, 0)
        cpl.setSpacing(SP.s)
        cpl.addWidget(self.chap_bar, 1)
        cpl.addWidget(self.chap_count)
        cpl.addWidget(self.chap_check)
        self.chap_prog.setMinimumWidth(150)
        self.chap_prog.setToolTip("Tiến độ gen video của chương đang mở")
        self.btn_prev = QPushButton("")
        self.btn_next = QPushButton("")
        icons.attach(self.btn_prev, "left", 20)
        icons.attach(self.btn_next, "right", 20)
        for b in (self.btn_prev, self.btn_next):
            b.setFixedSize(40, 40)                       # nút vuông có viền như các nút khác: nhìn là biết bấm được
            b.setToolTip("Chương trước" if b is self.btn_prev else "Chương sau")
        self.btn_prev.clicked.connect(lambda: self.step_chapter(-1))
        self.btn_next.clicked.connect(lambda: self.step_chapter(+1))
        self.pop_project = popover_button(self.nav_project, self.build_project_pop, side="below", align="left", width=420)
        self.pop_chapter = popover_button(self.nav_chapter, self.build_chapter_pop, side="below", align="left", width=460)
        self.sync_btn = QPushButton("Đồng bộ Flow")
        icons.attach(self.sync_btn, "refresh")
        self.sync_btn.clicked.connect(lambda *_: self.flow_sync())
        self.more = QPushButton("Khác")
        icons.attach(self.more, "down", 18)
        self.more.setLayoutDirection(Qt.RightToLeft)          # mũi tên xổ nằm bên phải chữ
        self.pop_more = popover_button(self.more, self.build_more_pop, side="below", align="right", width=380)
        self.btn_settings = QPushButton("Cài đặt")
        icons.attach(self.btn_settings, "gear")
        self.btn_settings.clicked.connect(self.open_settings)
        self.progress = QLabel("")                       # giữ làm thuộc tính cũ; tiến độ hiển thị ở thanh công cụ của thẻ Scene
        sep = QLabel("›")
        sep.setProperty("caption", True)
        top = QHBoxLayout()
        top.setSpacing(SP.s)
        for w in (self.sync_btn, self.more, self.btn_settings):
            w.setFixedHeight(40)                         # cùng một chiều cao: mọi điều khiển trong hàng thẳng hàng
        steps = QHBoxLayout()                           # cặp ‹ › đi liền nhau, đứng sau nút chương
        steps.setSpacing(SP.xs)
        steps.addWidget(self.btn_prev)
        steps.addWidget(self.btn_next)
        top.addWidget(self.nav_project, 3)
        top.addWidget(sep, 0, Qt.AlignVCenter)
        top.addWidget(self.nav_chapter, 4)
        top.addWidget(self.chap_prog, 2)
        top.addLayout(steps)
        top.addSpacing(SP.xl - SP.s)                    # cộng với spacing 8 của hàng = ngăn nhóm 24px
        top.addWidget(self.sync_btn)
        top.addWidget(self.more)
        top.addWidget(self.btn_settings)
        self._busy_widgets += [self.nav_project, self.nav_chapter, self.btn_prev, self.btn_next, self.sync_btn, self.more]

        # ---- cài đặt dự án: các ô nhập sống ở đây, được sắp xếp trong ProjectSettingsDialog ----
        self.style = QLineEdit()
        self.max_scenes = QSpinBox()
        self.max_scenes.setRange(1, 60)
        self.max_scenes.setValue(16)
        self.max_scenes.setFixedWidth(100)
        self.aspect = Segmented()
        for label, val in (("9:16 dọc", "9:16"), ("16:9 ngang", "16:9"), ("Theo Flow", "flow")):
            self.aspect.addItem(label, val)
        self.provider = Segmented()
        for k in tts.PROVIDERS:
            self.provider.addItem(PROVIDER_LABELS.get(k, k), k)
        self.narr_lang = Combo()                 # ngôn ngữ thuyết minh: quyết định danh sách giọng và cách AI viết thuyết minh
        for code, (label, _u, _r) in langs.LANGS.items():
            self.narr_lang.addItem(label, code)
        self.narr_lang.setMinimumWidth(240)
        self.voice = Combo()
        self.voice.setMinimumWidth(240)
        self.voice_style = QLineEdit()
        self.flow_account = Combo()              # tài khoản Google Flow của dự án (đổi có hiệu lực ngay, không đợi bấm Lưu)
        self.account_info = QLabel("")           # credit + gia hạn của tài khoản đó
        self.account_info.setProperty("caption", True)
        self.account_info.setWordWrap(True)
        self.account_changed.connect(self.fill_accounts)
        self.flow_account.activated.connect(lambda i: self.set_account(self.flow_account.itemData(i)))
        self.flow_model = Combo()
        self.flow_model.addItems(flow_selectors.MODELS)
        self.flow_model.setMinimumWidth(240)
        self.flow_res = Segmented()
        self.flow_res.addItems(["720p", "360p"])
        self.flow_parallel = QSpinBox()
        self.flow_parallel.setRange(1, 8)
        self.flow_parallel.setValue(1)
        self.flow_parallel.setFixedWidth(100)
        self.flow_auto_dur = QCheckBox("Tự chọn thời lượng clip theo thuyết minh (chỉ Omni)")
        self.provider.currentIndexChanged.connect(self.fill_voices)
        self.narr_lang.currentIndexChanged.connect(self.fill_voices)
        self.settings_dialog = ProjectSettingsDialog(self)
        theme.on_change(self.on_theme_changed)

        # ---- cột 1: truyện / bối cảnh / danh sách scene ----
        self.story = QPlainTextEdit()
        self.story.setPlaceholderText("Dán đoạn truyện (chapter) vào đây rồi bấm ① Tạo scene...")
        self.synopsis = QPlainTextEdit()
        self.synopsis.setPlaceholderText("Tóm tắt/bối cảnh bộ truyện (ngữ cảnh khi tách scene)")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Scene", "Trạng thái", ""])
        self.table.setItemDelegate(SceneDelegate(self.table))
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Shift/⌘+click, kéo chuột, ⌘A
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.verticalHeader().setDefaultSectionSize(44)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, hh.ResizeMode.ResizeToContents)   # luôn đủ chỗ cho số 2-3 chữ số
        hh.setSectionResizeMode(1, hh.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, hh.ResizeMode.Fixed)      # cố định: bộ đếm giờ khi đang gen không làm bảng nhảy
        self.table.setColumnWidth(2, 150)
        hh.setSectionResizeMode(3, hh.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 44)
        hh.setMinimumSectionSize(40)
        self.table.cellClicked.connect(self.on_cell_clicked)
        self.table.currentCellChanged.connect(self.on_row_changed)
        self.table.itemSelectionChanged.connect(self.update_sel_label)
        for key in (Qt.Key_Delete, Qt.Key_Backspace):
            QShortcut(QKeySequence(key), self.table, activated=self.delete_selected, context=Qt.WidgetShortcut)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_context_menu)

        def quick(text, pred, tip):
            b = QPushButton(text)
            b.setProperty("flat", True)
            b.setToolTip(tip)
            b.clicked.connect(lambda: self.select_where(pred))
            return b
        self.sel_label = QLabel("")
        self.sel_label.setProperty("caption", True)
        sel_bar = QHBoxLayout()
        sel_bar.setSpacing(SP.s)
        for text, pred, tip in (("Chọn tất cả", lambda s: True, "Chọn mọi scene (cũng có thể bấm ⌘A / Ctrl+A trong bảng)"),
                                ("Chưa xong", lambda s: s.status != "done", "Chọn các scene chưa xong"),
                                ("Lỗi", lambda s: s.status == "error", "Chọn các scene bị lỗi")):
            sel_bar.addWidget(quick(text, pred, tip))
        sel_bar.addStretch()
        self.sel_label.setFixedWidth(112)                  # cố định: đổi nội dung không làm xê dịch các nút bên cạnh
        self.sel_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sel_bar.addWidget(self.sel_label)
        self.scene_search = QLineEdit()
        self.scene_search.setPlaceholderText("Tìm scene…")
        self.scene_search.setClearButtonEnabled(True)
        self.scene_search.setFixedHeight(36)
        self.scene_search.addAction(QIcon(icons.pixmap("search", 18, theme.T["muted"])), QLineEdit.LeadingPosition)
        self.scene_search.textChanged.connect(self.apply_scene_filter)
        self.scene_filter = "all"
        self.btn_filter = QPushButton()
        self.btn_filter.setProperty("iconbtn", True)
        self.btn_filter.setFixedSize(36, 36)
        icons.attach(self.btn_filter, "filter", 18)
        fm = QMenu(self.btn_filter)
        for key, label in (("all", "Tất cả scene"), ("done", "Đã xong"), ("todo", "Chưa xong"), ("error", "Lỗi")):
            fm.addAction(label, lambda k=key: self.set_scene_filter(k))
        self.btn_filter.setMenu(fm)
        self.btn_filter.setToolTip("Lọc theo trạng thái")
        find_bar = QHBoxLayout()
        find_bar.setSpacing(SP.s)
        find_bar.addWidget(self.scene_search, 1)
        find_bar.addWidget(self.btn_filter)
        self.prog_dot = QFrame()
        self.prog_dot.setFixedSize(9, 9)
        self.prog_text = QLabel("")
        self.prog_text.setProperty("caption", True)
        self.scene_bar = QProgressBar()
        self.scene_bar.setProperty("acctbar", True)
        self.scene_bar.setProperty("greenbar", True)
        self.scene_bar.setTextVisible(False)
        self.scene_bar.setFixedHeight(8)
        prog_bar = QHBoxLayout()
        prog_bar.setSpacing(SP.s)
        prog_bar.addWidget(self.prog_dot, 0, Qt.AlignVCenter)
        prog_bar.addWidget(self.prog_text)
        prog_bar.addWidget(self.scene_bar, 1)
        self.manage_btn = QPushButton("Quản lý")
        icons.attach(self.manage_btn, "down", 18)
        self.manage_btn.setLayoutDirection(Qt.RightToLeft)
        self.manage_btn.setFixedSize(112, 36)
        self.pop_manage = popover_button(self.manage_btn, self.build_manage_pop, side="below", align="right", width=380)
        self._busy_widgets.append(self.manage_btn)
        self.scene_page = QWidget()
        spl = QVBoxLayout(self.scene_page)
        spl.setContentsMargins(0, 0, 0, 0)
        spl.setSpacing(SP.s)
        spl.addLayout(sel_bar)
        spl.addLayout(find_bar)
        spl.addLayout(prog_bar)
        self.empty_title = QLabel("Chưa có scene")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setProperty("heading", True)
        self.empty_text = QLabel("")
        self.empty_text.setAlignment(Qt.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_text.setProperty("caption", True)
        self.empty_btn = QPushButton("")
        self.empty_btn.setProperty("primary", True)
        self.empty_btn.clicked.connect(self.empty_action)
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(SP.xl, 0, SP.xl, 0)
        ev.addStretch(2)
        ev.addWidget(self.empty_title)
        ev.addWidget(self.empty_text)
        ev.addSpacing(SP.m)
        ev.addWidget(self.empty_btn, 0, Qt.AlignHCenter)
        ev.addStretch(3)
        self.table_stack = QStackedWidget()
        self.table_stack.addWidget(empty)
        self.table_stack.addWidget(self.table)
        spl.addWidget(self.table_stack)
        self.left_tabs = SegStack()
        self.left_tabs.addTab(self.scene_page, "Scene")
        self.left_tabs.addTab(self.story, "Truyện")
        self.left_tabs.addTab(self.synopsis, "Bối cảnh")

        # ---- cột 2: chi tiết scene (nội dung chính, giãn theo chiều cao) ----
        self.d_title = QLineEdit()
        self.d_chars = CharPicker(lambda: self.chars_tab.chars)
        self.d_src = QPlainTextEdit()
        self.d_src.setReadOnly(True)
        self.d_src.setPlaceholderText("Đoạn truyện gốc mà scene này diễn tả")
        self.d_narr = QPlainTextEdit()
        self.d_narr.setPlaceholderText("Thuyết minh (người dẫn truyện đọc)")
        self.d_visual = QPlainTextEdit()
        self.d_visual.setPlaceholderText("Mô tả hình ảnh (tiếng Anh)")
        for w in (self.d_src, self.d_narr, self.d_visual):
            w.setMinimumHeight(70)
        self.narr_count = QLabel("")
        self.narr_count.setProperty("caption", True)
        self.d_narr.textChanged.connect(self.update_narr_count)
        self.d_badge = QLabel("")
        f = self.d_badge.font()
        f.setBold(True)
        # đủ rộng cho nhãn dài nhất ("Scene 999: Đang gen… 99:59") nên không bị cắt chữ; vẫn cố định để không xê dịch bố cục
        self.d_badge.setFixedWidth(QFontMetrics(f).horizontalAdvance("Scene 999: Đang gen… 99:59") + 12)
        self.d_err = ElidedLabel()
        self.d_err.setProperty("caption", True)
        self.b_view_err = QPushButton("Xem lỗi")
        self.b_view_err.setProperty("flat", True)
        self.b_view_err.clicked.connect(self.view_error)
        b_gen_one = QPushButton("Gen lại scene")
        b_voice_one = QPushButton("Áp dụng giọng")
        b_copy = QPushButton("Copy prompt")
        b_gen_one.clicked.connect(lambda: self.flow_auto_run("current"))
        b_voice_one.clicked.connect(lambda: self.revoice(only_current=True))
        b_copy.clicked.connect(self.flow_copy)
        self._busy_widgets += [b_gen_one, b_voice_one]

        def caption(text: str) -> QLabel:
            c = QLabel(text)
            c.setProperty("caption", True)
            return c
        # ===== ba thẻ cùng cấu trúc: đầu thẻ (56px) / thân thẻ / chân thẻ (68px) =====
        self.preview = PreviewPanel()
        # --- thẻ 1: Scene (danh sách + truyện + bối cảnh) ---
        self.s1 = QPushButton("Tạo scene")
        self.s2 = QPushButton("Gen video")
        self.pop_gen = popover_button(self.s2, self.build_gen_pop, side="above", align="left", width=400)
        self.s3 = QPushButton("Ghép video")
        self.s2.setToolTip("Bấm để chọn cách gen: scene đang chọn, các scene đã chọn, chưa xong hoặc tất cả")
        for b, ic in ((self.s1, "step1"), (self.s2, "step2"), (self.s3, "step3")):
            icons.attach(b, ic, 20)
        for b in (self.s1, self.s2, self.s3):
            b.setFixedHeight(36)
            b.setProperty("primary", True)
            self._busy_widgets.append(b)

        f1 = QHBoxLayout()
        f1.setSpacing(SP.s)                              # vòng tròn số ①②③ trên mỗi nút đã chỉ rõ thứ tự, không cần mũi tên giữa các nút
        f1.addWidget(self.s1, 3)
        f1.addWidget(self.s2, 5)
        f1.addWidget(self.s3, 3)
        h1 = QHBoxLayout()
        h1.addWidget(self.left_tabs.seg)
        h1.addStretch(1)
        h1.addWidget(self.manage_btn)
        card1 = make_card(card_bar(h1, HEADER_H), card_body(self.left_tabs.stack), card_bar(f1, FOOTER_H))

        # --- thẻ 2: Chi tiết scene ---
        body2 = QWidget()
        dl = QVBoxLayout(body2)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(SP.m)                             # khoảng cách giữa các NHÓM trường

        def copy_btn(edit) -> QPushButton:
            b = QPushButton()
            b.setProperty("ghost", True)
            b.setFixedSize(24, 16)
            b.setStyleSheet("padding: 0;")
            icons.attach(b, "copy", 14, role="muted")
            b.setToolTip("Copy nội dung")
            b.clicked.connect(lambda: QApplication.clipboard().setText(edit.toPlainText()))
            return b

        def group(title: str, widget: QWidget, header_extra: QWidget | None = None, copy_of=None) -> QWidget:
            g = QWidget()
            gv = QVBoxLayout(g)
            gv.setContentsMargins(0, 0, 0, 0)
            gv.setSpacing(SP.xs)                        # nhãn gắn sát với ô nhập của nó
            head = QHBoxLayout()
            head.setSpacing(SP.s)
            head.addWidget(caption(title))
            head.addStretch()
            if header_extra is not None:
                head.addWidget(header_extra)
            if copy_of is not None:
                head.addWidget(copy_btn(copy_of))
            gv.addLayout(head)
            gv.addWidget(widget, 1)
            return g
        dl.addWidget(group("Tiêu đề", self.d_title))
        dl.addWidget(group("Nhân vật", self.d_chars))
        dl.addWidget(group("Đoạn truyện gốc", self.d_src, copy_of=self.d_src), 2)
        dl.addWidget(group("Thuyết minh", self.d_narr, self.narr_count, copy_of=self.d_narr), 2)
        dl.addWidget(group("Visual (prompt gửi Flow)", self.d_visual, copy_of=self.d_visual), 2)
        self.btn_sprev, self.btn_snext = QPushButton(""), QPushButton("")
        for b, ic, tip, d in ((self.btn_sprev, "left", "Scene trước", -1), (self.btn_snext, "right", "Scene sau", 1)):
            b.setProperty("iconbtn", True)
            b.setFixedSize(36, 36)
            icons.attach(b, ic, 18)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=d: self.step_scene(k))
        h2 = QHBoxLayout()
        h2.addWidget(self.d_badge)
        h2.addWidget(self.d_err, 1)
        h2.addWidget(self.b_view_err)           # 'Copy lỗi' nằm trong hộp thoại xem lỗi (đầu thẻ hẹp không đủ chỗ cho cả hai)
        h2.addWidget(self.btn_sprev)
        h2.addWidget(self.btn_snext)
        f2 = QHBoxLayout()
        for b in (b_gen_one, b_voice_one, b_copy):
            b.setFixedHeight(36)
            f2.addWidget(b, 1)
        card2 = make_card(card_bar(h2, HEADER_H), card_body(body2), card_bar(f2, FOOTER_H))

        # --- thẻ 3: Xem trước ---
        self.view_seg = Segmented()
        self.view_seg.addItems(["Scene", "Ghép"])               # ngắn để chừa chỗ cho nút Thư mục trong thẻ hẹp
        self.view_seg.setToolTip("Scene: xem clip của scene đang chọn  ·  Ghép: xem video ghép của cả chương")
        self.view_seg.currentIndexChanged.connect(self.on_view_changed)
        self.btn_reveal = QPushButton("")
        icons.attach(self.btn_reveal, "folder", 20)
        self.btn_reveal.setFixedSize(40, 32)               # chỉ icon (thẻ hẹp); tooltip nói rõ tác dụng
        self.btn_reveal.clicked.connect(self.reveal_current)
        self.view_seg.setMinimumWidth(self.view_seg.sizeHint().width())     # không để nút bên cạnh bóp cụt chữ của bộ chuyển
        h3 = QHBoxLayout()
        h3.addWidget(self.view_seg)
        h3.addStretch(1)
        h3.addWidget(self.btn_reveal)
        f3 = QHBoxLayout()
        f3.addWidget(self.preview.controls)
        card3 = make_card(card_bar(h3, HEADER_H), card_body(self.preview.stack), card_bar(f3, FOOTER_H))
        QShortcut(QKeySequence(Qt.Key_Space), card3, activated=self.preview.toggle, context=Qt.WidgetWithChildrenShortcut)
        for c, w in ((card1, 456), (card2, 430), (card3, 290)):
            c.setMinimumWidth(w)

        main = QSplitter(Qt.Horizontal)
        main.setHandleWidth(SP.l)
        main.addWidget(card1)
        main.addWidget(card2)
        main.addWidget(card3)
        main.setSizes([500, 470, 330])
        main.setStretchFactor(0, 4)
        main.setStretchFactor(1, 4)
        main.setStretchFactor(2, 3)
        main.setChildrenCollapsible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP.l)
        lay.addLayout(top)
        lay.addWidget(main, 1)

        self.s1.setToolTip("Tách truyện của chương thành scene. Nên có nhân vật trước (tab Nhân vật; AI đọc truyện, không cần scene) để scene gắn đúng nhân vật.")
        self.s1.clicked.connect(self.plan)
        self.s3.clicked.connect(self.merge_all)
        self.voices_ready.connect(self.fill_voices)
        tts.on_catalog_ready(self.voices_ready.emit)
        self.combo.currentTextChanged.connect(self.open_project)
        self.chap_combo.currentIndexChanged.connect(self.on_chapter_selected)
        self.fill_accounts()
        self.reload_projects()

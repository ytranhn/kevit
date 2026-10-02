"""Tab Dự án: danh sách scene (trái) | xem video + chi tiết scene (phải) | thanh thao tác theo thứ tự bước (dưới)."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFontMetrics, QBrush, QColor, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QStyle, QStyleOptionViewItem, QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame, QListWidget, QListWidgetItem, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter,
    QStackedWidget, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import credits, langs, flow, flow_auto, flow_selectors, llm, pipeline, scene_ops, scene_planner, trash, tts, veo_client
from .merger import merge
from . import models
from .models import Project, nfc, safe_dirname
from . import accounts, icons, theme
from .theme import SP
from .project_settings import ProjectSettingsDialog
from .widgets import (
    CharPicker, Combo, ElidedLabel, NavButton, Popover, SegStack, Segmented, card_bar, card_body, make_card, popover_button, repolish)
from .widgets import FOOTER_H, HEADER_H
from .workers import Worker

STATUS = {  # trạng thái -> (nhãn, tên token màu trong theme.T)
    "pending": ("Chờ gen", "faint"),
    "queued": ("Hàng đợi", "info"),
    "generating": ("Đang gen...", "warn"),
    "raw": ("Có clip, chưa có giọng", "info"),
    "done": ("Xong", "ok"),
    "error": ("Lỗi", "err"),
}
PROVIDER_LABELS = {"edge": "Edge · miễn phí", "gemini": "Gemini"}


def _fmt(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 60}:{s % 60:02d}"


class SceneDelegate(QStyledItemDelegate):
    """Vẽ bảng scene theo thiết kế mới: vạch nhấn ở dòng đang chọn (cột #), nhãn trạng thái dạng pill có chấm màu (cột 3), nút '···' (cột 4)."""

    def __init__(self, table):
        super().__init__(table)
        self.table = table

    def _background(self, painter: QPainter, option, index) -> None:
        """Nền dòng (chọn/hover) theo stylesheet, KHÔNG vẽ chữ: các cột tự vẽ nội dung riêng."""
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QIcon()
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, option.widget)

    def paint(self, painter: QPainter, option, index):
        col = index.column()
        selected = bool(option.state & QStyle.State_Selected)
        if col == 2:                                           # trạng thái: pill + chấm màu
            self._background(painter, option, index)
            color = index.data(Qt.ForegroundRole)
            c = color.color() if color is not None else QColor(theme.T["muted"])
            text = index.data(Qt.DisplayRole) or ""
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            fm = painter.fontMetrics()
            w = min(option.rect.width() - 12, fm.horizontalAdvance(text) + 36)
            r = QRect(option.rect.left() + 6, option.rect.center().y() - 13, w, 26)
            tint = QColor(c)
            tint.setAlpha(36)
            painter.setPen(Qt.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(r, 13, 13)
            painter.setBrush(c)
            painter.drawEllipse(r.left() + 11, r.center().y() - 3, 7, 7)
            painter.setPen(c)
            painter.drawText(QRect(r.left() + 24, r.top(), r.width() - 28, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                             fm.elidedText(text, Qt.ElideRight, r.width() - 28))
            painter.restore()
            return
        if col == 3:                                           # nút '···'
            self._background(painter, option, index)
            pm = icons.pixmap("more", 20, theme.T["muted"])
            painter.drawPixmap(option.rect.center().x() - 10, option.rect.center().y() - 10, pm)
            return
        super().paint(painter, option, index)
        if col == 0 and selected:                              # vạch nhấn bên trái
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.T["accent"]))
            painter.drawRoundedRect(QRect(option.rect.left(), option.rect.top() + 4, 4, option.rect.height() - 8), 2, 2)
            painter.restore()


class PreviewPanel(QWidget):
    """Trình phát video 9:16 gọn: phát/tạm dừng, thanh tua, thời gian."""

    def __init__(self):
        super().__init__()
        self.placeholder = QLabel("Chưa có clip để xem")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setProperty("videoph", True)
        self.video = QVideoWidget()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.video)
        self.stack.setMinimumSize(200, 356)

        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self._autoplay = False
        self._key = None

        self.btn = QPushButton("")
        icons.attach(self.btn, "play", 20)
        self.btn.setFixedWidth(48)
        self.slider = QSlider(Qt.Horizontal)
        self.time = QLabel("0:00 / 0:00")
        self.controls = QWidget()                       # được đặt ở chân thẻ "Xem trước"
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP.s)
        row.addWidget(self.btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        self.btn_vol = QPushButton("")
        self.btn_full = QPushButton("")
        for b, ic in ((self.btn_vol, "volume"), (self.btn_full, "expand")):
            b.setProperty("ghost", True)
            b.setFixedSize(34, 34)
            icons.attach(b, ic, 18)
            row.addWidget(b)
        self.btn_vol.setToolTip("Bật/tắt tiếng")
        self.btn_full.setToolTip("Toàn màn hình (Esc để thoát)")
        self.btn_vol.clicked.connect(self.toggle_mute)
        self.btn_full.clicked.connect(lambda: self.video.setFullScreen(True) if self.stack.currentIndex() == 1 else None)
        self.controls.installEventFilter(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.controls)

        self.btn.clicked.connect(self.toggle)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.playbackStateChanged.connect(
            lambda s: icons.attach(self.btn, "pause" if s == QMediaPlayer.PlayingState else "play", 20))
        self.player.mediaStatusChanged.connect(self._on_status)
    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())
        icons.attach(self.btn_vol, "volume_off" if self.audio.isMuted() else "volume", 18)

    def eventFilter(self, obj, ev):
        """Thẻ xem trước hẹp thì ẩn nút tiếng/toàn màn hình để thanh tua còn đủ chỗ; rộng thì hiện."""
        if obj is self.controls and ev.type() == QEvent.Resize:
            wide = obj.width() >= 290
            self.btn_vol.setVisible(wide)
            self.btn_full.setVisible(wide)
        return super().eventFilter(obj, ev)

    def _on_pos(self, pos: int):
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        self.time.setText(f"{_fmt(pos)} / {_fmt(self.player.duration())}")

    def _on_status(self, st):
        if st == QMediaPlayer.LoadedMedia:
            self.player.play() if self._autoplay else self.player.pause()  # pause = hiện khung hình đầu

    def toggle(self):
        if self.stack.currentIndex() == 0:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def load(self, path: str | None, autoplay: bool = False):
        if not path or not Path(path).exists():
            self._key = None
            self.player.stop()
            self.player.setSource(QUrl())
            self.stack.setCurrentIndex(0)
            self.time.setText("0:00 / 0:00")
            return
        key = (path, Path(path).stat().st_mtime)
        if key == self._key and not autoplay:
            return  # đang hiển thị đúng file này, không ngắt phát
        self._key = key
        self._autoplay = autoplay
        self.stack.setCurrentIndex(1)
        self.player.setSource(QUrl.fromLocalFile(path))


class ProjectTab(QWidget):
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
        self.sync_btn.clicked.connect(self.flow_sync)
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

    # ================= dự án / chương =================
    @property
    def chapter(self):
        p = self.project
        return p.chapters[self._chap_idx] if p and 0 <= self._chap_idx < len(p.chapters) else None

    @property
    def scenes(self) -> list:
        return self.chapter.scenes if self.chapter else []

    def owner_of(self, scene):
        for ch in self.project.chapters:
            if any(s is scene for s in ch.scenes):
                return ch
        return None

    def reload_projects(self, select: str = ""):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(Project.list_names())
        self.combo.blockSignals(False)
        if select:
            self.combo.setCurrentText(select)
        self.open_project(self.combo.currentText())
        self.update_welcome()

    def update_welcome(self):
        """Chưa có dự án nào -> phủ màn hình chào mừng (hướng dẫn thiết lập) lên toàn bộ tab."""
        w = getattr(self, "welcome", None)
        if w is None:
            return
        w.setVisible(not self.project)
        if w.isVisible():
            w.setGeometry(self.rect())
            w.raise_()
            w.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = getattr(self, "welcome", None)
        if w is not None and w.isVisible():
            w.setGeometry(self.rect())

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Dự án mới", "Tên dự án:")
        name = safe_dirname(name) if ok and name.strip() else ""
        if name:
            p = Project(name)
            p.use_flow_account(accounts.default_new_id())     # dự án mới dùng tài khoản mặc định (đổi được ở chip Flow / Cài đặt dự án)
            p.new_chapter()
            p.save()
            self.reload_projects(name)

    def open_project(self, name):
        if not name:
            self.project = None
            self._row, self._chap_idx = -1, -1
            self.table.setRowCount(0)
            self.update_empty_state()
            self.opened.emit("")            # tab Nhân vật cũng về trạng thái chưa có dự án
            self.refresh_nav()
            self.update_steps()
            return
        self._row, self._chap_idx = -1, -1
        self.project = p = Project.load(name)
        accounts.activate(p.account_id)         # mọi thao tác Flow của dự án này dùng đúng tài khoản (Chrome/hồ sơ/cổng) của nó
        self.account_changed.emit(p.account_id)
        if not p.chapters:
            p.new_chapter()
            p.save()
        stale = [s for _, s in p.all_scenes() if s.status == "generating"]
        for s in stale:  # không có tiến trình nào chạy lúc mở dự án => bị ngắt giữa chừng
            s.status = "error"
            s.error = "Bị ngắt giữa chừng. Bấm ⟳ Đồng bộ Flow để lấy lại clip đã render (không tốn credit)."
        if stale:
            p.save()
        self.synopsis.setPlainText(p.synopsis)
        self.style.setText(p.style)
        self.aspect.setCurrentIndex(max(0, self.aspect.findData(p.aspect_ratio)))
        self.provider.setCurrentIndex(max(0, self.provider.findData(p.tts_provider)))
        self.narr_lang.blockSignals(True)
        self.narr_lang.setCurrentIndex(max(0, self.narr_lang.findData(p.narration_lang)))
        self.narr_lang.blockSignals(False)
        self.fill_voices(p.voice)
        self.voice_style.setText(p.voice_style)
        self.flow_model.setCurrentText(p.flow_model)
        self.flow_res.setCurrentText(p.flow_resolution)
        self.flow_auto_dur.setChecked(p.flow_auto_duration)
        self.flow_parallel.setValue(max(1, min(8, p.flow_parallel)))
        self.opened.emit(name)   # nạp danh sách nhân vật trước khi dựng bảng/chi tiết
        self.fill_chapter_combo(0)
        self.show_chapter(0)

    def chapter_label(self, ch) -> str:
        return f"{ch.name}   ({ch.done}/{len(ch.scenes)})"

    def fill_chapter_combo(self, select: int):
        self.chap_combo.blockSignals(True)
        self.chap_combo.clear()
        for ch in self.project.chapters:
            self.chap_combo.addItem(self.chapter_label(ch))
        self.chap_combo.setCurrentIndex(select)
        self.chap_combo.blockSignals(False)

    def refresh_chapter_labels(self):
        self.refresh_nav()
        self.chap_combo.blockSignals(True)
        for i, ch in enumerate(self.project.chapters):
            self.chap_combo.setItemText(i, self.chapter_label(ch))
        self.chap_combo.blockSignals(False)

    def on_chapter_selected(self, idx: int):
        if not self.project or idx < 0 or idx == self._chap_idx:
            return
        self.save_edits()          # lưu chương đang mở trước khi chuyển
        self.show_chapter(idx)

    def show_chapter(self, idx: int):
        self._chap_idx, self._row = idx, -1
        self.table.clearSelection()
        ch = self.chapter
        self.story.setPlainText(ch.story if ch else "")
        self.fill_table()
        self.left_tabs.setCurrentWidget(self.scene_page if self.scenes else self.story)

    def new_chapter(self):
        if not self.project:
            return
        self.save_edits()
        n = len(self.project.chapters) + 1
        title, ok = QInputDialog.getText(self, "Chương mới", "Tên chương (có thể để mặc định, sẽ tự lấy từ dòng đầu truyện):",
                                         text=f"Chương {n}")
        if not ok:
            return
        self.project.new_chapter(title)
        self.project.save()
        last = len(self.project.chapters) - 1
        self.fill_chapter_combo(last)
        self.show_chapter(last)
        self.left_tabs.setCurrentWidget(self.story)
        self.story.setFocus()

    def rename_chapter(self):
        ch = self.chapter
        if not ch:
            return
        title, ok = QInputDialog.getText(self, "Đổi tên chương", "Tên chương:", text=ch.name)
        if ok and title.strip():
            ch.title = title.strip()
            self.project.save()
            self.refresh_chapter_labels()

    def delete_chapter(self, idx: int | None = None):
        """Xoá một chương (mặc định chương đang mở), thẳng từ danh sách mà không cần chuyển sang chương đó trước."""
        p = self.project
        if not p:
            return
        idx = self._chap_idx if idx is None else idx
        if not 0 <= idx < len(p.chapters):
            return
        ch = p.chapters[idx]
        if len(p.chapters) == 1:
            QMessageBox.information(self, "Không thể xoá", "Dự án phải còn ít nhất một chương.")
            return
        if self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong (hoặc bấm Dừng) rồi xoá chương.")
            return
        videos = sum(len(scene_ops.scene_files(s)) for s in ch.scenes)
        box = QMessageBox(self)
        box.setWindowTitle("Xoá chương")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Xoá '{ch.name}' ({len(ch.scenes)} scene, {videos} file video/giọng)?")
        box.setInformativeText("File sẽ được chuyển vào thùng rác của dự án (khôi phục được, chỉ mất hẳn khi bạn dọn thùng rác).")
        b_all = box.addButton("Xoá chương và file", QMessageBox.DestructiveRole)
        b_keep = box.addButton("Chỉ gỡ khỏi dự án (giữ file trên ổ đĩa)", QMessageBox.ActionRole)
        b_cancel = box.addButton("Huỷ", QMessageBox.RejectRole)
        box.setDefaultButton(b_cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_all, b_keep):
            return
        cur = self._chap_idx
        moved = scene_ops.delete_chapter(p, ch, delete_files=clicked is b_all)
        p.save()
        self.log(f"Đã xoá '{ch.name}'" + (f", {moved} mục chuyển vào thùng rác." if moved else " (giữ nguyên file)."))
        # giữ nguyên chương đang xem nếu xoá chương khác; xoá chính chương đang xem thì sang chương gần nhất
        new = cur - 1 if idx < cur else min(cur, len(p.chapters) - 1)
        self._chap_idx = -1
        self.fill_chapter_combo(new)
        self.show_chapter(new)

    def fill_accounts(self, current: str = ""):
        self.flow_account.blockSignals(True)
        self.flow_account.clear()
        cur = current or (self.project.account_id if self.project else accounts.DEFAULT_ID)
        for a in accounts.all_accounts():
            self.flow_account.addItem(a.name, a.id)
        self.flow_account.setCurrentIndex(max(0, self.flow_account.findData(cur)))
        self.flow_account.blockSignals(False)
        self.account_info.setText(accounts.describe_credits(accounts.get(cur)) + ("  ·  tự chuyển tài khoản: BẬT" if accounts.auto_switch() else ""))

    def current_voice(self) -> str:
        return self.voice.currentData() or self.voice.currentText()

    def fill_voices(self, keep=None):
        """Danh sách giọng theo nhà cung cấp + ngôn ngữ thuyết minh. Giữ giọng đang chọn nếu còn hợp lệ, không thì lấy giọng mặc định của ngôn ngữ."""
        provider, lang = self.provider.currentData(), self.narr_lang.currentData() or "vi"
        want = keep if isinstance(keep, str) else self.current_voice()
        voices = tts.voices_for(provider, lang)
        self.voice.blockSignals(True)
        self.voice.clear()
        for vid, label in voices:
            self.voice.addItem(label, vid)
        ids = [v for v, _ in voices]
        if want and want not in ids and isinstance(keep, str):           # giọng tuỳ chỉnh đã lưu trong dự án nhưng không có trong danh mục
            self.voice.insertItem(0, want, want)
            ids.insert(0, want)
        pick = want if want in ids else tts.default_voice(provider, lang)
        self.voice.setCurrentIndex(max(0, self.voice.findData(pick)))
        self.voice.blockSignals(False)
        self.voice_style.setEnabled(provider == "gemini")
        self.update_narr_count()

    # ================= bảng + chi tiết scene =================
    def fill_table(self, select: int | None = None):
        if select is None:
            select = self._row
        scenes = self.scenes
        keep = self.selected_indices()      # giữ nguyên các dòng đang chọn khi bảng được làm mới
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for s in scenes:
            r = self.table.rowCount()
            self.table.insertRow(r)
            label, color = self.status_text(s)
            vals = [str(s.index), s.title, label, ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QBrush(QColor(color)))
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self.apply_scene_filter()
        n = len(scenes)
        self.refresh_counts()
        self.update_empty_state()
        self.update_steps()
        self._row = -1
        if n:
            row = select if select is not None and 0 <= select < n else 0
            self.table.setCurrentCell(row, 0)  # phát currentCellChanged -> on_row_changed
            if len(keep) > 1:                  # khôi phục lựa chọn nhiều dòng
                sm = self.table.selectionModel()
                for r, s in enumerate(scenes):
                    if s.index in keep:
                        sm.select(self.table.model().index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.update_sel_label()
        else:
            self.clear_detail()
            self.preview.load(None)

    # ================= popover =================
    def gen_cost(self, mode: str) -> str:
        p = self.project
        todo = self.pick_todo(mode)
        if not todo or not p:
            return ""
        est = credits.estimate(p.flow_model, p.flow_resolution, todo, p.flow_auto_duration, p.narration_lang)
        return f"{len(todo)} clip  ·  ≈ {est} credit"

    def build_gen_pop(self, pop):
        sc = self.scenes
        cur = sc[self._row] if 0 <= self._row < len(sc) else None
        n = len(self.selected_indices())
        pend = sum(1 for s in sc if s.status != "done")
        pop.section("Gen video bằng Flow")
        pop.item(f"Scene đang xem  ·  #{cur.index}" if cur else "Scene đang xem",
                 f"{cur.title[:34]}  ·  {self.gen_cost('current')}" if cur else "Chưa chọn scene",
                 lambda: self.flow_auto_run("current"), enabled=cur is not None)
        pop.item(f"Các scene đang chọn  ·  {n}" if n >= 2 else "Các scene đang chọn",
                 self.gen_cost("selected") if n >= 2 else "Giữ ⇧ hoặc ⌘ rồi click để chọn nhiều dòng",
                 lambda: self.flow_auto_run("selected"), enabled=n >= 2)
        pop.separator()
        pop.item(f"Tất cả scene chưa xong  ·  {pend}", self.gen_cost("pending") or "Mọi scene đã xong",
                 lambda: self.flow_auto_run("pending"), enabled=pend > 0)
        pop.item(f"Gen lại tất cả  ·  {len(sc)}", "Ghi đè cả scene đã xong, tốn nhiều credit",
                 lambda: self.flow_auto_run("all"), enabled=bool(sc), danger=True)
        pop.separator()
        pop.section("Số scene gửi cùng lúc lên Flow")
        seg = Segmented()
        for k in (1, 2, 3, 4):
            seg.addItem(str(k), k)
        seg.setCurrentIndex(max(0, seg.findData(min(self.flow_parallel.value(), 4))))
        seg.currentIndexChanged.connect(lambda i: self.set_parallel(seg.currentData()))
        pop.widget(seg)
        hint = QLabel("1 = lần lượt từng scene. Số lớn hơn: nhiều scene render cùng lúc trên Flow để đỡ chờ, credit không đổi. Nếu Flow báo lỗi thì giảm xuống.")
        hint.setProperty("caption", True)
        hint.setWordWrap(True)
        pop.widget(hint)

    def set_parallel(self, k: int):
        """Đổi số scene gửi cùng lúc ngay từ menu Gen video (cùng giá trị với Cài đặt dự án)."""
        if self.project and k:
            self.flow_parallel.setValue(int(k))
            self.project.flow_parallel = int(k)
            self.project.save()
            self.log(f"Gen video: gửi {k} scene cùng lúc." if k > 1 else "Gen video: lần lượt từng scene.")

    def build_manage_pop(self, pop):
        sel = self.selected_scenes()
        n = len(sel)
        contiguous = n >= 2 and self.chapter is not None and scene_ops.is_contiguous(self.chapter, sel)
        has_final = any(s.clip or s.audio for s in sel)
        has_any = any(scene_ops.scene_files(s) for s in sel)
        pop.section("Gộp scene")
        pop.item(f"Gộp thông minh {n} scene đang chọn" if n >= 2 else "Gộp thông minh",
                 "Viết lại thuyết minh và visual thành một clip" if contiguous else "Chọn từ 2 scene liền kề (Shift+click)",
                 self.merge_selected, enabled=contiguous)
        pop.item("Gợi ý gộp các scene ngắn…", "Tự tìm nhóm scene ngắn để tiết kiệm credit",
                 self.suggest_merge_dialog, enabled=bool(self.scenes))
        pop.separator()
        pop.section("Video")
        pop.item(f"Xoá bản có giọng  ·  {n} scene", "Giữ clip Flow gốc, tạo lại giọng miễn phí",
                 lambda: self.clear_selected_videos(keep_raw=True), enabled=has_final)
        pop.item(f"Xoá toàn bộ video  ·  {n} scene", "Phải gen lại trên Flow nên tốn credit",
                 lambda: self.clear_selected_videos(keep_raw=False), enabled=has_any, danger=True)
        pop.separator()
        pop.section("Scene")
        pop.item(f"Xoá {n} scene…", "Chuyển vào thùng rác, khôi phục được", self.delete_selected,
                 enabled=n > 0, danger=True, shortcut="⌫")

    def build_project_pop(self, pop):
        names = Project.list_names()
        cur = self.combo.currentText()
        pop.section(f"Chuyển dự án  ·  {len(names)}")
        entries = []
        for name in names:
            nch, done, total = Project.summary(name)
            sub = f"{nch} chương  ·  {done}/{total} scene xong" if total else f"{nch} chương  ·  chưa có scene"
            entries.append(dict(title=name, sub=sub, cb=(lambda n=name: self.switch_project(n)), shortcut="✓" if name == cur else "",
                                progress=(done, total), action=("Xoá", (lambda n=name: self.delete_project(n)))))
        pop.searchable_list(entries, "Tìm dự án…", focus=names.index(cur) if cur in names else -1, empty="Không có dự án nào khớp")
        pop.separator()
        pop.item("+ Dự án mới…", "Tạo dự án trống với một chương", self.new_project)
        pop.separator()
        n_trash = len(trash.list_trashed_projects())
        pop.item("Dự án đã xoá…", f"{n_trash} dự án trong thùng rác" if n_trash else "Thùng rác dự án đang trống",
                 self.restore_projects_dialog, enabled=n_trash > 0)

    def delete_project(self, name: str | None = None):
        """Xoá một dự án (mặc định là dự án đang mở): chuyển nguyên thư mục (chương, scene, clip, giọng, nhân vật) vào thùng rác
        dự án, khôi phục được. Xoá thẳng từ danh sách, không cần chuyển sang dự án đó trước."""
        cur = self.combo.currentText()
        name = name or cur
        if not name:
            return
        is_open = bool(self.project) and name == cur
        if is_open and self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong (hoặc bấm Dừng) rồi xoá dự án đang mở.")
            return
        try:
            p = self.project if is_open else Project.load(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không đọc được dự án", str(e))
            return
        scenes = sum(len(c.scenes) for c in p.chapters)
        files, size = trash.dir_size(p.dir)
        nchars = len(models.load_characters(name))
        box = QMessageBox(self)
        box.setWindowTitle("Xoá dự án")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Xoá dự án '{name}'?")
        box.setInformativeText(
            f"{len(p.chapters)} chương · {scenes} scene · {nchars} nhân vật · {files} file ({size / 1_048_576:.0f} MB) "
            "sẽ được chuyển vào thùng rác dự án. Bạn khôi phục được bằng mục “Dự án đã xoá…”.\n\n"
            "Dự án tương ứng trên Google Flow (nếu có) không bị xoá.")
        b_del = box.addButton("Xoá dự án", QMessageBox.DestructiveRole)
        b_cancel = box.addButton("Huỷ", QMessageBox.RejectRole)
        box.setDefaultButton(b_cancel)          # Enter = Huỷ, tránh lỡ tay xoá
        box.exec()
        if box.clickedButton() is not b_del:
            return
        try:
            trash.trash_project(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không xoá được", str(e))
            return
        self.log(f"Đã xoá dự án '{name}' (nằm trong thùng rác dự án, khôi phục bằng “Dự án đã xoá…”).")
        if is_open:
            self.reload_projects()
        else:                                   # xoá dự án khác: giữ nguyên dự án đang mở, chỉ làm mới danh sách
            self.combo.blockSignals(True)
            self.combo.clear()
            self.combo.addItems(Project.list_names())
            self.combo.setCurrentText(cur)
            self.combo.blockSignals(False)
            self.refresh_nav()

    def restore_projects_dialog(self):
        from .trash_dialog import TrashedProjectsDialog
        dlg = TrashedProjectsDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.restored:
            self.log(f"Đã khôi phục dự án '{dlg.restored}'.")
            self.reload_projects(dlg.restored)

    def build_chapter_pop(self, pop):
        p = self.project
        chs = p.chapters if p else []
        pop.section(f"Chuyển chương  ·  {len(chs)}")
        entries = []
        for i, c in enumerate(chs):
            sub = f"{c.done}/{len(c.scenes)} scene xong" if c.scenes else ("Có truyện, chưa tạo scene" if c.story.strip() else "Chưa có nội dung")
            entries.append(dict(title=c.name, search=f"chuong {int(c.id)}" if c.id.isdigit() else "", sub=sub, cb=(lambda k=i: self.chap_combo.setCurrentIndex(k)),
                                shortcut="✓" if i == self._chap_idx else "", progress=(c.done, len(c.scenes)),
                                action=("Xoá", (lambda k=i: self.delete_chapter(k))) if len(chs) > 1 else None))
        pop.searchable_list(entries, "Tìm chương (tên hoặc số)…", focus=self._chap_idx, empty="Không có chương nào khớp")
        pop.separator()
        pop.item("+ Chương mới…", "Dán truyện rồi tạo scene", self.new_chapter)
        ch = self.chapter
        pop.separator()
        pop.section("Chương đang mở")
        pop.item("Đổi tên chương", ch.name if ch else "", self.rename_chapter, enabled=bool(ch))
        pop.item("Mở thư mục chương", "Xem file clip và giọng trên ổ đĩa",
                 lambda: flow.reveal(self.project.chapter_dir(self.chapter)), enabled=bool(ch))

    def switch_project(self, name: str):
        if name and name != self.combo.currentText():
            self.combo.setCurrentText(name)

    def step_chapter(self, delta: int):
        k = self._chap_idx + delta
        if self.project and 0 <= k < len(self.project.chapters):
            self.chap_combo.setCurrentIndex(k)

    def refresh_nav(self):
        """Cập nhật breadcrumb: tên dự án, tên chương + nhãn tiến độ (xanh khi xong hết), nút ‹ › theo vị trí chương."""
        p, ch = self.project, self.chapter
        self.nav_project.set_title(p.name if p else "Chưa có dự án")
        if ch:
            n = len(ch.scenes)
            self.nav_chapter.set_title(ch.name)
            self.nav_chapter.set_pill(f"{ch.done}/{n}" if n else "trống", "ok" if n and ch.done == n else "info")
            self.chap_bar.setRange(0, max(n, 1))
            self.chap_bar.setValue(ch.done)
            self.chap_count.setText(f"{ch.done}/{n}" if n else "trống")
            icons.attach(self.chap_check, "check", 18, role="muted")
            self.chap_check.setVisible(bool(n) and ch.done == n)
            if n and ch.done == n:
                self.chap_check.setStyleSheet(f"color: {theme.T['ok']}; background: transparent;")
        else:
            self.nav_chapter.set_title("Chưa có chương")
            self.nav_chapter.set_pill("", "info")
            self.chap_bar.setValue(0)
            self.chap_count.setText("")
            self.chap_check.setVisible(False)
        total = len(p.chapters) if p else 0
        self.btn_prev.setEnabled(not self._busy and self._chap_idx > 0)
        self.btn_next.setEnabled(not self._busy and 0 <= self._chap_idx < total - 1)

    def build_more_pop(self, pop):
        n, size = trash.trash_stats(self.project.dir) if self.project else (0, 0)
        pop.section("Google Flow")
        pop.item("Mở Chrome Flow", "Cửa sổ Chrome riêng để đăng nhập và điều khiển Flow", self.launch_flow_chrome)
        pop.item("Thủ công: xuất prompt và ảnh", "Tự dán vào Flow bằng tay", self.flow_export)
        pop.item("Thủ công: nhập clip tải từ Flow", "Gán clip đã tải về cho các scene", self.flow_import)
        pop.item("Gen bằng Gemini API", "Cần key có quyền Veo", lambda: self.generate("pending"))
        pop.separator()
        pop.section("Nội dung chương")
        pop.item("Gắn nhân vật vào scene đã có", "Nhân vật tạo sau khi tách scene; theo văn bản, không tốn credit",
                 lambda: self.attach_characters_to_scenes(None, ask=True), enabled=bool(self.scenes))
        pop.item("Nhận diện lại nhân vật cho scene", "Theo văn bản, không dùng LLM", self.reassign_characters)
        lname = langs.name(self.project.narration_lang) if self.project else ""
        pop.item("Viết lại thuyết minh bám truyện", "Giữ nguyên clip đã gen", self.realign)
        pop.item(f"Dịch thuyết minh sang {lname}…" if lname else "Dịch thuyết minh…",
                 "AI dịch từ truyện gốc, rồi tạo lại giọng, không tốn credit Flow", self.translate_narrations_dialog,
                 enabled=bool(self.project and any(c.scenes for c in self.project.chapters)))
        pop.item("Áp dụng lại giọng đọc cho cả chương", "Không tốn credit Flow",
                 lambda: self.revoice(only_current=False))
        pop.separator()
        pop.section("Dự án")
        pop.item("Ghép tất cả chương thành 1 video", "Cần mọi chương đã gen xong", self.merge_project)
        pop.item("Hiện video ghép của chương", "Mở thư mục và chọn sẵn file", self.reveal_merged,
                 enabled=bool(self.project and self.chapter and self.merged_path().exists()))
        pop.item("Hiện video ghép cả dự án", "Mở thư mục và chọn sẵn file", lambda: self.reveal_merged(True),
                 enabled=bool(self.project and self.project.full_path.exists()))
        pop.item("Mở thư mục dự án", "", lambda: self.project and flow.reveal(self.project.dir))
        pop.item("Dọn thùng rác…", f"{n} file  ·  {size / 1_048_576:.1f} MB" if n else "Đang trống", self.empty_trash_dialog)

    def build_context_pop(self, pop):
        if self.s2.isEnabled():
            self.build_gen_pop(pop)
            pop.separator()
        self.build_manage_pop(pop)

    def open_settings(self):
        if not self.project or self._busy:
            return
        old_lang = self.project.narration_lang
        if self.settings_dialog.open_for_project():
            self.publish_settings_changed.emit()
            self.update_steps()
            self.log("Đã lưu cài đặt dự án.")
            if self.project.narration_lang != old_lang and any(c.scenes for c in self.project.chapters):
                QTimer.singleShot(0, lambda: self.translate_narrations_dialog(changed_from=old_lang))

    def translate_narrations_dialog(self, changed_from: str | None = None):
        """Viết lại thuyết minh của các scene hiện có bằng ngôn ngữ đã chọn (AI dịch từ truyện gốc), rồi tạo lại giọng đọc."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        p = self.project
        lang = p.narration_lang
        here = len(self.chapter.scenes) if self.chapter else 0
        total = sum(len(c.scenes) for c in p.chapters)
        if not total:
            QMessageBox.information(self, "Chưa có scene", "Hãy tạo scene trước.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Dịch thuyết minh")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Viết lại thuyết minh bằng {langs.name(lang)}?" if changed_from is None else
                    f"Đã đổi ngôn ngữ thuyết minh sang {langs.name(lang)}. Dịch thuyết minh của các scene hiện có?")
        box.setInformativeText("AI viết lại từ đoạn truyện gốc của từng scene (không dịch nối từ bản cũ), ghi đè thuyết minh hiện tại rồi tạo lại "
                               "giọng đọc cho scene đã có clip. Không tốn credit Flow, chỉ tốn token của AI đang chọn. "
                               "Chọn “Để sau” thì các scene mới tạo vẫn dùng ngôn ngữ này.")
        b_here = box.addButton(f"Chương này ({here} scene)", QMessageBox.AcceptRole)
        b_all = box.addButton(f"Cả dự án ({total} scene)", QMessageBox.AcceptRole)
        box.addButton("Để sau", QMessageBox.RejectRole)
        b_here.setEnabled(here > 0)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_here, b_all):
            return
        chapters = [self.chapter] if clicked is b_here else [c for c in p.chapters if c.scenes]
        chapters = [c for c in chapters if c and c.scenes]
        self.log(f"Đang viết lại thuyết minh bằng {langs.name(lang)} ({sum(len(c.scenes) for c in chapters)} scene) bằng {llm.describe()}...")

        def job(log):
            changed = 0
            for ch in chapters:
                res = scene_planner.translate_narrations(ch.scenes, lang, log)
                for s in ch.scenes:
                    if s.index in res:
                        s.narration, changed = res[s.index], changed + 1
                p.save()
                log(f"[{ch.name}] Đã viết lại thuyết minh {len(res)}/{len(ch.scenes)} scene.")
            work = [(ch, s) for ch in chapters for s in ch.scenes if s.raw_clip and Path(s.raw_clip).exists()]
            if work:
                log(f"Tạo lại giọng đọc ({langs.name(lang)}) cho {len(work)} scene đã có clip...")

                def one(cs):
                    ch, s = cs
                    try:
                        pipeline.apply_voice(p, ch, s, log)
                        s.status, s.error = "done", ""
                    except Exception as e:  # noqa: BLE001
                        s.status, s.error = "error", str(e)[:1500]
                        log(f"Scene {s.index} lỗi giọng: {e}")
                    p.save()
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong") as pool:
                    list(pool.map(one, work))
            return changed
        self.run(job, lambda n: (self.refresh_view(), self.log(f"Xong: đã viết lại thuyết minh {n} scene bằng {langs.name(lang)}. "
                                                               "Bấm ③ Ghép video để ghép lại.")))

    def on_theme_changed(self):
        if self.project:
            self.fill_table(self._row)

    def selected_scenes(self) -> list:
        """Các scene đang chọn trong bảng theo thứ tự hiển thị; không chọn dòng nào thì lấy scene đang xem."""
        keep = self.selected_indices()
        out = [s for s in self.scenes if s.index in keep]
        if not out and 0 <= self._row < len(self.scenes):
            out = [self.scenes[self._row]]
        return out

    def _after_edit(self, select: int | None = None):
        self.project.save()
        self.fill_table(select if select is not None else self._row)

    def delete_selected(self):
        if not self.manage_btn.isEnabled() or not self.chapter:
            return
        sel = self.selected_scenes()
        if not sel:
            return
        files = sum(len(scene_ops.scene_files(s)) for s in sel)
        nums = ", ".join(str(s.index) for s in sel)
        if QMessageBox.question(self, "Xoá scene",
                                f"Xoá {len(sel)} scene (#{nums}) khỏi {self.chapter.name}?\n"
                                f"{files} file video/giọng của chúng sẽ chuyển vào thùng rác của dự án (khôi phục được).\n"
                                "Các scene còn lại được đánh số lại.") != QMessageBox.Yes:
            return
        pos = min(self.scenes.index(s) for s in sel)
        moved = scene_ops.delete_scenes(self.project, self.chapter, sel)
        self.log(f"[{self.chapter.name}] Đã xoá {len(sel)} scene, {moved} file vào thùng rác.")
        self._row = -1
        self._after_edit(min(pos, max(len(self.scenes) - 1, 0)))

    def clear_selected_videos(self, keep_raw: bool):
        sel = [s for s in self.selected_scenes() if scene_ops.scene_files(s)]
        if not sel or not self.chapter:
            return
        what = ("bản có giọng (clip Flow gốc được giữ, tạo lại giọng không tốn credit)" if keep_raw
                else "TOÀN BỘ video gồm cả clip Flow gốc (muốn có lại phải gen trên Flow, tốn credit)")
        if QMessageBox.question(self, "Xoá video",
                                f"Xoá {what} của {len(sel)} scene (#{', '.join(str(s.index) for s in sel)})?\n"
                                "File chuyển vào thùng rác của dự án (khôi phục được).") != QMessageBox.Yes:
            return
        moved = scene_ops.clear_videos(self.project, self.chapter, sel, keep_raw)
        self.log(f"[{self.chapter.name}] Đã xoá video của {len(sel)} scene, {moved} file vào thùng rác.")
        self._after_edit()

    # ---- gộp scene ----
    def _merge_data_job(self, groups: list[list]):
        """Tạo nội dung gộp cho từng nhóm: dùng LLM nếu đã cấu hình, lỗi hoặc chưa có thì dùng cách gộp đơn giản."""
        chars = self.chars_tab.chars
        use_llm = llm.is_configured()[0]
        lang = self.project.narration_lang if self.project else "vi"

        def job(log):
            out = []
            for g in groups:
                data, how = None, "đơn giản"
                if use_llm:
                    try:
                        data, how = scene_planner.merge_scenes_llm(g, chars, log, lang), "LLM"
                    except Exception as e:  # noqa: BLE001
                        log(f"LLM gộp lỗi ({str(e)[:80]}), dùng cách gộp đơn giản.")
                out.append((g, data or scene_ops.merge_heuristic(g), how))
            return out
        return job

    def merge_selected(self):
        sel = self.selected_scenes()
        if len(sel) < 2 or not scene_ops.is_contiguous(self.chapter, sel):
            QMessageBox.information(self, "Gộp scene", "Hãy chọn từ 2 scene liền kề nhau (Shift+click).")
            return
        self.save_edits()
        self.log(f"[{self.chapter.name}] Đang gộp scene {', '.join(str(s.index) for s in sel)}...")

        def done(res):
            group, data, how = res[0]
            self.show_merge_preview(group, data, how)
        self.run(self._merge_data_job([sel]), done)

    def show_merge_preview(self, group, data, how):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Gộp scene {', '.join(str(s.index) for s in group)} ({how})")
        dlg.resize(620, 520)
        title = QLineEdit(data["title"])
        chars = QLineEdit(", ".join(data["characters"]))
        narr = QPlainTextEdit(data["narration"])
        visual = QPlainTextEdit(data["visual"])
        count = QLabel("")
        count.setProperty("caption", True)

        def upd():
            w = len(narr.toPlainText().split())
            count.setText(f"{w} từ ≈ {w / 3.3:.0f}s" + ("  ⚠ dài hơn clip 8-10s" if w > 34 else ""))
        narr.textChanged.connect(upd)
        upd()
        lost = sum(len(scene_ops.scene_files(s)) for s in group)
        note = QLabel((f"⚠ {lost} file video/giọng của các scene này sẽ chuyển vào thùng rác (nội dung đã đổi nên phải gen lại). "
                       if lost else "") + "Bạn có thể sửa nội dung trước khi gộp.")
        note.setWordWrap(True)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(note)
        for cap, w in (("Tiêu đề", title), ("Nhân vật (tối đa 3)", chars)):
            lay.addWidget(QLabel(cap))
            lay.addWidget(w)
        head = QHBoxLayout()
        head.addWidget(QLabel("Thuyết minh"))
        head.addStretch()
        head.addWidget(count)
        lay.addLayout(head)
        lay.addWidget(narr, 2)
        lay.addWidget(QLabel("Visual (prompt gửi Flow)"))
        lay.addWidget(visual, 2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Gộp")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        valid = {nfc(c.name): c.name for c in self.chars_tab.chars}
        names = [valid[nfc(n.strip())] for n in chars.text().split(",") if nfc(n.strip()) in valid]
        merged = {"title": title.text().strip() or data["title"], "narration": narr.toPlainText().strip(),
                  "visual": visual.toPlainText().strip(), "characters": names[:3]}
        new = scene_ops.merge_group(self.project, self.chapter, group, merged)
        self.log(f"[{self.chapter.name}] Đã gộp {len(group)} scene thành scene {new.index}. Cần gen video cho scene này.")
        self._row = -1
        self._after_edit(new.index - 1)

    def suggest_merge_dialog(self):
        if not self.chapter:
            return
        self.save_edits()
        groups = scene_ops.suggest_merges(self.chapter)
        if not groups:
            QMessageBox.information(self, "Gợi ý gộp",
                                    "Không có nhóm scene ngắn liền kề nào phù hợp (mỗi scene ≤14 từ, tổng ≤32 từ, chưa có video).")
            return
        p = self.project
        per = credits.scene_cost(p.flow_model, p.flow_resolution, 8)
        dlg = QDialog(self)
        dlg.setWindowTitle("Gợi ý gộp scene ngắn")
        dlg.resize(640, 420)
        lst = QListWidget()
        for g in groups:
            txt = " / ".join(s.narration for s in g)
            it = QListWidgetItem(f"Scene {' + '.join(str(s.index) for s in g)}  ({sum(scene_ops.words(s) for s in g)} từ)  —  {txt[:90]}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            lst.addItem(it)
        info = QLabel("")
        info.setProperty("caption", True)

        def upd():
            chosen = [g for i, g in enumerate(groups) if lst.item(i).checkState() == Qt.Checked]
            saved = sum(len(g) - 1 for g in chosen)
            info.setText(f"Gộp {len(chosen)} nhóm: bớt {saved} scene, tiết kiệm khoảng {saved * per} credit Flow ({p.flow_model}).")
        lst.itemChanged.connect(upd)
        upd()
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(QLabel("Các scene liền kề đều ngắn có thể gộp thành 1 clip. Bỏ tick nhóm nào bạn không muốn gộp."))
        lay.addWidget(lst, 1)
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Gộp các nhóm đã chọn")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = [g for i, g in enumerate(groups) if lst.item(i).checkState() == Qt.Checked]
        if not chosen:
            return
        ch = self.chapter
        self.log(f"[{ch.name}] Đang gộp {len(chosen)} nhóm scene...")

        def done(results):
            n = 0
            for g, data, how in results:
                scene_ops.merge_group(self.project, ch, g, data)
                n += len(g) - 1
            self.log(f"[{ch.name}] Đã gộp {len(results)} nhóm, bớt {n} scene. Cần gen video cho các scene mới gộp.")
            self._row = -1
            self._after_edit(0)
        self.run(self._merge_data_job(chosen), done)

    def empty_trash_dialog(self):
        if not self.project:
            return
        n, size = trash.trash_stats(self.project.dir)
        if not n:
            QMessageBox.information(self, "Thùng rác", "Thùng rác của dự án đang trống.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Thùng rác của dự án")
        box.setText(f"Thùng rác có {n} file ({size / 1_048_576:.1f} MB).")
        box.setInformativeText("Dọn thùng rác sẽ xoá HẲN các file này, không khôi phục được.")
        b_open = box.addButton("Mở thư mục", QMessageBox.ActionRole)
        b_empty = box.addButton("Dọn thùng rác", QMessageBox.DestructiveRole)
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_open:
            flow.reveal(trash.trash_root(self.project.dir))
        elif box.clickedButton() is b_empty:
            trash.empty_trash(self.project.dir)
            self.log("Đã dọn thùng rác của dự án.")

    # ---- nhân vật bị xoá ở tab Nhân vật ----
    def character_usage(self, names: list[str]) -> dict[str, int]:
        if not self.project:
            return {}
        want = {nfc(n): n for n in names}
        out = {n: 0 for n in names}
        for _, s in self.project.all_scenes():
            for c in s.characters:
                if nfc(c) in want:
                    out[want[nfc(c)]] += 1
        return out

    def on_characters_deleted(self, names: list[str]):
        if not self.project:
            return
        gone = {nfc(n) for n in names}
        for _, s in self.project.all_scenes():
            s.characters = [c for c in s.characters if nfc(c) not in gone]
        self.project.save()
        if self.chapter:
            self.fill_table(self._row)

    def selected_indices(self) -> set[int]:
        sc = self.scenes
        return {sc[i.row()].index for i in self.table.selectionModel().selectedRows() if 0 <= i.row() < len(sc)}

    def update_sel_label(self):
        """Nhãn ở thanh công cụ thẻ Scene: đang chọn nhiều dòng thì hiện 'Đã chọn N/M', không thì hiện tiến độ chương."""
        n, total = len(self.selected_indices()), len(self.scenes)
        self.sel_label.setText(f"Đã chọn {n}/{total}" if n > 1 else getattr(self, "_progress_text", ""))

    def select_where(self, pred):
        sm, model = self.table.selectionModel(), self.table.model()
        self.table.clearSelection()
        for r, s in enumerate(self.scenes):
            if pred(s):
                sm.select(model.index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.setFocus()
        self.update_sel_label()

    def table_context_menu(self, pos):
        if not self.scenes:
            return
        self._ctx_pop = Popover(self, self.build_context_pop, 400)       # giữ tham chiếu để không bị thu hồi
        self._ctx_pop.show_at(self.table.viewport().mapToGlobal(pos))

    def step_scene(self, delta: int) -> None:
        r = self._row + delta
        if 0 <= r < self.table.rowCount():
            while 0 <= r < self.table.rowCount() and self.table.isRowHidden(r):      # bỏ qua dòng đang bị lọc ẩn
                r += delta
            if 0 <= r < self.table.rowCount():
                self.table.setCurrentCell(r, 0)

    def on_cell_clicked(self, row: int, col: int) -> None:
        """Bấm vào nút '···' ở cuối dòng: mở menu thao tác của dòng đó (cùng menu chuột phải)."""
        if col == 3 and 0 <= row < len(self.scenes):
            idx = self.table.model().index(row, 3)
            rect = self.table.visualRect(idx)
            self.table.setCurrentCell(row, 0)
            self.table_context_menu(rect.bottomLeft())

    def set_scene_filter(self, key: str) -> None:
        self.scene_filter = key
        self.btn_filter.setProperty("active", key != "all")
        repolish(self.btn_filter)
        self.apply_scene_filter()

    def apply_scene_filter(self, *_) -> None:
        """Ô tìm + bộ lọc trạng thái chỉ ẨN/HIỆN dòng (không đổi thứ tự) nên số dòng vẫn khớp danh sách scene."""
        import unicodedata
        norm = lambda t: "".join(c for c in unicodedata.normalize("NFD", t.replace("đ", "d").replace("Đ", "D").lower()) if unicodedata.category(c) != "Mn")
        q = norm(self.scene_search.text().strip())
        for r, s in enumerate(self.scenes):
            if r >= self.table.rowCount():
                break
            ok = (not q or q in norm(f"{s.index} {s.title} {s.narration} {s.visual}")) and \
                 (self.scene_filter == "all" or (self.scene_filter == "done" and s.status == "done")
                  or (self.scene_filter == "todo" and s.status != "done") or (self.scene_filter == "error" and s.status == "error"))
            self.table.setRowHidden(r, not ok)

    def on_row_changed(self, cur: int, _prev: int):
        self.commit_detail()
        self._row = cur
        self.load_detail(cur)
        if not self._skip_preview:
            self.set_view_mode(0)
            self.load_preview(cur)

    def clear_detail(self):
        for w in (self.d_title, self.d_chars):
            w.clear()
        for w in (self.d_src, self.d_narr, self.d_visual):
            w.clear()
        self.d_badge.setText("")
        self.d_err.set_full("")
        self.b_view_err.setVisible(False)

    def load_detail(self, row: int):
        if not 0 <= row < len(self.scenes):
            self.clear_detail()
            return
        s = self.scenes[row]
        self.d_title.setText(s.title)
        self.d_chars.setText(", ".join(s.characters))
        self.d_src.setPlainText(s.source_text)
        self.d_narr.setPlainText(s.narration)
        self.d_visual.setPlainText(s.visual)
        self.update_badge(s)

    def update_narr_count(self):
        lang = self.narr_lang.currentData() or "vi"
        text = self.d_narr.toPlainText()
        units, sec = langs.count_units(text, lang), langs.seconds(text, lang)
        warn = sec > langs.CLIP_SECONDS * langs.TEMPO  # quá ~8s x 1.3 (tăng tốc tối đa): phần dư phải giữ khung hình cuối
        self.narr_count.setText(f"{units} {langs.unit_label(lang)} ≈ {sec:.0f}s" + ("  ⚠ dài hơn clip 8s" if warn else ""))
        self.narr_count.setProperty("level", "error" if warn else "info")
        repolish(self.narr_count)

    def view_error(self):
        """Hiện toàn bộ nội dung lỗi trong hộp thoại gọn (chọn/copy được), thay vì làm phình khung chi tiết."""
        if not 0 <= self._row < len(self.scenes):
            return
        from .error_dialog import ErrorDialog
        s = self.scenes[self._row]
        ctx = f"{self.chapter.name}  ·  Scene {s.index}: {s.title}" if s.title else f"{self.chapter.name}  ·  Scene {s.index}"
        dlg = ErrorDialog(self, f"Scene {s.index} gặp lỗi", ctx, s.error,
                          copy_text=f"{self.chapter.name} / Scene {s.index} [{s.status}]\n{s.error}".strip(), sync_cb=self.flow_sync)
        dlg.exec()

    def copy_error(self):
        if not 0 <= self._row < len(self.scenes):
            return
        s = self.scenes[self._row]
        text = f"{self.chapter.name} / Scene {s.index} [{s.status}]\n{s.error}".strip()
        QApplication.clipboard().setText(text)
        self.log("Đã copy lỗi vào clipboard.")

    def commit_detail(self):
        """Ghi nội dung ô chi tiết ngược vào scene đang chọn."""
        if not 0 <= self._row < len(self.scenes):
            return
        s = self.scenes[self._row]
        valid = {nfc(c.name) for c in self.chars_tab.chars}
        s.title = self.d_title.text().strip()
        names = [nfc(n.strip()) for n in self.d_chars.text().split(",") if n.strip()]
        if valid:
            names = [n for n in names if n in valid]
        s.characters = names[:3]
        s.narration = self.d_narr.toPlainText().strip()
        s.visual = self.d_visual.toPlainText().strip()

    def commit_chapter(self):
        """Ghi truyện đang gõ vào chương hiện tại; chương còn tên mặc định thì lấy tên từ dòng đầu truyện."""
        ch = self.chapter
        if not ch:
            return
        ch.story = self.story.toPlainText()
        first = next((ln.strip() for ln in ch.story.splitlines() if ln.strip()), "")
        if re.fullmatch(r"Chương \d+", ch.title) and re.match(r"(?i)^chương\s*\d+", first):
            ch.title = first[:120]

    def load_preview(self, row: int, autoplay: bool = False):
        if not 0 <= row < len(self.scenes):
            self.preview.load(None)
            self.update_reveal()
            return
        s = self.scenes[row]
        self.preview.load(s.clip or s.raw_clip, autoplay)
        self.update_reveal()

    def merged_path(self) -> Path:
        return self.project.merged_path(self.chapter)

    def set_view_mode(self, i: int):
        self.view_seg.blockSignals(True)
        self.view_seg.setCurrentIndex(i)
        self.view_seg.blockSignals(False)
        self.update_reveal()

    def reveal_target(self) -> Path | None:
        """File đang xem ở khung xem trước: video ghép của chương (chế độ 'Video ghép') hoặc clip của scene đang chọn."""
        if not self.project or not self.chapter:
            return None
        if self.view_seg.currentIndex() == 1:
            f = self.merged_path()
        else:
            s = self.scenes[self._row] if 0 <= self._row < len(self.scenes) else None
            f = Path(s.clip or s.raw_clip) if s and (s.clip or s.raw_clip) else None
        return f if f and f.exists() else None

    def update_reveal(self):
        f = self.reveal_target()
        self.btn_reveal.setEnabled(f is not None)
        self.btn_reveal.setToolTip(f"Mở thư mục chứa {f.name} (chọn sẵn file)" if f else
                                   "Chưa có file để mở: gen xong rồi bấm ③ Ghép video")

    def reveal_current(self):
        f = self.reveal_target()
        if f:
            flow.reveal_file(f)

    def reveal_merged(self, whole_project: bool = False):
        """Hiện video ghép (của chương đang chọn, hoặc của cả dự án) trong Finder/Explorer."""
        if not self.project:
            return
        f = self.project.full_path if whole_project else (self.merged_path() if self.chapter else None)
        if f and f.exists():
            flow.reveal_file(f)
        else:
            QMessageBox.information(self, "Chưa có video ghép", "Chưa có file video ghép. Bấm ③ Ghép video sau khi gen xong các scene"
                                    + (" của tất cả chương." if whole_project else " của chương."))

    def on_view_changed(self, i: int):
        if i == 1:
            self.show_merged()
        else:
            self.load_preview(self._row)

    def show_merged(self):
        if self.chapter and self.merged_path().exists():
            self.set_view_mode(1)
            self.preview.load(str(self.merged_path()), autoplay=True)
        else:
            self.set_view_mode(0)
            QMessageBox.information(self, "Chưa có video ghép", "Bấm ③ Ghép video sau khi gen xong các scene của chương.")

    def save_edits(self):
        if not self.project:
            return
        self.commit_detail()
        self.commit_chapter()
        p = self.project
        p.synopsis = self.synopsis.toPlainText().strip()
        p.style, p.aspect_ratio = self.style.text().strip(), self.aspect.currentData() or "9:16"
        p.tts_provider, p.voice = self.provider.currentData(), self.current_voice()
        p.narration_lang = self.narr_lang.currentData() or "vi"
        p.voice_style = self.voice_style.text().strip()
        p.flow_model = self.flow_model.currentText()
        p.flow_resolution, p.flow_auto_duration = self.flow_res.currentText(), self.flow_auto_dur.isChecked()
        p.flow_parallel = self.flow_parallel.value()
        p.save()
        self.refresh_chapter_labels()

    # ================= chạy nền =================
    def set_busy(self, busy: bool):
        self._busy = busy
        for w in self._busy_widgets:
            w.setEnabled(not busy)
        if busy:
            self._tick_timer.start()
        else:
            self._tick_timer.stop()
        self.update_steps()
        self.refresh_nav()
        self.busy_changed.emit(busy, busy and self._cancelable)

    # ---- thông báo, tiến độ, dừng ----
    def log(self, msg: str):
        """Ghi nhật ký + đẩy dòng mới nhất lên thanh trạng thái (đỏ khi lỗi)."""
        self._log_sink(msg)
        low = msg.lower()
        if "lỗi" in low:
            level = "error"
        elif "cảnh báo" in low or "lưu ý" in low:
            level = "warn"
        elif low.rstrip().endswith(("xong.", "xong")) or msg.startswith("Đã "):
            level = "ok"
        else:
            level = "info"
        self.activity.emit(msg.splitlines()[0][:300] if msg else "", level)

    def request_cancel(self):
        if self._busy and self._cancelable:
            self._cancel.set()
            self.log("Sẽ dừng sau scene đang chạy (scene đã gửi lên Flow vẫn hoàn tất vì credit đã được trừ).")

    def status_text(self, s) -> tuple[str, str]:
        label, token = STATUS.get(s.status, (s.status, "faint"))
        color = theme.T[token]
        if s.status == "generating":
            el = int(time.time() - self._gen_t0.setdefault(id(s), time.time()))
            label = f"Đang gen… {el // 60}:{el % 60:02d}"
        else:
            self._gen_t0.pop(id(s), None)
        return label, color

    def tick(self):
        """Chạy mỗi 0.7s khi có tác vụ nền: cập nhật bảng/tiến độ theo trạng thái thật, không đợi cả lượt xong."""
        if not self.project:
            return
        self.update_rows_status()
        self.progress_changed.emit(*self._prog)

    def update_rows_status(self):
        sc = self.scenes
        if self.table.rowCount() != len(sc):
            return
        for r, s in enumerate(sc):
            label, color = self.status_text(s)
            it = self.table.item(r, 2)
            if it is not None and it.text() != label:
                it.setText(label)
                it.setForeground(QBrush(QColor(color)))
        self.refresh_counts()
        self.update_badge()
        self.update_steps()

    def refresh_counts(self):
        scenes = self.scenes
        n, done = len(scenes), sum(1 for s in scenes if s.status == "done")
        allp = self.project.all_scenes()
        total, total_done = len(allp), sum(1 for _, s in allp if s.status == "done")
        self.progress.setText(f"Chương: {done}/{n} scene  ·  Dự án: {total_done}/{total}")
        self._progress_text = f"{done}/{n} xong" if n else ""
        self.scene_bar.setRange(0, max(n, 1))
        self.scene_bar.setValue(done)
        self.prog_text.setText(self._progress_text)
        self.prog_dot.setStyleSheet(f"background: {theme.T['ok'] if n and done == n else theme.T['info']}; border-radius: 4px;")
        self.update_sel_label()
        self.refresh_chapter_labels()

    def update_badge(self, s=None):
        if s is None:
            s = self.scenes[self._row] if 0 <= self._row < len(self.scenes) else None
        if s is None:
            return
        label, color = self.status_text(s)
        self.d_badge.setText(f'<b style="color:{color}">Scene {s.index}: {label}</b>')
        self.d_err.set_full(s.error)
        self.b_view_err.setVisible(bool(s.error))

    def update_steps(self):
        """Ba bước ①②③: làm nổi bật bước kế tiếp, hiện tiến độ, và chỉ cho bấm ③ khi đã đủ clip."""
        sc = self.scenes
        n, done = len(sc), sum(1 for s in sc if s.status == "done")
        nxt = 1 if n == 0 else (2 if done < n else 3)
        self.s1.setText("Tạo scene" if n == 0 else "Tạo lại")
        self.s2.setText("Gen video" + (f" {done}/{n}" if n else ""))
        self.s3.setText("Ghép video" if not n or done == n else f"Ghép · còn {n - done}")
        for i, b in ((1, self.s1), (2, self.s2), (3, self.s3)):
            want = i == nxt
            if bool(b.property("primary")) != want:
                b.setProperty("primary", want)
                repolish(b)               # (repolish tự vẽ lại icon theo màu chữ mới)
        busy = self._busy
        self.s1.setEnabled(not busy)
        self.s2.setEnabled(n > 0 and not busy)
        self.s3.setEnabled(n > 0 and done == n and not busy)

    def update_empty_state(self):
        ch = self.chapter
        if self.scenes or ch is None:
            self.table_stack.setCurrentIndex(1)
            return
        words = len(ch.story.split())
        if words:
            self.empty_title.setText("Chương đã có truyện, chưa có scene")
            hint = "" if self.chars_tab.chars else "\nChưa có nhân vật: nên tạo ở tab Nhân vật (AI đọc truyện, không cần scene) trước để scene gắn đúng nhân vật."
            self.empty_text.setText(f"Truyện hiện có khoảng {words} từ. Bấm nút dưới để tách thành các scene.{hint}")
            self.empty_btn.setText("Tạo scene")
        else:
            self.empty_title.setText("Chương này đang trống")
            self.empty_text.setText("Dán nội dung chương vào tab “Truyện (chương)”, sau đó tạo scene.")
            self.empty_btn.setText("Mở tab Truyện")
        self.table_stack.setCurrentIndex(0)

    def empty_action(self):
        if self.chapter and self.chapter.story.strip() and not self._busy:
            self.plan()
        else:
            self.left_tabs.setCurrentWidget(self.story)
            self.story.setFocus()

    def set_account(self, acc_id: str):
        """Đổi tài khoản Flow của dự án đang mở. Project Flow là của riêng từng tài khoản nên địa chỉ project được cất/nạp theo tài khoản
        (đổi sang tài khoản chưa từng dùng thì lần gen sau tạo project mới theo tên chương)."""
        p = self.project
        if not p or acc_id == p.account_id:
            return
        if self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong (hoặc bấm Dừng) rồi đổi tài khoản Flow.")
            self.account_changed.emit(p.account_id)         # trả ô chọn về giá trị cũ
            return
        self.save_edits()
        old = accounts.get(p.account_id).name
        p.use_flow_account(acc_id)
        p.save()
        acc = accounts.activate(p.account_id)
        self.log(f"Dự án «{p.name}» chuyển từ tài khoản Flow «{old}» sang «{acc.name}»."
                 + ("" if flow_auto.cdp_state(0) else " Chrome của tài khoản này chưa mở: bấm chip Flow để mở và đăng nhập."))
        self.account_changed.emit(acc.id)

    def reload_flow_state(self):
        """Nạp lại từ đĩa các thông tin Flow do tiến trình nền khác ghi (địa chỉ project Flow của dự án/chương, tài khoản): nếu không, lần lưu
        kế tiếp của tab này sẽ ghi đè chúng bằng bản cũ trong bộ nhớ (vd. sau khi tạo ảnh nhân vật trong project Flow của chương)."""
        if not self.project or self._busy:
            return
        try:
            fresh = Project.load(self.project.name)
        except Exception:  # noqa: BLE001
            return
        self.project.flow_account, self.project.flow_stash = fresh.flow_account, fresh.flow_stash
        self.project.flow_project_url = fresh.flow_project_url
        by_id = {c.id: c for c in fresh.chapters}
        for c in self.project.chapters:
            if c.id in by_id:
                c.flow_project_url = by_id[c.id].flow_project_url

    def on_accounts_changed(self):
        """Danh sách tài khoản đổi (thêm/đổi tên/gỡ): nạp lại ô chọn và kích hoạt lại tài khoản của dự án (có thể đã bị chuyển về chính)."""
        if self.project:
            self.reload_flow_state()
            accounts.activate(self.project.account_id)
        self.account_changed.emit(self.project.account_id if self.project else "")

    def launch_flow_chrome(self):
        if not self._busy:
            acc = accounts.active()
            self.run(lambda log: flow_auto.launch_chrome(acc), lambda _: self.log(f"Chrome Flow ({acc.name}) đã mở."))

    def _restore_queue(self):
        """Scene còn đang 'Hàng đợi' khi tác vụ kết thúc/bị dừng thì trả về trạng thái trước đó."""
        for _, s in self.project.all_scenes() if self.project else []:
            if s.status == "queued":
                s.status, s.error = self._queue_prev.get(id(s), ("pending", ""))
        self._queue_prev = {}
        if self.project:
            self.project.save()

    def _on_worker_finished(self):
        self.set_busy(False)
        self._restore_queue()
        self.progress_changed.emit(0, 0)
        self.refresh_view()

    def need_project(self, need_key: bool = False) -> bool:
        if not self.project:
            QMessageBox.warning(self, "Thiếu dự án", "Hãy tạo dự án trước.")
        elif need_key and not llm.is_configured()[0]:
            QMessageBox.warning(self, "Thiếu API key", llm.is_configured()[1])
        else:
            return True
        return False

    def run(self, fn, on_done, cancelable: bool = False, on_fail=None):
        self._cancelable = cancelable
        self._cancel.clear()
        self._prog = (0, 0)
        self.set_busy(True)
        self.worker = Worker(fn)
        self.worker.log.connect(self.log)
        self.worker.failed.connect(lambda e: self.log(f"LỖI: {e}"))
        if on_fail:
            self.worker.failed.connect(on_fail)
        self.worker.done.connect(on_done)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def refresh_view(self):
        if not self.project:
            return
        self._skip_preview = self._keep_preview
        self._keep_preview = False
        self.fill_table(self._row)
        self._skip_preview = False

    MODE_LABEL = {"current": "scene đang xem", "selected": "các scene đang chọn",
                  "pending": "các scene chưa xong", "all": "TẤT CẢ scene (kể cả đã xong)"}

    def pick_todo(self, mode: str = "pending"):
        """current: scene đang xem | selected: các dòng đang chọn trong bảng | pending: chưa xong | all: tất cả (gen lại cả scene xong)."""
        sc = self.scenes
        if mode == "current":
            return [sc[self._row]] if 0 <= self._row < len(sc) else []
        if mode == "selected":
            keep = self.selected_indices()
            return [s for s in sc if s.index in keep]
        if mode == "all":
            return list(sc)
        return [s for s in sc if s.status != "done"]

    # ================= các bước (theo chương đang chọn) =================
    def plan(self):
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        ch = self.chapter
        if not ch.story.strip():
            self.left_tabs.setCurrentWidget(self.story)
            QMessageBox.information(self, "Thiếu truyện", f"Dán nội dung {ch.name} vào tab Truyện trước.")
            return
        if not self.chars_tab.chars and not self.ask_characters_first():
            return
        p, chars, n = self.project, self.chars_tab.chars, self.max_scenes.value()
        if ch.scenes and QMessageBox.question(
                self, "Tạo lại scene", f"{ch.name} đã có scene. Tạo lại sẽ xoá danh sách scene hiện tại của chương này. "
                "Tiếp tục?") != QMessageBox.Yes:
            return
        self.log(f"[{ch.name}] Đang tách scene bằng {llm.describe()}...")

        def done(res):
            scenes, notes = res
            ch.scenes = scenes
            self._row = -1
            p.save()
            for line in notes:
                self.log(line)
            self.log(f"[{ch.name}] Đã tạo {len(scenes)} scene.")
            self.left_tabs.setCurrentWidget(self.scene_page)
        self.run(lambda log: scene_planner.plan_scenes(ch.story, chars, n, p.synopsis, log, p.narration_lang), done)

    def ask_characters_first(self) -> bool:
        """Dự án chưa có nhân vật nào: scene tạo ra sẽ không gắn được nhân vật (clip mỗi cảnh một diện mạo). Cho chọn tạo nhân vật
        từ truyện NGAY (không cần scene), tạo scene luôn, hoặc huỷ. Trả về True nếu nên tiếp tục tạo scene."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Chưa có nhân vật")
        box.setText("Dự án chưa có nhân vật nào.")
        box.setInformativeText(
            "Nên tạo nhân vật TRƯỚC khi tạo scene: AI chỉ cần đọc truyện (không cần scene) để đề xuất nhân vật và ảnh, "
            "sau đó mỗi scene sẽ tự gắn đúng nhân vật.\n\nBạn vẫn có thể tạo scene ngay; nhân vật thêm sau sẽ được gắn vào các scene "
            "đã có (không tốn credit).")
        b_ai = box.addButton("Tạo nhân vật từ truyện (AI)…", QMessageBox.AcceptRole)
        b_go = box.addButton("Tạo scene luôn", QMessageBox.DestructiveRole)
        box.addButton("Huỷ", QMessageBox.RejectRole)
        box.setDefaultButton(b_ai)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_go:
            return True
        if clicked is b_ai:
            self.chars_tab.generate_from_story()          # hộp thoại AI; xong mà đã có nhân vật thì làm tiếp bước tạo scene
            return bool(self.chars_tab.chars) and QMessageBox.question(
                self, "Tạo scene", f"Đã có {len(self.chars_tab.chars)} nhân vật. Tạo scene cho {self.chapter.name} bây giờ?") == QMessageBox.Yes
        return False

    def on_characters_added(self, names: list[str]):
        """Nhân vật mới (AI, nhập gói, tạo tay): nếu các scene đã tạo trước đó nhắc tới họ thì hỏi để gắn vào."""
        if self.project and self.scenes_total():
            self.attach_characters_to_scenes(names, ask=True)

    def scenes_total(self) -> int:
        return sum(len(c.scenes) for c in self.project.chapters) if self.project else 0

    def attach_characters_to_scenes(self, names: list[str] | None = None, ask: bool = False):
        """Gắn nhân vật vào các scene ĐÃ CÓ (mọi chương) theo văn bản: không dùng LLM, không tốn credit, không đụng tới clip đã gen.
        names=None: xét mọi nhân vật; mỗi scene tối đa 3 nhân vật."""
        if not self.need_project() or not self.scenes_total():
            return
        self.save_edits()
        chars = self.chars_tab.chars
        scenes = [s for _, s in self.project.all_scenes()]
        saved = {id(s): list(s.characters) for s in scenes}
        changes = scene_planner.attach_characters(chars, scenes, names)
        if not changes:
            for s in scenes:
                s.characters = saved[id(s)]
            if not ask:
                self.log("Không có scene nào cần gắn thêm nhân vật (đã đủ hoặc văn bản không nhắc tới).")
            return
        who = sorted({n for _, new in changes for n in new})
        done_clips = sum(1 for sc, _ in changes if sc.status == "done")
        if ask:
            msg = (f"{len(who)} nhân vật ({', '.join(who[:6])}{'…' if len(who) > 6 else ''}) xuất hiện trong {len(changes)} scene đã tạo "
                   "nhưng chưa được gắn vào.\n\nGắn vào các scene đó? Không tốn credit, không gen lại gì."
                   + (f"\nLưu ý: {done_clips} scene đã có clip sẽ giữ nguyên clip; nhân vật mới chỉ ảnh hưởng khi gen lại." if done_clips else ""))
            if QMessageBox.question(self, "Gắn nhân vật vào scene", msg) != QMessageBox.Yes:
                for s in scenes:
                    s.characters = saved[id(s)]
                return
        self.project.save()
        self.fill_table(self._row)
        self.log(f"Đã gắn {len(who)} nhân vật vào {len(changes)} scene ({', '.join(who[:8])}{'…' if len(who) > 8 else ''}).")

    def reassign_characters(self):
        """Gán lại nhân vật theo văn bản cho các scene CHƯA có nhân vật nào (vd. bị mất do lỗi chuẩn hoá tên)."""
        if not self.need_project() or not self.scenes:
            return
        self.save_edits()
        chars = self.chars_tab.chars
        todo = [s for s in self.scenes if not s.characters]
        if not todo:
            self.log("Mọi scene của chương đã có nhân vật.")
            return
        n = 0
        for s in todo:
            s.characters = scene_planner.guess_characters(chars, s)
            n += bool(s.characters)
        self.project.save()
        self.fill_table(self._row)
        self.log(f"[{self.chapter.name}] Đã gán nhân vật cho {n}/{len(todo)} scene chưa có nhân vật. Kiểm tra lại trong ô Nhân vật.")

    def realign(self):
        """Gán lại đoạn truyện gốc + viết lại thuyết minh cho các scene HIỆN CÓ của chương, rồi tạo lại giọng."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        if not ch.scenes or not ch.story.strip():
            QMessageBox.information(self, "Thiếu dữ liệu", "Cần có scene và nội dung chương trong tab Truyện.")
            return
        if QMessageBox.question(self, "Viết lại thuyết minh",
                                f"LLM sẽ viết lại thuyết minh của TẤT CẢ scene trong {ch.name} theo đúng đoạn truyện "
                                "(ghi đè thuyết minh hiện tại), rồi tạo lại giọng đọc. Clip video giữ nguyên, không tốn "
                                "credit Flow. Tiếp tục?") != QMessageBox.Yes:
            return
        self.log(f"[{ch.name}] Đang căn lại thuyết minh theo truyện bằng {llm.describe()}...")

        def job(log):
            res, notes = scene_planner.realign_narration(ch.story, ch.scenes, log, p.narration_lang)
            for line in notes:
                log(line)
            for s in ch.scenes:
                if s.index in res:
                    s.source_text, s.narration = res[s.index]
            p.save()
            for s in ch.scenes:
                if s.raw_clip and Path(s.raw_clip).exists():
                    try:
                        pipeline.apply_voice(p, ch, s, log)
                        s.status, s.error = "done", ""
                    except Exception as e:  # noqa: BLE001
                        s.status, s.error = "error", str(e)[:1500]
                        log(f"Scene {s.index} lỗi: {e}")
                    p.save()
            log("Xong: đã cập nhật thuyết minh và giọng đọc. Bấm ③ Ghép video để ghép lại.")
        self.run(job, lambda _: None)

    # ---- credit: cổng chặn trước khi gen + làm mới ----
    def credit_message(self, v: dict) -> str:
        """Nội dung cảnh báo khi tài khoản không đủ credit (v: kết quả accounts.check_budget)."""
        cur = v["current"]
        lines = [f"Tài khoản Flow «{cur.name}» của dự án còn {accounts.fmt_credits(v['cur_credits'])} credit, "
                 f"nhưng lượt gen này cần khoảng {accounts.fmt_credits(v['need'])} credit.",
                 f"({accounts.describe_credits(cur)})"]
        if cur.renew:
            lines.append(f"Thời gian làm mới/gia hạn: {cur.renew}.")
        if v.get("auto"):
            others = ", ".join(f"«{a.name}» {accounts.fmt_credits(c)}" for a, c in v["others"]) or "không có tài khoản khác"
            lines.append(f"Đã bật tự chuyển tài khoản nhưng tổng credit các tài khoản ({accounts.fmt_credits(v['total'])}) vẫn không đủ. "
                         f"Các tài khoản khác: {others}.")
        else:
            others = [(a, c) for a, c in v["others"] if c is not None and c >= v["need"]]
            if others:
                lines.append("Tài khoản khác đủ credit: " + ", ".join(f"«{a.name}» ({accounts.fmt_credits(c)})" for a, c in others)
                             + ". Đổi tài khoản của dự án (chip Flow) hoặc bật tự chuyển tài khoản.")
            else:
                lines.append("Hãy nạp thêm credit, đợi đến kỳ làm mới, đổi sang tài khoản khác, hoặc giảm số scene gen.")
        return "\n".join(lines)

    def credit_gate(self, need: int, retry) -> bool:
        """Chặn việc gen nếu tài khoản đích CHẮC CHẮN không đủ credit (theo credit đã lưu, còn mới). True = được chạy tiếp.
        Credit đã lưu có thể cũ (vừa nạp thêm): có nút 'Kiểm tra lại credit' đọc lại từ Flow rồi tự chạy lại."""
        v = accounts.check_budget(need, self.project.account_id)
        if v["ok"] is not False:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Không đủ credit")
        box.setText("Không đủ credit để chạy tiến trình này.")
        box.setInformativeText(self.credit_message(v))
        b_re = box.addButton("Kiểm tra lại credit", QMessageBox.AcceptRole)
        b_auto = box.addButton("Bật tự chuyển tài khoản", QMessageBox.ActionRole) if not v["auto"] and len(v["others"]) else None
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.setDefaultButton(b_re)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_re:
            self.refresh_credits([accounts.active()], retry)
        elif b_auto is not None and clicked is b_auto:
            accounts.set_auto_switch(True)
            self.account_changed.emit(self.project.account_id)
            QTimer.singleShot(0, retry)
        return False

    def refresh_credits(self, accs: list | None = None, then=None, deep: bool = False):
        """Đọc lại credit của các tài khoản từ Flow (mở Chrome của tài khoản nếu chưa mở), lưu lại rồi gọi `then`."""
        if self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong rồi cập nhật credit.")
            return
        accs = accs or [accounts.active()]

        def job(log):
            out = []
            for a in accs:
                try:
                    with flow_auto.FlowAuto(log, acc=a) as f:
                        info = f.read_credits(deep=deep)
                except Exception as e:  # noqa: BLE001
                    log(f"«{a.name}»: không đọc được credit ({str(e)[:100]})")
                    continue
                if info:
                    accounts.save_credits(a.id, info["credits"], info.get("daily"), info.get("renew", ""), info.get("email", ""),
                                          info.get("plan_total"), info.get("daily_grant"))
                    log(f"«{a.name}»: còn {accounts.fmt_credits(info['credits'])} credit" + (f", gia hạn: {info['renew']}" if info.get("renew") else ""))
                    out.append(a.id)
            return out

        def done(res):
            self.account_changed.emit(self.project.account_id if self.project else "")
            if then:
                QTimer.singleShot(0, then)
        self.run(job, done)

    def flow_auto_run(self, mode: str = "pending"):
        if not self.need_project():
            return
        if not self.scenes:
            QMessageBox.warning(self, "Thiếu scene", "Bấm ① Tạo scene trước.")
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        todo = self.pick_todo(mode)
        if not todo:
            self.log(f"Không có scene nào để gen ({self.MODE_LABEL[mode]}).")
            return
        redo = sum(1 for s in todo if s.status == "done")
        est = credits.estimate(p.flow_model, p.flow_resolution, todo, p.flow_auto_duration, p.narration_lang)
        if not self.credit_gate(est, lambda: self.flow_auto_run(mode)):
            return
        cfg = p.flow_model + (f" {p.flow_resolution}" if p.flow_model == credits.OMNI else "")
        acc_now = accounts.get(p.account_id)
        auto = accounts.auto_switch()
        if QMessageBox.question(self, "Xác nhận trừ credit",
                                f"Sẽ tạo {len(todo)} clip ({ch.name}: {self.MODE_LABEL[mode]}) bằng {cfg} trên Google Flow.\n"
                                + (f"⚠ {redo} scene đã xong sẽ bị gen LẠI và ghi đè clip cũ.\n" if redo else "") +
                                f"Ước tính khoảng {est} credit (giá thật hiện trong nhật ký).\n"
                                f"Tài khoản Flow: «{acc_now.name}» ({accounts.describe_credits(acc_now)})"
                                + ("; tự chuyển sang tài khoản khác khi hết credit.\n" if auto and len(accounts.all_accounts()) > 1 else ".\n")
                                + (f"Gửi song song {p.flow_parallel} scene mỗi lượt (đổi trong Cài đặt dự án).\n" if p.flow_parallel > 1 and len(todo) > 1 else "") +
                                "Chrome Flow phải đã đăng nhập. Tiếp tục?") != QMessageBox.Yes:
            return
        chars = {c.name: c for c in self.chars_tab.chars}
        prev = {id(s): (s.status, s.error) for s in todo}     # trạng thái trước khi xếp hàng đợi
        self._queue_prev = dict(prev)
        for s in todo:                      # hiện ngay hàng đợi trên bảng, không đợi tới lượt
            s.status, s.error = "queued", ""
        self._prog = (0, len(todo))
        self.fill_table(self._row)
        acc_start = accounts.active()
        costs_of = lambda scs: credits.scene_costs(p.flow_model, p.flow_resolution, scs, p.flow_auto_duration, p.narration_lang)

        def job(log):
            # Làm giọng + ghép là việc chạy trên máy, không phụ thuộc Flow: cho chạy nền nhiều luồng để không chặn việc
            # gửi/nhận clip tiếp theo. (Phần điều khiển Flow vẫn một luồng cho mỗi tài khoản vì chỉ có một trình duyệt.)
            lock = threading.Lock()
            pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong")
            finished = 0
            par = max(1, p.flow_parallel)

            def save():
                with lock:
                    p.save()

            def count_done():
                nonlocal finished
                with lock:
                    finished += 1
                    self._prog = (finished, len(todo))

            def finish_work(s):
                try:
                    s.status = "raw"
                    pipeline.apply_voice(p, ch, s, log)
                    s.status = "done"
                    log(f"[{ch.name}] Scene {s.index} xong.")
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                save()
                count_done()

            def finish(s):
                pool.submit(finish_work, s)

            def run_batch(f, batch, budget):
                """Gen `batch` trên phiên Flow `f`. Trả về các scene CHƯA gửi vì không đủ credit (để chuyển tài khoản)."""
                if par > 1 and len(batch) > 1:
                    log(f"Gen song song: luôn giữ {par} scene đang render trên Flow, clip nào xong thì gửi scene kế tiếp.")
                    out_dir = lambda s: p.chapter_dir(ch) / "clips"
                    overloaded = []

                    def on_event(s, kind, msg):     # giao diện cập nhật từng scene ngay khi gửi / lỗi
                        if kind == "sent":
                            s.status, s.error = "generating", ""
                        else:
                            s.status, s.error = "error", msg
                            count_done()
                            if "quá tải" in msg:
                                overloaded.append(s)
                        save()

                    unsent = f.generate_sliding(p, ch, batch, chars, out_dir, par, on_event, finish, self._cancel, budget=budget) or []
                    if overloaded:
                        log("Flow đang quá tải: đã dừng gen các scene còn lại, hãy thử lại sau ít phút (credit của yêu cầu lỗi được Flow hoàn).")
                    elif self._cancel.is_set():
                        log("Đã dừng theo yêu cầu. Các scene còn lại giữ nguyên trạng thái trước đó.")
                    return unsent
                unsent = []
                for i, s in enumerate(batch):
                    if self._cancel.is_set():
                        log("Đã dừng theo yêu cầu. Các scene còn lại giữ nguyên trạng thái trước đó.")
                        break
                    s.status, s.error = "generating", ""
                    save()
                    try:
                        s.raw_clip = str(f.generate_scene(p, ch, s, chars, p.chapter_dir(ch) / "clips", budget))
                    except flow_auto.NoCreditError as e:
                        log(str(e))
                        s.status, s.error = "queued", ""
                        unsent = batch[i:]
                        break
                    except Exception as e:  # noqa: BLE001
                        s.status, s.error = "error", str(e)[:1500]
                        log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                        save()
                        count_done()
                        continue
                    finish(s)               # làm giọng chạy nền trong lúc Flow gen scene kế tiếp
                return unsent

            def out_of_credit(scs, why):
                for s in scs:
                    s.status, s.error = "error", why
                    count_done()
                save()

            acc, used, first, remaining = acc_start, set(), True, list(todo)
            try:
                while remaining and not self._cancel.is_set():
                    # ---- mở phiên Flow của tài khoản `acc` và đọc credit thật ----
                    try:
                        f_ctx = flow_auto.FlowAuto(log, acc=acc)
                        f = f_ctx.__enter__()
                    except Exception as e:  # noqa: BLE001
                        if first:
                            raise
                        log(f"Không dùng được tài khoản «{acc.name}» ({str(e)[:100]}), thử tài khoản khác.")
                        used.add(acc.id)
                        acc = next_account(used)
                        if acc is None:
                            break
                        continue
                    try:
                        left = None
                        try:
                            info = f.read_credits(deep=False)
                        except flow_auto.FlowError as e:
                            if first:
                                raise
                            log(f"Tài khoản «{acc.name}» chưa đăng nhập Flow: {str(e)[:100]}")
                            info = False
                        if info:
                            left = info["credits"]
                            accounts.save_credits(acc.id, left, email=info.get("email", ""))
                            log(f"Tài khoản «{acc.name}»: còn {accounts.fmt_credits(left)} credit.")
                        elif info is None:
                            log(f"Không đọc được credit của «{acc.name}»: cứ chạy, Flow sẽ báo nếu thiếu.")
                        usable = info is not False
                        if usable:
                            f.ensure_project(p, ch)
                            if first:
                                # scene lỗi do ngắt/tải thất bại có thể đã render xong trên Flow: lấy lại thay vì trả credit lần nữa
                                maybe = [s for s in todo if prev[id(s)][0] == "error"
                                         and any(k in (prev[id(s)][1] or "") for k in ("Bị ngắt", "Không tải được", "quá 15 phút", "chưa thấy clip"))]
                                if maybe:
                                    log("Kiểm tra clip đã render sẵn trên Flow trước khi gen lại (tránh trả credit trùng)...")
                                    recovered = f.sync_clips(p, ch, maybe, lambda s: p.chapter_dir(ch) / "clips")
                                    for s in recovered:
                                        try:
                                            s.status, s.error = "raw", ""
                                            pipeline.apply_voice(p, ch, s, log)
                                            s.status = "done"
                                        except Exception as e:  # noqa: BLE001
                                            s.status, s.error = "error", str(e)[:1500]
                                        p.save()
                                    finished = len(recovered)
                                    self._prog = (finished, len(todo))
                                    remaining = [x for x in remaining if x not in recovered]
                                if not remaining:
                                    break
                            take_i, rest_i = accounts.split_by_credits(costs_of(remaining), left)
                            if first and rest_i and not auto:
                                need = sum(costs_of(remaining))
                                raise flow_auto.NoCreditError(
                                    f"Không đủ credit: tài khoản «{acc.name}» còn {accounts.fmt_credits(left)}, cần khoảng {accounts.fmt_credits(need)}. "
                                    "Chưa gen gì. Nạp thêm credit, đổi tài khoản (chip Flow) hoặc bật tự chuyển tài khoản.")
                            batch = [remaining[i] for i in take_i]
                            later = [remaining[i] for i in rest_i]
                            if first and auto and (rest_i or left is None):
                                warm = [a for a in accounts.order_candidates({acc.id}) if not flow_auto._cdp_up(a.cdp_url)][:1]
                                for a in warm:                      # mở sẵn Chrome của tài khoản dự phòng để chuyển không phải chờ
                                    threading.Thread(target=lambda a=a: _quiet(flow_auto.launch_chrome, a), daemon=True).start()
                            if batch:
                                if len(batch) < len(remaining):
                                    log(f"Tài khoản «{acc.name}» đủ credit cho {len(batch)}/{len(remaining)} scene; phần còn lại sẽ chuyển tài khoản khác.")
                                unsent = run_batch(f, batch, flow_auto.Budget(left))
                            else:
                                unsent = []
                            remaining = unsent + later
                            try:                                    # cập nhật credit còn lại sau lượt gen (không bắt buộc)
                                info2 = f.read_credits(deep=False)
                                if info2:
                                    accounts.save_credits(acc.id, info2["credits"], email=info2.get("email", ""))
                            except Exception:  # noqa: BLE001
                                pass
                    finally:
                        f_ctx.__exit__(None, None, None)
                    first = False
                    if not remaining or self._cancel.is_set():
                        break
                    # ---- còn scene chưa gen vì hết credit: chuyển tài khoản (nếu bật) ----
                    used.add(acc.id)
                    nxt = next_account(used) if auto else None
                    if nxt is None:
                        why = (f"Hết credit trên tài khoản «{acc.name}»" + ("" if not auto else " và không tài khoản nào khác đủ credit")
                               + ". Nạp thêm credit/đổi tài khoản rồi gen lại các scene còn lại.")
                        log(why)
                        out_of_credit(remaining, why)
                        remaining = []
                        break
                    log(f"Chuyển tài khoản Flow: «{acc.name}» → «{nxt.name}» (còn {len(remaining)} scene).")
                    p.use_flow_account(nxt.id)
                    p.save()
                    accounts.activate(nxt.id)
                    self.account_changed.emit(nxt.id)
                    acc = nxt
            finally:
                pool.shutdown(wait=True)        # đợi các scene còn đang làm giọng/ghép

        def next_account(used):
            """Tài khoản kế tiếp để thử: nhiều credit (đã biết) trước; bỏ qua tài khoản đã biết là không đủ cho scene rẻ nhất."""
            cheapest = min(costs_of(todo) or [0])
            for a in accounts.order_candidates(used):
                c = accounts.known_credits(a)
                if c is None or c >= cheapest:
                    return a
            return None

        def _quiet(fn, *a):
            try:
                fn(*a)
            except Exception:  # noqa: BLE001
                pass

        def on_fail(msg):
            if "Không đủ credit" in msg:
                QMessageBox.warning(self, "Không đủ credit", msg)
        self._prog = (0, len(todo))
        self.run(job, lambda _: self.generation_done.emit(), cancelable=True, on_fail=on_fail)

    def flow_sync(self):
        """Đối soát với Flow, không tốn credit: (1) scene đã có clip gốc nhưng chưa xong -> tạo giọng + ghép;
        (2) scene chưa có clip -> tìm clip đã render trên Flow theo nội dung prompt (quét TẤT CẢ chương) và tải về."""
        if not self.need_project():
            return
        self.save_edits()
        p = self.project
        owner = {id(s): ch for ch, s in p.all_scenes()}
        has_raw = lambda s: bool(s.raw_clip and Path(s.raw_clip).exists())
        heal = [s for _, s in p.all_scenes() if s.status != "done" and has_raw(s)]
        missing = [s for _, s in p.all_scenes() if s.status != "done" and not has_raw(s)]
        if not heal and not missing:
            self.log("Mọi scene đều đã xong, không có gì để đồng bộ.")
            return

        def finish(s, log):
            try:
                s.status, s.error = "raw", ""
                pipeline.apply_voice(p, owner[id(s)], s, log)
                s.status = "done"
                log(f"[{owner[id(s)].name}] Scene {s.index}: xong.")
            except Exception as e:  # noqa: BLE001
                s.status, s.error = "error", str(e)[:1500]
                log(f"[{owner[id(s)].name}] Scene {s.index} lỗi: {e}")
            p.save()

        def job(log):
            if heal:
                log(f"{len(heal)} scene đã có clip nhưng chưa hoàn tất: đang tạo giọng và ghép (3 luồng song song)...")
                done_n = [0]

                def one(s):
                    if self._cancel.is_set():
                        return
                    finish(s, log)
                    done_n[0] += 1
                    self._prog = (done_n[0], len(heal))
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong") as pool:
                    list(pool.map(one, heal))
                if self._cancel.is_set():
                    log("Đã dừng theo yêu cầu.")
                    return
            if missing:
                with flow_auto.FlowAuto(log) as f:
                    by_ch: dict[str, list] = {}               # mỗi chương có project Flow riêng: đối soát lần lượt từng chương
                    for s in missing:
                        by_ch.setdefault(owner[id(s)].id, []).append(s)
                    for cid, group in by_ch.items():
                        if self._cancel.is_set():
                            break
                        c = owner[id(group[0])]
                        log(f"[{c.name}] đối soát {len(group)} scene với project Flow của chương...")
                        for s in f.sync_clips(p, c, group, lambda s: p.chapter_dir(owner[id(s)]) / "clips"):
                            finish(s, log)
                    for s in missing:  # kẹt "đang gen" mà Flow không có clip -> trả về chờ gen
                        if s.status == "generating":
                            s.status, s.error = "pending", ""
                    p.save()
            log("Đồng bộ xong.")
        self.run(job, lambda _: None, cancelable=bool(heal))

    def generate(self, mode: str = "pending"):
        """Đường Gemini API (Veo) — chỉ dùng khi có key."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        chars = {c.name: c for c in self.chars_tab.chars}
        todo = self.pick_todo(mode)
        if not todo:
            self.log(f"Không có scene nào để gen ({self.MODE_LABEL[mode]}).")
            return

        self._queue_prev = {id(s): (s.status, s.error) for s in todo}
        for s in todo:
            s.status, s.error = "queued", ""
        self.fill_table(self._row)

        def job(log):
            for k, s in enumerate(todo):
                if self._cancel.is_set():
                    log("Đã dừng theo yêu cầu.")
                    break
                self._prog = (k, len(todo))
                s.status, s.error = "generating", ""
                p.save()
                try:
                    s.raw_clip = str(veo_client.generate_clip(p, s, chars, p.chapter_dir(ch) / "clips", log))
                    pipeline.apply_voice(p, ch, s, log)
                    s.status = "done"
                    log(f"[{ch.name}] Scene {s.index} xong.")
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                p.save()
            self._prog = (len(todo), len(todo))
        self.run(job, lambda _: None, cancelable=True)

    def revoice(self, only_current: bool):
        """Đổi giọng/lời: chỉ tạo lại TTS + ghép trên clip gốc, không tốn credit Flow."""
        if not self.need_project():
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        pool = self.pick_todo("current") if only_current else ch.scenes
        todo = [s for s in pool if s.raw_clip and Path(s.raw_clip).exists()]
        if not todo:
            self.log("Chưa có clip gốc để áp dụng giọng.")
            return

        def job(log):
            for k, s in enumerate(todo):
                if self._cancel.is_set():
                    log("Đã dừng theo yêu cầu.")
                    break
                self._prog = (k, len(todo))
                try:
                    pipeline.apply_voice(p, ch, s, log)
                    s.status, s.error = "done", ""
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"Scene {s.index} lỗi: {e}")
                p.save()
            self._prog = (len(todo), len(todo))
        self.run(job, lambda _: None, cancelable=True)

    def merge_all(self):
        """Ghép các scene của CHƯƠNG đang chọn thành 1 video chương."""
        if not self.need_project():
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        clips = [Path(s.clip) for s in ch.scenes if s.status == "done" and s.clip and Path(s.clip).exists()]
        if not clips or len(clips) != len(ch.scenes):
            QMessageBox.warning(self, "Chưa đủ clip",
                                f"Cần gen xong tất cả scene của {ch.name} (trạng thái Xong) trước khi ghép.")
            return
        out = p.merged_path(ch)
        self.log(f"[{ch.name}] Đang ghép video...")

        def done(o):
            self.log(f"Xong: {o}")
            self.set_view_mode(1)
            self.preview.load(str(o), autoplay=True)
        self._keep_preview = True
        self.run(lambda log: merge(clips, out), done)

    def merge_project(self):
        """Ghép tất cả chương (theo thứ tự) thành 1 video của cả dự án."""
        if not self.need_project():
            return
        self.save_edits()
        p = self.project
        missing = [ch.name for ch in p.chapters
                   if not ch.scenes or any(not (s.status == "done" and s.clip and Path(s.clip).exists()) for s in ch.scenes)]
        if missing:
            QMessageBox.warning(self, "Chưa đủ clip", "Các chương chưa gen xong hết scene:\n- " + "\n- ".join(missing))
            return
        clips = [Path(s.clip) for _, s in p.all_scenes()]
        out = p.full_path
        self.log(f"Đang ghép toàn bộ {len(p.chapters)} chương ({len(clips)} scene)...")

        def done(o):
            self.log(f"Xong: {o}")
            self.set_view_mode(1)
            self.preview.load(str(o), autoplay=True)
        self._keep_preview = True
        self.run(lambda log: merge(clips, out), done)

    # ================= Flow thủ công =================
    def flow_export(self):
        if not self.need_project() or not self.scenes:
            return
        self.save_edits()
        chars = {c.name: c for c in self.chars_tab.chars}
        d = flow.export_all(self.project, self.chapter, chars)
        self.log(f"Đã xuất prompt + ảnh nhân vật vào {d}.")
        flow.reveal(d)

    def flow_copy(self):
        if not 0 <= self._row < len(self.scenes):
            return
        self.save_edits()
        s = self.scenes[self._row]
        chars = {c.name: c for c in self.chars_tab.chars}
        QApplication.clipboard().setText(veo_client.build_prompt(self.project, s, chars))
        flow.export_scene(self.project, self.chapter, s, chars)
        self.log(f"Đã copy prompt scene {s.index}.")

    def flow_import(self):
        if not self.need_project() or not self.scenes:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn clip tải từ Flow", "", "Video (*.mp4 *.mov)")
        if not files:
            return
        p, ch = self.project, self.chapter
        got = flow.import_clips(p, ch, files, max(self._row, 0))
        p.save()
        self.log(f"Nhập {len(got)} clip từ scene {got[0].index}. Đang thêm giọng đọc...")

        def job(log):
            for s in got:
                try:
                    pipeline.apply_voice(p, ch, s, log)
                    s.status = "done"
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"Scene {s.index} lỗi: {e}")
                p.save()
        self.run(job, lambda _: None)

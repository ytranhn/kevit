"""Tab Dự án: danh sách scene (trái) | xem video + chi tiết scene (phải) | thanh thao tác theo thứ tự bước (dưới)."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFontMetrics, QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import credits, langs, flow, flow_auto, flow_selectors, llm, pipeline, scene_ops, scene_planner, trash, tts, veo_client
from .merger import merge
from . import models
from .models import Project, nfc, safe_dirname
from . import theme
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

        self.btn = QPushButton("▶")
        self.btn.setFixedWidth(44)
        self.slider = QSlider(Qt.Horizontal)
        self.time = QLabel("0:00 / 0:00")
        self.controls = QWidget()                       # được đặt ở chân thẻ "Xem trước"
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP.s)
        row.addWidget(self.btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.controls)

        self.btn.clicked.connect(self.toggle)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.playbackStateChanged.connect(
            lambda s: self.btn.setText("⏸" if s == QMediaPlayer.PlayingState else "▶"))
        self.player.mediaStatusChanged.connect(self._on_status)
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
        self.nav_project = NavButton()
        self.nav_project.setMinimumWidth(200)
        self.nav_chapter = NavButton(with_pill=True)
        self.nav_chapter.setMinimumWidth(240)
        self.btn_prev = QPushButton("‹")
        self.btn_next = QPushButton("›")
        for b in (self.btn_prev, self.btn_next):
            b.setFixedSize(36, 36)                       # nút vuông có viền như các nút khác: nhìn là biết bấm được
            b.setProperty("arrow", True)
        self.btn_prev.clicked.connect(lambda: self.step_chapter(-1))
        self.btn_next.clicked.connect(lambda: self.step_chapter(+1))
        self.pop_project = popover_button(self.nav_project, self.build_project_pop, side="below", align="left", width=420)
        self.pop_chapter = popover_button(self.nav_chapter, self.build_chapter_pop, side="below", align="left", width=460)
        self.sync_btn = QPushButton("⟳ Đồng bộ Flow")
        self.sync_btn.clicked.connect(self.flow_sync)
        self.more = QPushButton("Khác ▾")
        self.pop_more = popover_button(self.more, self.build_more_pop, side="below", align="right", width=380)
        self.btn_settings = QPushButton("⚙ Cài đặt")
        self.btn_settings.clicked.connect(self.open_settings)
        self.progress = QLabel("")                       # giữ làm thuộc tính cũ; tiến độ hiển thị ở thanh công cụ của thẻ Scene
        sep = QLabel("›")
        sep.setProperty("caption", True)
        top = QHBoxLayout()
        top.setSpacing(SP.s)
        for w in (self.sync_btn, self.more, self.btn_settings):
            w.setFixedHeight(36)                         # cùng một chiều cao: mọi điều khiển trong hàng thẳng hàng
        steps = QHBoxLayout()                           # cặp ‹ › đi liền nhau, đứng sau nút chương
        steps.setSpacing(SP.xs)
        steps.addWidget(self.btn_prev)
        steps.addWidget(self.btn_next)
        top.addWidget(self.nav_project, 3)
        top.addWidget(sep, 0, Qt.AlignVCenter)
        top.addWidget(self.nav_chapter, 4)
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
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Scene", "Trạng thái"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Shift/⌘+click, kéo chuột, ⌘A
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.verticalHeader().setDefaultSectionSize(36)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, hh.ResizeMode.ResizeToContents)   # luôn đủ chỗ cho số 2-3 chữ số
        hh.setSectionResizeMode(1, hh.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, hh.ResizeMode.Fixed)      # cố định: bộ đếm giờ khi đang gen không làm bảng nhảy
        self.table.setColumnWidth(2, 136)
        hh.setMinimumSectionSize(46)
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
        self.manage_btn = QPushButton("Quản lý ▾")
        self.manage_btn.setFixedSize(112, 36)
        self.pop_manage = popover_button(self.manage_btn, self.build_manage_pop, side="below", align="right", width=380)
        self._busy_widgets.append(self.manage_btn)
        self.scene_page = QWidget()
        spl = QVBoxLayout(self.scene_page)
        spl.setContentsMargins(0, 0, 0, 0)
        spl.setSpacing(SP.s)
        spl.addLayout(sel_bar)
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
        b_gen_one = QPushButton("Gen lại scene này")
        b_voice_one = QPushButton("Áp dụng lại giọng")
        b_copy = QPushButton("Copy prompt")
        b_gen_one.clicked.connect(lambda: self.flow_auto_run("current"))
        b_voice_one.clicked.connect(lambda: self.revoice(only_current=True))
        b_copy.clicked.connect(self.flow_copy)
        self.b_copy_err = QPushButton("Copy lỗi")
        self.b_copy_err.setProperty("flat", True)
        self.b_copy_err.clicked.connect(self.copy_error)
        self._busy_widgets += [b_gen_one, b_voice_one]

        def caption(text: str) -> QLabel:
            c = QLabel(text)
            c.setProperty("caption", True)
            return c
        # ===== ba thẻ cùng cấu trúc: đầu thẻ (56px) / thân thẻ / chân thẻ (68px) =====
        self.preview = PreviewPanel()
        # --- thẻ 1: Scene (danh sách + truyện + bối cảnh) ---
        self.s1 = QPushButton("① Tạo scene")
        self.s2 = QPushButton("② Gen video")
        self.pop_gen = popover_button(self.s2, self.build_gen_pop, side="above", align="left", width=400)
        self.s3 = QPushButton("③ Ghép video")
        for b in (self.s1, self.s2, self.s3):
            b.setFixedHeight(36)
            b.setProperty("primary", True)
            self._busy_widgets.append(b)

        def arrow() -> QLabel:
            a = QLabel("›")
            a.setProperty("caption", True)
            a.setAlignment(Qt.AlignCenter)
            a.setFixedWidth(14)
            return a
        f1 = QHBoxLayout()
        f1.addWidget(self.s1, 3)
        f1.addWidget(arrow())
        f1.addWidget(self.s2, 4)
        f1.addWidget(arrow())
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

        def group(title: str, widget: QWidget, header_extra: QWidget | None = None) -> QWidget:
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
            gv.addLayout(head)
            gv.addWidget(widget, 1)
            return g
        dl.addWidget(group("Tiêu đề", self.d_title))
        dl.addWidget(group("Nhân vật", self.d_chars))
        dl.addWidget(group("Đoạn truyện gốc", self.d_src), 2)
        dl.addWidget(group("Thuyết minh", self.d_narr, self.narr_count), 2)
        dl.addWidget(group("Visual (prompt gửi Flow)", self.d_visual), 2)
        h2 = QHBoxLayout()
        h2.addWidget(self.d_badge)
        h2.addWidget(self.d_err, 1)
        h2.addWidget(self.b_view_err)
        h2.addWidget(self.b_copy_err)
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
        self.btn_reveal = QPushButton("Thư mục")
        self.btn_reveal.setFixedHeight(32)
        self.btn_reveal.setMinimumWidth(78)
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
        for c, w in ((card1, 440), (card2, 430), (card3, 290)):
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

        self.s1.clicked.connect(self.plan)
        self.s3.clicked.connect(self.merge_all)
        self.voices_ready.connect(self.fill_voices)
        tts.on_catalog_ready(self.voices_ready.emit)
        self.combo.currentTextChanged.connect(self.open_project)
        self.chap_combo.currentIndexChanged.connect(self.on_chapter_selected)
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
            vals = [str(s.index), s.title, label]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QBrush(QColor(color)))
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
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
        else:
            self.nav_chapter.set_title("Chưa có chương")
            self.nav_chapter.set_pill("", "info")
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
        self.b_copy_err.setVisible(False)
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
        self.b_copy_err.setVisible(bool(s.error))
        self.b_view_err.setVisible(bool(s.error))

    def update_steps(self):
        """Ba bước ①②③: làm nổi bật bước kế tiếp, hiện tiến độ, và chỉ cho bấm ③ khi đã đủ clip."""
        sc = self.scenes
        n, done = len(sc), sum(1 for s in sc if s.status == "done")
        nxt = 1 if n == 0 else (2 if done < n else 3)
        self.s1.setText("① Tạo scene" if n == 0 else "① Tạo lại")
        self.s2.setText("② Gen video" + (f"  {done}/{n}" if n else "") + "  ▾")
        self.s3.setText("③ Ghép video" if not n or done == n else f"③ Ghép · còn {n - done}")
        for i, b in ((1, self.s1), (2, self.s2), (3, self.s3)):
            want = i == nxt
            if bool(b.property("primary")) != want:
                b.setProperty("primary", want)
                repolish(b)
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
            self.empty_text.setText(f"Truyện hiện có khoảng {words} từ. Bấm nút dưới để tách thành các scene.")
            self.empty_btn.setText("① Tạo scene")
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

    def launch_flow_chrome(self):
        if not self._busy:
            self.run(lambda log: flow_auto.launch_chrome(), lambda _: self.log("Chrome Flow đã mở."))

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

    def run(self, fn, on_done, cancelable: bool = False):
        self._cancelable = cancelable
        self._cancel.clear()
        self._prog = (0, 0)
        self.set_busy(True)
        self.worker = Worker(fn)
        self.worker.log.connect(self.log)
        self.worker.failed.connect(lambda e: self.log(f"LỖI: {e}"))
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
        cfg = p.flow_model + (f" {p.flow_resolution}" if p.flow_model == credits.OMNI else "")
        if QMessageBox.question(self, "Xác nhận trừ credit",
                                f"Sẽ tạo {len(todo)} clip ({ch.name}: {self.MODE_LABEL[mode]}) bằng {cfg} trên Google Flow.\n"
                                + (f"⚠ {redo} scene đã xong sẽ bị gen LẠI và ghi đè clip cũ.\n" if redo else "") +
                                f"Ước tính khoảng {est} credit (giá thật hiện trong nhật ký).\n"
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

        def job(log):
            with flow_auto.FlowAuto(log) as f:
                f.ensure_project(p, ch)
                # scene lỗi do ngắt/tải thất bại có thể đã render xong trên Flow: lấy lại thay vì trả credit lần nữa
                maybe = [s for s in todo if prev[id(s)][0] == "error"
                         and any(k in (prev[id(s)][1] or "") for k in ("Bị ngắt", "Không tải được", "quá 15 phút", "chưa thấy clip"))]
                recovered = []
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
                rest = [x for x in todo if x not in recovered]
                par = max(1, p.flow_parallel)
                # Làm giọng + ghép là việc chạy trên máy, không phụ thuộc Flow: cho chạy nền nhiều luồng để không chặn việc
                # gửi/nhận clip tiếp theo. (Phần điều khiển Flow vẫn một luồng vì chỉ có một trình duyệt.)
                lock = threading.Lock()
                pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong")

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

                try:
                    if par > 1 and len(rest) > 1:
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

                        f.generate_sliding(p, ch, rest, chars, out_dir, par, on_event, finish, self._cancel)
                        if overloaded:
                            log("Flow đang quá tải: đã dừng gen các scene còn lại, hãy thử lại sau ít phút (credit của yêu cầu lỗi được Flow hoàn).")
                        elif self._cancel.is_set():
                            log("Đã dừng theo yêu cầu. Các scene còn lại giữ nguyên trạng thái trước đó.")
                    else:
                        for s in rest:
                            if self._cancel.is_set():
                                log("Đã dừng theo yêu cầu. Các scene còn lại giữ nguyên trạng thái trước đó.")
                                break
                            s.status, s.error = "generating", ""
                            save()
                            try:
                                s.raw_clip = str(f.generate_scene(p, ch, s, chars, p.chapter_dir(ch) / "clips"))
                            except Exception as e:  # noqa: BLE001
                                s.status, s.error = "error", str(e)[:1500]
                                log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                                save()
                                count_done()
                                continue
                            finish(s)               # làm giọng chạy nền trong lúc Flow gen scene kế tiếp
                finally:
                    pool.shutdown(wait=True)        # đợi các scene còn đang làm giọng/ghép
        self._prog = (0, len(todo))
        self.run(job, lambda _: None, cancelable=True)

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

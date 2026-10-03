"""Thành phần giao diện dùng riêng cho scene_widgets."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt, QUrl
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QStyle, QStyleOptionViewItem, QApplication, QHBoxLayout, QLabel, QPushButton, QSlider, QStackedWidget, QStyledItemDelegate,
    QVBoxLayout, QWidget)

from .. import icons, theme
from ..theme import SP


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
            if selected and c == QColor(theme.T["faint"]):          # chữ xám mờ chìm vào nền dòng đang chọn
                c = QColor(theme.T["text"])
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

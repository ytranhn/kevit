"""Thanh trạng thái, nút phân đoạn, ngăn xếp phân đoạn."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QWidget

from .. import icons
from ..theme import SP
from .basic import repolish, ElidedLabel


# ---------------------------------------------------------------- thanh trạng thái
class StatusStrip(QWidget):
    """Thanh dưới cùng: hoạt động mới nhất (đỏ khi lỗi), tiến độ + nút dừng khi đang chạy, chip môi trường, nút mở nhật ký."""
    stop_clicked = Signal()
    log_toggled = Signal()
    chip_clicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(34)
        self.activity = ElidedLabel()                    # chỉ hiển thị; KHÔNG bắt chuột (nhật ký chỉ mở bằng nút "Nhật ký")
        self.activity.setProperty("level", "info")
        self.activity.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bar = QProgressBar()
        self.bar.setFixedSize(160, 8)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.stop = QPushButton("Dừng sau scene này")
        self.stop.setProperty("flat", True)
        self.stop.clicked.connect(self.stop_clicked.emit)
        self.stop.hide()
        self.chips: dict[str, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP.s)
        row.addWidget(self.activity, 1)
        row.addWidget(self.bar)
        row.addWidget(self.stop)
        for key in ("llm", "flow", "voice"):
            b = QPushButton("")
            b.setProperty("chipbtn", True)
            b.clicked.connect(lambda _=False, k=key: self.chip_clicked.emit(k))
            self.chips[key] = b
            row.addWidget(b)
        self.log_btn = QPushButton("Nhật ký")
        icons.attach(self.log_btn, "up", 16)
        self.log_btn.setLayoutDirection(Qt.RightToLeft)
        self.log_btn.setProperty("flat", True)
        self.log_btn.clicked.connect(self.log_toggled.emit)
        row.addWidget(self.log_btn)

    def set_activity(self, text: str, level: str = "info") -> None:
        self.activity.set_full(text)
        if self.activity.property("level") != level:
            self.activity.setProperty("level", level)
            repolish(self.activity)

    def set_busy(self, busy: bool, cancelable: bool = False) -> None:
        self.bar.setVisible(busy)
        self.stop.setVisible(busy and cancelable)
        if busy:
            self.stop.setEnabled(True)
            self.stop.setText("Dừng sau scene này")
            self.bar.setRange(0, 0)           # chạy không xác định cho tới khi biết tiến độ

    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        else:
            self.bar.setRange(0, 0)

    def set_chip(self, key: str, text: str, ok: bool) -> None:
        b = self.chips[key]
        b.setText(text)
        state = "ok" if ok else "warn"
        if b.property("state") != state:
            b.setProperty("state", state)
            repolish(b)

    def set_log_open(self, is_open: bool) -> None:
        icons.attach(self.log_btn, "down" if is_open else "up", 16)


# ---------------------------------------------------------------- điều khiển phân đoạn
class Segmented(QFrame):
    """Nhóm nút chọn 1 trong nhiều (thay QComboBox cho lựa chọn ngắn). Có API giống QComboBox để dùng thay thế."""
    currentIndexChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setProperty("seg", True)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(SP.xs, SP.xs, SP.xs, SP.xs)
        self._row.setSpacing(SP.xs)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: list[QPushButton] = []
        self._data: list = []
        self._idx = -1
        self._group.idClicked.connect(self._clicked)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def addItem(self, text: str, data=None) -> None:
        b = QPushButton(text)
        b.setCheckable(True)
        b.setProperty("segbtn", True)
        b.setCursor(Qt.PointingHandCursor)
        self._group.addButton(b, len(self._btns))
        self._btns.append(b)
        self._data.append(data if data is not None else text)
        self._row.addWidget(b)
        if self._idx < 0:
            self.setCurrentIndex(0)

    def addItems(self, texts) -> None:
        for x in texts:
            self.addItem(x)

    def _clicked(self, i: int) -> None:
        if i != self._idx:
            self._idx = i
            self.currentIndexChanged.emit(i)

    def setCurrentIndex(self, i: int) -> None:
        if not 0 <= i < len(self._btns):
            return
        self._btns[i].setChecked(True)
        if i != self._idx:
            self._idx = i
            self.currentIndexChanged.emit(i)

    def currentIndex(self) -> int:
        return self._idx

    def currentText(self) -> str:
        return self._btns[self._idx].text() if self._idx >= 0 else ""

    def currentData(self):
        return self._data[self._idx] if self._idx >= 0 else None

    def setCurrentText(self, text: str) -> None:
        for i, b in enumerate(self._btns):
            if b.text() == text:
                self.setCurrentIndex(i)
                return

    def findData(self, data) -> int:
        return self._data.index(data) if data in self._data else -1

    def setItemEnabled(self, i: int, enabled: bool) -> None:
        self._btns[i].setEnabled(enabled)


class SegStack:
    """Thay QTabWidget: nút phân đoạn (seg) đặt ở đầu thẻ, các trang (stack) ở thân thẻ. API giống QTabWidget."""

    def __init__(self):
        self.seg = Segmented()
        self.stack = QStackedWidget()
        self.seg.currentIndexChanged.connect(self.stack.setCurrentIndex)

    def addTab(self, widget: QWidget, title: str) -> None:
        self.stack.addWidget(widget)
        self.seg.addItem(title)

    def setCurrentWidget(self, widget: QWidget) -> None:
        i = self.stack.indexOf(widget)
        if i >= 0:
            self.seg.setCurrentIndex(i)
            self.stack.setCurrentIndex(i)

    def currentWidget(self) -> QWidget:
        return self.stack.currentWidget()

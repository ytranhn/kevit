"""Hộp thoại 'Hẹn giờ đăng': đặt giờ cho từng video, hoặc dùng quy tắc giờ cố định (mỗi lần đăng 1 video theo thứ tự) rồi chỉnh từng dòng."""
from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QLocale, QTime, Qt
from PySide6.QtWidgets import (QAbstractItemView, QDateEdit, QDateTimeEdit, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget)

from . import icons
from .publish import schedule
from .shell import AdaptiveRow
from .theme import SP
from .widgets import repolish

DISPLAY = "ddd dd/MM/yyyy  HH:mm"
VI = QLocale(QLocale.Vietnamese, QLocale.Vietnam)          # tên thứ trong tuần bằng tiếng Việt
DEFAULT_TIME = QTime(20, 0)
MIN_LEAD_MIN = 1          # giờ hẹn phải cách hiện tại ít nhất 1 phút


def _qdt(dt: datetime) -> QDateTime:
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute))


def _py(q: QDateTime) -> datetime:
    d, t = q.date(), q.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())


def ask_datetime(parent, title: str, current: datetime, now=datetime.now) -> datetime | None:
    """Hộp thoại nhỏ chọn một thời điểm (đổi giờ của một lượt trong lịch)."""
    d = QDialog(parent)
    d.setWindowTitle(title)
    ed = QDateTimeEdit(_qdt(max(current, now() + timedelta(minutes=5))))
    ed.setCalendarPopup(True)
    ed.setLocale(VI)
    ed.setDisplayFormat(DISPLAY)
    ok, cancel = QPushButton("Lưu giờ mới"), QPushButton("Huỷ")
    ok.setProperty("primary", True)
    ok.clicked.connect(d.accept)
    cancel.clicked.connect(d.reject)
    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(cancel)
    row.addWidget(ok)
    v = QVBoxLayout(d)
    v.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.l)
    v.setSpacing(SP.l)
    v.addWidget(QLabel(title))
    v.addWidget(ed)
    v.addLayout(row)
    d.setMinimumWidth(380)
    return _py(ed.dateTime()) if d.exec() == QDialog.Accepted else None


class ScheduleDialog(QDialog):
    """items: [(key, nhãn video)] theo thứ tự đăng. summary: mô tả tài khoản/chế độ để người dùng soát lại trước khi hẹn."""

    def __init__(self, parent, items: list[tuple[str, str]], summary: str, now=datetime.now):
        super().__init__(parent)
        self.now = now
        self.items = list(items)
        self.setWindowTitle("Hẹn giờ đăng")
        self.setMinimumSize(720, 560)
        self.resize(820, 680)

        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(44, 44)
        tile.setAlignment(Qt.AlignCenter)
        icons.attach(tile, "clock", 24)
        title = QLabel("Hẹn giờ đăng")
        title.setProperty("heading", True)
        sub = QLabel(summary)
        sub.setProperty("caption", True)
        sub.setWordWrap(True)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title)
        col.addWidget(sub)
        head = QHBoxLayout()
        head.setSpacing(SP.m)
        head.addWidget(tile)
        head.addLayout(col, 1)

        # ---- quy tắc: giờ cố định, mỗi lần đăng 1 video theo thứ tự ----
        n = self.now()
        start = n.date() if n.time() < datetime(2000, 1, 1, 19, 55).time() else n.date() + timedelta(days=1)
        self.start_date = QDateEdit(QDate(start.year, start.month, start.day))
        self.start_date.setCalendarPopup(True)
        self.start_date.setLocale(VI)
        self.start_date.setDisplayFormat("dd/MM/yyyy")
        self.start_time = QTimeEdit(DEFAULT_TIME)
        self.start_time.setDisplayFormat("HH:mm")
        self.every = QSpinBox()
        self.every.setRange(1, 30)
        self.every.setSuffix(" ngày")
        self.days: list[QPushButton] = []
        drow = QHBoxLayout()
        drow.setSpacing(SP.xs)
        for i, lab in enumerate(schedule.WEEKDAY_LABELS):
            b = QPushButton(lab)
            b.setCheckable(True)
            b.setChecked(True)
            b.setProperty("platab", True)              # kiểu nút bật/tắt có nền màu khi được chọn
            b.setFixedWidth(58)
            drow.addWidget(b)
            self.days.append(b)
        drow.addStretch(1)
        apply_btn = QPushButton("Xếp giờ theo quy tắc")
        apply_btn.setProperty("primary", True)
        apply_btn.clicked.connect(self.apply_rule)

        def labeled(text: str, w: QWidget) -> QVBoxLayout:
            v = QVBoxLayout()
            v.setSpacing(SP.xs)
            v.addWidget(QLabel(text))
            v.addWidget(w)
            v.addStretch(1)                       # các cột canh trên cùng, không lệch hàng khi ô nhập cao thấp khác nhau
            return v
        top = AdaptiveRow(560, SP.m)
        top.addLayout(labeled("Ngày bắt đầu", self.start_date), 1)
        top.addLayout(labeled("Giờ đăng", self.start_time), 1)
        top.addLayout(labeled("Lặp lại mỗi", self.every), 1)
        rule = QFrame()
        rule.setProperty("card", True)
        rv = QVBoxLayout(rule)
        rv.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
        rv.setSpacing(SP.s)
        t = QLabel("Quy tắc giờ cố định")
        t.setProperty("subheading", True)
        rv.addWidget(t)
        cap = QLabel("Mỗi lần đến giờ, Kevit đăng 1 video, theo thứ tự trong danh sách bên dưới. Bỏ chọn những thứ không muốn đăng.")
        cap.setProperty("caption", True)
        cap.setWordWrap(True)
        rv.addWidget(cap)
        rv.addWidget(top)
        rv.addLayout(drow)
        rr = QHBoxLayout()
        rr.addStretch(1)
        rr.addWidget(apply_btn)
        rv.addLayout(rr)

        # ---- danh sách video + giờ ----
        self.table = QTableWidget(len(self.items), 3)
        self.table.setHorizontalHeaderLabels(["#", "Video", "Giờ đăng"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 250)
        self.editors: list[QDateTimeEdit] = []
        for r in range(len(self.items)):
            ed = QDateTimeEdit()
            ed.setCalendarPopup(True)
            ed.setLocale(VI)
            ed.setDisplayFormat(DISPLAY)
            ed.dateTimeChanged.connect(self.validate)
            self.table.setCellWidget(r, 2, ed)
            self.editors.append(ed)
            self.table.setRowHeight(r, 46)
        self.fill_labels()
        up, down = QPushButton("Lên"), QPushButton("Xuống")
        icons.attach(up, "up", 16)
        icons.attach(down, "down", 16)
        up.clicked.connect(lambda: self.move(-1))
        down.clicked.connect(lambda: self.move(1))
        mv = QHBoxLayout()
        mv.addWidget(QLabel("Đổi thứ tự đăng:"))
        mv.addWidget(up)
        mv.addWidget(down)
        mv.addStretch(1)

        self.warn = QLabel("")
        self.warn.setProperty("statusline", True)
        self.warn.setProperty("kind", "warn")
        self.warn.setWordWrap(True)
        self.warn.hide()
        note = QLabel("Kevit phải đang mở và máy không ngủ đúng giờ hẹn thì mới đăng được. Quá 30 phút sau giờ hẹn mà Kevit chưa chạy thì sẽ "
                      "không tự đăng bù, bạn sẽ được chọn đăng ngay, dời giờ hay huỷ. Video chưa ghép sẽ được ghép lúc đăng.")
        note.setProperty("caption", True)
        note.setWordWrap(True)

        self.ok = QPushButton()
        self.ok.setProperty("primary", True)
        cancel = QPushButton("Huỷ")
        for b in (self.ok, cancel):
            b.setFixedHeight(40)
        cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.l)
        lay.setSpacing(SP.m)
        lay.addLayout(head)
        lay.addWidget(rule)
        lay.addWidget(self.table, 1)
        lay.addLayout(mv)
        lay.addWidget(self.warn)
        lay.addWidget(note)
        lay.addLayout(foot)
        self.apply_rule()

    # ---- nội dung bảng ----
    def fill_labels(self) -> None:
        for r, (_, label) in enumerate(self.items):
            for c, text in ((0, str(r + 1)), (1, label)):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                self.table.setItem(r, c, it)

    def move(self, d: int) -> None:
        r = self.table.currentRow()
        j = r + d
        if r < 0 or not 0 <= j < len(self.items):
            return
        self.items[r], self.items[j] = self.items[j], self.items[r]       # đổi video, giữ nguyên các mốc giờ
        self.fill_labels()
        self.table.selectRow(j)

    def weekdays(self) -> set[int]:
        return {i for i, b in enumerate(self.days) if b.isChecked()}

    def apply_rule(self) -> None:
        wd = self.weekdays()
        if not wd:
            self.warn_text("Hãy chọn ít nhất một thứ trong tuần.")
            return
        d, t = self.start_date.date(), self.start_time.time()
        start = datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())
        times = schedule.occurrences(start, len(self.items), self.every.value(), None if len(wd) == 7 else wd)
        for ed, at in zip(self.editors, times):
            ed.blockSignals(True)
            ed.setDateTime(_qdt(at))
            ed.blockSignals(False)
        self.validate()

    def warn_text(self, text: str) -> None:
        self.warn.setText(text)
        self.warn.setVisible(bool(text))
        repolish(self.warn)

    def validate(self, *_) -> None:
        limit = self.now() + timedelta(minutes=MIN_LEAD_MIN)
        past = [r + 1 for r, ed in enumerate(self.editors) if _py(ed.dateTime()) < limit]
        self.warn_text(("Dòng " + ", ".join(map(str, past)) + " có giờ đã qua hoặc quá gần hiện tại. Hãy chọn giờ trong tương lai.") if past else "")
        self.ok.setEnabled(not past and bool(self.items))
        self.ok.setText(f"Hẹn giờ {len(self.items)} video")

    def result(self) -> list[tuple[str, datetime]]:
        return [(key, _py(ed.dateTime())) for (key, _), ed in zip(self.items, self.editors)]

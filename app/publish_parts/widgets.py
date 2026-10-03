"""Thành phần giao diện dùng riêng cho widgets."""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLayout, QMenu, QPushButton, QVBoxLayout, QWidget

from .. import icons, publish, theme
from ..shell import platform_tile
from ..theme import SP
from ..widgets import ElidedLabel, repolish


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
        menu.addSeparator()
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

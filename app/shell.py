"""Khối giao diện dùng chung cho khung ứng dụng mới: thanh điều hướng bên, tiêu đề trang, ô logo, banner thông tin, hàng nhà cung cấp."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import icons
from .theme import SP
from .widgets import ElidedLabel, repolish

# màu ô logo theo loại nhà cung cấp / tên (không dùng logo thương hiệu: ô chữ cái màu)
LOGO_COLORS = {"claude": "#D97757", "gemini": "#4F7BF2", "openai": "#10A37F", "default": "#6B6FF2"}


def logo_tile(letter: str, kind: str = "default", size: int = 40) -> QLabel:
    t = QLabel((letter or "?")[:1].upper())
    t.setProperty("logotile", True)
    t.setAlignment(Qt.AlignCenter)
    t.setFixedSize(size, size)
    t.setStyleSheet(f"background: {LOGO_COLORS.get(kind, LOGO_COLORS['default'])};")
    return t


# logo nền tảng đăng video: ô bo góc màu thương hiệu + ký hiệu (không dùng logo thật)
PLATFORM_BRAND = {"youtube": ("▶", "#E62117"), "tiktok": ("♪", "#111111"), "facebook": ("f", "#1877F2"), "instagram": ("◎", "#C13584")}


def platform_pixmap(key: str, size: int = 32):
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    glyph, color = PLATFORM_BRAND.get(key, ("?", "#6B6FF2"))
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(0, 0, size, size, size * 0.28, size * 0.28)
    f = QFont(p.font())
    f.setBold(True)
    f.setPixelSize(int(size * 0.52))
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, glyph)
    p.end()
    return pm


def platform_tile(key: str, size: int = 32) -> QLabel:
    t = QLabel()
    t.setFixedSize(size, size)
    t.setPixmap(platform_pixmap(key, size))
    t.setStyleSheet("background: transparent;")
    return t


class NavItem(QPushButton):
    """Một mục điều hướng: ô icon + tiêu đề + mô tả ngắn. `text()` trả về tiêu đề (các nút này không vẽ chữ của chính nó)."""

    def __init__(self, title: str, sub: str, icon: str):
        super().__init__()
        self._title = title
        self.setProperty("sidenav", True)
        self.setCheckable(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(60)
        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(40, 40)
        tile.setAlignment(Qt.AlignCenter)
        tile.setAttribute(Qt.WA_TransparentForMouseEvents)
        icons.attach(tile, icon, 22)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        t.setAttribute(Qt.WA_TransparentForMouseEvents)
        s = ElidedLabel()
        s.setProperty("caption", True)
        s.set_full(sub)
        s.setAttribute(Qt.WA_TransparentForMouseEvents)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        col.addWidget(t)
        col.addWidget(s)
        col.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 0, SP.s, 0)
        row.setSpacing(SP.m)
        row.addWidget(tile)
        row.addLayout(col, 1)
        self.tile = tile

    def text(self) -> str:                      # noqa: D401 - giữ API của QPushButton cho mã/kiểm thử cũ
        return self._title

    def set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        repolish(self)


class SideNav(QFrame):
    """Thanh điều hướng bên: tiêu đề + danh sách mục. API gần giống Segmented (addItem/currentIndex/findData/currentIndexChanged)."""
    currentIndexChanged = Signal(int)

    def __init__(self, title: str, sub: str):
        super().__init__()
        self.setProperty("sidebar", True)
        self.setFixedWidth(300)
        self._btns: list[NavItem] = []
        self._data: list = []
        self._cur = -1
        head = QLabel(title)
        head.setProperty("heading", True)
        cap = QLabel(sub)
        cap.setProperty("caption", True)
        cap.setWordWrap(True)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(SP.l, SP.xl, SP.l, SP.l)
        self.col.setSpacing(SP.s)
        self.col.addWidget(head)
        self.col.addWidget(cap)
        self.col.addSpacing(SP.m)
        self.col.addStretch(1)

    def addItem(self, title: str, data, sub: str = "", icon: str = "gear") -> None:
        b = NavItem(title, sub, icon)
        i = len(self._btns)
        b.clicked.connect(lambda _=False, k=i: self.setCurrentIndex(k))
        self.col.insertWidget(self.col.count() - 1, b)
        self._btns.append(b)
        self._data.append(data)

    def currentIndex(self) -> int:
        return self._cur

    def setCurrentIndex(self, i: int) -> None:
        if not 0 <= i < len(self._btns) or i == self._cur:
            return
        self._cur = i
        for k, b in enumerate(self._btns):
            b.set_active(k == i)
        self.currentIndexChanged.emit(i)

    def findData(self, data) -> int:
        return self._data.index(data) if data in self._data else -1


class PageHeader(QWidget):
    """Tiêu đề trang lớn + mô tả, nút hành động chính ở bên phải."""

    def __init__(self, title: str, desc: str, *actions: QWidget):
        super().__init__()
        t = QLabel(title)
        t.setProperty("pagetitle", True)
        d = QLabel(desc)
        d.setProperty("caption", True)
        d.setWordWrap(True)
        col = QVBoxLayout()
        col.setSpacing(SP.xs)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(t)
        col.addWidget(d)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP.l)
        row.addLayout(col, 1)
        for a in actions:
            row.addWidget(a, 0, Qt.AlignTop)


def info_banner(icon: str, title: str, text: str) -> QFrame:
    f = QFrame()
    f.setProperty("banner", True)
    tile = QLabel()
    tile.setProperty("navtile", True)
    tile.setFixedSize(40, 40)
    tile.setAlignment(Qt.AlignCenter)
    icons.attach(tile, icon, 22)
    t = QLabel(title)
    t.setStyleSheet("font-weight: 600; background: transparent;")
    d = QLabel(text)
    d.setProperty("caption", True)
    d.setWordWrap(True)
    col = QVBoxLayout()
    col.setSpacing(0)
    col.addWidget(t)
    col.addWidget(d)
    row = QHBoxLayout(f)
    row.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
    row.setSpacing(SP.m)
    row.addWidget(tile, 0, Qt.AlignTop)
    row.addLayout(col, 1)
    return f


class ProviderRow(QFrame):
    """Một mô hình trong danh sách: radio (đang dùng) · logo · tên · loại·model · nhãn 'Đang dùng' · nút 'Dùng mô hình' · menu '···'."""
    selected = Signal()
    use = Signal()
    duplicate = Signal()
    delete = Signal()

    def __init__(self, name: str, kind: str, sub: str, active: bool, can_delete: bool):
        super().__init__()
        self.setProperty("provrow", True)
        self.setFixedHeight(68)
        from PySide6.QtWidgets import QMenu, QRadioButton
        self.radio = QRadioButton()
        self.radio.setAutoExclusive(False)
        self.radio.setChecked(active)
        self.radio.setToolTip("Mô hình đang dùng")
        self.radio.toggled.connect(lambda on: on and not active and self.use.emit())
        logo = logo_tile(name, kind)
        n = QLabel(name)
        n.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        s = ElidedLabel()
        s.setProperty("caption", True)
        s.set_full(sub)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        col.addWidget(n)
        col.addWidget(s)
        col.addStretch(1)
        self.pill = QLabel("Đang dùng")
        self.pill.setProperty("pill", "ok")
        self.pill.setVisible(active)
        self.btn_use = QPushButton("Dùng mô hình")
        self.btn_use.setFixedHeight(36)
        self.btn_use.setVisible(not active)
        self.btn_use.clicked.connect(self.use.emit)
        self.btn_more = QPushButton()
        self.btn_more.setProperty("iconbtn", True)
        icons.attach(self.btn_more, "more", 20)
        menu = QMenu(self.btn_more)
        menu.addAction("Nhân bản", self.duplicate.emit)
        act = menu.addAction("Xoá", self.delete.emit)
        act.setEnabled(can_delete)
        self.btn_more.setMenu(menu)
        self.btn_more.setToolTip("Thêm thao tác")
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.l, 0, SP.m, 0)
        row.setSpacing(SP.m)
        row.addWidget(self.radio, 0, Qt.AlignVCenter)
        row.addWidget(logo, 0, Qt.AlignVCenter)
        row.addLayout(col, 1)
        row.addWidget(self.pill, 0, Qt.AlignVCenter)
        row.addWidget(self.btn_use, 0, Qt.AlignVCenter)
        row.addWidget(self.btn_more, 0, Qt.AlignVCenter)

    def mousePressEvent(self, e):
        self.selected.emit()
        super().mousePressEvent(e)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        repolish(self)


class TopBar(QFrame):
    """Thanh trên cùng: logo + tên ứng dụng, các tab (icon + chữ, gạch chân tab đang mở) và chip tài khoản Flow ở bên phải."""
    tab_clicked = Signal(int)
    account_clicked = Signal()

    def __init__(self, logo_path, titles: list[tuple[str, str]]):
        super().__init__()
        self.setProperty("topbar", True)
        self.setFixedHeight(56)
        logo = QLabel()
        from PySide6.QtGui import QPixmap
        pm = QPixmap(str(logo_path))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        name = QLabel("Kevit")
        name.setStyleSheet("font-weight: 700; font-size: 16px; background: transparent;")
        self.tabs: list[QPushButton] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.xl, 0, SP.l, 0)
        row.setSpacing(SP.s)
        row.addWidget(logo)
        row.addWidget(name)
        row.addSpacing(SP.xl)
        for i, (label, icon) in enumerate(titles):
            b = QPushButton(label)
            b.setProperty("topnav", True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(56)
            icons.attach(b, icon, 20, role="muted")
            b.clicked.connect(lambda _=False, k=i: self.tab_clicked.emit(k))
            row.addWidget(b)
            row.addSpacing(SP.m)
            self.tabs.append(b)
        row.addStretch(1)
        self.avatar = QLabel("K")
        self.avatar.setProperty("avatar2", True)
        self.avatar.setFixedSize(30, 30)
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.acct_name = QLabel("")
        self.acct_name.setStyleSheet("background: transparent;")
        self.acct_name.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pill = QPushButton()
        self.pill.setProperty("acctpill", True)
        self.pill.setCursor(Qt.PointingHandCursor)
        self.pill.setToolTip("Tài khoản Google Flow của dự án: bấm để chọn, xem credit")
        self.pill.clicked.connect(self.account_clicked.emit)
        caret = QLabel()
        icons.attach(caret, "down", 16, role="muted")
        caret.setAttribute(Qt.WA_TransparentForMouseEvents)
        pr = QHBoxLayout(self.pill)
        pr.setContentsMargins(4, 0, SP.m, 0)
        pr.setSpacing(SP.s)
        pr.addWidget(self.avatar)
        pr.addWidget(self.acct_name)
        pr.addWidget(caret)
        row.addWidget(self.pill)

    def set_current(self, i: int) -> None:
        for k, b in enumerate(self.tabs):
            b.setProperty("active", k == i)
            repolish(b)

    def set_account(self, name: str) -> None:
        self.acct_name.setText(name)
        self.avatar.setText((name or "?").strip()[:1].upper() or "?")
        self.pill.setFixedWidth(max(120, self.acct_name.fontMetrics().horizontalAdvance(name) + 90))


class CharRow(QFrame):
    """Một nhân vật trong danh sách: số thứ tự, ảnh tròn, tên + vai trò, menu '···'."""
    selected = Signal()
    action = Signal(str)

    def __init__(self, index: int, name: str, role: str, pix, has_image: bool):
        super().__init__()
        self.setProperty("provrow", True)
        self.setFixedHeight(68)
        self.name_text = name
        num = QLabel(str(index))
        num.setProperty("navtile", True)
        num.setAlignment(Qt.AlignCenter)
        num.setFixedSize(34, 34)
        num.setStyleSheet("font-weight: 600;")
        pic = QLabel()
        pic.setFixedSize(46, 46)
        if pix is not None:
            pic.setPixmap(pix)
        else:                                       # chưa có ảnh: avatar chữ cái đầu của tên
            pic.setText((name or "?").strip()[:1].upper())
            pic.setAlignment(Qt.AlignCenter)
            color = LOGO_COLORS["default"] if not name else list(LOGO_COLORS.values())[sum(map(ord, name)) % len(LOGO_COLORS)]
            pic.setStyleSheet(f"background: {color}; color: #FFFFFF; border-radius: 23px; font-weight: 700; font-size: 18px;")
        n = ElidedLabel()
        n.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        n.set_full(name)
        r = ElidedLabel()
        r.setProperty("caption", True)
        r.set_full(role or ("Chưa có vai trò" if has_image else "Chưa có ảnh"))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        col.addWidget(n)
        col.addWidget(r)
        col.addStretch(1)
        from PySide6.QtWidgets import QMenu
        more = QPushButton()
        more.setProperty("iconbtn", True)
        icons.attach(more, "more", 20)
        menu = QMenu(more)
        menu.addAction("Gen lại ảnh (AI)…", lambda: self.action.emit("ai"))
        menu.addAction("Chọn ảnh…", lambda: self.action.emit("image"))
        menu.addSeparator()
        menu.addAction("Xoá", lambda: self.action.emit("delete"))
        more.setMenu(menu)
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 0, SP.s, 0)
        row.setSpacing(SP.m)
        row.addWidget(num, 0, Qt.AlignVCenter)
        row.addWidget(pic, 0, Qt.AlignVCenter)
        row.addLayout(col, 1)
        row.addWidget(more, 0, Qt.AlignVCenter)

    def mousePressEvent(self, e):
        self.selected.emit()
        super().mousePressEvent(e)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        repolish(self)

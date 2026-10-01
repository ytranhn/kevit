"""Thành phần giao diện dùng chung: avatar nhân vật, chọn nhân vật có ảnh, thanh trạng thái, nhãn cắt chữ."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QStyledItemDelegate, QVBoxLayout, QWidget)

from .models import nfc
from .theme import SP
from .scene_planner import _fold


def repolish(w: QWidget) -> None:
    """Áp lại stylesheet sau khi đổi property (Qt không tự làm)."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


_AV: dict[tuple, QPixmap] = {}


def avatar(path: str, size: int) -> QPixmap:
    """Ảnh tròn lấy từ phần ĐẦU ảnh chân dung (mặt nằm ở phía trên), có bộ nhớ đệm theo (đường dẫn, kích thước, mtime)."""
    p = Path(path) if path else None
    mt = p.stat().st_mtime if p and p.exists() else 0
    key = (path, size, mt)
    if key in _AV:
        return _AV[key]
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    pm = QPixmap(path) if mt else QPixmap()
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    painter.setClipPath(clip)
    if pm.isNull():
        painter.fillRect(0, 0, size, size, QColor(128, 128, 128, 110))
    else:
        sc = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter.drawPixmap(0, 0, sc, (sc.width() - size) // 2, 0, size, size)
    painter.end()
    _AV[key] = out
    return out


def rounded_pixmap(path: str, size: int, radius: int = 16) -> QPixmap:
    """Ảnh vuông bo góc, lấy phần ĐẦU ảnh chân dung (khuôn mặt)."""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    pm = QPixmap(path) if path and Path(path).exists() else QPixmap()
    if pm.isNull():
        return out
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(clip)
    sc = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    painter.drawPixmap(0, 0, sc, (sc.width() - size) // 2, 0, size, size)
    painter.end()
    return out


class ElidedLabel(QLabel):
    """Nhãn 1 dòng, tự cắt '…' theo bề rộng: nội dung dài không bao giờ làm đổi chiều cao (tránh lệch bố cục)."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def set_full(self, text: str):
        self._full = " ".join(text.split())
        self._refit()

    def _refit(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width() - 4, 10)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def minimumSizeHint(self):
        return QSize(10, super().minimumSizeHint().height())


# ---------------------------------------------------------------- chọn nhân vật có ảnh
class CharPickDialog(QDialog):
    MAX = 3

    def __init__(self, parent, chars, selected: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Chọn nhân vật cho scene")
        self.resize(460, 560)
        self.chars = chars
        self.order = [n for n in selected]
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm theo tên, vai trò, tên Hán...")
        self.list = QListWidget()
        self.list.setIconSize(QSize(44, 44))
        self.list.setSpacing(SP.xs)
        self.count = QLabel("")
        self.count.setProperty("caption", True)
        sel = {nfc(n) for n in selected}
        for c in chars:
            it = QListWidgetItem(avatar(c.image, 44), c.name + (f"  ·  {c.role}" if c.role else ""))
            it.setData(Qt.UserRole, c.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if nfc(c.name) in sel else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(self._changed)
        self.search.textChanged.connect(self._filter)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Chọn")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(QLabel(f"Tối đa {self.MAX} nhân vật (Flow nhận tối đa 3 ảnh tham chiếu). Người đầu tiên quan trọng nhất."))
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.count)
        lay.addWidget(bb)
        self._update()

    def _checked(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _changed(self, item: QListWidgetItem):
        name = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            if len(self._checked()) > self.MAX:           # quá giới hạn: bỏ tick mục vừa chọn
                self.list.blockSignals(True)
                item.setCheckState(Qt.Unchecked)
                self.list.blockSignals(False)
                self.count.setText(f"Đã đủ {self.MAX} nhân vật. Bỏ tick một người trước khi chọn thêm.")
                return
            if name not in self.order:
                self.order.append(name)
        elif name in self.order:
            self.order.remove(name)
        self._update()

    def _update(self):
        self.count.setText(f"Đã chọn {len(self._checked())}/{self.MAX}")

    def _filter(self, text: str):
        q = _fold(text)
        for i in range(self.list.count()):
            c = self.chars[i]
            hay = _fold(f"{c.name} {c.role} {c.zh} {' '.join(c.aliases)}")
            self.list.item(i).setHidden(bool(q) and q not in hay)

    def names(self) -> list[str]:
        have = set(self._checked())
        return [n for n in self.order if n in have]


class CharPicker(QWidget):
    """Hàng nhân vật của scene: ảnh tròn + tên, nút 'Chọn…' mở hộp thoại có ảnh và ô tìm kiếm.
    Có text()/setText()/clear() tương thích QLineEdit (danh sách phân tách bằng dấu phẩy)."""
    changed = Signal()

    def __init__(self, get_chars):
        super().__init__()
        self._get = get_chars
        self._names: list[str] = []
        self.setFixedHeight(40)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(SP.s)
        self.btn = QPushButton("Chọn…")
        self.btn.setProperty("flat", True)
        self.btn.clicked.connect(self.pick)
        self._render()

    def names(self) -> list[str]:
        return list(self._names)

    def set_names(self, names: list[str]) -> None:
        self._names = [nfc(n) for n in names]
        self._render()

    def text(self) -> str:
        return ", ".join(self._names)

    def setText(self, s: str) -> None:
        self.set_names([x.strip() for x in s.split(",") if x.strip()])

    def clear(self) -> None:
        self.set_names([])

    def _render(self) -> None:
        while self.row.count():
            it = self.row.takeAt(0)
            if it.widget() and it.widget() is not self.btn:
                it.widget().deleteLater()
        by = {nfc(c.name): c for c in self._get()}
        for n in self._names:
            c = by.get(n)
            chip = QFrame()
            chip.setProperty("chip", True)
            h = QHBoxLayout(chip)
            h.setContentsMargins(SP.xs, SP.xs, SP.m, SP.xs)
            h.setSpacing(SP.s)
            av = QLabel()
            av.setPixmap(avatar(c.image if c else "", 28))
            av.setFixedSize(28, 28)
            name = QLabel(n)
            h.addWidget(av)
            h.addWidget(name)
            if c is None:
                chip.setProperty("missing", True)       # tên không còn trong danh sách nhân vật
            self.row.addWidget(chip)
        if not self._names:
            hint = QLabel("Chưa có nhân vật")
            hint.setProperty("caption", True)
            self.row.addWidget(hint)
        self.row.addStretch(1)
        self.row.addWidget(self.btn)

    def refresh(self) -> None:
        """Vẽ lại các chip (gọi khi danh sách nhân vật vừa được nạp/đổi, để ảnh và cờ 'thiếu' đúng với dữ liệu mới)."""
        self._render()

    def pick(self) -> None:
        chars = self._get()
        if not chars:
            return
        dlg = CharPickDialog(self.window(), chars, self._names)
        if dlg.exec() == QDialog.Accepted:
            self.set_names(dlg.names())
            self.changed.emit()


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
        self.log_btn = QPushButton("Nhật ký ▴")
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
        self.log_btn.setText("Nhật ký ▾" if is_open else "Nhật ký ▴")


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


# ---------------------------------------------------------------- popover kiểu thẻ
class PopItem(QPushButton):
    """Một dòng hành động: tiêu đề + mô tả phụ (tuỳ chọn) + phím tắt; mục nguy hiểm tô đỏ."""

    def __init__(self, title: str, sub: str = "", danger: bool = False, shortcut: str = "", progress=None, action: str = ""):
        super().__init__()
        self.action_btn = None
        self.setProperty("popitem", True)
        self.setCursor(Qt.PointingHandCursor)
        if sub:
            self.setProperty("popsub", True)     # chiều cao do stylesheet quyết định (min-height của QSS thắng setMinimumHeight)
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, SP.s, SP.m, SP.s)
        col = QVBoxLayout()
        col.setSpacing(SP.xs)
        col.setContentsMargins(0, 0, 0, 0)
        t = QLabel(title)
        t.setAttribute(Qt.WA_TransparentForMouseEvents)
        if danger:
            t.setProperty("level", "error")
        col.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setProperty("caption", True)
            s.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(s)
        if progress is not None and progress[1] > 0:
            self.setProperty("popprog", True)
            bar = QProgressBar()
            bar.setProperty("popbar", True)
            bar.setRange(0, progress[1])
            bar.setValue(progress[0])
            bar.setTextVisible(False)
            bar.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(bar)
        row.addLayout(col, 1)
        if shortcut or action:
            k = QLabel(shortcut)
            k.setProperty("caption", True)
            k.setAttribute(Qt.WA_TransparentForMouseEvents)
            if action:
                k.setFixedWidth(16)             # chừa chỗ cố định cho dấu ✓ để các dòng thẳng hàng
                k.setAlignment(Qt.AlignCenter)
            row.addWidget(k)
        if action:                                  # nút hành động riêng ngay trên dòng (vd. "Xoá"), không phải chọn dòng trước
            self.action_btn = QPushButton(action)
            self.action_btn.setProperty("popaction", True)
            self.action_btn.setCursor(Qt.PointingHandCursor)
            row.addWidget(self.action_btn, 0, Qt.AlignVCenter)


class Popover(QFrame):
    """Thẻ nổi bo góc, có bóng, gắn vào nút bấm: nhóm hành động có tiêu đề, mô tả phụ, mục nguy hiểm màu đỏ.
    `builder(pop)` được gọi mỗi lần mở để dựng nội dung theo trạng thái hiện tại."""
    MARGIN = SP.l

    def __init__(self, parent, builder, width: int = 340):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._builder, self._width = builder, width
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        self.card = QFrame()
        self.card.setObjectName("popCard")
        self.card.setFixedWidth(width)
        sh = QGraphicsDropShadowEffect(self.card)
        sh.setBlurRadius(30)
        sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 80))
        self.card.setGraphicsEffect(sh)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(SP.s, SP.s, SP.s, SP.s)
        self.body.setSpacing(0)
        self._targets = [self.body]
        outer.addWidget(self.card)

    # ---- dựng nội dung ----
    def clear(self) -> None:
        self._targets = [self.body]
        while self.body.count():
            it = self.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def section(self, text: str) -> None:
        lab = QLabel(text.upper())
        lab.setProperty("popsection", True)
        self.body.addWidget(lab)

    def separator(self) -> None:
        sep = QFrame()
        sep.setProperty("popsep", True)
        sep.setFixedHeight(1)
        self.body.addWidget(sep)

    def item(self, title: str, sub: str = "", cb=None, enabled: bool = True, danger: bool = False, shortcut: str = "",
             progress=None, action: tuple | None = None) -> None:
        """action=("Xoá", callback): thêm nút nhỏ bên phải dòng, bấm là chạy callback ngay mà không kích hoạt dòng."""
        b = PopItem(title, sub, danger, shortcut, progress, action[0] if action else "")
        b.setEnabled(enabled)
        b.clicked.connect(lambda: self._fire(cb))
        if action:
            b.action_btn.clicked.connect(lambda: self._fire(action[1]))
        self._targets[-1].addWidget(b)

    def widget(self, w: QWidget) -> None:
        """Chèn một điều khiển tuỳ ý (vd. nút phân đoạn) vào popover, canh lề như các mục khác."""
        holder = QWidget()
        h = QVBoxLayout(holder)
        h.setContentsMargins(SP.m, SP.xs, SP.m, SP.s)
        h.setSpacing(SP.xs)
        h.addWidget(w)
        self._targets[-1].addWidget(holder)

    def begin_scroll(self, max_height: int) -> None:
        """Các mục thêm sau đó nằm trong vùng cuộn có chiều cao tối đa (dành cho danh sách dài); kết thúc bằng end_scroll()."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        area.setWidget(inner)
        self.body.addWidget(area)
        self._scroll = (area, inner, lay, max_height)
        self._targets.append(lay)

    def end_scroll(self) -> None:
        area, inner, lay, max_h = self._scroll
        self._targets.pop()
        area.setFixedHeight(min(inner.sizeHint().height() + 2, max_h))

    def searchable_list(self, entries: list[dict], placeholder: str, max_height: int = 360, limit: int = 40,
                        focus: int = -1, empty: str = "Không có kết quả") -> None:
        """Danh sách dài có ô tìm kiếm (bỏ dấu, không phân biệt hoa thường). entries: [{title, sub, cb, shortcut, progress, action}].
        Chỉ dựng tối đa `limit` dòng mỗi lần (hàng trăm dòng sẽ làm popover mở chậm); khi chưa gõ gì thì lấy cửa sổ quanh mục `focus`
        (mục đang chọn). Enter mở kết quả đầu tiên. Chiều cao vùng cuộn cố định để popover không nhảy khi lọc."""
        import unicodedata

        def norm(t: str) -> str:
            t = unicodedata.normalize("NFD", t.replace("đ", "d").replace("Đ", "D")).lower()
            return "".join(c for c in t if unicodedata.category(c) != "Mn")

        keys = [norm(e["title"] + " " + e.get("search", "")) for e in entries]
        box = QLineEdit()
        box.setPlaceholderText(placeholder)
        box.setClearButtonEnabled(True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(SP.xs, SP.xs, SP.xs, SP.s)
        hl.addWidget(box)
        self.body.addWidget(holder)
        count = QLabel("")
        count.setProperty("caption", True)
        self.begin_scroll(max_height)
        area, inner, lay, _ = self._scroll
        self._targets.pop()                    # tự quản lý nội dung vùng cuộn, không dùng end_scroll
        count.setContentsMargins(SP.m, 0, SP.m, 0)
        count.setFixedHeight(28)
        self.body.addWidget(count)
        shown: list[dict] = []

        def fill(q: str) -> None:
            while lay.count():
                it = lay.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            nq = norm(q.strip())
            idx = [i for i, k in enumerate(keys) if nq in k]
            if nq or len(idx) <= limit:
                pick = idx[:limit]
            else:                              # chưa gõ: hiện quanh mục đang chọn để thấy ngay vị trí hiện tại
                lo = max(0, min(max(focus, 0) - limit // 2, len(idx) - limit))
                pick = idx[lo:lo + limit]
            shown[:] = [entries[i] for i in pick]
            for e in shown:
                b = PopItem(e["title"], e.get("sub", ""), False, e.get("shortcut", ""), e.get("progress"),
                            e["action"][0] if e.get("action") else "")
                b.clicked.connect(lambda _=False, cb=e.get("cb"): self._fire(cb))
                if e.get("action"):
                    b.action_btn.clicked.connect(lambda _=False, cb=e["action"][1]: self._fire(cb))
                lay.addWidget(b)
            if not shown:
                lab = QLabel(empty)
                lab.setProperty("caption", True)
                lab.setContentsMargins(SP.m, SP.m, SP.m, SP.m)
                lay.addWidget(lab)
            lay.addStretch(1)
            more = len(idx) - len(shown)
            if nq:
                count.setText(f"{len(idx)} kết quả" + (f" · hiển thị {len(shown)} đầu" if more > 0 else ""))
            else:
                count.setText(f"Hiển thị {len(shown)}/{len(entries)} · gõ để tìm các mục còn lại" if more > 0 else f"{len(entries)} mục")
            area.verticalScrollBar().setValue(0)

        box.textChanged.connect(fill)
        box.returnPressed.connect(lambda: self._fire(shown[0].get("cb")) if shown else None)
        fill("")
        if len(entries) > 6:
            area.setFixedHeight(max_height)    # danh sách dài: cố định chiều cao, popover không nhảy khi lọc
        else:
            inner.adjustSize()
            area.setFixedHeight(min(max_height, inner.sizeHint().height() + 2))
        self._focus_widget = box

    def _fire(self, cb) -> None:
        self.hide()
        if cb:
            QTimer.singleShot(0, cb)      # chạy sau khi popup đã đóng hẳn (an toàn khi cb mở hộp thoại)

    # ---- hiển thị ----
    def _prepare(self) -> None:
        self.clear()
        self._focus_widget = None
        self._builder(self)
        self.adjustSize()

    def _clamp(self, x: int, y: int, anchor_pt: QPoint) -> QPoint:
        scr = (QGuiApplication.screenAt(anchor_pt) or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(scr.left() + 4, min(x, scr.right() - self.width() - 4))
        y = max(scr.top() + 4, min(y, scr.bottom() - self.height() - 4))
        return QPoint(x, y)

    def show_for(self, anchor: QWidget, side: str = "auto", align: str = "right") -> None:
        """Mở sát nút `anchor`. side: below/above/auto (auto = hướng nào đủ chỗ). align: canh mép phải hay trái của nút."""
        self._prepare()
        tl = anchor.mapToGlobal(QPoint(0, 0))
        br = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height()))
        scr = (QGuiApplication.screenAt(tl) or QGuiApplication.primaryScreen()).availableGeometry()
        m = self.MARGIN
        below_y = br.y() + 2 - m
        above_y = tl.y() - 2 - self.height() + m
        if side == "auto":
            side = "below" if below_y + self.height() <= scr.bottom() else "above"
        y = below_y if side == "below" else above_y
        x = (br.x() - self.width() + m) if align == "right" else (tl.x() - m)
        self.move(self._clamp(x, y, tl))
        self.show()
        if self._focus_widget is not None:
            self._focus_widget.setFocus()

    def show_at(self, global_pos: QPoint) -> None:
        self._prepare()
        self.move(self._clamp(global_pos.x() - self.MARGIN, global_pos.y() - self.MARGIN, global_pos))
        self.show()


def popover_button(btn: QPushButton, builder, side: str = "auto", align: str = "right", width: int = 340) -> Popover:
    """Gắn popover vào một nút: bấm nút thì mở/đóng thẻ."""
    pop = Popover(btn, builder, width)
    btn.clicked.connect(lambda: pop.show_for(btn, side, align))
    return pop


# ---------------------------------------------------------------- thẻ ba phần: đầu / thân / chân
HEADER_H, FOOTER_H = 56, 68


def card_bar(layout, height: int) -> QWidget:
    """Thanh đầu/chân thẻ: chiều cao cố định (để mọi thẻ cùng hàng luôn thẳng hàng) và lề ngang thống nhất."""
    w = QWidget()
    w.setFixedHeight(height)
    layout.setContentsMargins(SP.l, 0, SP.l, 0)
    layout.setSpacing(SP.s)
    w.setLayout(layout)
    return w


def card_body(widget: QWidget, margins=(SP.l, SP.l, SP.l, SP.l)) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(*margins)
    v.setSpacing(0)
    v.addWidget(widget, 1)
    return w


def card_sep() -> QFrame:
    s = QFrame()
    s.setProperty("cardsep", True)
    s.setFixedHeight(1)
    return s


def make_card(header: QWidget, body: QWidget, footer: QWidget | None = None) -> QFrame:
    card = QFrame()
    card.setProperty("card", True)
    v = QVBoxLayout(card)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    v.addWidget(header)
    v.addWidget(card_sep())
    v.addWidget(body, 1)
    if footer is not None:
        v.addWidget(card_sep())
        v.addWidget(footer)
    return card


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


# ---------------------------------------------------------------- nút điều hướng breadcrumb
class NavButton(QPushButton):
    """Nút chuyển dự án/chương: tên (tự cắt '…') + nhãn tiến độ tuỳ chọn + mũi tên; bấm để mở danh sách chuyển nhanh."""

    def __init__(self, with_pill: bool = False):
        super().__init__()
        self.setProperty("navbtn", True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(SP.m, 0, SP.m, 0)
        row.setSpacing(SP.s)
        self.title = ElidedLabel()
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.title, 1)
        self.pill = QLabel("")
        self.pill.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pill.setVisible(False)
        self._with_pill = with_pill
        row.addWidget(self.pill, 0, Qt.AlignVCenter)        # canh giữa theo chiều dọc: không bị kéo giãn hết chiều cao nút
        caret = QLabel("▾")
        caret.setProperty("caption", True)
        caret.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(caret, 0, Qt.AlignVCenter)

    def set_title(self, text: str) -> None:
        self.title.set_full(text)

    def set_pill(self, text: str, kind: str = "info") -> None:
        if not self._with_pill:
            return
        self.pill.setText(text)
        self.pill.setVisible(bool(text))
        if self.pill.property("pill") != kind:
            self.pill.setProperty("pill", kind)
            repolish(self.pill)


# ---------------------------------------------------------------- ô chọn có danh sách xổ ra theo thiết kế thẻ
class Combo(QComboBox):
    """QComboBox với danh sách xổ ra bo góc, có đệm, dòng chọn tô nhạt, xổ ngay bên dưới ô.
    Cần QStyledItemDelegate (bộ vẽ mặc định của Fusion bỏ qua stylesheet) và khung chứa trong suốt để bo góc hiện ra."""

    def __init__(self):
        super().__init__()
        self.setItemDelegate(QStyledItemDelegate(self))
        box = self.view().window()                      # khung chứa danh sách (QComboBoxPrivateContainer)
        box.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        box.setAttribute(Qt.WA_TranslucentBackground)
        self.setMaxVisibleItems(10)

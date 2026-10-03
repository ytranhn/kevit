"""Popover (menu nổi) và các mục của nó."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QVBoxLayout,
    QWidget)

from ..theme import SP


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
                w = it.widget()
                w.hide()                      # gỡ khỏi popover ngay (deleteLater chỉ chạy sau): nếu không, sizeHint còn tính cả nội dung lần mở trước
                w.setParent(None)
                w.deleteLater()

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
        self.setMinimumSize(0, 0)                 # layout đã đặt minimumSize theo lần mở trước (cao hơn) nên phải gỡ trước khi co lại
        self.body.invalidate()
        self.body.activate()
        self.card.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        self.resize(self.sizeHint())

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

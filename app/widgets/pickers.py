"""Chọn nhân vật có ảnh (hộp thoại và thanh chọn)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
    QWidget)

from ..models import nfc
from ..theme import SP
from ..scene_planner import _fold
from .basic import avatar


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

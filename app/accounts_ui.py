"""Quản lý tài khoản Google Flow (tab Cài đặt): danh sách, thêm/đổi tên/gỡ, mở Chrome để đăng nhập, đặt mặc định cho dự án mới."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import accounts, flow_auto, icons
from .theme import SP
from .workers import Worker


class AccountsPanel(QWidget):
    changed = Signal()                 # danh sách tài khoản vừa đổi (thêm/đổi tên/gỡ/đổi mặc định)
    _status = Signal(str, bool)        # (id tài khoản, Chrome đang mở?) từ luồng kiểm tra nền

    def __init__(self, parent=None):
        super().__init__(parent)
        self._up: dict[str, bool] = {}
        self.worker = None
        self.list = QListWidget()
        self.list.setFixedHeight(168)
        self.list.setSpacing(2)
        self.list.currentRowChanged.connect(self.update_buttons)
        self.list.itemDoubleClicked.connect(lambda *_: self.open_chrome())
        self.b_add = QPushButton("Thêm tài khoản…")
        icons.attach(self.b_add, "plus", 18)
        self.b_open = QPushButton("Mở Chrome để đăng nhập")
        icons.attach(self.b_open, "open", 18)
        self.b_rename = QPushButton("Đổi tên…")
        self.b_default = QPushButton("Mặc định cho dự án mới")
        self.b_remove = QPushButton("Gỡ")
        self.b_remove.setProperty("danger", True)
        self.note = QLabel("Mỗi tài khoản có một cửa sổ Chrome và đăng nhập Google riêng (credit Flow riêng), mở song song được. "
                           "Bấm “Mở Chrome để đăng nhập” rồi đăng nhập Google một lần cho từng tài khoản. Mỗi dự án gắn một tài khoản "
                           "(chọn ở chip Flow dưới cùng hoặc trong Cài đặt dự án).")
        self.note.setProperty("caption", True)
        self.note.setWordWrap(True)
        self.msg = QLabel("")
        self.msg.setProperty("caption", True)
        self.msg.setWordWrap(True)
        # lưới 3 cột đều nhau, mọi nút cùng chiều cao: hàng 1 = Thêm | Mở Chrome (rộng 2 cột); hàng 2 = Đổi tên | Mặc định | Gỡ
        grid = QGridLayout()
        grid.setHorizontalSpacing(SP.s)
        grid.setVerticalSpacing(SP.s)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        for b in (self.b_add, self.b_open, self.b_rename, self.b_default, self.b_remove):
            b.setFixedHeight(36)
        grid.addWidget(self.b_add, 0, 0)
        grid.addWidget(self.b_open, 0, 1, 1, 2)
        grid.addWidget(self.b_rename, 1, 0)
        grid.addWidget(self.b_default, 1, 1)
        grid.addWidget(self.b_remove, 1, 2)
        self.grid = grid
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SP.m)
        v.addWidget(self.note)
        v.addWidget(self.list)
        v.addLayout(grid)
        v.addWidget(self.msg)
        self.b_add.clicked.connect(self.add)
        self.b_open.clicked.connect(self.open_chrome)
        self.b_rename.clicked.connect(self.rename)
        self.b_default.clicked.connect(self.make_default)
        self.b_remove.clicked.connect(self.remove)
        self._status.connect(self._on_status)
        self.refresh()

    # ---- danh sách ----
    def current(self) -> accounts.Account | None:
        it = self.list.currentItem()
        return accounts.get(it.data(Qt.UserRole)) if it else None

    def refresh(self, keep: str | None = None) -> None:
        keep = keep or (self.current().id if self.current() else None)
        accs, dflt = accounts.all_accounts(), accounts.default_new_id()
        self.list.blockSignals(True)
        self.list.clear()
        for a in accs:
            n = len(accounts.usage(a.id))
            up = self._up.get(a.id)
            state = "Chrome đang mở" if up else ("Chrome chưa mở" if up is False else "…")
            tags = ("  ·  mặc định cho dự án mới" if a.id == dflt else "") + ("  ·  đang dùng" if a.id == accounts.active().id else "")
            it = QListWidgetItem(f"{a.name}{tags}\n{state}  ·  cổng {a.port}  ·  {n} dự án")
            it.setData(Qt.UserRole, a.id)
            self.list.addItem(it)
        row = next((i for i, a in enumerate(accs) if a.id == keep), 0)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self.update_buttons()
        for a in accs:                                             # kiểm tra Chrome từng tài khoản ở luồng nền (không chặn giao diện)
            threading.Thread(target=lambda a=a: self._status.emit(a.id, flow_auto._cdp_up(a.cdp_url)), daemon=True).start()

    def _on_status(self, acc_id: str, up: bool) -> None:
        if self._up.get(acc_id) == up:
            return
        self._up[acc_id] = up
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == acc_id:
                head, _, tail = it.text().partition("\n")
                parts = tail.split("  ·  ")
                parts[0] = "Chrome đang mở" if up else "Chrome chưa mở"
                it.setText(head + "\n" + "  ·  ".join(parts))

    def update_buttons(self, *_):
        a = self.current()
        self.b_open.setEnabled(a is not None and self.worker is None)
        self.b_rename.setEnabled(a is not None)
        self.b_default.setEnabled(a is not None and a.id != accounts.default_new_id())
        self.b_remove.setEnabled(a is not None and a.id != accounts.DEFAULT_ID)

    # ---- thao tác ----
    def add(self) -> None:
        name, ok = QInputDialog.getText(self, "Thêm tài khoản Flow", "Tên gợi nhớ (vd. email của tài khoản):")
        if not ok or not name.strip():
            return
        try:
            a = accounts.add(name)
        except ValueError as e:
            QMessageBox.warning(self, "Không thêm được", str(e))
            return
        self.refresh(a.id)
        self.msg.setText(f"Đã thêm “{a.name}”. Bấm “Mở Chrome để đăng nhập” rồi đăng nhập Google trong cửa sổ Chrome vừa mở.")
        self.changed.emit()

    def rename(self) -> None:
        a = self.current()
        if not a:
            return
        name, ok = QInputDialog.getText(self, "Đổi tên tài khoản", "Tên mới:", text=a.name)
        if not ok or not name.strip():
            return
        try:
            accounts.rename(a.id, name)
        except ValueError as e:
            QMessageBox.warning(self, "Không đổi được", str(e))
            return
        self.refresh(a.id)
        self.changed.emit()

    def make_default(self) -> None:
        a = self.current()
        if a:
            accounts.set_default_new(a.id)
            self.refresh(a.id)
            self.msg.setText(f"Dự án tạo mới sẽ dùng tài khoản “{a.name}”.")
            self.changed.emit()

    def remove(self) -> None:
        a = self.current()
        if not a or a.id == accounts.DEFAULT_ID:
            return
        used = accounts.usage(a.id)
        extra = (f"\n\n{len(used)} dự án đang dùng tài khoản này ({', '.join(used[:5])}{'…' if len(used) > 5 else ''}) "
                 "sẽ chuyển về tài khoản chính; clip đã tải về máy không bị ảnh hưởng.") if used else ""
        if QMessageBox.question(self, "Gỡ tài khoản", f"Gỡ “{a.name}” khỏi danh sách?\nĐăng nhập Chrome của tài khoản vẫn nằm trên ổ đĩa "
                                f"({a.profile}), thêm lại cùng tên là dùng tiếp được.{extra}") != QMessageBox.Yes:
            return
        try:
            moved = accounts.remove(a.id)
        except ValueError as e:
            QMessageBox.warning(self, "Không gỡ được", str(e))
            return
        self.refresh()
        self.msg.setText(f"Đã gỡ “{a.name}”." + (f" {len(moved)} dự án đã chuyển về tài khoản chính." if moved else ""))
        self.changed.emit()

    def open_chrome(self) -> None:
        a = self.current()
        if not a or self.worker is not None:
            return
        self.msg.setText(f"Đang mở Chrome cho “{a.name}”…")
        self.worker = Worker(lambda log: flow_auto.launch_chrome(a))
        self.worker.done.connect(lambda _: self._opened(a, None))
        self.worker.failed.connect(lambda e: self._opened(a, e))
        self.update_buttons()
        self.worker.start()

    def _opened(self, a: accounts.Account, err: str | None) -> None:
        self.worker = None
        if err:
            self.msg.setText(f"Không mở được Chrome: {err}")
        else:
            self.msg.setText(f"Chrome của “{a.name}” đã mở. Đăng nhập Google Flow trong cửa sổ đó (chỉ cần một lần).")
            self._up[a.id] = True
        self.refresh(a.id)

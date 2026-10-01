"""Hộp thoại hướng dẫn 'Nhập nhân vật theo lô': 3 cách chuẩn bị, tạo gói mẫu, kéo-thả, xem trước trước khi nhập."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import characters_pack
from .theme import SP

TREE_SIMPLE = """goi-nhan-vat/
├── Cố An.png
├── Cố An.txt              ← mô tả (tuỳ chọn)
├── An Tâm.jpg
└── Trương Xuân Thu.webp"""

CSV = """Tên,Vai trò,Tên Trung,Ảnh,Mô tả
Cố An,Nhân vật chính,顾安,Cố An.png,Nam thanh niên tóc đen dài
An Tâm,Đệ tử,安心,An Tâm.jpg,Nữ tóc búi cao"""


def _mono(text: str) -> QLabel:
    t = QLabel(text)
    t.setProperty("mono", True)
    t.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return t


def _caption(text: str) -> QLabel:
    c = QLabel(text)
    c.setProperty("caption", True)
    c.setWordWrap(True)
    return c


def _vbox(*widgets: QWidget) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(SP.s)
    for x in widgets:
        v.addWidget(x)
    return w


def _step(no: int, title: str, body: QWidget | str) -> QWidget:
    badge = QLabel(str(no))
    badge.setProperty("stepnum", True)
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedSize(28, 28)
    head = QLabel(title)
    head.setProperty("subheading", True)
    col = QVBoxLayout()
    col.setSpacing(SP.s)
    col.addWidget(head)
    col.addWidget(_caption(body) if isinstance(body, str) else body)
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(SP.m)
    h.addWidget(badge, 0, Qt.AlignTop)
    h.addLayout(col, 1)
    return w


class PackGuideDialog(QDialog):
    """Kết quả: self.source (thư mục hoặc .zip) khi người dùng chọn hoặc kéo-thả vào hộp thoại."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: Path | None = None
        self.setAcceptDrops(True)
        self.setWindowTitle("Nhập nhân vật theo lô")
        self.resize(720, 760)
        title = QLabel("Nhập nhân vật theo lô")
        title.setProperty("heading", True)
        sub = _caption("Nạp nhiều nhân vật cùng lúc. Tool ghép theo tên: nhân vật đã có thì cập nhật ảnh, chưa có thì thêm mới; "
                       "mô tả prompt (tiếng Anh) và tên gọi khác bạn đã chỉnh được giữ nguyên. "
                       "Một nhân vật lỗi không làm hỏng cả lô, tool nhập phần còn lại và báo rõ lý do.")
        drop = QLabel("Kéo thư mục hoặc file .zip vào đây\nhoặc bấm “Chọn…” bên dưới")
        drop.setProperty("dropzone", True)
        drop.setAlignment(Qt.AlignCenter)
        drop.setMinimumHeight(72)

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, SP.m, 0)
        bv.setSpacing(SP.l)
        bv.addWidget(drop)
        bv.addWidget(_step(1, "Cách đơn giản nhất: chỉ cần một thư mục ảnh", _vbox(
            _caption("Mỗi ảnh là một nhân vật, tên nhân vật lấy từ tên file (số thứ tự đầu như “01_” được bỏ). "
                     "Ảnh PNG, JPG hoặc WEBP đều được. Muốn kèm mô tả thì đặt file .txt cùng tên."), _mono(TREE_SIMPLE))))
        bv.addWidget(_step(2, "Muốn thêm vai trò, tên Hán, tên gọi khác: dùng bảng CSV", _vbox(
            _caption("Đặt một file .csv bất kỳ tên vào cùng thư mục. Dùng dấu phẩy hoặc chấm phẩy đều được (Excel tiếng Việt hay lưu bằng "
                     "chấm phẩy), UTF-8 hoặc Excel. Chỉ cột Tên là bắt buộc; các cột Vai trò, Tên Trung, Ảnh, Mô tả, Tên khác "
                     "thiếu cột nào cũng được, tên cột có thể là tiếng Anh (Name, Role, Image…)."), _mono(CSV))))
        bv.addWidget(_step(3, "Chia sẻ gói: nén thành một file .zip", _caption(
            "Bạn có thể nén cả thư mục thành .zip rồi chọn thẳng file đó, không cần giải nén.")))
        bv.addWidget(_step(4, "Xem trước rồi mới nhập", _caption(
            "Tool đọc gói và cho bạn xem: bao nhiêu nhân vật sẽ cập nhật, thêm mới, và nhân vật nào bị bỏ qua kèm lý do. "
            "Chỉ khi bạn xác nhận mới ghi vào dự án. Ảnh được chép vào dự án nên có thể xoá thư mục gói sau đó.")))
        tip = QFrame()
        tip.setProperty("card", True)
        tv = QVBoxLayout(tip)
        tv.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
        tv.setSpacing(SP.xs)
        t1 = QLabel("Chưa có gói? Tạo gói mẫu để xem tận mắt")
        t1.setProperty("subheading", True)
        tv.addWidget(t1)
        tv.addWidget(_caption("Tool tạo sẵn một thư mục gồm 2 nhân vật mẫu (ảnh giữ chỗ, file mô tả, bảng CSV). "
                              "Bạn thay ảnh và tên bằng dữ liệu thật rồi nhập."))
        sample = QPushButton("Tạo gói mẫu…")
        sample.clicked.connect(self.make_sample)
        tv.addWidget(sample, 0, Qt.AlignLeft)
        bv.addWidget(tip)
        bv.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)

        close = QPushButton("Đóng")
        pick_zip = QPushButton("Chọn file .zip…")
        pick = QPushButton("Chọn thư mục…")
        pick.setProperty("primary", True)
        pick.setDefault(True)
        close.clicked.connect(self.reject)
        pick.clicked.connect(self.pick_folder)
        pick_zip.clicked.connect(self.pick_zip)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(close)
        foot.addWidget(pick_zip)
        foot.addWidget(pick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.l)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(scroll, 1)
        lay.addLayout(foot)

    # kéo-thả thư mục / .zip
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir() or p.suffix.lower() == ".zip":
                self.source = p
                self.accept()
                return

    def pick_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Chọn thư mục gói nhân vật")
        if f:
            self.source = Path(f)
            self.accept()

    def pick_zip(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn file zip chứa nhân vật", "", "File zip (*.zip)")
        if f:
            self.source = Path(f)
            self.accept()

    def make_sample(self):
        dest = QFileDialog.getExistingDirectory(self, "Chọn nơi tạo gói mẫu")
        if not dest:
            return
        root = characters_pack.write_sample(Path(dest))
        box = QMessageBox(self)
        box.setWindowTitle("Đã tạo gói mẫu")
        box.setText(f"Đã tạo gói mẫu tại:\n{root}\n\nMở thư mục này, thay ảnh và tên bằng dữ liệu thật, rồi quay lại bấm “Chọn thư mục…”.")
        open_btn = box.addButton("Mở thư mục", QMessageBox.AcceptRole)
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            from .flow import reveal
            reveal(root)


def _names(xs: list[str], n: int = 5) -> str:
    return ", ".join(xs[:n]) + ("…" if len(xs) > n else "")


def confirm_text(pv: dict) -> str:
    """Nội dung xem trước trước khi nhập."""
    lines = [f"Tìm thấy {pv['total']} nhân vật trong gói:", f"• Cập nhật ảnh/dữ liệu: {len(pv['update'])}",
             f"• Thêm mới: {len(pv['new'])}" + (f"  ({_names(pv['new'])})" if pv["new"] else "")]
    if pv.get("extra"):
        lines.append(f"• Ảnh không có trong bảng CSV, nhập theo tên file: {len(pv['extra'])}  ({_names(pv['extra'])})")
    if pv["problems"]:
        lines.append(f"• Sẽ bỏ qua: {len(pv['problems'])}")
        lines += [f"    – {x}" for x in pv["problems"][:6]]
        if len(pv["problems"]) > 6:
            lines.append(f"    – … và {len(pv['problems']) - 6} nhân vật khác")
    if pv["no_desc"]:
        lines.append(f"• Chưa có mô tả: {len(pv['no_desc'])} nhân vật (vẫn nhập, mô tả để trống, bạn điền sau)")
    lines.append("\nNhân vật có trong dự án nhưng không có trong gói được giữ nguyên. Tiếp tục nhập?")
    return "\n".join(lines)

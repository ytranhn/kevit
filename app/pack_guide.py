"""Hộp thoại hướng dẫn 'Nhập gói nhân vật': các bước, cấu trúc thư mục, ví dụ CSV, tạo gói mẫu và xem trước trước khi nhập."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import characters_pack
from .theme import SP

TREE = """goi-nhan-vat/
├── character_index.csv        ← bảng danh sách (bắt buộc)
├── 01_Cố An/
│   ├── 01_Cố An.png           ← ảnh nhân vật
│   └── description.md         ← mô tả (tuỳ chọn)
├── 02_An Tâm/
│   ├── 02_An Tâm.png
│   └── description.md
└── ..."""

CSV = """No,Tên,Tên Trung,Vai trò,Folder
1,Cố An,顾安,Nhân vật chính,01_Cố An/01_Cố An.png
2,An Tâm,安心,Đệ tử của Cố An,02_An Tâm/02_An Tâm.png"""

MD = """# Cố An

## Mô tả ngoại hình
Nam, thanh niên, tóc đen dài buộc hờ, áo choàng xanh lục viền vàng."""


def _mono(text: str) -> QLabel:
    t = QLabel(text)
    t.setProperty("mono", True)
    t.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return t


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
    if isinstance(body, str):
        body = _caption(body)
    col.addWidget(body)
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(SP.m)
    h.addWidget(badge, 0, Qt.AlignTop)
    h.addLayout(col, 1)
    return w


def _caption(text: str) -> QLabel:
    c = QLabel(text)
    c.setProperty("caption", True)
    c.setWordWrap(True)
    return c


class PackGuideDialog(QDialog):
    """Trả về thư mục gói người dùng chọn (self.folder) khi bấm 'Chọn thư mục gói…'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder: Path | None = None
        self.setWindowTitle("Nhập gói nhân vật")
        self.resize(720, 760)
        title = QLabel("Nhập gói nhân vật")
        title.setProperty("heading", True)
        sub = _caption("Nạp nhiều nhân vật cùng lúc từ một thư mục. Tool ghép theo tên: nhân vật đã có thì cập nhật ảnh, "
                       "chưa có thì thêm mới. Mô tả prompt (tiếng Anh) và tên gọi khác bạn đã chỉnh được giữ nguyên.")

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, SP.m, 0)
        bv.setSpacing(SP.l)
        bv.addWidget(_step(1, "Chuẩn bị một thư mục theo cấu trúc này", _mono(TREE)))
        bv.addWidget(_step(2, "Điền bảng character_index.csv", _vbox(
            _caption("Lưu dạng UTF-8 (Excel: “CSV UTF-8”). Giữ đúng tên 5 cột. Cột Folder là đường dẫn tới ảnh, tính từ thư mục gói. "
                     "Tên Trung và Vai trò có thể để trống."), _mono(CSV))))
        bv.addWidget(_step(3, "Viết mô tả (tuỳ chọn)", _vbox(
            _caption("Mỗi nhân vật một file description.md, tool chỉ đọc mục có tiêu đề đúng “## Mô tả ngoại hình”."), _mono(MD))))
        bv.addWidget(_step(4, "Bấm “Chọn thư mục gói…”", _caption(
            "Tool đọc gói và cho bạn xem trước: bao nhiêu nhân vật sẽ cập nhật, thêm mới, hoặc bị bỏ qua vì thiếu ảnh. "
            "Chỉ khi bạn bấm xác nhận thì mới ghi vào dự án. Ảnh được chép vào dự án nên có thể xoá thư mục gói sau đó.")))
        tip = QFrame()
        tip.setProperty("card", True)
        tv = QVBoxLayout(tip)
        tv.setContentsMargins(SP.l, SP.m, SP.l, SP.m)
        tv.setSpacing(SP.xs)
        t1 = QLabel("Chưa có gói? Tạo gói mẫu để xem tận mắt")
        t1.setProperty("subheading", True)
        tv.addWidget(t1)
        tv.addWidget(_caption("Tool tạo sẵn một thư mục gồm 2 nhân vật mẫu (ảnh giữ chỗ, bảng CSV, mô tả). "
                              "Bạn mở ra, thay ảnh và tên bằng dữ liệu thật rồi nhập."))
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
        pick = QPushButton("Chọn thư mục gói…")
        pick.setProperty("primary", True)
        pick.setDefault(True)
        close.clicked.connect(self.reject)
        pick.clicked.connect(self.pick_folder)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(close)
        foot.addWidget(pick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.l)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(scroll, 1)
        lay.addLayout(foot)

    def pick_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Chọn thư mục gói nhân vật")
        if f:
            self.folder = Path(f)
            self.accept()

    def make_sample(self):
        dest = QFileDialog.getExistingDirectory(self, "Chọn nơi tạo gói mẫu")
        if not dest:
            return
        root = characters_pack.write_sample(Path(dest))
        box = QMessageBox(self)
        box.setWindowTitle("Đã tạo gói mẫu")
        box.setText(f"Đã tạo gói mẫu tại:\n{root}\n\nMở thư mục này, thay ảnh và tên bằng dữ liệu thật, rồi quay lại bấm “Chọn thư mục gói…”.")
        open_btn = box.addButton("Mở thư mục", QMessageBox.AcceptRole)
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            from .flow import reveal
            reveal(root)


def _vbox(*widgets: QWidget) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(SP.s)
    for x in widgets:
        v.addWidget(x)
    return w


def confirm_text(pv: dict) -> str:
    """Nội dung xem trước trước khi nhập."""
    lines = [f"Tìm thấy {pv['total']} nhân vật trong gói:", f"• Cập nhật ảnh/dữ liệu: {len(pv['update'])}",
             f"• Thêm mới: {len(pv['new'])}" + (f"  ({', '.join(pv['new'][:5])}{'…' if len(pv['new']) > 5 else ''})" if pv["new"] else "")]
    if pv["no_image"]:
        lines.append(f"• Bỏ qua vì thiếu ảnh: {len(pv['no_image'])}  ({', '.join(pv['no_image'][:5])}{'…' if len(pv['no_image']) > 5 else ''})")
    if pv["no_desc"]:
        lines.append(f"• Chưa có description.md: {len(pv['no_desc'])} nhân vật (vẫn nhập, mô tả để trống)")
    lines.append("\nNhân vật có trong dự án nhưng không có trong gói được giữ nguyên. Tiếp tục nhập?")
    return "\n".join(lines)

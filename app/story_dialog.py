"""Hộp thoại 'Viết truyện từ bối cảnh': nhập bối cảnh, số chương, độ dài, thể loại; AI lập dàn ý rồi viết từng chương."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout

from . import icons, llm
from .models import Project
from .story_writer import MAX_CHAPTERS
from .theme import SP


class StoryDialog(QDialog):
    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.setWindowTitle("Viết truyện từ bối cảnh")
        self.setMinimumSize(620, 520)
        self.resize(720, 600)
        tile = QLabel()
        tile.setProperty("navtile", True)
        tile.setFixedSize(44, 44)
        tile.setAlignment(Qt.AlignCenter)
        icons.attach(tile, "sparkle", 24)
        title = QLabel("Viết truyện từ bối cảnh")
        title.setProperty("heading", True)
        sub = QLabel(f"{project.name}  ·  dùng {llm.describe()}")
        sub.setProperty("caption", True)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title)
        col.addWidget(sub)
        head = QHBoxLayout()
        head.setSpacing(SP.m)
        head.addWidget(tile)
        head.addLayout(col, 1)

        self.brief = QPlainTextEdit(project.synopsis)
        self.brief.setPlaceholderText("Mô tả bối cảnh: thời đại, nơi chốn, nhân vật chính, mâu thuẫn, kết cục mong muốn...")
        self.genre = QLineEdit()
        self.genre.setPlaceholderText("Thể loại / giọng văn (tuỳ chọn), ví dụ: cổ tích, trinh thám, kinh dị nhẹ")
        self.n = QSpinBox()
        self.n.setRange(1, MAX_CHAPTERS)
        self.n.setValue(3)
        self.words = QSpinBox()
        self.words.setRange(200, 3000)
        self.words.setSingleStep(100)
        self.words.setValue(600)
        self.note = QLabel("")
        self.note.setProperty("caption", True)
        self.note.setWordWrap(True)
        for w in (self.n, self.words):
            w.setFixedWidth(110)
            w.valueChanged.connect(self.refresh)
        self.brief.textChanged.connect(self.refresh)
        nums = QHBoxLayout()
        nums.setSpacing(SP.m)
        for text, w in (("Số chương", self.n), ("Từ mỗi chương", self.words)):
            nums.addWidget(QLabel(text))
            nums.addWidget(w)
        nums.addStretch(1)

        self.ok = QPushButton("Viết truyện")
        self.ok.setProperty("primary", True)
        cancel = QPushButton("Huỷ")
        for b in (self.ok, cancel):
            b.setFixedHeight(40)
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self.ok)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.l)
        lay.setSpacing(SP.m)
        lay.addLayout(head)
        lay.addWidget(QLabel("Bối cảnh"))
        lay.addWidget(self.brief, 1)
        lay.addWidget(self.genre)
        lay.addLayout(nums)
        lay.addWidget(self.note)
        lay.addLayout(foot)
        self.refresh()

    def refresh(self) -> None:
        has = bool(self.brief.toPlainText().strip())
        self.ok.setEnabled(has)
        calls = self.n.value() + 1
        self.note.setText(f"Gọi AI {calls} lượt (1 dàn ý + {self.n.value()} chương, tổng ≈ {self.n.value() * self.words.value():,} từ). "
                          "Chương mới được thêm vào sau các chương hiện có; chương đầu dùng lại chương trống nếu có. "
                          "Chỉ tốn lượt gọi LLM, không tốn credit Flow." if has else "Hãy nhập bối cảnh để bắt đầu.")

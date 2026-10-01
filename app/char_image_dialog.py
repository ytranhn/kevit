"""Hộp thoại 'Tạo ảnh (AI)' cho một nhân vật đã có: chỉnh giới tính + mô tả, tạo ảnh bằng Flow/Gemini, xem trước, dùng ảnh."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from . import char_gen, models, settings
from .theme import SP
from .widgets import ElidedLabel
from .workers import Worker


def guess_gender(c: models.Character, story: str = "") -> str:
    """Đoán giới tính từ mô tả hiện có (man/woman...) rồi đối chiếu truyện."""
    words = {w.lower().strip(".,;:") for w in c.description.split()}
    male, female = {"man", "male", "boy", "he", "his", "gentleman"}, {"woman", "female", "girl", "she", "her", "lady"}
    from_desc = "male" if words & male and not words & female else "female" if words & female and not words & male else "unknown"
    return char_gen.decide_gender(from_desc, story, [c.name] + list(c.aliases))


class CharImageDialog(QDialog):
    """self.saved = True nếu người dùng đã bấm 'Dùng ảnh này' (ảnh + mô tả mới đã được lưu vào nhân vật)."""

    def __init__(self, project_name: str, char: models.Character, parent=None):
        super().__init__(parent)
        self.project_name, self.char = project_name, char
        self.saved, self.data = False, None
        self.worker: Worker | None = None
        self.setWindowTitle(f"Tạo ảnh cho {char.name}")
        self.setMinimumSize(780, 660)
        self.resize(860, 700)
        story = char_gen.full_text(models.Project.load(project_name))

        title = QLabel(f"Tạo ảnh cho “{char.name}”")
        title.setProperty("heading", True)
        sub = QLabel("Ảnh được tạo từ mô tả ngoại hình bên dưới (tiếng Anh) và phong cách hình ảnh của dự án. Chọn đúng giới tính trước khi tạo.")
        sub.setProperty("caption", True)
        sub.setWordWrap(True)
        sub.setMinimumHeight(sub.fontMetrics().lineSpacing() * 2 + 4)

        self.gender = QComboBox()
        for label, val in (("Không rõ", "unknown"), ("Nam", "male"), ("Nữ", "female")):
            self.gender.addItem(label, val)
        self.gender.setCurrentIndex(max(0, self.gender.findData(guess_gender(char, story))))
        self.backend = QComboBox()
        self.backend.addItem("Google Flow (Nano Banana)", "flow")
        self.backend.addItem("Gemini API (cần bật billing)", "gemini")
        self.backend.setCurrentIndex(max(0, self.backend.findData(settings.image_backend())))
        self.backend.currentIndexChanged.connect(lambda *_: settings.set_image_backend(self.backend.currentData()))
        self.prompt = QPlainTextEdit(char.description)
        self.prompt.setPlaceholderText("Mô tả ngoại hình bằng tiếng Anh")
        self.prompt.setPlainText(char_gen.apply_gender(char.description, self.gender.currentData()))   # mô tả khớp giới tính ngay khi mở
        self.gender.currentIndexChanged.connect(self.on_gender)
        self.preview = QLabel("Chưa tạo ảnh")
        self.preview.setFixedSize(180, 240)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setProperty("avatarph", True)
        self.old = QLabel()
        self.old.setFixedSize(94, 124)
        self.old.setAlignment(Qt.AlignCenter)
        self.old.setProperty("avatarph", True)
        img = QImage(char.image) if char.image else QImage()
        if not img.isNull():
            self.old.setPixmap(QPixmap.fromImage(img).scaled(90, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.old.setText("Chưa có")
        self.status = ElidedLabel()
        self.status.setProperty("caption", True)
        self.btn_gen, self.btn_copy = QPushButton("Tạo ảnh"), QPushButton("Copy prompt ảnh")
        self.btn_use, btn_close = QPushButton("Dùng ảnh này"), QPushButton("Đóng")
        self.btn_gen.setProperty("primary", True)
        self.btn_use.setEnabled(False)
        self.btn_gen.clicked.connect(self.generate)
        self.btn_copy.clicked.connect(self.copy_prompt)
        self.btn_use.clicked.connect(self.use)
        btn_close.clicked.connect(self.reject)

        def field(label, w):
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(SP.xs)
            c = QLabel(label)
            c.setProperty("caption", True)
            v.addWidget(c)
            v.addWidget(w)
            return box
        opts = QHBoxLayout()
        opts.setSpacing(SP.m)
        opts.addWidget(field("Giới tính", self.gender))
        opts.addWidget(field("Tạo ảnh bằng", self.backend), 1)
        left = QVBoxLayout()
        left.setSpacing(SP.m)
        left.addLayout(opts)
        left.addWidget(field("Ngoại hình (prompt, tiếng Anh)", self.prompt), 1)
        right = QVBoxLayout()
        right.setSpacing(SP.s)
        l_new, l_old = QLabel("Ảnh mới"), QLabel("Ảnh hiện tại")
        for lb in (l_new, l_old):
            lb.setFixedHeight(20)
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(l_new, 0, Qt.AlignHCenter)
        right.addWidget(self.preview, 0, Qt.AlignHCenter)
        right.addWidget(l_old, 0, Qt.AlignHCenter)
        right.addWidget(self.old, 0, Qt.AlignHCenter)
        rightbox = QWidget()                      # khối cố định kích thước: không bao giờ bị nén đè lên nhau
        rightbox.setLayout(right)
        rightbox.setFixedSize(196, 20 + 244 + 20 + 124 + SP.s * 3)
        self.preview.setFixedSize(184, 244)
        card = QFrame()
        card.setProperty("card", True)
        ch = QHBoxLayout(card)
        ch.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        ch.setSpacing(SP.l)
        ch.addLayout(left, 1)
        ch.addWidget(rightbox, 0, Qt.AlignTop)
        foot = QHBoxLayout()
        foot.addWidget(self.status, 1)
        foot.addWidget(btn_close)
        foot.addWidget(self.btn_copy)
        foot.addWidget(self.btn_gen)
        foot.addWidget(self.btn_use)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        for w in (title, sub):
            lay.addWidget(w)
        lay.addWidget(card, 1)
        lay.addLayout(foot)

    def on_gender(self, *_):
        txt = char_gen.apply_gender(self.prompt.toPlainText(), self.gender.currentData())
        self.prompt.setPlainText(txt)

    def full_prompt(self) -> str:
        p = models.Project.load(self.project_name)
        return char_gen.image_prompt(p, self.prompt.toPlainText().strip(), self.gender.currentData())

    def copy_prompt(self):
        QGuiApplication.clipboard().setText(self.full_prompt())
        self.status.set_full("Đã copy prompt ảnh.")

    def generate(self):
        if not self.prompt.toPlainText().strip():
            QMessageBox.information(self, "Thiếu mô tả", "Hãy nhập mô tả ngoại hình (tiếng Anh) trước.")
            return
        prompt, backend, name = self.full_prompt(), self.backend.currentData(), self.char.name
        self.btn_gen.setEnabled(False)
        self.btn_use.setEnabled(False)
        self.status.set_full("Đang tạo ảnh…" + (" (cần Chrome Flow đã đăng nhập, khoảng 30-60 giây)" if backend == "flow" else ""))
        self.worker = Worker(lambda log: char_gen.generate_images(self.project_name, {name: prompt}, backend, log))
        self.worker.log.connect(lambda m: self.status.set_full(m[:120]))
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(lambda e: (self.btn_gen.setEnabled(True), self.status.set_full(""), QMessageBox.warning(self, "Không tạo được ảnh", e)))
        self.worker.start()

    def on_done(self, res):
        out, errs = res
        self.btn_gen.setEnabled(True)
        if not out:
            self.status.set_full("")
            QMessageBox.warning(self, "Không tạo được ảnh", "\n".join(errs) or "Không có ảnh nào được tạo.")
            return
        self.data = next(iter(out.values()))
        img = QImage()
        if img.loadFromData(self.data):
            self.preview.setPixmap(QPixmap.fromImage(img).scaled(180, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.btn_use.setEnabled(True)
        self.status.set_full("Đã tạo ảnh. Bấm “Dùng ảnh này” để thay ảnh của nhân vật, hoặc “Tạo ảnh” để thử lại.")

    def use(self):
        """Lưu ảnh mới + mô tả đã chỉnh vào nhân vật (ảnh cũ do tool tạo được thay thế)."""
        if not self.data:
            return
        try:
            path = char_gen.save_character_image(self.project_name, self.char.name, self.data)
            chars = models.load_characters(self.project_name)
            for c in chars:
                if models.nfc(c.name) == models.nfc(self.char.name):
                    c.image = path
                    c.description = self.prompt.toPlainText().strip() or c.description
            models.save_characters(self.project_name, chars)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không lưu được", str(e))
            return
        self.saved = True
        self.accept()

"""Hộp thoại 'Tạo nhân vật từ truyện': AI đề xuất nhân vật + mô tả ngoại hình, duyệt/sửa, tuỳ chọn tạo ảnh bằng Gemini, rồi thêm vào dự án."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import char_gen, llm, models, settings
from .theme import SP
from .widgets import ElidedLabel
from .workers import Worker


class CharGenDialog(QDialog):
    """self.added = tên các nhân vật vừa thêm vào dự án (rỗng nếu đóng mà không thêm)."""

    def __init__(self, project_name: str, parent=None):
        super().__init__(parent)
        self.project_name = project_name
        self.added: list[str] = []
        self.cands: list[dict] = []
        self.worker: Worker | None = None
        self._row = -1
        self.setWindowTitle("Tạo nhân vật từ truyện")
        self.setMinimumSize(900, 830)
        self.resize(980, 840)

        title = QLabel("Tạo nhân vật từ truyện")
        title.setProperty("heading", True)
        sub = QLabel("AI đọc bối cảnh và các chương của dự án, đề xuất nhân vật kèm mô tả ngoại hình (prompt tiếng Anh, theo phong cách hình ảnh "
                     "của dự án). Bạn duyệt và sửa, tuỳ chọn tạo ảnh tham chiếu bằng Gemini, rồi thêm vào dự án.")
        sub.setProperty("caption", True)
        sub.setWordWrap(True)
        sub.setMinimumHeight(sub.fontMetrics().lineSpacing() * 3 + 4)   # nhãn tự xuống dòng: chừa sẵn 3 dòng để Qt không đánh giá thấp chiều cao cửa sổ

        self.max_n = QSpinBox()
        self.max_n.setRange(1, 30)
        self.max_n.setValue(10)
        self.max_n.setFixedWidth(80)
        self.btn_analyze = QPushButton("Phân tích truyện")
        self.btn_analyze.setProperty("primary", True)
        self.btn_analyze.clicked.connect(self.analyze)
        self.status = ElidedLabel()                  # một dòng, tự cắt "…" khi dài: không làm đổi chiều cao
        self.status.setProperty("caption", True)
        top = QHBoxLayout()
        top.setSpacing(SP.s)
        top.addWidget(QLabel("Số nhân vật tối đa"))
        top.addWidget(self.max_n)
        top.addWidget(self.btn_analyze)
        top.addWidget(self.status, 1)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select)
        self.list.itemChanged.connect(lambda *_: self.update_buttons())
        self.name, self.role, self.aliases = QLineEdit(), QLineEdit(), QLineEdit()
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Mô tả ngoại hình bằng tiếng Anh (dùng làm prompt ảnh và prompt Flow)")
        self.desc_vi = QPlainTextEdit()
        self.desc_vi.setFixedHeight(92)
        self.preview = QLabel("Chưa có ảnh")
        self.preview.setFixedSize(150, 200)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setProperty("avatarph", True)
        self.btn_img = QPushButton("Tạo ảnh bằng Gemini")
        self.btn_copy = QPushButton("Copy prompt ảnh")
        self.btn_img.clicked.connect(self.make_image_current)
        self.btn_copy.clicked.connect(self.copy_prompt)
        for w in (self.name, self.role, self.aliases):
            w.editingFinished.connect(self.store)
        self.prompt.textChanged.connect(self.store)
        self.desc_vi.textChanged.connect(self.store)

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
        for b in (self.btn_img, self.btn_copy):
            b.setFixedHeight(36)
        picbox = QWidget()                        # khối ảnh + nút có kích thước cố định: không bao giờ bị nén đè lên nhau
        pic = QVBoxLayout(picbox)
        pic.setContentsMargins(0, 0, 0, 0)
        pic.setSpacing(SP.s)
        pic.addWidget(self.preview, 0, Qt.AlignHCenter)
        pic.addWidget(self.btn_img)
        pic.addWidget(self.btn_copy)
        picbox.setFixedSize(196, 200 + 36 * 2 + SP.s * 2)    # đủ rộng cho nhãn nút dài nhất
        form = QVBoxLayout()
        form.setSpacing(SP.m)
        form.addWidget(field("Tên", self.name))
        form.addWidget(field("Vai trò", self.role))
        form.addWidget(field("Tên gọi khác (cách nhau dấu phẩy)", self.aliases))
        row = QHBoxLayout()
        row.setSpacing(SP.l)
        row.addWidget(picbox, 0, Qt.AlignTop)
        row.addLayout(form, 1)
        card = QFrame()
        card.setProperty("card", True)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        cv.setSpacing(SP.m)
        cv.addLayout(row)
        cv.addWidget(field("Ngoại hình (prompt, tiếng Anh)", self.prompt), 1)
        cv.addWidget(field("Mô tả tiếng Việt", self.desc_vi))
        body = QHBoxLayout()
        body.setSpacing(SP.l)
        body.addWidget(self.list, 3)
        body.addWidget(card, 5)

        self.btn_close = QPushButton("Đóng")
        self.btn_imgs = QPushButton("Tạo ảnh cho các mục đã chọn")
        self.btn_add = QPushButton("Thêm vào dự án")
        self.btn_add.setProperty("primary", True)
        self.btn_close.clicked.connect(self.reject)
        self.btn_imgs.clicked.connect(self.make_images_checked)
        self.btn_add.clicked.connect(self.add)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(self.btn_close)
        foot.addWidget(self.btn_imgs)
        foot.addWidget(self.btn_add)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        for item in (title, sub):
            lay.addWidget(item)
        lay.addLayout(top)
        lay.addLayout(body, 1)
        lay.addLayout(foot)
        self.update_buttons()
        self.set_editor_enabled(False)

    # ---------- trạng thái ----------
    def busy(self, on: bool, text: str = ""):
        self.btn_analyze.setEnabled(not on)
        self.status.set_full(text)
        self.update_buttons(on)

    def update_buttons(self, busy: bool = False):
        checked = self.checked()
        self.btn_add.setEnabled(bool(checked) and not busy)
        self.btn_imgs.setEnabled(bool(checked) and not busy)
        self.btn_img.setEnabled(0 <= self._row < len(self.cands) and not busy)
        self.btn_copy.setEnabled(0 <= self._row < len(self.cands))

    def set_editor_enabled(self, on: bool):
        for w in (self.name, self.role, self.aliases, self.prompt, self.desc_vi):
            w.setEnabled(on)

    def checked(self) -> list[int]:
        return [i for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]

    # ---------- phân tích ----------
    def analyze(self):
        try:
            p = models.Project.load(self.project_name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không đọc được dự án", str(e))
            return
        ok, why = llm.is_configured()
        if not ok:
            QMessageBox.warning(self, "Chưa cấu hình mô hình AI", why)
            return
        existing = models.load_characters(self.project_name)
        self.busy(True, f"Đang đọc truyện bằng {llm.describe()}…")
        n = self.max_n.value()
        self.worker = Worker(lambda log: char_gen.suggest_characters(p, existing, n, log))
        self.worker.log.connect(lambda m: self.status.set_full(m[:120]))
        self.worker.done.connect(self.on_suggested)
        self.worker.failed.connect(lambda e: (self.busy(False, ""), QMessageBox.warning(self, "Không phân tích được", e)))
        self.worker.start()

    def on_suggested(self, res):
        self.cands = list(res)
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.cands:
            it = QListWidgetItem(f"{c['name']}  ·  {c['role']}" if c["role"] else c["name"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.busy(False, f"Đề xuất {len(self.cands)} nhân vật mới." if self.cands else
                  "Không có nhân vật mới nào để đề xuất (các nhân vật chính đã có trong dự án).")
        if self.cands:
            self.list.setCurrentRow(0)

    # ---------- chỉnh từng nhân vật ----------
    def select(self, row: int):
        self.store()                                  # lưu thay đổi của dòng trước
        self._row = row
        ok = 0 <= row < len(self.cands)
        self.set_editor_enabled(ok)
        if ok:
            c = self.cands[row]
            for w, v in ((self.name, c["name"]), (self.role, c["role"]), (self.aliases, ", ".join(c["aliases"]))):
                w.blockSignals(True); w.setText(v); w.blockSignals(False)
            for w, v in ((self.prompt, c["appearance_en"]), (self.desc_vi, c["description_vi"])):
                w.blockSignals(True); w.setPlainText(v); w.blockSignals(False)
            self.show_image(c)
        self.update_buttons()

    def store(self):
        r = self._row
        if not (0 <= r < len(self.cands)) or not self.name.isEnabled():
            return
        c = self.cands[r]
        c["name"] = models.nfc(self.name.text().strip()) or c["name"]
        c["role"] = self.role.text().strip()
        c["aliases"] = [models.nfc(a.strip()) for a in self.aliases.text().split(",") if a.strip()]
        c["appearance_en"] = self.prompt.toPlainText().strip()
        c["description_vi"] = self.desc_vi.toPlainText().strip()
        self.list.item(r).setText(f"{c['name']}  ·  {c['role']}" if c["role"] else c["name"])

    def show_image(self, c: dict):
        data = c.get("image_bytes")
        img = QImage()
        if data and img.loadFromData(data):
            self.preview.setPixmap(QPixmap.fromImage(img).scaled(150, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Chưa có ảnh")

    def copy_prompt(self):
        if 0 <= self._row < len(self.cands):
            self.store()
            p = models.Project.load(self.project_name)
            QGuiApplication.clipboard().setText(char_gen.image_prompt(p, self.cands[self._row]["appearance_en"]))
            self.status.set_full("Đã copy prompt ảnh. Dán vào công cụ tạo ảnh bạn dùng (Flow, Gemini…), rồi gắn ảnh vào nhân vật ở tab Nhân vật.")

    # ---------- tạo ảnh ----------
    def _gen(self, rows: list[int]):
        self.store()
        p = models.Project.load(self.project_name)
        prompts = {r: char_gen.image_prompt(p, self.cands[r]["appearance_en"]) for r in rows}
        self.busy(True, f"Đang tạo ảnh 0/{len(rows)}…")

        def job(log):
            out, errs = {}, []
            for k, r in enumerate(rows, 1):
                log(f"Đang tạo ảnh {k}/{len(rows)}: {self.cands[r]['name']}…")
                try:
                    out[r] = char_gen.generate_image(prompts[r], None, log)
                except Exception as e:  # noqa: BLE001 - một ảnh lỗi không làm hỏng cả lô
                    errs.append(f"{self.cands[r]['name']}: {e}")
            return out, errs
        self.worker = Worker(job)
        self.worker.log.connect(lambda m: self.status.set_full(m[:120]))
        self.worker.done.connect(self.on_images)
        self.worker.failed.connect(lambda e: (self.busy(False, ""), QMessageBox.warning(self, "Không tạo được ảnh", e)))
        self.worker.start()

    def on_images(self, res):
        out, errs = res
        for r, data in out.items():
            self.cands[r]["image_bytes"] = data
        if 0 <= self._row < len(self.cands):
            self.show_image(self.cands[self._row])
        self.busy(False, f"Đã tạo {len(out)} ảnh." + (f" {len(errs)} ảnh lỗi." if errs else ""))
        if errs:
            QMessageBox.warning(self, "Một số ảnh không tạo được", "\n".join(errs[:6]))

    def make_image_current(self):
        if 0 <= self._row < len(self.cands):
            self._gen([self._row])

    def make_images_checked(self):
        rows = self.checked()
        if rows and QMessageBox.question(
                self, "Tạo ảnh bằng Gemini", f"Tạo ảnh cho {len(rows)} nhân vật bằng model {settings.image_model()}? "
                "Mỗi ảnh tính phí theo bảng giá Gemini API của bạn.") == QMessageBox.Yes:
            self._gen(rows)

    # ---------- thêm vào dự án ----------
    def add(self):
        self.store()
        rows = self.checked()
        if not rows:
            return
        try:
            self.added = char_gen.add_characters(self.project_name, [self.cands[r] for r in rows])
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không thêm được", str(e))
            return
        self.accept()

"""Hộp thoại 'Tạo nhân vật từ truyện': AI đề xuất nhân vật + mô tả ngoại hình, duyệt/sửa, tuỳ chọn tạo ảnh bằng Gemini, rồi thêm vào dự án."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import char_gen, llm, models, settings
from .theme import SP
from .widgets import ElidedLabel
from .workers import Worker


class ChapterPicker(QDialog):
    """Chọn các chương để AI phân tích (có ô tìm, chọn nhanh). Chương chưa có truyện bị khoá. self.selected = id các chương đã chọn."""

    def __init__(self, project: models.Project, selected: list[str], current_id: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chọn chương để phân tích")
        self.setMinimumSize(520, 560)
        self.current_id = current_id
        self.selected: list[str] = list(selected)
        head = QLabel("Chỉ đọc các chương bạn chọn: AI tập trung hơn nên nhận diện nhân vật chính xác hơn, và tạo ảnh trên Flow sẽ nằm trong "
                      "project của chương nếu chỉ chọn một chương.")
        head.setProperty("caption", True)
        head.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm chương (tên hoặc số)…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter)
        self.list = QListWidget()
        for c in project.chapters:
            words = len(c.story.split())
            it = QListWidgetItem(f"{c.name}   ·   " + (f"{words:,} từ".replace(",", ".") if words else "chưa có truyện"))
            it.setData(Qt.UserRole, c.id)
            flags = it.flags() | Qt.ItemIsUserCheckable
            if not words:
                flags &= ~Qt.ItemIsEnabled
            it.setFlags(flags)
            it.setCheckState(Qt.Checked if c.id in selected and words else Qt.Unchecked)
            self.list.addItem(it)
        self.count = QLabel("")
        self.count.setProperty("caption", True)
        self.list.itemChanged.connect(lambda *_: self.update_count())
        b_cur, b_all, b_none = QPushButton("Chương đang mở"), QPushButton("Tất cả"), QPushButton("Bỏ chọn")
        b_cur.setEnabled(any(c.id == current_id and c.story.strip() for c in project.chapters))
        b_cur.clicked.connect(lambda: self.set_only(current_id))
        b_all.clicked.connect(lambda: self.set_all(True))
        b_none.clicked.connect(lambda: self.set_all(False))
        quick = QHBoxLayout()
        quick.setSpacing(SP.s)
        for b in (b_cur, b_all, b_none):
            b.setFixedHeight(36)
            quick.addWidget(b)
        quick.addStretch()
        self.btn_ok = QPushButton("Dùng các chương đã chọn")
        self.btn_ok.setProperty("primary", True)
        self.btn_ok.setFixedHeight(36)
        self.btn_ok.clicked.connect(self.accept_selection)
        btn_cancel = QPushButton("Huỷ")
        btn_cancel.setFixedHeight(36)
        btn_cancel.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addWidget(self.count)
        foot.addStretch()
        foot.addWidget(btn_cancel)
        foot.addWidget(self.btn_ok)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        for w in (head, self.search):
            lay.addWidget(w)
        lay.addLayout(quick)
        lay.addWidget(self.list, 1)
        lay.addLayout(foot)
        self.update_count()

    def _items(self):
        return [self.list.item(i) for i in range(self.list.count())]

    def filter(self, q: str):
        import unicodedata
        norm = lambda t: "".join(c for c in unicodedata.normalize("NFD", t.replace("đ", "d").replace("Đ", "D").lower()) if unicodedata.category(c) != "Mn")
        nq = norm(q.strip())
        for it in self._items():
            it.setHidden(bool(nq) and nq not in norm(it.text()))

    def set_all(self, on: bool):
        for it in self._items():
            if it.flags() & Qt.ItemIsEnabled and not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def set_only(self, cid: str):
        for it in self._items():
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) == cid and it.flags() & Qt.ItemIsEnabled else Qt.Unchecked)

    def checked_ids(self) -> list[str]:
        return [it.data(Qt.UserRole) for it in self._items() if it.checkState() == Qt.Checked]

    def update_count(self):
        n = len(self.checked_ids())
        self.count.setText(f"Đã chọn {n}/{self.list.count()} chương")
        self.btn_ok.setEnabled(n > 0)

    def accept_selection(self):
        self.selected = self.checked_ids()
        self.accept()


class CharGenDialog(QDialog):
    """self.added = tên các nhân vật vừa thêm vào dự án (rỗng nếu đóng mà không thêm)."""

    def __init__(self, project_name: str, parent=None, current_chapter_id: str = ""):
        super().__init__(parent)
        self.project_name = project_name
        self.current_chapter_id = current_chapter_id
        self.chapter_ids: list[str] = []
        self.proj = models.Project.load(project_name)
        self.added: list[str] = []
        self.cands: list[dict] = []
        self.worker: Worker | None = None
        self._row = -1
        self.setWindowTitle("Tạo nhân vật từ truyện")
        self.setMinimumSize(900, 880)
        self.resize(980, 900)

        title = QLabel("Tạo nhân vật từ truyện")
        title.setProperty("heading", True)
        sub = QLabel("AI đọc bối cảnh và các chương bạn chọn, đề xuất nhân vật kèm mô tả ngoại hình (prompt tiếng Anh, theo phong cách hình ảnh "
                     "của dự án). Bạn duyệt và sửa, tuỳ chọn tạo ảnh tham chiếu bằng Google Flow hoặc Gemini, rồi thêm vào dự án.")
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
        self.backend = QComboBox()                # nơi tạo ảnh: Flow (mặc định, dùng tài khoản Flow đã đăng nhập) hoặc Gemini API
        self.backend.addItem("Google Flow (Nano Banana)", "flow")
        self.backend.addItem("Gemini API (cần bật billing)", "gemini")
        self.backend.setCurrentIndex(max(0, self.backend.findData(settings.image_backend())))
        self.backend.currentIndexChanged.connect(lambda *_: settings.set_image_backend(self.backend.currentData()))
        top = QHBoxLayout()
        top.setSpacing(SP.s)
        top.addWidget(QLabel("Số nhân vật tối đa"))
        top.addWidget(self.max_n)
        top.addWidget(self.btn_analyze)
        top.addWidget(self.status, 1)
        top.addWidget(QLabel("Tạo ảnh bằng"))
        top.addWidget(self.backend)

        self.scope = ElidedLabel()                   # tóm tắt chương đang được chọn để phân tích
        self.btn_scope = QPushButton("Chọn chương…")
        self.btn_scope.setFixedHeight(36)
        self.btn_scope.clicked.connect(self.pick_chapters)
        self.img_target = ElidedLabel()              # nơi ảnh Flow sẽ được tạo (project nào)
        self.img_target.setProperty("caption", True)
        scope_row = QHBoxLayout()
        scope_row.setSpacing(SP.s)
        scope_row.addWidget(QLabel("Chương phân tích"))
        scope_row.addWidget(self.scope, 1)
        scope_row.addWidget(self.btn_scope)
        pick_default = [c.id for c in char_gen.pick_chapters(self.proj)]
        self.chapter_ids = [self.current_chapter_id] if self.current_chapter_id in pick_default else list(pick_default)
        self.backend.currentIndexChanged.connect(lambda *_: self.update_scope())

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select)
        self.list.itemChanged.connect(lambda *_: self.update_buttons())
        self.name, self.role, self.aliases = QLineEdit(), QLineEdit(), QLineEdit()
        self.gender = QComboBox()                 # giới tính: AI đoán + đối chiếu truyện, bạn chỉnh được; đổi là mô tả ngoại hình đổi theo
        for label, val in (("Không rõ", "unknown"), ("Nam", "male"), ("Nữ", "female")):
            self.gender.addItem(label, val)
        self.gender.currentIndexChanged.connect(self.on_gender)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Mô tả ngoại hình bằng tiếng Anh (dùng làm prompt ảnh và prompt Flow)")
        self.desc_vi = QPlainTextEdit()
        self.desc_vi.setFixedHeight(92)
        self.preview = QLabel("Chưa có ảnh")
        self.preview.setFixedSize(154, 204)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setProperty("avatarph", True)
        self.btn_img = QPushButton("Tạo ảnh")
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
        picbox.setFixedSize(196, 204 + 36 * 2 + SP.s * 2)    # đủ rộng cho nhãn nút dài nhất
        form = QVBoxLayout()
        form.setSpacing(SP.m)
        form.addWidget(field("Tên", self.name))
        form.addWidget(field("Vai trò", self.role))
        form.addWidget(field("Giới tính (quyết định hình tượng khi tạo ảnh)", self.gender))
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
        lay.addLayout(scope_row)
        lay.addWidget(self.img_target)
        lay.addLayout(top)
        lay.addLayout(body, 1)
        lay.addLayout(foot)
        self.update_buttons()
        self.set_editor_enabled(False)
        self.update_scope()

    # ---------- chọn chương ----------
    def chapter_name(self, cid: str) -> str:
        c = next((c for c in self.proj.chapters if c.id == cid), None)
        return c.name if c else cid

    def image_chapter_id(self) -> str | None:
        """Chương có project Flow riêng để tạo ảnh: chỉ khi phân tích đúng MỘT chương; nhiều chương thì dùng project chung của dự án."""
        return self.chapter_ids[0] if len(self.chapter_ids) == 1 else None

    def update_scope(self):
        n, total = len(self.chapter_ids), len(char_gen.pick_chapters(self.proj))
        if n == 1:
            self.scope.set_full(f"{self.chapter_name(self.chapter_ids[0])}" + ("  (đang mở)" if self.chapter_ids[0] == self.current_chapter_id else ""))
        elif n and n == total:
            self.scope.set_full(f"Tất cả {n} chương có truyện")
        else:
            self.scope.set_full(f"{n}/{total} chương: " + ", ".join(self.chapter_name(c) for c in self.chapter_ids[:3]) + ("…" if n > 3 else ""))
        if self.backend.currentData() != "flow":
            self.img_target.set_full("")
        elif self.image_chapter_id():
            self.img_target.set_full(f"Ảnh tạo trên Flow sẽ nằm trong project «{self.proj.name} · {self.chapter_name(self.image_chapter_id())}» "
                                     "(cùng project với clip video của chương, tự tạo nếu chưa có).")
        else:
            self.img_target.set_full(f"Nhiều chương được chọn: ảnh tạo trên Flow nằm trong project chung của dự án «{self.proj.name}». "
                                     "Chọn đúng một chương để tạo ảnh ngay trong project của chương.")

    def pick_chapters(self):
        self.proj = models.Project.load(self.project_name)
        dlg = ChapterPicker(self.proj, self.chapter_ids, self.current_chapter_id, self)
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            self.chapter_ids = dlg.selected
            self.update_scope()

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
        for w in (self.name, self.role, self.aliases, self.prompt, self.desc_vi, self.gender):
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
        ids = [c for c in self.chapter_ids if any(x.id == c for x in p.chapters)] or None
        self.busy(True, f"Đang đọc {len(ids) if ids else 'mọi'} chương bằng {llm.describe()}…")
        n = self.max_n.value()
        self.worker = Worker(lambda log: char_gen.suggest_characters(p, existing, n, log, chapters=ids))
        self.worker.log.connect(lambda m: self.status.set_full(m[:120]))
        self.worker.done.connect(self.on_suggested)
        self.worker.failed.connect(lambda e: (self.busy(False, ""), QMessageBox.warning(self, "Không phân tích được", e)))
        self.worker.start()

    def on_suggested(self, res):
        self.cands = list(res)
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.cands:
            it = QListWidgetItem(self._label(c))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.busy(False, f"Đề xuất {len(self.cands)} nhân vật mới." if self.cands else
                  "Không có nhân vật mới nào để đề xuất (các nhân vật chính đã có trong dự án).")
        if self.cands:
            self.list.setCurrentRow(0)

    @staticmethod
    def _label(c: dict) -> str:
        g = {"male": "nam", "female": "nữ"}.get(c.get("gender", ""), "")
        return "  ·  ".join(x for x in (c["name"], c.get("role", ""), g) if x)

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
            self.gender.blockSignals(True)
            self.gender.setCurrentIndex(max(0, self.gender.findData(c.get("gender", "unknown"))))
            self.gender.blockSignals(False)
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
        c["gender"] = self.gender.currentData()
        c["description_vi"] = self.desc_vi.toPlainText().strip()
        self.list.item(r).setText(self._label(c))

    def on_gender(self, *_):
        """Đổi giới tính -> sửa luôn mô tả ngoại hình cho khớp (man/woman, he/she)."""
        if not (0 <= self._row < len(self.cands)):
            return
        g = self.gender.currentData()
        txt = char_gen.apply_gender(self.prompt.toPlainText(), g)
        self.prompt.blockSignals(True)
        self.prompt.setPlainText(txt)
        self.prompt.blockSignals(False)
        self.store()

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
            c = self.cands[self._row]
            QGuiApplication.clipboard().setText(char_gen.image_prompt(p, c["appearance_en"], c.get("gender", "unknown")))
            self.status.set_full("Đã copy prompt ảnh. Dán vào công cụ tạo ảnh bạn dùng, rồi gắn ảnh vào nhân vật ở tab Nhân vật.")

    # ---------- tạo ảnh ----------
    def _gen(self, rows: list[int]):
        self.store()
        p = models.Project.load(self.project_name)
        names = [self.cands[r]["name"] for r in rows]
        prompts = {self.cands[r]["name"]: char_gen.image_prompt(p, self.cands[r]["appearance_en"], self.cands[r].get("gender", "unknown")) for r in rows}
        backend = self.backend.currentData()
        chap = self.image_chapter_id()
        self.busy(True, f"Đang tạo ảnh 0/{len(rows)}…")

        def job(log):
            out, errs = char_gen.generate_images(self.project_name, prompts, backend, log, chapter_id=chap)
            return {rows[names.index(n)]: data for n, data in out.items()}, errs
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
        if not rows:
            return
        if self.backend.currentData() == "flow":
            msg = (f"Tạo ảnh cho {len(rows)} nhân vật bằng Google Flow (model Nano Banana)? Cần Chrome Flow đã đăng nhập; "
                   "ảnh được tạo trong " + (f"project Flow của {self.chapter_name(self.image_chapter_id())}" if self.image_chapter_id() else "project chung của dự án")
                   + ", thường không tốn tín dụng (giá hiện trong nhật ký).")
        else:
            msg = f"Tạo ảnh cho {len(rows)} nhân vật bằng Gemini API (model {settings.image_model()})? Mỗi ảnh tính phí theo bảng giá Gemini API của bạn."
        if QMessageBox.question(self, "Tạo ảnh nhân vật", msg) == QMessageBox.Yes:
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

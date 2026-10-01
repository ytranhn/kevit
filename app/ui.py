from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from . import theme, characters_pack, flow_auto, llm, models, settings, trash
from .models import Character
from .project_tab import ProjectTab
from .pack_guide import PackGuideDialog, confirm_text
from .welcome import Welcome
from .theme import SP
from .widgets import Segmented, StatusStrip, avatar, repolish, rounded_pixmap
from .workers import Worker


class CharactersTab(QWidget):
    deleted = Signal(list)          # tên các nhân vật vừa bị xoá (để dọn khỏi scene)
    chars_changed = Signal()        # danh sách nhân vật vừa được nạp/đổi (để nơi khác vẽ lại ảnh)

    def __init__(self):
        super().__init__()
        self.usage_counter = None   # callable(names) -> {tên: số scene đang dùng}, do cửa sổ chính gắn vào
        self.project_name = ""
        self._chars: list[Character] = []
        self._mtime = 0.0
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)   # Shift/⌘+click để xoá nhiều nhân vật
        self.hint = QLabel("", self.list.viewport())                # trạng thái trống: hướng dẫn thay vì ô trắng
        self.hint.setProperty("caption", True)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.list.viewport().installEventFilter(self)
        self.name, self.aliases, self.role, self.zh = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Ví dụ: young man, long black hair, dark green robe with gold trim")
        self.desc_vi = QPlainTextEdit()
        self.desc_vi.setPlaceholderText("Mô tả gốc tiếng Việt")
        self.desc_vi.setMaximumHeight(92)
        self.img_path, self.preview = "", QLabel("Chưa có ảnh")
        self.preview.setFixedSize(176, 176)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setProperty("avatarph", True)
        self.desc_vi.setFixedHeight(88)

        def field(label: str, w: QWidget) -> QWidget:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(SP.xs)
            cap = QLabel(label)
            cap.setProperty("caption", True)
            v.addWidget(cap)
            v.addWidget(w)
            return box

        btn_img, btn_save, btn_new, btn_del, btn_pack = (QPushButton(t) for t in
                                                         ("Chọn ảnh…", "Lưu", "+ Thêm mới", "Xoá", "Nhập gói…"))
        btn_save.setProperty("primary", True)
        btn_del.setProperty("danger", True)
        btn_img.clicked.connect(self.pick_image)
        btn_save.clicked.connect(self.save)
        btn_new.clicked.connect(self.new)
        btn_del.clicked.connect(self.delete)
        btn_pack.clicked.connect(self.import_pack)

        top = QHBoxLayout()
        top.setSpacing(SP.l)
        pic = QVBoxLayout()
        pic.setSpacing(SP.s)
        pic.addWidget(self.preview)
        pic.addWidget(btn_img)
        pic.addStretch()
        top.addLayout(pic)
        main = QVBoxLayout()
        main.setSpacing(SP.m)
        main.addWidget(field("Tên chuẩn", self.name))
        main.addWidget(field("Tên Hán", self.zh))
        main.addWidget(field("Vai trò", self.role))
        top.addLayout(main, 1)

        card = QFrame()
        card.setProperty("card", True)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        cv.setSpacing(SP.m)
        title = QLabel("Thông tin nhân vật")
        title.setProperty("subheading", True)
        cv.addWidget(title)
        cv.addLayout(top)
        cv.addWidget(field("Tên gọi khác (cách nhau dấu phẩy)", self.aliases))
        cv.addWidget(field("Mô tả (prompt, tiếng Anh): đưa thẳng vào prompt Flow, nên khớp với ảnh tham chiếu", self.desc), 1)
        cv.addWidget(field("Mô tả gốc tiếng Việt (từ gói nhân vật, chỉ để tham khảo)", self.desc_vi))
        foot = QHBoxLayout()
        foot.setSpacing(SP.s)
        foot.addWidget(btn_del)
        foot.addStretch()
        foot.addWidget(btn_save)
        cv.addLayout(foot)

        lcol = QVBoxLayout()
        lcol.setSpacing(SP.m)
        lcol.addWidget(self.list, 1)
        lr = QHBoxLayout()
        lr.setSpacing(SP.s)
        lr.addWidget(btn_new, 1)
        lr.addWidget(btn_pack, 1)
        lcol.addLayout(lr)
        lay = QHBoxLayout(self)
        lay.setSpacing(SP.l)
        lay.addLayout(lcol, 4)
        lay.addWidget(card, 7)
        self.list.currentRowChanged.connect(self.show_char)
        self.refresh()

    @property
    def chars(self) -> list[Character]:
        """Danh sách nhân vật của dự án; tự nạp lại nếu file trên đĩa vừa được cập nhật bởi nơi khác (vd. nhập gói)."""
        if self.project_name and models.characters_mtime(self.project_name) != self._mtime:
            self._reload()
        return self._chars

    def _reload(self):
        keep = self.list.currentRow()
        self._chars = models.load_characters(self.project_name) if self.project_name else []
        self._mtime = models.characters_mtime(self.project_name) if self.project_name else 0.0
        self.refresh()
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)
        self.chars_changed.emit()

    def set_project(self, name: str):
        self.project_name = name
        self._chars = models.load_characters(name) if name else []
        self._mtime = models.characters_mtime(name) if name else 0.0
        self.new()
        self.refresh()
        if self._chars:
            self.list.setCurrentRow(0)
        self.chars_changed.emit()

    def showEvent(self, e):
        super().showEvent(e)
        _ = self.chars      # kích hoạt tự nạp lại nếu file đã đổi

    def eventFilter(self, obj, ev):
        if obj is self.list.viewport() and ev.type() == QEvent.Resize:
            self.hint.setGeometry(SP.xl, 0, obj.width() - 2 * SP.xl, obj.height())
        return super().eventFilter(obj, ev)

    def refresh(self):
        self.hint.setText("Chưa có nhân vật.\n\nBấm “+ Thêm mới” để tạo từng nhân vật, hoặc “Nhập gói…” để nạp nhiều nhân vật "
                          "cùng lúc từ một thư mục ảnh." if self.project_name else
                          "Chưa có dự án.\n\nHãy tạo dự án ở tab Dự án trước, nhân vật được lưu riêng cho từng dự án.")
        self.hint.setGeometry(SP.xl, 0, self.list.viewport().width() - 2 * SP.xl, self.list.viewport().height())
        self.hint.setVisible(not self._chars)
        self.list.blockSignals(True)
        self.list.clear()
        self.list.setIconSize(QSize(34, 34))
        for c in self._chars:
            self.list.addItem(QListWidgetItem(QIcon(avatar(c.image, 34)), c.name + (f"  ·  {c.role}" if c.role else "")))
        self.list.blockSignals(False)

    def new(self):
        self.list.clearSelection()
        for w in (self.name, self.aliases, self.role, self.zh):
            w.clear()
        self.desc.clear()
        self.desc_vi.clear()
        self.img_path = ""
        self.set_preview()

    def show_char(self, i):
        if i < 0 or i >= len(self._chars):
            return
        c = self._chars[i]
        self.name.setText(c.name)
        self.zh.setText(c.zh)
        self.role.setText(c.role)
        self.aliases.setText(", ".join(c.aliases))
        self.desc.setPlainText(c.description)
        self.desc_vi.setPlainText(c.description_vi)
        self.img_path = c.image
        self.set_preview()

    def set_preview(self):
        if self.img_path and Path(self.img_path).exists():
            self.preview.setProperty("avatarph", False)
            self.preview.setPixmap(rounded_pixmap(self.img_path, 176, 16))
        else:
            self.preview.setProperty("avatarph", True)
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Chưa có ảnh")
        repolish(self.preview)

    def pick_image(self):
        f, _ = QFileDialog.getOpenFileName(self, "Ảnh nhân vật", "", "Ảnh (*.png *.jpg *.jpeg *.webp)")
        if f:
            self.img_path = f
            self.set_preview()

    def save(self):
        name = models.nfc(self.name.text().strip())
        if not name:
            return
        if not self.project_name:
            QMessageBox.warning(self, "Thiếu dự án", "Chọn hoặc tạo dự án trước.")
            return
        _ = self.chars
        img = models.import_image(self.project_name, self.img_path, name) if self.img_path else ""
        c = Character(name, self.desc.toPlainText().strip(),
                      [models.nfc(a.strip()) for a in self.aliases.text().split(",") if a.strip()], img,
                      self.role.text().strip(), self.zh.text().strip(), self.desc_vi.toPlainText().strip())
        for i, old in enumerate(self._chars):
            if old.name == name:
                self._chars[i] = c
                break
        else:
            self._chars.append(c)
        models.save_characters(self.project_name, self._chars)
        self._mtime = models.characters_mtime(self.project_name)
        self.refresh()
        self.chars_changed.emit()

    def delete(self):
        """Xoá nhân vật đang chọn (có thể nhiều): bỏ khỏi danh sách, ảnh vào thùng rác của dự án, gỡ tên khỏi các scene."""
        rows = sorted({i.row() for i in self.list.selectedIndexes()})
        if not rows or not self.project_name:
            return
        _ = self.chars
        victims = [self._chars[r] for r in rows if 0 <= r < len(self._chars)]
        names = [c.name for c in victims]
        usage = self.usage_counter(names) if callable(self.usage_counter) else {}
        used = {n: k for n, k in usage.items() if k}
        extra = ("\n\nĐang được dùng trong scene: " + ", ".join(f"{n} ({k})" for n, k in used.items())
                 + ". Tên sẽ được gỡ khỏi các scene đó.") if used else ""
        label = names[0] if len(names) == 1 else f"{len(names)} nhân vật"
        if QMessageBox.question(self, "Xoá nhân vật",
                                f"Xoá {label}?\nẢnh tham chiếu sẽ chuyển vào thùng rác của dự án (khôi phục được).{extra}"
                                ) != QMessageBox.Yes:
            return
        trash.move_to_trash(models.PROJ_DIR / self.project_name, [c.image for c in victims])
        gone = {id(c) for c in victims}
        self._chars[:] = [c for c in self._chars if id(c) not in gone]
        models.save_characters(self.project_name, self._chars)
        self._mtime = models.characters_mtime(self.project_name)
        self.new()
        self.refresh()
        self.chars_changed.emit()
        self.deleted.emit(names)

    def import_pack(self):
        """Hướng dẫn -> chọn thư mục -> xem trước -> xác nhận -> nhập."""
        if not self.project_name:
            QMessageBox.warning(self, "Thiếu dự án", "Chọn hoặc tạo dự án trước.")
            return
        guide = PackGuideDialog(self)
        if guide.exec() != QDialog.Accepted or not guide.folder:
            return
        folder = guide.folder
        try:
            pv = characters_pack.preview_pack(self.project_name, folder)
        except FileNotFoundError:
            QMessageBox.warning(self, "Chưa đúng cấu trúc gói", f"Không thấy file character_index.csv trong:\n{folder}\n\n"
                                "Chọn đúng thư mục chứa file đó (không phải thư mục con). Bấm “Nhập gói…” lại để xem hướng dẫn.")
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không đọc được gói", f"{e}\n\nKiểm tra file character_index.csv: UTF-8, đủ cột No, Tên, Tên Trung, Vai trò, Folder.")
            return
        if not pv["update"] and not pv["new"]:
            QMessageBox.warning(self, "Gói không có nhân vật hợp lệ", confirm_text(pv).split("\n\n")[0])
            return
        if QMessageBox.question(self, "Xem trước gói nhân vật", confirm_text(pv)) != QMessageBox.Yes:
            return
        try:
            report = characters_pack.import_pack(self.project_name, folder)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Nhập gói lỗi", str(e))
            return
        self._reload()
        QMessageBox.information(self, "Nhập gói nhân vật", "\n".join(report))


class SettingsTab(QWidget):
    """Cài đặt chung (mô hình LLM, khoá API): bố cục một cột canh giữa, mỗi nhóm một thẻ."""

    def __init__(self):
        super().__init__()
        self.provider = Segmented()
        self.provider.addItem("Claude", "claude")
        self.provider.addItem("Gemini", "gemini")
        self.provider.setCurrentIndex(max(0, self.provider.findData(settings.llm_provider())))

        self.claude_key = self._secret(settings.claude_api_key(), "sk-ant-…")
        self.base_url = QLineEdit(settings.claude_base_url())
        self.base_url.setPlaceholderText("https://proxy.example.com")
        self.model = QLineEdit(settings.claude_model())
        self.model.setPlaceholderText(settings.CLAUDE_DEFAULT_MODEL)
        self.proxy = QLineEdit(settings.claude_proxy())
        self.proxy.setPlaceholderText("http://127.0.0.1:7890")
        self.gemini_key = self._secret(settings.get_api_key(), "AIza…")

        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        self.btn_test = QPushButton("Lưu và thử kết nối")
        btn_save = QPushButton("Lưu")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self.save)
        self.btn_test.clicked.connect(self.test)

        # --- thẻ 1: mô hình tách scene ---
        self.claude_box = QWidget()
        cv = QVBoxLayout(self.claude_box)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(SP.m)
        cv.addWidget(self._field("API key", "Key của bạn hoặc key do nhà cung cấp proxy cấp. Được lưu trên máy này.", self.claude_key))
        cv.addWidget(self._field("Địa chỉ API (proxy)",
                                 "Để trống để dùng api.anthropic.com. Dán địa chỉ gốc, không kèm /v1/messages.", self.base_url))
        cv.addWidget(self._field("Model", "Tên model theo proxy của bạn.", self.model))
        cv.addWidget(self._field("HTTP proxy mạng (tuỳ chọn)", "Chỉ cần khi máy phải đi qua proxy mạng.", self.proxy))
        self.gemini_note = QLabel("Gemini dùng khoá ở thẻ “Gemini API” bên dưới.")
        self.gemini_note.setProperty("caption", True)

        llm_card = self._card("Mô hình tách scene và viết thuyết minh",
                              "Dùng để chia chương thành scene, viết lại thuyết minh và gộp scene.")
        row = QHBoxLayout()
        lab = QLabel("Nhà cung cấp")
        row.addWidget(lab)
        row.addStretch()
        row.addWidget(self.provider)
        llm_card.layout().addLayout(row)
        llm_card.layout().addWidget(self.claude_box)
        llm_card.layout().addWidget(self.gemini_note)
        foot = QHBoxLayout()
        foot.setSpacing(SP.s)
        foot.addWidget(self.status, 0, Qt.AlignVCenter)    # nhãn viên thuốc chỉ rộng bằng chữ, không giãn hết dòng
        foot.addStretch(1)                                 # phần trống bên trái, nút luôn nằm sát phải
        foot.addWidget(self.btn_test)
        foot.addWidget(btn_save)
        llm_card.layout().addLayout(foot)

        # --- thẻ 2: Gemini API ---
        gem_card = self._card("Gemini API", "Dùng khi chọn Gemini làm mô hình, hoặc khi gen video và giọng trực tiếp qua API.")
        gem_card.layout().addWidget(self._field("API key", "Lấy từ Google AI Studio.", self.gemini_key))

        head = QLabel("Cài đặt")
        head.setProperty("heading", True)
        sub = QLabel("Cấu hình dùng chung cho mọi dự án.")
        sub.setProperty("caption", True)
        col = QVBoxLayout()
        col.setSpacing(SP.l)
        col.addWidget(head)
        col.addWidget(sub)
        col.addWidget(llm_card)
        col.addWidget(gem_card)
        col.addStretch()
        holder = QWidget()
        holder.setMaximumWidth(680)
        holder.setLayout(col)
        row = QHBoxLayout()                          # canh giữa một cột
        row.setContentsMargins(0, 0, SP.m, 0)
        row.addStretch(1)
        row.addWidget(holder, 100)
        row.addStretch(1)
        page = QWidget()
        page.setLayout(row)
        scroll = QScrollArea()                       # nội dung dài hơn cửa sổ thì cuộn, không ép xẹp các ô nhập
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.provider.currentIndexChanged.connect(self.refresh)
        self.refresh()
        self.worker = None

    # ---- khối dựng nhỏ ----
    @staticmethod
    def _card(title: str, subtitle: str) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        v = QVBoxLayout(card)
        v.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        v.setSpacing(SP.m)
        t = QLabel(title)
        t.setProperty("subheading", True)
        v.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setProperty("caption", True)
            s.setWordWrap(True)
            v.addWidget(s)
        return card

    @staticmethod
    def _field(title: str, hint: str, control: QWidget) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SP.xs)
        v.addWidget(QLabel(title))
        v.addWidget(control)
        if hint:
            h = QLabel(hint)
            h.setProperty("caption", True)
            h.setWordWrap(True)
            v.addWidget(h)
        return w

    @staticmethod
    def _secret(value: str, placeholder: str) -> QWidget:
        """Ô nhập khoá có nút Hiện/Ẩn. Dùng .edit để đọc giá trị."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(SP.s)
        e = QLineEdit(value)
        e.setEchoMode(QLineEdit.Password)
        e.setPlaceholderText(placeholder)
        b = QPushButton("Hiện")
        b.setProperty("ghost", True)
        b.setCheckable(True)

        def flip(on):
            e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
            b.setText("Ẩn" if on else "Hiện")
        b.toggled.connect(flip)
        h.addWidget(e, 1)
        h.addWidget(b)
        w.edit = e
        return w

    def refresh(self) -> None:
        claude = self.provider.currentData() == "claude"
        self.claude_box.setVisible(claude)
        self.gemini_note.setVisible(not claude)

    def show_status(self, text: str, kind: str) -> None:
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)

    def save(self) -> None:
        settings.save_llm(self.provider.currentData(), self.claude_key.edit.text(), self.base_url.text(),
                          self.model.text(), self.proxy.text())
        settings.set_api_key(self.gemini_key.edit.text())
        self.show_status("Đã lưu.", "ok")

    def test(self) -> None:
        self.save()
        self.show_status("Đang thử kết nối…", "warn")
        self.btn_test.setEnabled(False)
        self.worker = Worker(lambda log: llm.ping())
        self.worker.done.connect(lambda m: self.show_status(str(m), "ok"))
        self.worker.failed.connect(lambda e: self.show_status(f"Lỗi: {e}", "err"))
        self.worker.finished.connect(lambda: self.btn_test.setEnabled(True))
        self.worker.start()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Veo Story Studio")
        scr = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1360, int(scr.width() * 0.94)), min(900, int(scr.height() * 0.88)))
        self.setMinimumSize(1200, 640)
        self.logbox = QPlainTextEdit(readOnly=True)  # chọn/copy được
        self.logbox.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.logbox.setPlaceholderText("Nhật ký hoạt động")
        chars = CharactersTab()
        self.tabs = tabs = QTabWidget()
        self.proj = proj = ProjectTab(chars, self.logbox.appendPlainText)
        proj.opened.connect(chars.set_project)
        chars.usage_counter = proj.character_usage
        chars.deleted.connect(proj.on_characters_deleted)
        chars.chars_changed.connect(proj.d_chars.refresh)     # nối TRƯỚC khi nạp để chip luôn được vẽ lại với ảnh
        chars.set_project(proj.combo.currentText())
        self.settings_tab = SettingsTab()
        proj.welcome = Welcome(proj, lambda: tabs.setCurrentWidget(self.settings_tab), proj.new_project, proj.launch_flow_chrome)
        proj.update_welcome()
        tabs.addTab(proj, "Dự án")
        tabs.addTab(chars, "Nhân vật")
        tabs.addTab(self.settings_tab, "Cài đặt")

        # nhật ký: ngăn kéo nổi phía trên thanh trạng thái, ẩn mặc định (không chiếm chỗ của nội dung)
        self.strip = StatusStrip()
        self.strip.log_toggled.connect(self.toggle_log)
        self.strip.stop_clicked.connect(proj.request_cancel)
        self.strip.chip_clicked.connect(self.on_chip)
        proj.activity.connect(self.strip.set_activity)
        proj.busy_changed.connect(lambda busy, cancel: self.strip.set_busy(busy, cancel))
        proj.progress_changed.connect(self.strip.set_progress)
        self.strip.set_activity("Sẵn sàng.", "info")

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        cl.setSpacing(SP.s)
        cl.addWidget(tabs, 1)
        cl.addWidget(self.strip)
        self.setCentralWidget(central)
        self.central = central
        # Ngăn nhật ký là cửa sổ nổi riêng (không viền): khung video dùng bề mặt vẽ riêng nên luôn đè lên mọi widget con,
        # chỉ cửa sổ cấp cao nhất mới nằm trên được.
        self.log_win = QWidget(self, Qt.Tool | Qt.FramelessWindowHint)
        self.log_win.setAttribute(Qt.WA_TranslucentBackground)
        self.log_win.setAttribute(Qt.WA_ShowWithoutActivating)
        lw = QVBoxLayout(self.log_win)
        lw.setContentsMargins(0, 0, 0, 0)
        self.logbox.setParent(self.log_win)
        self.logbox.setObjectName("logOverlay")
        lw.addWidget(self.logbox)
        self.log_win.hide()

        self.chip_timer = QTimer(self)
        self.chip_timer.setInterval(4000)
        self.chip_timer.timeout.connect(self.refresh_chips)
        self.chip_timer.start()
        self.refresh_chips()

    LOG_HEIGHT = 230

    def place_log(self):
        """Đặt ngăn nhật ký ngay phía trên thanh trạng thái, rộng bằng vùng nội dung."""
        c = self.central
        h = min(self.LOG_HEIGHT, max(120, c.height() - 200))
        top_left = c.mapToGlobal(QPoint(16, 0))
        strip_top = self.strip.mapToGlobal(QPoint(0, 0)).y()
        self.log_win.setGeometry(top_left.x(), strip_top - 6 - h, c.width() - 32, h)

    def toggle_log(self):
        show = not self.log_win.isVisible()
        if show:
            self.place_log()
            self.log_win.show()
            self.log_win.raise_()
            self.logbox.verticalScrollBar().setValue(self.logbox.verticalScrollBar().maximum())
        else:
            self.log_win.hide()
        self.strip.set_log_open(show)

    def _follow(self):
        if self.log_win.isVisible():
            self.place_log()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._follow()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._follow()

    def closeEvent(self, e):
        self.log_win.hide()
        super().closeEvent(e)

    def refresh_chips(self):
        ok, _ = llm.is_configured()
        self.strip.set_chip("llm", f"LLM · {llm.short_name()}" if ok else "LLM · chưa cấu hình", ok)
        up = flow_auto._cdp_up()
        self.strip.set_chip("flow", "Flow ● sẵn sàng" if up else "Flow ○ chưa mở Chrome", up)
        voice = self.proj.project.voice.split("-")[-1].replace("Neural", "") if self.proj.project else "—"
        self.strip.set_chip("voice", f"Giọng · {voice}", True)
        if self.proj.welcome.isVisible():
            self.proj.welcome.refresh()

    def on_chip(self, key: str):
        if key == "llm":
            self.tabs.setCurrentWidget(self.settings_tab)
        elif key == "flow":
            if not flow_auto._cdp_up():
                self.proj.launch_flow_chrome()
        elif key == "voice":
            self.tabs.setCurrentWidget(self.proj)
            self.proj.open_settings()


ICON_PATH = models.resource_path("assets/icon.png")


def main():
    import sys
    if sys.platform == "win32":   # để thanh tác vụ Windows dùng đúng biểu tượng của app thay vì biểu tượng Python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("veo.story.studio")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Veo Story Studio")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))   # cửa sổ, Dock (macOS), thanh tác vụ (Windows)
    theme.install(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

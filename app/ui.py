from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from . import accounts, theme, characters_pack, flow_auto, icons, llm, models, settings, trash
from .publish_tab import PublishTab
from .settings_tab import SettingsTab
from .models import Character
from .project_tab import ProjectTab
from .pack_guide import PackGuideDialog, confirm_text
from .welcome import Welcome
from .theme import SP
from .shell import CharRow, TopBar
from .widgets import Popover, StatusStrip, avatar, repolish, rounded_pixmap


class CharactersTab(QWidget):
    deleted = Signal(list)          # tên các nhân vật vừa bị xoá (để dọn khỏi scene)
    flow_state_changed = Signal()   # vừa tạo ảnh trên Flow: địa chỉ project Flow (của chương) có thể mới, tab Dự án cần nạp lại
    added = Signal(list)            # tên các nhân vật MỚI vừa thêm (AI, nhập gói, tạo tay): để gắn vào các scene đã tạo trước đó
    chars_changed = Signal()        # danh sách nhân vật vừa được nạp/đổi (để nơi khác vẽ lại ảnh)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.usage_counter = None   # callable(names) -> {tên: số scene đang dùng}, do cửa sổ chính gắn vào
        self.chapter_provider = None   # callable() -> id chương đang mở ở tab Dự án (để tạo ảnh trong project Flow của chương)
        self.project_name = ""
        self._chars: list[Character] = []
        self._mtime = 0.0
        self.list = QListWidget()
        self.list.setObjectName("provList")
        self.list.setSpacing(SP.s)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)   # Shift/⌘+click để xoá nhiều nhân vật
        self.hint = QLabel("", self.list.viewport())                # trạng thái trống: hướng dẫn thay vì ô trắng
        self.hint.setProperty("caption", True)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.list.viewport().installEventFilter(self)
        self.list.itemSelectionChanged.connect(self.mark_rows)
        self.name, self.aliases, self.role, self.zh = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Ví dụ: young man, long black hair, dark green robe with gold trim")
        self.desc_vi = QPlainTextEdit()
        self.desc_vi.setPlaceholderText("Mô tả gốc tiếng Việt")
        self.img_path, self.preview = "", QLabel("Chưa có ảnh")
        self.preview.setFixedSize(224, 224)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setProperty("avatarph", True)
        self.btn_edit_img = QPushButton(self.preview)          # nút bút chì nổi trên góc ảnh
        self.btn_edit_img.setProperty("iconbtn", True)
        self.btn_edit_img.setFixedSize(34, 34)
        self.btn_edit_img.move(224 - 34 - 10, 10)
        icons.attach(self.btn_edit_img, "edit", 18)
        self.btn_edit_img.setToolTip("Đổi ảnh")

        def field(label: str, w: QWidget) -> QWidget:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(SP.xs)
            cap = QLabel(label)
            cap.setStyleSheet("font-weight: 500; background: transparent;")
            v.addWidget(cap)
            v.addWidget(w)
            return box

        def text_card(title: str, edit: QPlainTextEdit, count: bool) -> QFrame:
            """Khung mô tả: tiêu đề + số từ + nút copy, ô nhập bên dưới."""
            f = QFrame()
            f.setProperty("banner", True)
            cap = QLabel(title)
            cap.setProperty("caption", True)
            cnt = QLabel("")
            cnt.setProperty("caption", True)
            cp = QPushButton()
            cp.setProperty("ghost", True)
            cp.setFixedSize(30, 30)
            icons.attach(cp, "copy", 16, role="muted")
            cp.setToolTip("Copy nội dung")
            cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(edit.toPlainText()))
            head = QHBoxLayout()
            head.setSpacing(SP.s)
            head.addWidget(cap, 1)
            head.addWidget(cnt)
            head.addWidget(cp)
            v = QVBoxLayout(f)
            v.setContentsMargins(SP.l, SP.m, SP.l, SP.l)
            v.setSpacing(SP.s)
            v.addLayout(head)
            v.addWidget(edit, 1)
            edit.setMinimumHeight(92)
            if count:
                edit.textChanged.connect(lambda: cnt.setText(f"{len(edit.toPlainText().split())} từ"))
            return f

        btn_img, btn_save, btn_new, btn_del, btn_pack, btn_cancel = (QPushButton(t) for t in
                                                                      ("Chọn ảnh…", "Lưu", "Thêm mới", "Xoá", "Nhập gói…", "Huỷ"))
        btn_save.setProperty("primary", True)
        btn_new.setProperty("primary", True)
        btn_del.setProperty("danger", True)
        icons.attach(btn_img, "image", 18)
        icons.attach(btn_save, "save", 18)
        icons.attach(btn_new, "plus", 18)
        icons.attach(btn_del, "trash", 18)
        icons.attach(btn_pack, "info", 18)
        for b in (btn_img, btn_save, btn_new, btn_del, btn_pack, btn_cancel):
            b.setFixedHeight(40)
        btn_img.clicked.connect(self.pick_image)
        self.btn_edit_img.clicked.connect(self.pick_image)
        btn_save.clicked.connect(self.save)
        btn_new.clicked.connect(self.new)
        btn_del.clicked.connect(self.delete)
        btn_cancel.clicked.connect(self.revert)
        btn_pack.clicked.connect(lambda _checked=False: self.import_pack())     # clicked gửi thêm 'checked': không được lọt vào tham số source
        self.btn_ai = btn_ai = QPushButton("Tạo ảnh (AI)…")      # đổi thành "Gen lại ảnh (AI)…" khi nhân vật đã có ảnh
        btn_ai.setFixedHeight(40)
        btn_ai.setProperty("primary", True)
        icons.attach(btn_ai, "sparkle", 18)
        btn_ai.clicked.connect(self.make_image_ai)

        # ---- thẻ phải: thông tin nhân vật ----
        self.idx_pill = QLabel("mới")
        self.idx_pill.setProperty("pill", "info")
        title = QLabel("Thông tin nhân vật")
        title.setProperty("subheading", True)
        more = QPushButton()
        more.setProperty("iconbtn", True)
        icons.attach(more, "more", 20)
        mmenu = QMenu(more)
        mmenu.addAction("Gen lại ảnh (AI)…", self.make_image_ai)
        mmenu.addAction("Chọn ảnh…", self.pick_image)
        mmenu.addAction("Nhân bản", self.duplicate)
        more.setMenu(mmenu)
        trash_btn = QPushButton()
        trash_btn.setProperty("iconbtn", True)
        icons.attach(trash_btn, "trash", 20, role="danger")
        trash_btn.setToolTip("Xoá nhân vật đang chọn")
        trash_btn.clicked.connect(self.delete)
        head = QHBoxLayout()
        head.setSpacing(SP.s)
        head.addWidget(title)
        head.addWidget(self.idx_pill)
        head.addStretch(1)
        head.addWidget(trash_btn)
        head.addWidget(more)
        picbox = QWidget()                      # khối ảnh + nút có kích thước cố định
        pic = QVBoxLayout(picbox)
        pic.setContentsMargins(0, 0, 0, 0)
        pic.setSpacing(SP.s)
        pic.addWidget(self.preview)
        pic.addWidget(btn_img)
        pic.addWidget(btn_ai)
        picbox.setFixedSize(224, 224 + (SP.s + 40) * 2)
        fields = QVBoxLayout()
        fields.setSpacing(SP.m)
        fields.addWidget(field("Tên chuẩn", self.name))
        fields.addWidget(field("Tên Hán", self.zh))
        fields.addWidget(field("Vai trò", self.role))
        fields.addWidget(field("Tên gọi khác (cách nhau dấu phẩy)", self.aliases))
        fields.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(SP.xl)
        top.addWidget(picbox, 0, Qt.AlignTop)
        top.addLayout(fields, 1)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, SP.s, 0)
        bv.setSpacing(SP.l)
        bv.addLayout(top)
        bv.addWidget(text_card("Mô tả (prompt, tiếng Anh): đưa thẳng vào prompt Flow, nên khớp với ảnh tham chiếu", self.desc, True))
        bv.addWidget(text_card("Mô tả gốc tiếng Việt (từ gói nhân vật, chỉ để tham khảo)", self.desc_vi, False))
        scroll = QScrollArea()                  # cửa sổ thấp thì cuộn thay vì nén các ô nhập đè lên nhau
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setWidget(body)
        foot = QHBoxLayout()
        foot.setSpacing(SP.s)
        foot.addWidget(btn_del)
        foot.addStretch()
        foot.addWidget(btn_cancel)
        foot.addWidget(btn_save)
        card = QFrame()
        card.setProperty("card", True)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(SP.xl, SP.l, SP.xl, SP.l)
        cv.setSpacing(SP.m)
        cv.addLayout(head)
        cv.addWidget(scroll, 1)
        cv.addLayout(foot)

        # ---- thẻ trái: danh sách ----
        self.count_pill = QLabel("0")
        self.count_pill.setProperty("pill", "info")
        ltitle = QLabel("Danh sách nhân vật")
        ltitle.setProperty("subheading", True)
        lhead = QHBoxLayout()
        lhead.setSpacing(SP.s)
        lhead.addWidget(ltitle)
        lhead.addWidget(self.count_pill)
        lhead.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm nhân vật…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(40)
        self.search.addAction(QIcon(icons.pixmap("search", 18, theme.T["muted"])), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.apply_filter)
        self.filter = QComboBox()
        for label, key in (("Tất cả", "all"), ("Có ảnh", "image"), ("Chưa có ảnh", "noimage"), ("Đang dùng", "used")):
            self.filter.addItem(label, key)
        self.filter.setFixedHeight(40)
        self.filter.setFixedWidth(130)
        self.filter.currentIndexChanged.connect(self.apply_filter)
        srow = QHBoxLayout()
        srow.setSpacing(SP.s)
        srow.addWidget(self.search, 1)
        srow.addWidget(self.filter)
        btn_gen = QPushButton("Tạo nhân vật từ truyện (AI)…")
        btn_gen.setFixedHeight(40)
        icons.attach(btn_gen, "sparkle", 18)
        btn_gen.clicked.connect(self.generate_from_story)
        lr = QGridLayout()
        lr.setHorizontalSpacing(SP.s)
        lr.setColumnStretch(0, 1)
        lr.setColumnStretch(1, 1)
        lr.addWidget(btn_new, 0, 0)
        lr.addWidget(btn_pack, 0, 1)
        lr.addWidget(btn_gen, 1, 0, 1, 2)
        left = QFrame()
        left.setProperty("card", True)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        lv.setSpacing(SP.m)
        lv.addLayout(lhead)
        lv.addLayout(srow)
        lv.addWidget(self.list, 1)
        lv.addLayout(lr)
        lay = QHBoxLayout(self)
        lay.setSpacing(SP.l)
        lay.addWidget(left, 4)
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
        for i, c in enumerate(self._chars):
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 68))
            self.list.addItem(it)
            has = bool(c.image and Path(c.image).exists())
            row = CharRow(i + 1, c.name, c.role, avatar(c.image, 46) if has else None, has)
            row.selected.connect(lambda r=i: self.select_row(r))
            row.action.connect(lambda k, r=i: self.row_action(r, k))
            self.list.setItemWidget(it, row)
        self.list.blockSignals(False)
        self.count_pill.setText(str(len(self._chars)))
        self.apply_filter()
        self.mark_rows()

    def select_row(self, r: int) -> None:
        """Bấm vào hàng: chọn hàng đó (Shift/⌘ + bấm để chọn nhiều thì dùng danh sách như thường)."""
        if not (QGuiApplication.keyboardModifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier)):
            self.list.clearSelection()
            self.list.setCurrentRow(r)

    def row_action(self, r: int, key: str) -> None:
        self.list.clearSelection()
        self.list.setCurrentRow(r)
        QTimer.singleShot(0, {"ai": self.make_image_ai, "image": self.pick_image, "delete": self.delete}[key])

    def mark_rows(self) -> None:
        sel = {i.row() for i in self.list.selectedIndexes()}
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if w is not None:
                w.set_selected(i in sel or i == self.list.currentRow() and not sel)

    def apply_filter(self, *_) -> None:
        """Ô tìm + bộ lọc chỉ ẨN/HIỆN hàng (không đổi thứ tự) nên số dòng vẫn khớp danh sách nhân vật."""
        import unicodedata
        norm = lambda t: "".join(c for c in unicodedata.normalize("NFD", t.replace("đ", "d").replace("Đ", "D").lower()) if unicodedata.category(c) != "Mn")
        q, mode = norm(self.search.text().strip()), self.filter.currentData()
        used = self.usage_counter([c.name for c in self._chars]) if callable(self.usage_counter) and mode == "used" else {}
        for i, c in enumerate(self._chars):
            has = bool(c.image and Path(c.image).exists())
            ok = (not q or q in norm(c.name + " " + c.role + " " + " ".join(c.aliases))) and \
                 (mode == "all" or (mode == "image" and has) or (mode == "noimage" and not has) or (mode == "used" and used.get(c.name, 0) > 0))
            it = self.list.item(i)
            if it is not None:
                it.setHidden(not ok)

    def revert(self) -> None:
        """Nút Huỷ: bỏ các sửa đổi chưa lưu, nạp lại nhân vật đang chọn (hoặc xoá ô nhập nếu đang tạo mới)."""
        r = self.list.currentRow()
        if 0 <= r < len(self._chars):
            self.show_char(r)
        else:
            self.new()

    def duplicate(self) -> None:
        r = self.list.currentRow()
        if not (0 <= r < len(self._chars)):
            return
        c = self._chars[r]
        names = {x.name for x in self._chars}
        name, n = f"{c.name} (bản sao)", 2
        while name in names:
            name, n = f"{c.name} (bản sao {n})", n + 1
        import dataclasses
        self._chars.append(dataclasses.replace(c, name=name))
        models.save_characters(self.project_name, self._chars)
        self._mtime = models.characters_mtime(self.project_name)
        self.refresh()
        self.chars_changed.emit()
        self.list.setCurrentRow(len(self._chars) - 1)

    def new(self):
        self.list.clearSelection()
        for w in (self.name, self.aliases, self.role, self.zh):
            w.clear()
        self.desc.clear()
        self.desc_vi.clear()
        self.img_path = ""
        self.idx_pill.setText("mới")
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
        self.idx_pill.setText(f"#{i + 1}")
        self.set_preview()

    def set_preview(self):
        has = bool(self.img_path and Path(self.img_path).exists())
        self.btn_ai.setText("Gen lại ảnh (AI)…" if has else "Tạo ảnh (AI)…")
        if has:
            self.preview.setProperty("avatarph", False)
            self.preview.setPixmap(rounded_pixmap(self.img_path, 222, 16))
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
        is_new = True
        for i, old in enumerate(self._chars):
            if old.name == name:
                self._chars[i] = c
                is_new = False
                break
        else:
            self._chars.append(c)
        models.save_characters(self.project_name, self._chars)
        self._mtime = models.characters_mtime(self.project_name)
        self.refresh()
        self.chars_changed.emit()
        if is_new:
            self.added.emit([name])

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

    def import_pack(self, source=None):
        """Hướng dẫn -> chọn thư mục/zip (hoặc kéo-thả) -> xem trước -> xác nhận -> nhập."""
        if not self.project_name:
            QMessageBox.warning(self, "Thiếu dự án", "Chọn hoặc tạo dự án trước.")
            return
        if source is None:
            guide = PackGuideDialog(self)
            if guide.exec() != QDialog.Accepted or not guide.source:
                return
            source = guide.source
        try:
            pv = characters_pack.preview_pack(self.project_name, source)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Không đọc được gói", f"{e}\n\nBấm “Nhập gói…” lại để xem hướng dẫn chuẩn bị gói.")
            return
        if not pv["update"] and not pv["new"]:
            QMessageBox.warning(self, "Gói không có nhân vật hợp lệ", confirm_text(pv).rsplit("\n\n", 1)[0])
            return
        if QMessageBox.question(self, "Xem trước gói nhân vật", confirm_text(pv)) != QMessageBox.Yes:
            return
        before = {c.name for c in models.load_characters(self.project_name)}
        try:
            report = characters_pack.import_pack(self.project_name, source)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Nhập gói lỗi", str(e))
            return
        self._reload()
        QMessageBox.information(self, "Nhập gói nhân vật", "\n".join(report[:30]) + ("\n…" if len(report) > 30 else ""))
        new = [c.name for c in self._chars if c.name not in before]
        if new:
            self.added.emit(new)

    def make_image_ai(self):
        """Tạo (lại) ảnh cho nhân vật đang chọn bằng AI: chọn giới tính, chỉnh mô tả, xem trước rồi dùng."""
        row = self.list.currentRow()
        if not self.project_name or not (0 <= row < len(self.chars)):
            QMessageBox.information(self, "Chưa chọn nhân vật", "Chọn một nhân vật trong danh sách (hoặc lưu nhân vật mới) trước.")
            return
        from .char_image_dialog import CharImageDialog
        c = self.chars[row]
        dlg = CharImageDialog(self.project_name, c, self, chapter_id=self.current_chapter_id())
        accepted = dlg.exec() == QDialog.Accepted
        self.flow_state_changed.emit()
        if accepted and dlg.saved:
            self._reload()
            self.list.setCurrentRow(row)

    def current_chapter_id(self) -> str:
        try:
            return str(self.chapter_provider() or "") if callable(self.chapter_provider) else ""
        except Exception:  # noqa: BLE001
            return ""

    def generate_from_story(self):
        """AI đọc truyện của dự án, đề xuất nhân vật + mô tả ngoại hình, tuỳ chọn tạo ảnh bằng Gemini."""
        if not self.project_name:
            QMessageBox.warning(self, "Thiếu dự án", "Chọn hoặc tạo dự án trước.")
            return
        from .char_gen_dialog import CharGenDialog
        dlg = CharGenDialog(self.project_name, self, current_chapter_id=self.current_chapter_id())
        accepted = dlg.exec() == QDialog.Accepted
        self.flow_state_changed.emit()
        if accepted and dlg.added:
            self._reload()
            QMessageBox.information(self, "Đã thêm nhân vật", f"Đã thêm {len(dlg.added)} nhân vật: {', '.join(dlg.added[:8])}"
                                    + ("…" if len(dlg.added) > 8 else "") + ".\nBạn có thể sửa mô tả hoặc đổi ảnh từng nhân vật ở đây.")
            self.added.emit(list(dlg.added))

    # kéo-thả thư mục hoặc .zip thẳng vào tab Nhân vật
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir() or p.suffix.lower() == ".zip":
                self.import_pack(p)
                return


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kevit")
        scr = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1360, int(scr.width() * 0.94)), min(900, int(scr.height() * 0.88)))
        self.setMinimumSize(1216, 640)
        self.logbox = QPlainTextEdit(readOnly=True)  # chọn/copy được
        self.logbox.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.logbox.setPlaceholderText("Nhật ký hoạt động")
        chars = CharactersTab()
        self.tabs = tabs = QTabWidget()
        self.proj = proj = ProjectTab(chars, self.logbox.appendPlainText)
        proj.opened.connect(chars.set_project)
        chars.usage_counter = proj.character_usage
        chars.chapter_provider = lambda: proj.chapter.id if proj.chapter else ""
        chars.flow_state_changed.connect(proj.reload_flow_state)
        chars.deleted.connect(proj.on_characters_deleted)
        chars.added.connect(proj.on_characters_added)
        chars.chars_changed.connect(proj.update_empty_state)
        chars.chars_changed.connect(proj.d_chars.refresh)     # nối TRƯỚC khi nạp để chip luôn được vẽ lại với ảnh
        chars.set_project(proj.combo.currentText())
        self.settings_tab = SettingsTab()
        self.settings_tab.accounts_changed.connect(proj.on_accounts_changed)
        self.settings_tab.llm_changed.connect(lambda: self.refresh_chips())
        proj.account_changed.connect(lambda *_: (self.refresh_chips(), self.settings_tab.accounts_panel.refresh()))
        proj.busy_changed.connect(lambda busy, *_: self.settings_tab.accounts_panel.set_busy(busy))
        proj.welcome = Welcome(proj, lambda: tabs.setCurrentWidget(self.settings_tab), proj.new_project, proj.launch_flow_chrome)
        proj.update_welcome()
        tabs.addTab(proj, "Dự án")
        tabs.addTab(chars, "Nhân vật")
        self.publish_tab = PublishTab(lambda: proj.project, self.logbox.appendPlainText)
        proj.generation_done.connect(self.publish_tab.on_generation_done)
        proj.publish_settings_changed.connect(self.publish_tab.reload)
        self.settings_tab.publish_changed.connect(self.publish_tab.refresh_accounts)
        tabs.addTab(self.publish_tab, "Đăng video")
        tabs.addTab(self.settings_tab, "Cài đặt")

        # nhật ký: ngăn kéo nổi phía trên thanh trạng thái, ẩn mặc định (không chiếm chỗ của nội dung)
        self.strip = StatusStrip()
        self.strip.log_toggled.connect(self.toggle_log)
        self.strip.stop_clicked.connect(proj.request_cancel)
        self.strip.chip_clicked.connect(self.on_chip)
        proj.activity.connect(self.strip.set_activity)
        proj.busy_changed.connect(lambda busy, cancel: self.strip.set_busy(busy, cancel))
        proj.progress_changed.connect(self.strip.set_progress)
        self.publish_tab.activity.connect(self.strip.set_activity)
        self.strip.set_activity("Sẵn sàng.", "info")

        central = QWidget()
        # thanh trên cùng (logo, tab có icon, tài khoản Flow) thay thanh tab mặc định; QTabWidget vẫn giữ để chuyển trang
        tabs.tabBar().hide()
        self.topbar = TopBar(ICON_PATH, [("Dự án", "folder"), ("Nhân vật", "users"), ("Đăng video", "open"), ("Cài đặt", "gear")])
        self.topbar.tab_clicked.connect(tabs.setCurrentIndex)
        self.topbar.account_clicked.connect(lambda: self.on_chip("flow", anchor=self.topbar.pill))
        tabs.currentChanged.connect(self.topbar.set_current)
        self.topbar.set_current(tabs.currentIndex())
        body = QWidget()
        cl = QVBoxLayout(body)
        cl.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        cl.setSpacing(SP.s)
        cl.addWidget(tabs, 1)
        cl.addWidget(self.strip)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.topbar)
        root.addWidget(body, 1)
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
        QTimer.singleShot(900, self.refresh_chips)     # lần đầu: kết quả kiểm tra Chrome ở luồng nền đã về, cập nhật chip ngay

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
        prof = settings.active_llm()
        self.strip.set_chip("llm", f"LLM · {llm.short_name()}" if ok else f"LLM · {prof.name} chưa nhập key", ok)
        up = flow_auto.cdp_state()
        accs = accounts.all_accounts()
        act = accounts.active()
        who = f" · {act.name}" if len(accs) > 1 else ""         # chỉ nêu tên tài khoản khi có nhiều hơn một
        cr = f" · {accounts.fmt_credits(act.credits)} cr" if act.credits is not None else ""
        self.topbar.set_account(act.name)
        self.strip.set_chip("flow", f"Flow{who}{cr} ● sẵn sàng" if up else f"Flow{who}{cr} ○ chưa mở Chrome", up)
        voice = self.proj.project.voice.split("-")[-1].replace("Neural", "") if self.proj.project else "—"
        self.strip.set_chip("voice", f"Giọng · {voice}", True)
        if self.proj.welcome.isVisible():
            self.proj.welcome.refresh()

    def build_flow_pop(self, pop):
        """Chip Flow: chọn tài khoản Flow cho DỰ ÁN đang mở, mở Chrome của tài khoản đó, hoặc vào quản lý tài khoản."""
        cur = accounts.active().id
        pop.section("Tài khoản Flow của dự án" if self.proj.project else "Tài khoản Flow")
        for a in accounts.all_accounts():
            pop.item(a.name, accounts.describe_credits(a) + ("  ·  mặc định cho dự án mới" if a.id == accounts.default_new_id() else ""),
                     (lambda i=a.id: self.proj.set_account(i)), shortcut="✓" if a.id == cur else "", enabled=bool(self.proj.project))
        pop.separator()
        on = accounts.auto_switch()
        pop.item("Tự chuyển tài khoản: " + ("BẬT" if on else "tắt"),
                 "Hết credit thì tự sang tài khoản khác còn credit" if not on else "Bấm để tắt: hết credit sẽ chặn và báo cảnh báo",
                 lambda: (accounts.set_auto_switch(not on), self.settings_tab.accounts_panel.auto.setChecked(not on)))
        pop.item("Cập nhật credit tài khoản này", "Đọc lại credit từ Flow (mở Chrome nếu chưa mở)",
                 lambda: self.proj.refresh_credits([accounts.active()], deep=True), enabled=not self.proj._busy)
        pop.separator()
        if not flow_auto.cdp_state(0):
            pop.item("Mở Chrome cho tài khoản này", "Đăng nhập Google Flow một lần trong cửa sổ đó", self.proj.launch_flow_chrome)
        pop.item("Quản lý tài khoản…", "Thêm, đổi tên, gỡ, mở Chrome để đăng nhập",
                 lambda: (self.tabs.setCurrentWidget(self.settings_tab), self.settings_tab.select_section("flow")))

    def build_llm_pop(self, pop):
        """Chip LLM: chọn nhanh mô hình AI đang dùng (áp dụng cho mọi dự án), hoặc vào quản lý mô hình."""
        pop.section("Mô hình AI đang dùng")
        cur = settings.active_llm_id()
        for p in settings.llm_profiles():
            ok, _ = llm.is_configured(p)
            sub = f"{p.kind_label.split(' (')[0]}  ·  {p.effective_model}" + ("" if ok else "  ·  chưa nhập key")
            pop.item(p.name, sub, (lambda i=p.id: self.set_llm(i)), shortcut="✓" if p.id == cur else "")
        pop.separator()
        pop.item("Quản lý mô hình…", "Thêm Claude, Gemini, OpenAI và dịch vụ tương thích",
                 lambda: (self.tabs.setCurrentWidget(self.settings_tab), self.settings_tab.select_section("llm")))

    def set_llm(self, profile_id: str):
        settings.set_active_llm(profile_id)
        self.settings_tab.llm_panel.active_id = profile_id
        self.settings_tab.llm_panel.refresh_list(profile_id)
        self.refresh_chips()

    def on_chip(self, key: str, anchor=None):
        if key == "llm":
            if not hasattr(self, "llm_pop"):
                self.llm_pop = Popover(self, self.build_llm_pop, 380)
            self.llm_pop.show_for(self.strip.chips["llm"], "above", "right")
        elif key == "flow":
            if not hasattr(self, "flow_pop"):
                self.flow_pop = Popover(self, self.build_flow_pop, 360)
            if anchor is not None:
                self.flow_pop.show_for(anchor, "below", "right")
            else:
                self.flow_pop.show_for(self.strip.chips["flow"], "above", "right")
        elif key == "voice":
            self.tabs.setCurrentWidget(self.proj)
            self.proj.open_settings()


ICON_PATH = models.resource_path("assets/icon.png")


def main():
    import sys
    if sys.platform == "win32":   # để thanh tác vụ Windows dùng đúng biểu tượng của app thay vì biểu tượng Python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.kevit")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Kevit")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))   # cửa sổ, Dock (macOS), thanh tác vụ (Windows)
    theme.install(app)
    models.remember_dev_data_dir()
    if models.FROZEN and not models.has_projects(models.DATA_DIR):
        old = models.legacy_data_dir()
        if old and QMessageBox.question(
                None, "Tìm thấy dữ liệu cũ",
                f"Tìm thấy {len(list((old / 'projects').glob('*/project.json')))} dự án đã tạo trước đó tại:\n{old}\n\n"
                "Dùng thư mục này làm nơi lưu dữ liệu? Dữ liệu giữ nguyên tại chỗ (không sao chép), đăng nhập Flow cũng dùng tiếp, "
                "và các lần cập nhật app sau này vẫn dùng đúng thư mục này.") == QMessageBox.Yes:
            models.set_data_dir(old)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

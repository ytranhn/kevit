"""Tab Dự án: mở/tạo dự án, quản lý chương, tài khoản Flow và giọng đọc."""
from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMessageBox

from .. import scene_ops, tts
from ..models import Project, safe_dirname
from .. import accounts


class ChaptersMixin:
    """Tab Dự án: mở/tạo dự án, quản lý chương, tài khoản Flow và giọng đọc."""

    # ================= dự án / chương =================
    @property
    def chapter(self):
        p = self.project
        return p.chapters[self._chap_idx] if p and 0 <= self._chap_idx < len(p.chapters) else None

    @property
    def scenes(self) -> list:
        return self.chapter.scenes if self.chapter else []

    def owner_of(self, scene):
        for ch in self.project.chapters:
            if any(s is scene for s in ch.scenes):
                return ch
        return None

    def reload_projects(self, select: str = ""):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(Project.list_names())
        self.combo.blockSignals(False)
        if select:
            self.combo.setCurrentText(select)
        self.open_project(self.combo.currentText())
        self.update_welcome()

    def update_welcome(self):
        """Chưa có dự án nào -> phủ màn hình chào mừng (hướng dẫn thiết lập) lên toàn bộ tab."""
        w = getattr(self, "welcome", None)
        if w is None:
            return
        w.setVisible(not self.project)
        if w.isVisible():
            w.setGeometry(self.rect())
            w.raise_()
            w.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = getattr(self, "welcome", None)
        if w is not None and w.isVisible():
            w.setGeometry(self.rect())

    def new_project(self):
        name, ok = QInputDialog.getText(self, "Dự án mới", "Tên dự án:")
        name = safe_dirname(name) if ok and name.strip() else ""
        if name:
            p = Project(name)
            p.use_flow_account(accounts.default_new_id())     # dự án mới dùng tài khoản mặc định (đổi được ở chip Flow / Cài đặt dự án)
            p.new_chapter()
            p.save()
            self.reload_projects(name)

    def open_project(self, name):
        if not name:
            self.project = None
            self._row, self._chap_idx = -1, -1
            self.table.setRowCount(0)
            self.update_empty_state()
            self.opened.emit("")            # tab Nhân vật cũng về trạng thái chưa có dự án
            self.refresh_nav()
            self.update_steps()
            return
        self._row, self._chap_idx = -1, -1
        self.project = p = Project.load(name)
        accounts.activate(p.account_id)         # mọi thao tác Flow của dự án này dùng đúng tài khoản (Chrome/hồ sơ/cổng) của nó
        self.account_changed.emit(p.account_id)
        if not p.chapters:
            p.new_chapter()
            p.save()
        stale = [s for _, s in p.all_scenes() if s.status == "generating"]
        for s in stale:  # không có tiến trình nào chạy lúc mở dự án => bị ngắt giữa chừng
            s.status = "error"
            s.error = "Bị ngắt giữa chừng. Bấm ⟳ Đồng bộ Flow để lấy lại clip đã render (không tốn credit)."
        if stale:
            p.save()
        self.synopsis.setPlainText(p.synopsis)
        self.style.setText(p.style)
        self.aspect.setCurrentIndex(max(0, self.aspect.findData(p.aspect_ratio)))
        self.provider.setCurrentIndex(max(0, self.provider.findData(p.tts_provider)))
        self.narr_lang.blockSignals(True)
        self.narr_lang.setCurrentIndex(max(0, self.narr_lang.findData(p.narration_lang)))
        self.narr_lang.blockSignals(False)
        self.fill_voices(p.voice)
        self.voice_style.setText(p.voice_style)
        self.flow_model.setCurrentText(p.flow_model)
        self.flow_res.setCurrentText(p.flow_resolution)
        self.flow_auto_dur.setChecked(p.flow_auto_duration)
        self.flow_parallel.setValue(max(1, min(8, p.flow_parallel)))
        self.opened.emit(name)   # nạp danh sách nhân vật trước khi dựng bảng/chi tiết
        self.fill_chapter_combo(0)
        self.show_chapter(0)

    def chapter_label(self, ch) -> str:
        return f"{ch.name}   ({ch.done}/{len(ch.scenes)})"

    def fill_chapter_combo(self, select: int):
        self.chap_combo.blockSignals(True)
        self.chap_combo.clear()
        for ch in self.project.chapters:
            self.chap_combo.addItem(self.chapter_label(ch))
        self.chap_combo.setCurrentIndex(select)
        self.chap_combo.blockSignals(False)

    def refresh_chapter_labels(self):
        self.refresh_nav()
        self.chap_combo.blockSignals(True)
        for i, ch in enumerate(self.project.chapters):
            self.chap_combo.setItemText(i, self.chapter_label(ch))
        self.chap_combo.blockSignals(False)

    def on_chapter_selected(self, idx: int):
        if not self.project or idx < 0 or idx == self._chap_idx:
            return
        self.save_edits()          # lưu chương đang mở trước khi chuyển
        self.show_chapter(idx)

    def show_chapter(self, idx: int):
        self._chap_idx, self._row = idx, -1
        self.table.clearSelection()
        ch = self.chapter
        self.story.setPlainText(ch.story if ch else "")
        self.fill_table()
        self.left_tabs.setCurrentWidget(self.scene_page if self.scenes else self.story)

    def new_chapter(self):
        if not self.project:
            return
        self.save_edits()
        n = len(self.project.chapters) + 1
        title, ok = QInputDialog.getText(self, "Chương mới", "Tên chương (có thể để mặc định, sẽ tự lấy từ dòng đầu truyện):",
                                         text=f"Chương {n}")
        if not ok:
            return
        self.project.new_chapter(title)
        self.project.save()
        last = len(self.project.chapters) - 1
        self.fill_chapter_combo(last)
        self.show_chapter(last)
        self.left_tabs.setCurrentWidget(self.story)
        self.story.setFocus()

    def rename_chapter(self):
        ch = self.chapter
        if not ch:
            return
        title, ok = QInputDialog.getText(self, "Đổi tên chương", "Tên chương:", text=ch.name)
        if ok and title.strip():
            ch.title = title.strip()
            self.project.save()
            self.refresh_chapter_labels()

    def delete_chapter(self, idx: int | None = None):
        """Xoá một chương (mặc định chương đang mở), thẳng từ danh sách mà không cần chuyển sang chương đó trước."""
        p = self.project
        if not p:
            return
        idx = self._chap_idx if idx is None else idx
        if not 0 <= idx < len(p.chapters):
            return
        ch = p.chapters[idx]
        if len(p.chapters) == 1:
            QMessageBox.information(self, "Không thể xoá", "Dự án phải còn ít nhất một chương.")
            return
        if self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong (hoặc bấm Dừng) rồi xoá chương.")
            return
        videos = sum(len(scene_ops.scene_files(s)) for s in ch.scenes)
        box = QMessageBox(self)
        box.setWindowTitle("Xoá chương")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Xoá '{ch.name}' ({len(ch.scenes)} scene, {videos} file video/giọng)?")
        box.setInformativeText("File sẽ được chuyển vào thùng rác của dự án (khôi phục được, chỉ mất hẳn khi bạn dọn thùng rác).")
        b_all = box.addButton("Xoá chương và file", QMessageBox.DestructiveRole)
        b_keep = box.addButton("Chỉ gỡ khỏi dự án (giữ file trên ổ đĩa)", QMessageBox.ActionRole)
        b_cancel = box.addButton("Huỷ", QMessageBox.RejectRole)
        box.setDefaultButton(b_cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_all, b_keep):
            return
        cur = self._chap_idx
        moved = scene_ops.delete_chapter(p, ch, delete_files=clicked is b_all)
        p.save()
        self.log(f"Đã xoá '{ch.name}'" + (f", {moved} mục chuyển vào thùng rác." if moved else " (giữ nguyên file)."))
        # giữ nguyên chương đang xem nếu xoá chương khác; xoá chính chương đang xem thì sang chương gần nhất
        new = cur - 1 if idx < cur else min(cur, len(p.chapters) - 1)
        self._chap_idx = -1
        self.fill_chapter_combo(new)
        self.show_chapter(new)

    def fill_accounts(self, current: str = ""):
        self.flow_account.blockSignals(True)
        self.flow_account.clear()
        cur = current or (self.project.account_id if self.project else accounts.DEFAULT_ID)
        for a in accounts.all_accounts():
            self.flow_account.addItem(a.name, a.id)
        self.flow_account.setCurrentIndex(max(0, self.flow_account.findData(cur)))
        self.flow_account.blockSignals(False)
        self.account_info.setText(accounts.describe_credits(accounts.get(cur)) + ("  ·  tự chuyển tài khoản: BẬT" if accounts.auto_switch() else ""))

    def current_voice(self) -> str:
        return self.voice.currentData() or self.voice.currentText()

    def fill_voices(self, keep=None):
        """Danh sách giọng theo nhà cung cấp + ngôn ngữ thuyết minh. Giữ giọng đang chọn nếu còn hợp lệ, không thì lấy giọng mặc định của ngôn ngữ."""
        provider, lang = self.provider.currentData(), self.narr_lang.currentData() or "vi"
        want = keep if isinstance(keep, str) else self.current_voice()
        voices = tts.voices_for(provider, lang)
        self.voice.blockSignals(True)
        self.voice.clear()
        for vid, label in voices:
            self.voice.addItem(label, vid)
        ids = [v for v, _ in voices]
        if want and want not in ids and isinstance(keep, str):           # giọng tuỳ chỉnh đã lưu trong dự án nhưng không có trong danh mục
            self.voice.insertItem(0, want, want)
            ids.insert(0, want)
        pick = want if want in ids else tts.default_voice(provider, lang)
        self.voice.setCurrentIndex(max(0, self.voice.findData(pick)))
        self.voice.blockSignals(False)
        self.voice_style.setEnabled(provider == "gemini")
        self.update_narr_count()

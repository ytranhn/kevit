"""Tab Dự án: khung chi tiết scene, xem trước video, mở thư mục và lưu chỉnh sửa."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .. import langs, flow
from ..models import nfc
from ..widgets import repolish


class SceneDetailMixin:
    """Tab Dự án: khung chi tiết scene, xem trước video, mở thư mục và lưu chỉnh sửa."""

    def clear_detail(self):
        for w in (self.d_title, self.d_chars):
            w.clear()
        for w in (self.d_src, self.d_narr, self.d_visual):
            w.clear()
        self.d_badge.setText("")
        self.d_err.set_full("")
        self.b_view_err.setVisible(False)

    def load_detail(self, row: int):
        if not 0 <= row < len(self.scenes):
            self.clear_detail()
            return
        s = self.scenes[row]
        self.d_title.setText(s.title)
        self.d_chars.setText(", ".join(s.characters))
        self.d_src.setPlainText(s.source_text)
        self.d_narr.setPlainText(s.narration)
        self.d_visual.setPlainText(s.visual)
        self.update_badge(s)

    def update_narr_count(self):
        lang = self.narr_lang.currentData() or "vi"
        text = self.d_narr.toPlainText()
        units, sec = langs.count_units(text, lang), langs.seconds(text, lang)
        warn = sec > langs.CLIP_SECONDS * langs.TEMPO  # quá ~8s x 1.3 (tăng tốc tối đa): phần dư phải giữ khung hình cuối
        self.narr_count.setText(f"{units} {langs.unit_label(lang)} ≈ {sec:.0f}s" + ("  ⚠ dài hơn clip 8s" if warn else ""))
        self.narr_count.setProperty("level", "error" if warn else "info")
        repolish(self.narr_count)

    def view_error(self):
        """Hiện toàn bộ nội dung lỗi trong hộp thoại gọn (chọn/copy được), thay vì làm phình khung chi tiết."""
        if not 0 <= self._row < len(self.scenes):
            return
        from ..error_dialog import ErrorDialog
        s = self.scenes[self._row]
        ctx = f"{self.chapter.name}  ·  Scene {s.index}: {s.title}" if s.title else f"{self.chapter.name}  ·  Scene {s.index}"
        dlg = ErrorDialog(self, f"Scene {s.index} gặp lỗi", ctx, s.error,
                          copy_text=f"{self.chapter.name} / Scene {s.index} [{s.status}]\n{s.error}".strip(), sync_cb=lambda *_: self.flow_sync())
        dlg.exec()

    def copy_error(self):
        if not 0 <= self._row < len(self.scenes):
            return
        s = self.scenes[self._row]
        text = f"{self.chapter.name} / Scene {s.index} [{s.status}]\n{s.error}".strip()
        QApplication.clipboard().setText(text)
        self.log("Đã copy lỗi vào clipboard.")

    def commit_detail(self):
        """Ghi nội dung ô chi tiết ngược vào scene đang chọn."""
        if not 0 <= self._row < len(self.scenes):
            return
        s = self.scenes[self._row]
        valid = {nfc(c.name) for c in self.chars_tab.chars}
        s.title = self.d_title.text().strip()
        names = [nfc(n.strip()) for n in self.d_chars.text().split(",") if n.strip()]
        if valid:
            names = [n for n in names if n in valid]
        s.characters = names[:3]
        s.narration = self.d_narr.toPlainText().strip()
        s.visual = self.d_visual.toPlainText().strip()

    def commit_chapter(self):
        """Ghi truyện đang gõ vào chương hiện tại; chương còn tên mặc định thì lấy tên từ dòng đầu truyện."""
        ch = self.chapter
        if not ch:
            return
        ch.story = self.story.toPlainText()
        first = next((ln.strip() for ln in ch.story.splitlines() if ln.strip()), "")
        if re.fullmatch(r"Chương \d+", ch.title) and re.match(r"(?i)^chương\s*\d+", first):
            ch.title = first[:120]

    def load_preview(self, row: int, autoplay: bool = False):
        if not 0 <= row < len(self.scenes):
            self.preview.load(None)
            self.update_reveal()
            return
        s = self.scenes[row]
        self.preview.load(s.clip or s.raw_clip, autoplay)
        self.update_reveal()

    def merged_path(self) -> Path:
        return self.project.merged_path(self.chapter)

    def set_view_mode(self, i: int):
        self.view_seg.blockSignals(True)
        self.view_seg.setCurrentIndex(i)
        self.view_seg.blockSignals(False)
        self.update_reveal()

    def reveal_target(self) -> Path | None:
        """File đang xem ở khung xem trước: video ghép của chương (chế độ 'Video ghép') hoặc clip của scene đang chọn."""
        if not self.project or not self.chapter:
            return None
        if self.view_seg.currentIndex() == 1:
            f = self.merged_path()
        else:
            s = self.scenes[self._row] if 0 <= self._row < len(self.scenes) else None
            f = Path(s.clip or s.raw_clip) if s and (s.clip or s.raw_clip) else None
        return f if f and f.exists() else None

    def update_reveal(self):
        f = self.reveal_target()
        self.btn_reveal.setEnabled(f is not None)
        self.btn_reveal.setToolTip(f"Mở thư mục chứa {f.name} (chọn sẵn file)" if f else
                                   "Chưa có file để mở: gen xong rồi bấm ③ Ghép video")

    def reveal_current(self):
        f = self.reveal_target()
        if f:
            flow.reveal_file(f)

    def reveal_merged(self, whole_project: bool = False):
        """Hiện video ghép (của chương đang chọn, hoặc của cả dự án) trong Finder/Explorer."""
        if not self.project:
            return
        f = self.project.full_path if whole_project else (self.merged_path() if self.chapter else None)
        if f and f.exists():
            flow.reveal_file(f)
        else:
            QMessageBox.information(self, "Chưa có video ghép", "Chưa có file video ghép. Bấm ③ Ghép video sau khi gen xong các scene"
                                    + (" của tất cả chương." if whole_project else " của chương."))

    def on_view_changed(self, i: int):
        if i == 1:
            self.show_merged()
        else:
            self.load_preview(self._row)

    def show_merged(self):
        if self.chapter and self.merged_path().exists():
            self.set_view_mode(1)
            self.preview.load(str(self.merged_path()), autoplay=True)
        else:
            self.set_view_mode(0)
            QMessageBox.information(self, "Chưa có video ghép", "Bấm ③ Ghép video sau khi gen xong các scene của chương.")

    def save_edits(self):
        if not self.project:
            return
        self.commit_detail()
        self.commit_chapter()
        p = self.project
        p.synopsis = self.synopsis.toPlainText().strip()
        p.style, p.aspect_ratio = self.style.text().strip(), self.aspect.currentData() or "9:16"
        p.tts_provider, p.voice = self.provider.currentData(), self.current_voice()
        p.narration_lang = self.narr_lang.currentData() or "vi"
        p.voice_style = self.voice_style.text().strip()
        p.flow_model = self.flow_model.currentText()
        p.flow_resolution, p.flow_auto_duration = self.flow_res.currentText(), self.flow_auto_dur.isChecked()
        p.flow_parallel = self.flow_parallel.value()
        p.save()
        self.refresh_chapter_labels()

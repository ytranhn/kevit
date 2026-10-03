"""Tab Dự án: bảng scene, chọn/xoá/ghép, chi tiết scene, xem video, lưu chỉnh sửa."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QTableWidgetItem, QVBoxLayout)

from .. import credits, langs, flow, llm, scene_ops, scene_planner, trash
from ..models import nfc
from ..theme import SP
from ..widgets import Popover, repolish


class SceneTableMixin:
    """Tab Dự án: bảng scene, chọn/xoá/ghép, chi tiết scene, xem video, lưu chỉnh sửa."""

    # ================= bảng + chi tiết scene =================
    def fill_table(self, select: int | None = None):
        if select is None:
            select = self._row
        scenes = self.scenes
        keep = self.selected_indices()      # giữ nguyên các dòng đang chọn khi bảng được làm mới
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for s in scenes:
            r = self.table.rowCount()
            self.table.insertRow(r)
            label, color = self.status_text(s)
            vals = [str(s.index), s.title, label, ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QBrush(QColor(color)))
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self.apply_scene_filter()
        n = len(scenes)
        self.refresh_counts()
        self.update_empty_state()
        self.update_steps()
        self._row = -1
        if n:
            row = select if select is not None and 0 <= select < n else 0
            self.table.setCurrentCell(row, 0)  # phát currentCellChanged -> on_row_changed
            if len(keep) > 1:                  # khôi phục lựa chọn nhiều dòng
                sm = self.table.selectionModel()
                for r, s in enumerate(scenes):
                    if s.index in keep:
                        sm.select(self.table.model().index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.update_sel_label()
        else:
            self.clear_detail()
            self.preview.load(None)

    def selected_scenes(self) -> list:
        """Các scene đang chọn trong bảng theo thứ tự hiển thị; không chọn dòng nào thì lấy scene đang xem."""
        keep = self.selected_indices()
        out = [s for s in self.scenes if s.index in keep]
        if not out and 0 <= self._row < len(self.scenes):
            out = [self.scenes[self._row]]
        return out

    def _after_edit(self, select: int | None = None):
        self.project.save()
        self.fill_table(select if select is not None else self._row)

    def delete_selected(self):
        if not self.manage_btn.isEnabled() or not self.chapter:
            return
        sel = self.selected_scenes()
        if not sel:
            return
        files = sum(len(scene_ops.scene_files(s)) for s in sel)
        nums = ", ".join(str(s.index) for s in sel)
        if QMessageBox.question(self, "Xoá scene",
                                f"Xoá {len(sel)} scene (#{nums}) khỏi {self.chapter.name}?\n"
                                f"{files} file video/giọng của chúng sẽ chuyển vào thùng rác của dự án (khôi phục được).\n"
                                "Các scene còn lại được đánh số lại.") != QMessageBox.Yes:
            return
        pos = min(self.scenes.index(s) for s in sel)
        moved = scene_ops.delete_scenes(self.project, self.chapter, sel)
        self.log(f"[{self.chapter.name}] Đã xoá {len(sel)} scene, {moved} file vào thùng rác.")
        self._row = -1
        self._after_edit(min(pos, max(len(self.scenes) - 1, 0)))

    def clear_selected_videos(self, keep_raw: bool):
        sel = [s for s in self.selected_scenes() if scene_ops.scene_files(s)]
        if not sel or not self.chapter:
            return
        what = ("bản có giọng (clip Flow gốc được giữ, tạo lại giọng không tốn credit)" if keep_raw
                else "TOÀN BỘ video gồm cả clip Flow gốc (muốn có lại phải gen trên Flow, tốn credit)")
        if QMessageBox.question(self, "Xoá video",
                                f"Xoá {what} của {len(sel)} scene (#{', '.join(str(s.index) for s in sel)})?\n"
                                "File chuyển vào thùng rác của dự án (khôi phục được).") != QMessageBox.Yes:
            return
        moved = scene_ops.clear_videos(self.project, self.chapter, sel, keep_raw)
        self.log(f"[{self.chapter.name}] Đã xoá video của {len(sel)} scene, {moved} file vào thùng rác.")
        self._after_edit()

    # ---- gộp scene ----
    def _merge_data_job(self, groups: list[list]):
        """Tạo nội dung gộp cho từng nhóm: dùng LLM nếu đã cấu hình, lỗi hoặc chưa có thì dùng cách gộp đơn giản."""
        chars = self.chars_tab.chars
        use_llm = llm.is_configured()[0]
        lang = self.project.narration_lang if self.project else "vi"

        def job(log):
            out = []
            for g in groups:
                data, how = None, "đơn giản"
                if use_llm:
                    try:
                        data, how = scene_planner.merge_scenes_llm(g, chars, log, lang), "LLM"
                    except Exception as e:  # noqa: BLE001
                        log(f"LLM gộp lỗi ({str(e)[:80]}), dùng cách gộp đơn giản.")
                out.append((g, data or scene_ops.merge_heuristic(g), how))
            return out
        return job

    def merge_selected(self):
        sel = self.selected_scenes()
        if len(sel) < 2 or not scene_ops.is_contiguous(self.chapter, sel):
            QMessageBox.information(self, "Gộp scene", "Hãy chọn từ 2 scene liền kề nhau (Shift+click).")
            return
        self.save_edits()
        self.log(f"[{self.chapter.name}] Đang gộp scene {', '.join(str(s.index) for s in sel)}...")

        def done(res):
            group, data, how = res[0]
            self.show_merge_preview(group, data, how)
        self.run(self._merge_data_job([sel]), done)

    def show_merge_preview(self, group, data, how):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Gộp scene {', '.join(str(s.index) for s in group)} ({how})")
        dlg.resize(620, 520)
        title = QLineEdit(data["title"])
        chars = QLineEdit(", ".join(data["characters"]))
        narr = QPlainTextEdit(data["narration"])
        visual = QPlainTextEdit(data["visual"])
        count = QLabel("")
        count.setProperty("caption", True)

        def upd():
            w = len(narr.toPlainText().split())
            count.setText(f"{w} từ ≈ {w / 3.3:.0f}s" + ("  ⚠ dài hơn clip 8-10s" if w > 34 else ""))
        narr.textChanged.connect(upd)
        upd()
        lost = sum(len(scene_ops.scene_files(s)) for s in group)
        note = QLabel((f"⚠ {lost} file video/giọng của các scene này sẽ chuyển vào thùng rác (nội dung đã đổi nên phải gen lại). "
                       if lost else "") + "Bạn có thể sửa nội dung trước khi gộp.")
        note.setWordWrap(True)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(note)
        for cap, w in (("Tiêu đề", title), ("Nhân vật (tối đa 3)", chars)):
            lay.addWidget(QLabel(cap))
            lay.addWidget(w)
        head = QHBoxLayout()
        head.addWidget(QLabel("Thuyết minh"))
        head.addStretch()
        head.addWidget(count)
        lay.addLayout(head)
        lay.addWidget(narr, 2)
        lay.addWidget(QLabel("Visual (prompt gửi Flow)"))
        lay.addWidget(visual, 2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Gộp")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        valid = {nfc(c.name): c.name for c in self.chars_tab.chars}
        names = [valid[nfc(n.strip())] for n in chars.text().split(",") if nfc(n.strip()) in valid]
        merged = {"title": title.text().strip() or data["title"], "narration": narr.toPlainText().strip(),
                  "visual": visual.toPlainText().strip(), "characters": names[:3]}
        new = scene_ops.merge_group(self.project, self.chapter, group, merged)
        self.log(f"[{self.chapter.name}] Đã gộp {len(group)} scene thành scene {new.index}. Cần gen video cho scene này.")
        self._row = -1
        self._after_edit(new.index - 1)

    def suggest_merge_dialog(self):
        if not self.chapter:
            return
        self.save_edits()
        groups = scene_ops.suggest_merges(self.chapter)
        if not groups:
            QMessageBox.information(self, "Gợi ý gộp",
                                    "Không có nhóm scene ngắn liền kề nào phù hợp (mỗi scene ≤14 từ, tổng ≤32 từ, chưa có video).")
            return
        p = self.project
        per = credits.scene_cost(p.flow_model, p.flow_resolution, 8)
        dlg = QDialog(self)
        dlg.setWindowTitle("Gợi ý gộp scene ngắn")
        dlg.resize(640, 420)
        lst = QListWidget()
        for g in groups:
            txt = " / ".join(s.narration for s in g)
            it = QListWidgetItem(f"Scene {' + '.join(str(s.index) for s in g)}  ({sum(scene_ops.words(s) for s in g)} từ)  —  {txt[:90]}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            lst.addItem(it)
        info = QLabel("")
        info.setProperty("caption", True)

        def upd():
            chosen = [g for i, g in enumerate(groups) if lst.item(i).checkState() == Qt.Checked]
            saved = sum(len(g) - 1 for g in chosen)
            info.setText(f"Gộp {len(chosen)} nhóm: bớt {saved} scene, tiết kiệm khoảng {saved * per} credit Flow ({p.flow_model}).")
        lst.itemChanged.connect(upd)
        upd()
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(QLabel("Các scene liền kề đều ngắn có thể gộp thành 1 clip. Bỏ tick nhóm nào bạn không muốn gộp."))
        lay.addWidget(lst, 1)
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Gộp các nhóm đã chọn")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = [g for i, g in enumerate(groups) if lst.item(i).checkState() == Qt.Checked]
        if not chosen:
            return
        ch = self.chapter
        self.log(f"[{ch.name}] Đang gộp {len(chosen)} nhóm scene...")

        def done(results):
            n = 0
            for g, data, how in results:
                scene_ops.merge_group(self.project, ch, g, data)
                n += len(g) - 1
            self.log(f"[{ch.name}] Đã gộp {len(results)} nhóm, bớt {n} scene. Cần gen video cho các scene mới gộp.")
            self._row = -1
            self._after_edit(0)
        self.run(self._merge_data_job(chosen), done)

    def empty_trash_dialog(self):
        if not self.project:
            return
        n, size = trash.trash_stats(self.project.dir)
        if not n:
            QMessageBox.information(self, "Thùng rác", "Thùng rác của dự án đang trống.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Thùng rác của dự án")
        box.setText(f"Thùng rác có {n} file ({size / 1_048_576:.1f} MB).")
        box.setInformativeText("Dọn thùng rác sẽ xoá HẲN các file này, không khôi phục được.")
        b_open = box.addButton("Mở thư mục", QMessageBox.ActionRole)
        b_empty = box.addButton("Dọn thùng rác", QMessageBox.DestructiveRole)
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_open:
            flow.reveal(trash.trash_root(self.project.dir))
        elif box.clickedButton() is b_empty:
            trash.empty_trash(self.project.dir)
            self.log("Đã dọn thùng rác của dự án.")

    # ---- nhân vật bị xoá ở tab Nhân vật ----
    def character_usage(self, names: list[str]) -> dict[str, int]:
        if not self.project:
            return {}
        want = {nfc(n): n for n in names}
        out = {n: 0 for n in names}
        for _, s in self.project.all_scenes():
            for c in s.characters:
                if nfc(c) in want:
                    out[want[nfc(c)]] += 1
        return out

    def on_characters_deleted(self, names: list[str]):
        if not self.project:
            return
        gone = {nfc(n) for n in names}
        for _, s in self.project.all_scenes():
            s.characters = [c for c in s.characters if nfc(c) not in gone]
        self.project.save()
        if self.chapter:
            self.fill_table(self._row)

    def selected_indices(self) -> set[int]:
        sc = self.scenes
        return {sc[i.row()].index for i in self.table.selectionModel().selectedRows() if 0 <= i.row() < len(sc)}

    def update_sel_label(self):
        """Nhãn ở thanh công cụ thẻ Scene: đang chọn nhiều dòng thì hiện 'Đã chọn N/M', không thì hiện tiến độ chương."""
        n, total = len(self.selected_indices()), len(self.scenes)
        self.sel_label.setText(f"Đã chọn {n}/{total}" if n > 1 else getattr(self, "_progress_text", ""))

    def select_where(self, pred):
        sm, model = self.table.selectionModel(), self.table.model()
        self.table.clearSelection()
        for r, s in enumerate(self.scenes):
            if pred(s):
                sm.select(model.index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.setFocus()
        self.update_sel_label()

    def table_context_menu(self, pos):
        if not self.scenes:
            return
        self._ctx_pop = Popover(self, self.build_context_pop, 400)       # giữ tham chiếu để không bị thu hồi
        self._ctx_pop.show_at(self.table.viewport().mapToGlobal(pos))

    def step_scene(self, delta: int) -> None:
        r = self._row + delta
        if 0 <= r < self.table.rowCount():
            while 0 <= r < self.table.rowCount() and self.table.isRowHidden(r):      # bỏ qua dòng đang bị lọc ẩn
                r += delta
            if 0 <= r < self.table.rowCount():
                self.table.setCurrentCell(r, 0)

    def on_cell_clicked(self, row: int, col: int) -> None:
        """Bấm vào nút '···' ở cuối dòng: mở menu thao tác của dòng đó (cùng menu chuột phải)."""
        if col == 3 and 0 <= row < len(self.scenes):
            idx = self.table.model().index(row, 3)
            rect = self.table.visualRect(idx)
            self.table.setCurrentCell(row, 0)
            self.table_context_menu(rect.bottomLeft())

    def set_scene_filter(self, key: str) -> None:
        self.scene_filter = key
        self.btn_filter.setProperty("active", key != "all")
        repolish(self.btn_filter)
        self.apply_scene_filter()

    def apply_scene_filter(self, *_) -> None:
        """Ô tìm + bộ lọc trạng thái chỉ ẨN/HIỆN dòng (không đổi thứ tự) nên số dòng vẫn khớp danh sách scene."""
        import unicodedata
        norm = lambda t: "".join(c for c in unicodedata.normalize("NFD", t.replace("đ", "d").replace("Đ", "D").lower()) if unicodedata.category(c) != "Mn")
        q = norm(self.scene_search.text().strip())
        for r, s in enumerate(self.scenes):
            if r >= self.table.rowCount():
                break
            ok = (not q or q in norm(f"{s.index} {s.title} {s.narration} {s.visual}")) and \
                 (self.scene_filter == "all" or (self.scene_filter == "done" and s.status == "done")
                  or (self.scene_filter == "todo" and s.status != "done") or (self.scene_filter == "error" and s.status == "error"))
            self.table.setRowHidden(r, not ok)

    def on_row_changed(self, cur: int, _prev: int):
        self.commit_detail()
        self._row = cur
        self.load_detail(cur)
        if not self._skip_preview:
            self.set_view_mode(0)
            self.load_preview(cur)

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

"""Tab Dự án: bảng scene, chọn/xoá/ghép, chi tiết scene, xem video, lưu chỉnh sửa."""
from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

from .. import flow, scene_ops, trash
from ..models import nfc
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


















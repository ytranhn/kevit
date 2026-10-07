"""Tab Dự án: các menu nổi (gen, quản lý, dự án, chương, thêm), xoá dự án, dịch lời đọc."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

from .. import credits, langs, flow, llm, pipeline, scene_ops, scene_planner, trash
from .. import models
from ..models import Project
from .. import icons, theme
from ..widgets import Segmented


class PopoverMixin:
    """Tab Dự án: các menu nổi (gen, quản lý, dự án, chương, thêm), xoá dự án, dịch lời đọc."""

    # ================= popover =================
    def gen_cost(self, mode: str) -> str:
        p = self.project
        todo = self.pick_todo(mode)
        if not todo or not p:
            return ""
        est = credits.estimate(p.flow_model, p.flow_resolution, todo, p.flow_auto_duration, p.narration_lang)
        return f"{len(todo)} clip  ·  ≈ {est} credit"

    def build_gen_pop(self, pop):
        sc = self.scenes
        cur = sc[self._row] if 0 <= self._row < len(sc) else None
        n = len(self.selected_indices())
        pend = sum(1 for s in sc if s.status != "done")
        pop.section("Gen video bằng Flow")
        pop.item(f"Scene đang xem  ·  #{cur.index}" if cur else "Scene đang xem",
                 "  ·  ".join(x for x in (cur.title[:34], self.gen_cost("current")) if x) if cur else "Chưa chọn scene",
                 lambda: self.flow_auto_run("current"), enabled=cur is not None)
        pop.item(f"Các scene đang chọn  ·  {n}" if n >= 2 else "Các scene đang chọn",
                 self.gen_cost("selected") if n >= 2 else "Giữ ⇧ hoặc ⌘ rồi click để chọn nhiều dòng",
                 lambda: self.flow_auto_run("selected"), enabled=n >= 2)
        pop.separator()
        pop.item(f"Tất cả scene chưa xong  ·  {pend}", self.gen_cost("pending") or "Mọi scene đã xong",
                 lambda: self.flow_auto_run("pending"), enabled=pend > 0)
        pop.item(f"Gen lại tất cả  ·  {len(sc)}", "Ghi đè cả scene đã xong, tốn nhiều credit",
                 lambda: self.flow_auto_run("all"), enabled=bool(sc), danger=True)
        pop.separator()
        chap_pend = sum(1 for c in (self.project.chapters if self.project else []) if any(s.status != "done" for s in c.scenes))
        pop.item(f"Gen nhiều chương…  ·  {chap_pend} chương chưa xong", "Gen lần lượt, tự ghép video, tự đồng bộ khi lỗi",
                 self.gen_chapters_dialog, enabled=chap_pend > 0)
        pop.separator()
        pop.section("Số scene gửi cùng lúc lên Flow")
        seg = Segmented()
        for k in (1, 2, 3, 4):
            seg.addItem(str(k), k)
        seg.setCurrentIndex(max(0, seg.findData(min(self.flow_parallel.value(), 4))))
        seg.currentIndexChanged.connect(lambda i: self.set_parallel(seg.currentData()))
        pop.widget(seg)
        hint = QLabel("1 = lần lượt từng scene. Số lớn hơn: nhiều scene render cùng lúc trên Flow để đỡ chờ, credit không đổi. Nếu Flow báo lỗi thì giảm xuống.")
        hint.setProperty("caption", True)
        hint.setWordWrap(True)
        pop.widget(hint)

    def set_parallel(self, k: int):
        """Đổi số scene gửi cùng lúc ngay từ menu Gen video (cùng giá trị với Cài đặt dự án)."""
        if self.project and k:
            self.flow_parallel.setValue(int(k))
            self.project.flow_parallel = int(k)
            self.project.save()
            self.log(f"Gen video: gửi {k} scene cùng lúc." if k > 1 else "Gen video: lần lượt từng scene.")

    def build_manage_pop(self, pop):
        sel = self.selected_scenes()
        n = len(sel)
        contiguous = n >= 2 and self.chapter is not None and scene_ops.is_contiguous(self.chapter, sel)
        has_final = any(s.clip or s.audio for s in sel)
        has_any = any(scene_ops.scene_files(s) for s in sel)
        pop.section("Gộp scene")
        pop.item(f"Gộp thông minh {n} scene đang chọn" if n >= 2 else "Gộp thông minh",
                 "Viết lại thuyết minh và visual thành một clip" if contiguous else "Chọn từ 2 scene liền kề (Shift+click)",
                 self.merge_selected, enabled=contiguous)
        pop.item("Gợi ý gộp các scene ngắn…", "Tự tìm nhóm scene ngắn để tiết kiệm credit",
                 self.suggest_merge_dialog, enabled=bool(self.scenes))
        pop.separator()
        pop.section("Video")
        pop.item(f"Xoá bản có giọng  ·  {n} scene", "Giữ clip Flow gốc, tạo lại giọng miễn phí",
                 lambda: self.clear_selected_videos(keep_raw=True), enabled=has_final)
        pop.item(f"Xoá toàn bộ video  ·  {n} scene", "Phải gen lại trên Flow nên tốn credit",
                 lambda: self.clear_selected_videos(keep_raw=False), enabled=has_any, danger=True)
        pop.separator()
        pop.section("Scene")
        pop.item(f"Xoá {n} scene…", "Chuyển vào thùng rác, khôi phục được", self.delete_selected,
                 enabled=n > 0, danger=True, shortcut="⌫")

    def build_project_pop(self, pop):
        names = Project.list_names()
        cur = self.combo.currentText()
        pop.section(f"Chuyển dự án  ·  {len(names)}")
        entries = []
        for name in names:
            nch, done, total = Project.summary(name)
            sub = f"{nch} chương  ·  {done}/{total} scene xong" if total else f"{nch} chương  ·  chưa có scene"
            entries.append(dict(title=name, sub=sub, cb=(lambda n=name: self.switch_project(n)), shortcut="✓" if name == cur else "",
                                progress=(done, total), action=("Xoá", (lambda n=name: self.delete_project(n)))))
        pop.searchable_list(entries, "Tìm dự án…", focus=names.index(cur) if cur in names else -1, empty="Không có dự án nào khớp")
        pop.separator()
        pop.item("+ Dự án mới…", "Tạo dự án trống với một chương", self.new_project)
        pop.separator()
        n_trash = len(trash.list_trashed_projects())
        pop.item("Dự án đã xoá…", f"{n_trash} dự án trong thùng rác" if n_trash else "Thùng rác dự án đang trống",
                 self.restore_projects_dialog, enabled=n_trash > 0)

    def delete_project(self, name: str | None = None):
        """Xoá một dự án (mặc định là dự án đang mở): chuyển nguyên thư mục (chương, scene, clip, giọng, nhân vật) vào thùng rác
        dự án, khôi phục được. Xoá thẳng từ danh sách, không cần chuyển sang dự án đó trước."""
        cur = self.combo.currentText()
        name = name or cur
        if not name:
            return
        is_open = bool(self.project) and name == cur
        if is_open and self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong (hoặc bấm Dừng) rồi xoá dự án đang mở.")
            return
        try:
            p = self.project if is_open else Project.load(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không đọc được dự án", str(e))
            return
        scenes = sum(len(c.scenes) for c in p.chapters)
        files, size = trash.dir_size(p.dir)
        nchars = len(models.load_characters(name))
        box = QMessageBox(self)
        box.setWindowTitle("Xoá dự án")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Xoá dự án '{name}'?")
        box.setInformativeText(
            f"{len(p.chapters)} chương · {scenes} scene · {nchars} nhân vật · {files} file ({size / 1_048_576:.0f} MB) "
            "sẽ được chuyển vào thùng rác dự án. Bạn khôi phục được bằng mục “Dự án đã xoá…”.\n\n"
            "Dự án tương ứng trên Google Flow (nếu có) không bị xoá.")
        b_del = box.addButton("Xoá dự án", QMessageBox.DestructiveRole)
        b_cancel = box.addButton("Huỷ", QMessageBox.RejectRole)
        box.setDefaultButton(b_cancel)          # Enter = Huỷ, tránh lỡ tay xoá
        box.exec()
        if box.clickedButton() is not b_del:
            return
        try:
            trash.trash_project(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Không xoá được", str(e))
            return
        self.log(f"Đã xoá dự án '{name}' (nằm trong thùng rác dự án, khôi phục bằng “Dự án đã xoá…”).")
        if is_open:
            self.reload_projects()
        else:                                   # xoá dự án khác: giữ nguyên dự án đang mở, chỉ làm mới danh sách
            self.combo.blockSignals(True)
            self.combo.clear()
            self.combo.addItems(Project.list_names())
            self.combo.setCurrentText(cur)
            self.combo.blockSignals(False)
            self.refresh_nav()

    def restore_projects_dialog(self):
        from ..trash_dialog import TrashedProjectsDialog
        dlg = TrashedProjectsDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.restored:
            self.log(f"Đã khôi phục dự án '{dlg.restored}'.")
            self.reload_projects(dlg.restored)

    def build_chapter_pop(self, pop):
        p = self.project
        chs = p.chapters if p else []
        pop.section(f"Chuyển chương  ·  {len(chs)}")
        entries = []
        for i, c in enumerate(chs):
            sub = f"{c.done}/{len(c.scenes)} scene xong" if c.scenes else ("Có truyện, chưa tạo scene" if c.story.strip() else "Chưa có nội dung")
            entries.append(dict(title=c.name, search=f"chuong {int(c.id)}" if c.id.isdigit() else "", sub=sub, cb=(lambda k=i: self.chap_combo.setCurrentIndex(k)),
                                shortcut="✓" if i == self._chap_idx else "", progress=(c.done, len(c.scenes)),
                                action=("Xoá", (lambda k=i: self.delete_chapter(k))) if len(chs) > 1 else None))
        pop.searchable_list(entries, "Tìm chương (tên hoặc số)…", focus=self._chap_idx, empty="Không có chương nào khớp")
        pop.separator()
        pop.item("+ Chương mới…", "Dán truyện rồi tạo scene", self.new_chapter)
        ch = self.chapter
        pop.separator()
        pop.section("Chương đang mở")
        pop.item("Đổi tên chương", ch.name if ch else "", self.rename_chapter, enabled=bool(ch))
        pop.item("Mở thư mục chương", "Xem file clip và giọng trên ổ đĩa",
                 lambda: flow.reveal(self.project.chapter_dir(self.chapter)), enabled=bool(ch))

    def switch_project(self, name: str):
        if name and name != self.combo.currentText():
            self.combo.setCurrentText(name)

    def step_chapter(self, delta: int):
        k = self._chap_idx + delta
        if self.project and 0 <= k < len(self.project.chapters):
            self.chap_combo.setCurrentIndex(k)

    def refresh_nav(self):
        """Cập nhật breadcrumb: tên dự án, tên chương + nhãn tiến độ (xanh khi xong hết), nút ‹ › theo vị trí chương."""
        p, ch = self.project, self.chapter
        self.nav_project.set_title(p.name if p else "Chưa có dự án")
        if ch:
            n = len(ch.scenes)
            self.nav_chapter.set_title(ch.name)
            self.nav_chapter.set_pill(f"{ch.done}/{n}" if n else "trống", "ok" if n and ch.done == n else "info")
            self.chap_bar.setRange(0, max(n, 1))
            self.chap_bar.setValue(ch.done)
            self.chap_count.setText(f"{ch.done}/{n}" if n else "trống")
            icons.attach(self.chap_check, "check", 18, role="muted")
            self.chap_check.setVisible(bool(n) and ch.done == n)
            if n and ch.done == n:
                self.chap_check.setStyleSheet(f"color: {theme.T['ok']}; background: transparent;")
        else:
            self.nav_chapter.set_title("Chưa có chương")
            self.nav_chapter.set_pill("", "info")
            self.chap_bar.setValue(0)
            self.chap_count.setText("")
            self.chap_check.setVisible(False)
        total = len(p.chapters) if p else 0
        self.btn_prev.setEnabled(not self._busy and self._chap_idx > 0)
        self.btn_next.setEnabled(not self._busy and 0 <= self._chap_idx < total - 1)

    def build_more_pop(self, pop):
        n, size = trash.trash_stats(self.project.dir) if self.project else (0, 0)
        pop.section("Google Flow")
        pop.item("Mở Chrome Flow", "Cửa sổ Chrome riêng để đăng nhập và điều khiển Flow", self.launch_flow_chrome)
        pop.item("Thủ công: xuất prompt và ảnh", "Tự dán vào Flow bằng tay", self.flow_export)
        pop.item("Thủ công: nhập clip tải từ Flow", "Gán clip đã tải về cho các scene", self.flow_import)
        pop.item("Gen bằng Gemini API", "Cần key có quyền Veo", lambda: self.generate("pending"))
        pop.separator()
        pop.section("Nội dung chương")
        pop.item("Viết truyện từ bối cảnh…", "AI lập dàn ý và viết các chương, không tốn credit Flow", self.write_story_dialog)
        pop.item("Tạo scene cho nhiều chương…", "AI tách scene lần lượt các chương đã chọn, không tốn credit Flow", self.plan_chapters_dialog,
                 enabled=bool(self.project and len(self.project.chapters) > 1))
        pop.item("Gắn nhân vật vào scene đã có", "Nhân vật tạo sau khi tách scene; theo văn bản, không tốn credit",
                 lambda: self.attach_characters_to_scenes(None, ask=True), enabled=bool(self.scenes))
        pop.item("Nhận diện lại nhân vật cho scene", "Theo văn bản, không dùng LLM", self.reassign_characters)
        lname = langs.name(self.project.narration_lang) if self.project else ""
        pop.item("Viết lại thuyết minh bám truyện", "Giữ nguyên clip đã gen", self.realign)
        pop.item(f"Dịch thuyết minh sang {lname}…" if lname else "Dịch thuyết minh…",
                 "AI dịch từ truyện gốc, rồi tạo lại giọng, không tốn credit Flow", self.translate_narrations_dialog,
                 enabled=bool(self.project and any(c.scenes for c in self.project.chapters)))
        pop.item("Áp dụng lại giọng đọc cho cả chương", "Không tốn credit Flow",
                 lambda: self.revoice(only_current=False))
        pop.separator()
        pop.section("Dự án")
        pop.item("Ghép tất cả chương thành 1 video", "Cần mọi chương đã gen xong", self.merge_project)
        pop.item("Hiện video ghép của chương", "Mở thư mục và chọn sẵn file", self.reveal_merged,
                 enabled=bool(self.project and self.chapter and self.merged_path().exists()))
        pop.item("Hiện video ghép cả dự án", "Mở thư mục và chọn sẵn file", lambda: self.reveal_merged(True),
                 enabled=bool(self.project and self.project.full_path.exists()))
        pop.item("Mở thư mục dự án", "", lambda: self.project and flow.reveal(self.project.dir))
        pop.item("Dọn thùng rác…", f"{n} file  ·  {size / 1_048_576:.1f} MB" if n else "Đang trống", self.empty_trash_dialog)

    def build_context_pop(self, pop):
        if self.s2.isEnabled():
            self.build_gen_pop(pop)
            pop.separator()
        self.build_manage_pop(pop)

    def open_settings(self):
        if not self.project or self._busy:
            return
        old_lang = self.project.narration_lang
        if self.settings_dialog.open_for_project():
            self.publish_settings_changed.emit()
            self.update_steps()
            self.log("Đã lưu cài đặt dự án.")
            if self.project.narration_lang != old_lang and any(c.scenes for c in self.project.chapters):
                QTimer.singleShot(0, lambda: self.translate_narrations_dialog(changed_from=old_lang))

    def translate_narrations_dialog(self, changed_from: str | None = None):
        """Viết lại thuyết minh của các scene hiện có bằng ngôn ngữ đã chọn (AI dịch từ truyện gốc), rồi tạo lại giọng đọc."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        p = self.project
        lang = p.narration_lang
        here = len(self.chapter.scenes) if self.chapter else 0
        total = sum(len(c.scenes) for c in p.chapters)
        if not total:
            QMessageBox.information(self, "Chưa có scene", "Hãy tạo scene trước.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Dịch thuyết minh")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Viết lại thuyết minh bằng {langs.name(lang)}?" if changed_from is None else
                    f"Đã đổi ngôn ngữ thuyết minh sang {langs.name(lang)}. Dịch thuyết minh của các scene hiện có?")
        box.setInformativeText("AI viết lại từ đoạn truyện gốc của từng scene (không dịch nối từ bản cũ), ghi đè thuyết minh hiện tại rồi tạo lại "
                               "giọng đọc cho scene đã có clip. Không tốn credit Flow, chỉ tốn token của AI đang chọn. "
                               "Chọn “Để sau” thì các scene mới tạo vẫn dùng ngôn ngữ này.")
        b_here = box.addButton(f"Chương này ({here} scene)", QMessageBox.AcceptRole)
        b_all = box.addButton(f"Cả dự án ({total} scene)", QMessageBox.AcceptRole)
        box.addButton("Để sau", QMessageBox.RejectRole)
        b_here.setEnabled(here > 0)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_here, b_all):
            return
        chapters = [self.chapter] if clicked is b_here else [c for c in p.chapters if c.scenes]
        chapters = [c for c in chapters if c and c.scenes]
        self.log(f"Đang viết lại thuyết minh bằng {langs.name(lang)} ({sum(len(c.scenes) for c in chapters)} scene) bằng {llm.describe()}...")

        def job(log):
            changed = 0
            for ch in chapters:
                res = scene_planner.translate_narrations(ch.scenes, lang, log)
                for s in ch.scenes:
                    if s.index in res:
                        s.narration, changed = res[s.index], changed + 1
                p.save()
                log(f"[{ch.name}] Đã viết lại thuyết minh {len(res)}/{len(ch.scenes)} scene.")
            work = [(ch, s) for ch in chapters for s in ch.scenes if s.raw_clip and Path(s.raw_clip).exists()]
            if work:
                log(f"Tạo lại giọng đọc ({langs.name(lang)}) cho {len(work)} scene đã có clip...")

                def one(cs):
                    ch, s = cs
                    try:
                        pipeline.apply_voice(p, ch, s, log)
                        s.status, s.error = "done", ""
                    except Exception as e:  # noqa: BLE001
                        s.status, s.error = "error", str(e)[:1500]
                        log(f"Scene {s.index} lỗi giọng: {e}")
                    p.save()
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong") as pool:
                    list(pool.map(one, work))
            return changed
        self.run(job, lambda n: (self.refresh_view(), self.log(f"Xong: đã viết lại thuyết minh {n} scene bằng {langs.name(lang)}. "
                                                               "Bấm ③ Ghép video để ghép lại.")))

    def on_theme_changed(self):
        if self.project:
            self.fill_table(self._row)

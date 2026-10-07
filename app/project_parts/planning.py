"""Tab Dự án: tách scene, nhân vật, căn lại, kiểm tra credit."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from .. import flow_auto, llm, pipeline, scene_planner
from .. import accounts


class PlanningMixin:
    """Tab Dự án: tách scene, nhân vật, căn lại, kiểm tra credit."""

    # ================= các bước (theo chương đang chọn) =================
    def plan(self):
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        ch = self.chapter
        if not ch.story.strip():
            self.left_tabs.setCurrentWidget(self.story)
            QMessageBox.information(self, "Thiếu truyện", f"Dán nội dung {ch.name} vào tab Truyện trước.")
            return
        if not self.chars_tab.chars and not self.ask_characters_first():
            return
        p, chars, n = self.project, self.chars_tab.chars, self.max_scenes.value()
        if ch.scenes and QMessageBox.question(
                self, "Tạo lại scene", f"{ch.name} đã có scene. Tạo lại sẽ xoá danh sách scene hiện tại của chương này. "
                "Tiếp tục?") != QMessageBox.Yes:
            return
        self.log(f"[{ch.name}] Đang tách scene bằng {llm.describe()}...")

        def done(res):
            scenes, notes = res
            ch.scenes = scenes
            self._row = -1
            p.save()
            for line in notes:
                self.log(line)
            self.log(f"[{ch.name}] Đã tạo {len(scenes)} scene.")
            self.left_tabs.setCurrentWidget(self.scene_page)
        self.run(lambda log: scene_planner.plan_scenes(ch.story, chars, n, p.synopsis, log, p.narration_lang), done)

    def plan_chapters_dialog(self) -> None:
        """Tạo scene hàng loạt: chọn nhiều chương, AI tách scene lần lượt từng chương."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        from ..scene_batch_dialog import SceneBatchDialog
        dlg = SceneBatchDialog(self, self.project)
        if dlg.exec() != SceneBatchDialog.Accepted or not dlg.chosen():
            return
        if not self.chars_tab.chars and not self.ask_characters_first():
            return
        rep = dlg.replaced()
        if rep and QMessageBox.question(
                self, "Tạo lại scene", f"{len(rep)} chương đã có scene ({', '.join(c.name for c in rep[:4])}{'…' if len(rep) > 4 else ''}) "
                "sẽ bị xoá danh sách scene hiện tại. Tiếp tục?") != QMessageBox.Yes:
            return
        p, chars, n, chosen = self.project, self.chars_tab.chars, self.max_scenes.value(), dlg.chosen()
        self.log(f"Tạo scene cho {len(chosen)} chương bằng {llm.describe()}...")

        def done(res):
            ok, failed = res
            self._row = -1
            self.refresh_chapter_labels()
            self.show_chapter(self._chap_idx)
            msg = f"Đã tạo scene cho {len(ok)}/{len(chosen)} chương."
            if failed:
                msg += " Lỗi: " + "; ".join(f"{c.name} ({why[:60]})" for c, why in failed)
            self.log(msg)
        self.run(lambda log: scene_planner.plan_many(chosen, chars, n, p.synopsis, log, p.narration_lang, self._cancel.is_set, p.save),
                 done, cancelable=True)

    def ask_characters_first(self) -> bool:
        """Dự án chưa có nhân vật nào: scene tạo ra sẽ không gắn được nhân vật (clip mỗi cảnh một diện mạo). Cho chọn tạo nhân vật
        từ truyện NGAY (không cần scene), tạo scene luôn, hoặc huỷ. Trả về True nếu nên tiếp tục tạo scene."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Chưa có nhân vật")
        box.setText("Dự án chưa có nhân vật nào.")
        box.setInformativeText(
            "Nên tạo nhân vật TRƯỚC khi tạo scene: AI chỉ cần đọc truyện (không cần scene) để đề xuất nhân vật và ảnh, "
            "sau đó mỗi scene sẽ tự gắn đúng nhân vật.\n\nBạn vẫn có thể tạo scene ngay; nhân vật thêm sau sẽ được gắn vào các scene "
            "đã có (không tốn credit).")
        b_ai = box.addButton("Tạo nhân vật từ truyện (AI)…", QMessageBox.AcceptRole)
        b_go = box.addButton("Tạo scene luôn", QMessageBox.DestructiveRole)
        box.addButton("Huỷ", QMessageBox.RejectRole)
        box.setDefaultButton(b_ai)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_go:
            return True
        if clicked is b_ai:
            self.chars_tab.generate_from_story()          # hộp thoại AI; xong mà đã có nhân vật thì làm tiếp bước tạo scene
            return bool(self.chars_tab.chars) and QMessageBox.question(
                self, "Tạo scene", f"Đã có {len(self.chars_tab.chars)} nhân vật. Tạo scene cho {self.chapter.name} bây giờ?") == QMessageBox.Yes
        return False

    def on_characters_added(self, names: list[str]):
        """Nhân vật mới (AI, nhập gói, tạo tay): nếu các scene đã tạo trước đó nhắc tới họ thì hỏi để gắn vào."""
        if self.project and self.scenes_total():
            self.attach_characters_to_scenes(names, ask=True)

    def scenes_total(self) -> int:
        return sum(len(c.scenes) for c in self.project.chapters) if self.project else 0

    def attach_characters_to_scenes(self, names: list[str] | None = None, ask: bool = False):
        """Gắn nhân vật vào các scene ĐÃ CÓ (mọi chương) theo văn bản: không dùng LLM, không tốn credit, không đụng tới clip đã gen.
        names=None: xét mọi nhân vật; mỗi scene tối đa 3 nhân vật."""
        if not self.need_project() or not self.scenes_total():
            return
        self.save_edits()
        chars = self.chars_tab.chars
        scenes = [s for _, s in self.project.all_scenes()]
        saved = {id(s): list(s.characters) for s in scenes}
        changes = scene_planner.attach_characters(chars, scenes, names)
        if not changes:
            for s in scenes:
                s.characters = saved[id(s)]
            if not ask:
                self.log("Không có scene nào cần gắn thêm nhân vật (đã đủ hoặc văn bản không nhắc tới).")
            return
        who = sorted({n for _, new in changes for n in new})
        done_clips = sum(1 for sc, _ in changes if sc.status == "done")
        if ask:
            msg = (f"{len(who)} nhân vật ({', '.join(who[:6])}{'…' if len(who) > 6 else ''}) xuất hiện trong {len(changes)} scene đã tạo "
                   "nhưng chưa được gắn vào.\n\nGắn vào các scene đó? Không tốn credit, không gen lại gì."
                   + (f"\nLưu ý: {done_clips} scene đã có clip sẽ giữ nguyên clip; nhân vật mới chỉ ảnh hưởng khi gen lại." if done_clips else ""))
            if QMessageBox.question(self, "Gắn nhân vật vào scene", msg) != QMessageBox.Yes:
                for s in scenes:
                    s.characters = saved[id(s)]
                return
        self.project.save()
        self.fill_table(self._row)
        self.log(f"Đã gắn {len(who)} nhân vật vào {len(changes)} scene ({', '.join(who[:8])}{'…' if len(who) > 8 else ''}).")

    def reassign_characters(self):
        """Gán lại nhân vật theo văn bản cho các scene CHƯA có nhân vật nào (vd. bị mất do lỗi chuẩn hoá tên)."""
        if not self.need_project() or not self.scenes:
            return
        self.save_edits()
        chars = self.chars_tab.chars
        todo = [s for s in self.scenes if not s.characters]
        if not todo:
            self.log("Mọi scene của chương đã có nhân vật.")
            return
        n = 0
        for s in todo:
            s.characters = scene_planner.guess_characters(chars, s)
            n += bool(s.characters)
        self.project.save()
        self.fill_table(self._row)
        self.log(f"[{self.chapter.name}] Đã gán nhân vật cho {n}/{len(todo)} scene chưa có nhân vật. Kiểm tra lại trong ô Nhân vật.")

    def realign(self):
        """Gán lại đoạn truyện gốc + viết lại thuyết minh cho các scene HIỆN CÓ của chương, rồi tạo lại giọng."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        if not ch.scenes or not ch.story.strip():
            QMessageBox.information(self, "Thiếu dữ liệu", "Cần có scene và nội dung chương trong tab Truyện.")
            return
        if QMessageBox.question(self, "Viết lại thuyết minh",
                                f"LLM sẽ viết lại thuyết minh của TẤT CẢ scene trong {ch.name} theo đúng đoạn truyện "
                                "(ghi đè thuyết minh hiện tại), rồi tạo lại giọng đọc. Clip video giữ nguyên, không tốn "
                                "credit Flow. Tiếp tục?") != QMessageBox.Yes:
            return
        self.log(f"[{ch.name}] Đang căn lại thuyết minh theo truyện bằng {llm.describe()}...")

        def job(log):
            res, notes = scene_planner.realign_narration(ch.story, ch.scenes, log, p.narration_lang)
            for line in notes:
                log(line)
            for s in ch.scenes:
                if s.index in res:
                    s.source_text, s.narration = res[s.index]
            p.save()
            for s in ch.scenes:
                if s.raw_clip and Path(s.raw_clip).exists():
                    try:
                        pipeline.apply_voice(p, ch, s, log)
                        s.status, s.error = "done", ""
                    except Exception as e:  # noqa: BLE001
                        s.status, s.error = "error", str(e)[:1500]
                        log(f"Scene {s.index} lỗi: {e}")
                    p.save()
            log("Xong: đã cập nhật thuyết minh và giọng đọc. Bấm ③ Ghép video để ghép lại.")
        self.run(job, lambda _: None)

    # ---- credit: cổng chặn trước khi gen + làm mới ----
    def credit_message(self, v: dict) -> str:
        """Nội dung cảnh báo khi tài khoản không đủ credit (v: kết quả accounts.check_budget)."""
        cur = v["current"]
        lines = [f"Tài khoản Flow «{cur.name}» của dự án còn {accounts.fmt_credits(v['cur_credits'])} credit, "
                 f"nhưng lượt gen này cần khoảng {accounts.fmt_credits(v['need'])} credit.",
                 f"({accounts.describe_credits(cur)})"]
        if cur.renew:
            lines.append(f"Thời gian làm mới/gia hạn: {cur.renew}.")
        if v.get("auto"):
            others = ", ".join(f"«{a.name}» {accounts.fmt_credits(c)}" for a, c in v["others"]) or "không có tài khoản khác"
            lines.append(f"Đã bật tự chuyển tài khoản nhưng tổng credit các tài khoản ({accounts.fmt_credits(v['total'])}) vẫn không đủ. "
                         f"Các tài khoản khác: {others}.")
        else:
            others = [(a, c) for a, c in v["others"] if c is not None and c >= v["need"]]
            if others:
                lines.append("Tài khoản khác đủ credit: " + ", ".join(f"«{a.name}» ({accounts.fmt_credits(c)})" for a, c in others)
                             + ". Đổi tài khoản của dự án (chip Flow) hoặc bật tự chuyển tài khoản.")
            else:
                lines.append("Hãy nạp thêm credit, đợi đến kỳ làm mới, đổi sang tài khoản khác, hoặc giảm số scene gen.")
        return "\n".join(lines)

    def credit_gate(self, need: int, retry) -> bool:
        """Chặn việc gen nếu tài khoản đích CHẮC CHẮN không đủ credit (theo credit đã lưu, còn mới). True = được chạy tiếp.
        Credit đã lưu có thể cũ (vừa nạp thêm): có nút 'Kiểm tra lại credit' đọc lại từ Flow rồi tự chạy lại."""
        v = accounts.check_budget(need, self.project.account_id)
        if v["ok"] is not False:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Không đủ credit")
        box.setText("Không đủ credit để chạy tiến trình này.")
        box.setInformativeText(self.credit_message(v))
        b_re = box.addButton("Kiểm tra lại credit", QMessageBox.AcceptRole)
        b_auto = box.addButton("Bật tự chuyển tài khoản", QMessageBox.ActionRole) if not v["auto"] and len(v["others"]) else None
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.setDefaultButton(b_re)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_re:
            self.refresh_credits([accounts.active()], retry)
        elif b_auto is not None and clicked is b_auto:
            accounts.set_auto_switch(True)
            self.account_changed.emit(self.project.account_id)
            QTimer.singleShot(0, retry)
        return False

    def refresh_credits(self, accs: list | None = None, then=None, deep: bool = False):
        """Đọc lại credit của các tài khoản từ Flow (mở Chrome của tài khoản nếu chưa mở), lưu lại rồi gọi `then`."""
        if self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong rồi cập nhật credit.")
            return
        accs = accs or [accounts.active()]

        def job(log):
            out = []
            for a in accs:
                try:
                    with flow_auto.FlowAuto(log, acc=a) as f:
                        info = f.read_credits(deep=deep)
                except Exception as e:  # noqa: BLE001
                    log(f"«{a.name}»: không đọc được credit ({str(e)[:100]})")
                    continue
                if info:
                    accounts.save_credits(a.id, info["credits"], info.get("daily"), info.get("renew", ""), info.get("email", ""),
                                          info.get("plan_total"), info.get("daily_grant"))
                    log(f"«{a.name}»: còn {accounts.fmt_credits(info['credits'])} credit" + (f", gia hạn: {info['renew']}" if info.get("renew") else ""))
                    out.append(a.id)
            return out

        def done(res):
            self.account_changed.emit(self.project.account_id if self.project else "")
            if then:
                QTimer.singleShot(0, then)
        self.run(job, done)

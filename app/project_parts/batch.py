"""Tab Dự án: gen nhiều chương, tự đồng bộ Flow khi lỗi, tự ghép khi chương xong."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from .. import credits
from ..merger import merge
from .. import accounts


class BatchMixin:
    """Tab Dự án: gen nhiều chương, tự đồng bộ Flow khi lỗi, tự ghép khi chương xong."""

    # ================= hậu xử lý sau khi gen: tự đồng bộ Flow, tự ghép =================
    def _skip_run(self, then, ch=None) -> None:
        """Không có gì để gen nhưng vẫn phải nối tiếp chuỗi (và vẫn xử lý lỗi/ghép của chương nếu có)."""
        if ch is not None:
            QTimer.singleShot(0, lambda: self._finish_chapter(ch, then))
        elif then:
            QTimer.singleShot(0, then)

    def _stopping(self) -> bool:
        return bool(self._cancel.is_set() or (getattr(self, "_batch", None) or {}).get("stop"))

    def _chapter_ready(self, ch) -> bool:
        return bool(ch.scenes) and all(s.status == "done" and s.clip and Path(s.clip).exists() for s in ch.scenes)

    def _needs_merge(self, ch) -> bool:
        from ..publish import service
        return service.is_stale(self.project, service.Target(ch.id, ch.name, ch))

    def _finish_chapter(self, ch, cont=None, synced: bool = False) -> None:
        """Sau lượt gen của một chương: (1) còn scene lỗi -> tự đồng bộ Flow (miễn phí, chỉ chương này); (2) chương đủ scene xong -> tự ghép
        video; rồi gọi `cont`. Mỗi bước tự bỏ qua khi tắt trong Cài đặt dự án hoặc khi người dùng đã bấm Dừng."""
        p = self.project
        if p is None or ch not in p.chapters:
            return cont() if cont else None
        if not self._stopping():
            errs = [s for s in ch.scenes if s.status == "error"]
            if errs and p.gen_auto_sync and not synced:
                self.log(f"[{ch.name}] {len(errs)} scene lỗi: tự đồng bộ với Flow (không tốn credit)…")
                if self.flow_sync(chapters=[ch], then=lambda: self._finish_chapter(ch, cont, True), quiet=True):
                    return
            if p.gen_auto_merge and self._chapter_ready(ch) and self._needs_merge(ch):
                return self._merge_chapter(ch, cont)
        if cont:
            cont()

    def _merge_chapter(self, ch, then=None) -> None:
        """Ghép video của chương ở nền (không đổi khung xem trước đang mở)."""
        p = self.project
        clips = [Path(s.clip) for s in ch.scenes]
        out = p.merged_path(ch)
        self.log(f"[{ch.name}] Đã gen xong mọi scene: tự ghép video…")

        def fail(msg):
            self.log(f"[{ch.name}] LỖI khi tự ghép video: {msg}")
        self._keep_preview = True
        self.run(lambda log: merge(clips, out), lambda o: self.log(f"[{ch.name}] Đã ghép video: {o}"), on_fail=fail, then=then)

    def _merge_ready_chapters(self) -> None:
        """Ghép lần lượt mọi chương đã đủ scene mà video ghép chưa có/đã cũ (dùng sau Đồng bộ Flow)."""
        p = self.project
        if p is None or not p.gen_auto_merge or self._stopping():
            return
        ch = next((c for c in p.chapters if self._chapter_ready(c) and self._needs_merge(c)), None)
        if ch is not None:
            self._merge_chapter(ch, self._merge_ready_chapters)

    # ================= gen nhiều chương =================
    _batch = None

    def gen_chapters_dialog(self) -> None:
        if not self.need_project():
            return
        self.save_edits()
        from ..chapter_gen_dialog import ChapterGenDialog
        dlg = ChapterGenDialog(self, self.project)
        if dlg.exec() != ChapterGenDialog.Accepted or not dlg.chosen():
            return
        p = self.project
        p.gen_auto_merge, p.gen_auto_sync = dlg.auto_merge.isChecked(), dlg.auto_sync.isChecked()
        p.save()
        self.start_batch([c.id for c in dlg.chosen()])

    def start_batch(self, ids: list[str]) -> None:
        p = self.project
        chosen = [c for c in p.chapters if c.id in ids]
        todo = [s for c in chosen for s in c.scenes if s.status != "done"]
        if not todo:
            QMessageBox.information(self, "Không có gì để gen", "Các chương đã chọn không còn scene nào chưa xong.")
            return
        est = credits.estimate(p.flow_model, p.flow_resolution, todo, p.flow_auto_duration, p.narration_lang)
        if not self.credit_gate(est, lambda: self.start_batch(ids)):
            return
        acc = accounts.get(p.account_id)
        cfg = p.flow_model + (f" {p.flow_resolution}" if p.flow_model == credits.OMNI else "")
        auto = accounts.auto_switch() and len(accounts.all_accounts()) > 1
        if QMessageBox.question(
                self, "Xác nhận gen nhiều chương",
                f"Sẽ gen {len(todo)} scene của {len(chosen)} chương, lần lượt từng chương, bằng {cfg} trên Google Flow.\n"
                f"Ước tính khoảng {est} credit (giá thật hiện trong nhật ký).\n"
                f"Tài khoản Flow: «{acc.name}» ({accounts.describe_credits(acc)})" + ("; tự chuyển tài khoản khi hết credit." if auto else ".") + "\n\n"
                + ("• Chương đủ scene sẽ tự ghép video.\n" if p.gen_auto_merge else "") + ("• Scene lỗi sẽ tự đồng bộ với Flow (không tốn credit).\n" if p.gen_auto_sync else "")
                + "\nBấm Dừng để ngừng sau scene đang chạy. Chrome Flow phải đã đăng nhập. Tiếp tục?") != QMessageBox.Yes:
            return
        self._batch = dict(ids=[c.id for c in chosen], i=0, stop=False, why="", start_ok=sum(1 for c in chosen for s in c.scenes if s.status == "done"))
        self._batch_next()

    def _batch_next(self) -> None:
        b = self._batch
        if not b:
            return
        p = self.project
        if b["i"] > 0:
            self.generation_done.emit()           # chương vừa rồi đã xong (đã đồng bộ/ghép): tab Đăng video có thể tự đăng ngay chương đó
        if b["stop"] or b["i"] >= len(b["ids"]) or p is None:
            return self._batch_end()
        cid = b["ids"][b["i"]]
        b["i"] += 1
        idx = next((i for i, c in enumerate(p.chapters) if c.id == cid), -1)
        if idx < 0:
            return self._batch_next()
        self.fill_chapter_combo(idx)
        self.show_chapter(idx)
        self.log(f"[Lô] Chương {b['i']}/{len(b['ids'])}: {self.chapter.name}")
        self.flow_auto_run("pending", silent=True, then=self._batch_next)

    def _batch_end(self) -> None:
        b, self._batch = self._batch, None
        p = self.project
        if not b or p is None:
            return
        chosen = [c for c in p.chapters if c.id in b["ids"]]
        done = sum(1 for c in chosen for s in c.scenes if s.status == "done")
        total = sum(len(c.scenes) for c in chosen)
        full = sum(1 for c in chosen if self._chapter_ready(c))
        errs = sum(1 for c in chosen for s in c.scenes if s.status == "error")
        head = "Đã dừng gen nhiều chương." if b["stop"] else "Gen nhiều chương xong."
        self.log(f"{head} {done}/{total} scene xong, {full}/{len(chosen)} chương đủ scene"
                 + (f", {errs} scene lỗi (gen lại hoặc Đồng bộ Flow)" if errs else "") + "." + (f" {b['why']}" if b["why"] else ""))

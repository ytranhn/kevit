"""Tab Dự án: chạy nền, nhật ký, trạng thái, tài khoản Flow, hàng đợi worker."""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QMessageBox

from .. import flow_auto, llm
from ..models import Project
from .. import accounts, theme
from ..widgets import repolish
from ..workers import Worker
from .scene_widgets import STATUS


class RuntimeMixin:
    """Tab Dự án: chạy nền, nhật ký, trạng thái, tài khoản Flow, hàng đợi worker."""

    # ================= chạy nền =================
    def set_busy(self, busy: bool):
        self._busy = busy
        for w in self._busy_widgets:
            w.setEnabled(not busy)
        if busy:
            self._tick_timer.start()
        else:
            self._tick_timer.stop()
        self.update_steps()
        self.refresh_nav()
        self.busy_changed.emit(busy, busy and self._cancelable)

    # ---- thông báo, tiến độ, dừng ----
    def log(self, msg: str):
        """Ghi nhật ký + đẩy dòng mới nhất lên thanh trạng thái (đỏ khi lỗi)."""
        self._log_sink(msg)
        low = msg.lower()
        if "lỗi" in low:
            level = "error"
        elif "cảnh báo" in low or "lưu ý" in low:
            level = "warn"
        elif low.rstrip().endswith(("xong.", "xong")) or msg.startswith("Đã "):
            level = "ok"
        else:
            level = "info"
        self.activity.emit(msg.splitlines()[0][:300] if msg else "", level)

    def request_cancel(self):
        if getattr(self, "_batch", None):
            self._batch["stop"] = True            # dừng cả lô: không bắt đầu chương kế tiếp
        if self._busy and self._cancelable:
            self._cancel.set()
            self.log("Sẽ dừng sau scene đang chạy (scene đã gửi lên Flow vẫn hoàn tất vì credit đã được trừ).")

    def status_text(self, s) -> tuple[str, str]:
        label, token = STATUS.get(s.status, (s.status, "faint"))
        color = theme.T[token]
        if s.status == "generating":
            el = int(time.time() - self._gen_t0.setdefault(id(s), time.time()))
            label = f"Đang gen… {el // 60}:{el % 60:02d}"
        else:
            self._gen_t0.pop(id(s), None)
        return label, color

    def tick(self):
        """Chạy mỗi 0.7s khi có tác vụ nền: cập nhật bảng/tiến độ theo trạng thái thật, không đợi cả lượt xong."""
        if not self.project:
            return
        self.update_rows_status()
        self.progress_changed.emit(*self._prog)

    def update_rows_status(self):
        sc = self.scenes
        if self.table.rowCount() != len(sc):
            return
        for r, s in enumerate(sc):
            label, color = self.status_text(s)
            it = self.table.item(r, 2)
            if it is not None and it.text() != label:
                it.setText(label)
                it.setForeground(QBrush(QColor(color)))
        self.refresh_counts()
        self.update_badge()
        self.update_steps()

    def refresh_counts(self):
        scenes = self.scenes
        n, done = len(scenes), sum(1 for s in scenes if s.status == "done")
        allp = self.project.all_scenes()
        total, total_done = len(allp), sum(1 for _, s in allp if s.status == "done")
        self.progress.setText(f"Chương: {done}/{n} scene  ·  Dự án: {total_done}/{total}")
        self._progress_text = f"{done}/{n} xong" if n else ""
        self.scene_bar.setRange(0, max(n, 1))
        self.scene_bar.setValue(done)
        self.prog_text.setText(self._progress_text)
        self.prog_dot.setStyleSheet(f"background: {theme.T['ok'] if n and done == n else theme.T['info']}; border-radius: 4px;")
        self.update_sel_label()
        self.refresh_chapter_labels()

    def update_badge(self, s=None):
        if s is None:
            s = self.scenes[self._row] if 0 <= self._row < len(self.scenes) else None
        if s is None:
            return
        label, color = self.status_text(s)
        self.d_badge.setText(f'<b style="color:{color}">Scene {s.index}: {label}</b>')
        self.d_err.set_full(s.error)
        self.b_view_err.setVisible(bool(s.error))

    def update_steps(self):
        """Ba bước ①②③: làm nổi bật bước kế tiếp, hiện tiến độ, và chỉ cho bấm ③ khi đã đủ clip."""
        sc = self.scenes
        n, done = len(sc), sum(1 for s in sc if s.status == "done")
        nxt = 1 if n == 0 else (2 if done < n else 3)
        self.s1.setText("Tạo scene" if n == 0 else "Tạo lại")
        self.s2.setText("Gen video" + (f" {done}/{n}" if n else ""))
        self.s3.setText("Ghép video" if not n or done == n else f"Ghép · còn {n - done}")
        for i, b in ((1, self.s1), (2, self.s2), (3, self.s3)):
            want = i == nxt
            if bool(b.property("primary")) != want:
                b.setProperty("primary", want)
                repolish(b)               # (repolish tự vẽ lại icon theo màu chữ mới)
        busy = self._busy
        self.s1.setEnabled(not busy)
        self.s2.setEnabled(n > 0 and not busy)
        self.s3.setEnabled(n > 0 and done == n and not busy)

    def update_empty_state(self):
        ch = self.chapter
        if self.scenes or ch is None:
            self.table_stack.setCurrentIndex(1)
            return
        words = len(ch.story.split())
        if words:
            self.empty_title.setText("Chương đã có truyện, chưa có scene")
            hint = "" if self.chars_tab.chars else "\nChưa có nhân vật: nên tạo ở tab Nhân vật (AI đọc truyện, không cần scene) trước để scene gắn đúng nhân vật."
            self.empty_text.setText(f"Truyện hiện có khoảng {words} từ. Bấm nút dưới để tách thành các scene.{hint}")
            self.empty_btn.setText("Tạo scene")
        else:
            self.empty_title.setText("Chương này đang trống")
            self.empty_text.setText("Dán nội dung chương vào tab “Truyện (chương)”, sau đó tạo scene.")
            self.empty_btn.setText("Mở tab Truyện")
        self.table_stack.setCurrentIndex(0)

    def empty_action(self):
        if self.chapter and self.chapter.story.strip() and not self._busy:
            self.plan()
        else:
            self.left_tabs.setCurrentWidget(self.story)
            self.story.setFocus()

    def set_account(self, acc_id: str):
        """Đổi tài khoản Flow của dự án đang mở. Project Flow là của riêng từng tài khoản nên địa chỉ project được cất/nạp theo tài khoản
        (đổi sang tài khoản chưa từng dùng thì lần gen sau tạo project mới theo tên chương)."""
        p = self.project
        if not p or acc_id == p.account_id:
            return
        if self._busy:
            QMessageBox.information(self, "Đang chạy tác vụ", "Hãy đợi tác vụ nền xong (hoặc bấm Dừng) rồi đổi tài khoản Flow.")
            self.account_changed.emit(p.account_id)         # trả ô chọn về giá trị cũ
            return
        self.save_edits()
        old = accounts.get(p.account_id).name
        p.use_flow_account(acc_id)
        p.save()
        acc = accounts.activate(p.account_id)
        self.log(f"Dự án «{p.name}» chuyển từ tài khoản Flow «{old}» sang «{acc.name}»."
                 + ("" if flow_auto.cdp_state(0) else " Chrome của tài khoản này chưa mở: bấm chip Flow để mở và đăng nhập."))
        self.account_changed.emit(acc.id)

    def reload_flow_state(self):
        """Nạp lại từ đĩa các thông tin Flow do tiến trình nền khác ghi (địa chỉ project Flow của dự án/chương, tài khoản): nếu không, lần lưu
        kế tiếp của tab này sẽ ghi đè chúng bằng bản cũ trong bộ nhớ (vd. sau khi tạo ảnh nhân vật trong project Flow của chương)."""
        if not self.project or self._busy:
            return
        try:
            fresh = Project.load(self.project.name)
        except Exception:  # noqa: BLE001
            return
        self.project.flow_account, self.project.flow_stash = fresh.flow_account, fresh.flow_stash
        self.project.flow_project_url = fresh.flow_project_url
        by_id = {c.id: c for c in fresh.chapters}
        for c in self.project.chapters:
            if c.id in by_id:
                c.flow_project_url = by_id[c.id].flow_project_url

    def on_accounts_changed(self):
        """Danh sách tài khoản đổi (thêm/đổi tên/gỡ): nạp lại ô chọn và kích hoạt lại tài khoản của dự án (có thể đã bị chuyển về chính)."""
        if self.project:
            self.reload_flow_state()
            accounts.activate(self.project.account_id)
        self.account_changed.emit(self.project.account_id if self.project else "")

    def launch_flow_chrome(self):
        if not self._busy:
            acc = accounts.active()
            self.run(lambda log: flow_auto.launch_chrome(acc), lambda _: self.log(f"Chrome Flow ({acc.name}) đã mở."))

    def _restore_queue(self):
        """Scene còn đang 'Hàng đợi' khi tác vụ kết thúc/bị dừng thì trả về trạng thái trước đó."""
        for _, s in self.project.all_scenes() if self.project else []:
            if s.status == "queued":
                s.status, s.error = self._queue_prev.get(id(s), ("pending", ""))
        self._queue_prev = {}
        if self.project:
            self.project.save()

    def _on_worker_finished(self):
        self.set_busy(False)
        self._restore_queue()
        self.progress_changed.emit(0, 0)
        self.refresh_view()
        cb, self._then = getattr(self, "_then", None), None
        if cb:
            QTimer.singleShot(0, cb)

    def need_project(self, need_key: bool = False) -> bool:
        if not self.project:
            QMessageBox.warning(self, "Thiếu dự án", "Hãy tạo dự án trước.")
        elif need_key and not llm.is_configured()[0]:
            QMessageBox.warning(self, "Thiếu API key", llm.is_configured()[1])
        else:
            return True
        return False

    def run(self, fn, on_done, cancelable: bool = False, on_fail=None, then=None):
        """Chạy `fn` ở luồng nền. `then` (tuỳ chọn) được gọi trên luồng giao diện SAU KHI tiến trình kết thúc và giao diện đã mở khoá
        (dù thành công, lỗi hay bị dừng), dùng để nối tiếp các bước (đồng bộ → ghép → chương kế tiếp)."""
        self._then = then
        self._cancelable = cancelable
        self._cancel.clear()
        self._prog = (0, 0)
        self.set_busy(True)
        self.worker = Worker(fn)
        self.worker.log.connect(self.log)
        self.worker.failed.connect(lambda e: self.log(f"LỖI: {e}"))
        if on_fail:
            self.worker.failed.connect(on_fail)
        self.worker.done.connect(on_done)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def refresh_view(self):
        if not self.project:
            return
        self._skip_preview = self._keep_preview
        self._keep_preview = False
        self.fill_table(self._row)
        self._skip_preview = False

    MODE_LABEL = {"current": "scene đang xem", "selected": "các scene đang chọn",
                  "pending": "các scene chưa xong", "all": "TẤT CẢ scene (kể cả đã xong)"}

    def pick_todo(self, mode: str = "pending"):
        """current: scene đang xem | selected: các dòng đang chọn trong bảng | pending: chưa xong | all: tất cả (gen lại cả scene xong)."""
        sc = self.scenes
        if mode == "current":
            return [sc[self._row]] if 0 <= self._row < len(sc) else []
        if mode == "selected":
            keep = self.selected_indices()
            return [s for s in sc if s.index in keep]
        if mode == "all":
            return list(sc)
        return [s for s in sc if s.status != "done"]

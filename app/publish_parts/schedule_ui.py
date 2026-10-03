"""Tab Đăng video: khu 'Lịch đăng' (hẹn giờ, chỉnh/bỏ lượt)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem

from .. import publish, theme
from ..publish import schedule, service
from ..schedule_dialog import ScheduleDialog, ask_datetime


class ScheduleMixin:
    """Tab Đăng video: khu 'Lịch đăng' (hẹn giờ, chỉnh/bỏ lượt)."""

    # ================= hẹn giờ =================
    scheduler = None

    def set_scheduler(self, scheduler) -> None:
        self.scheduler = scheduler
        scheduler.changed.connect(self.on_schedule_changed)

    def on_schedule_changed(self, name: str) -> None:
        p = self.get_project()
        if p and p.name == name:
            self.refresh_schedule()
            self.fill_history()
            self.rebuild_list_statuses()

    def rebuild_list_statuses(self) -> None:
        p = self.get_project()
        if p:
            for t in self._targets:
                if t.key in self._rows:
                    self._rows[t.key].update_info(self.row_info(p, t))

    def schedule_selected(self) -> None:
        p, tgts, accs = self.get_project(), self.checked_targets(), self.chosen_accounts()
        if not (p and tgts and accs):
            return
        self.commit_editor(quiet=True)
        priv = self.privacy.currentData()
        summary = (f"{len(tgts)} video → " + ", ".join(publish.account_name(a) for a in accs)
                   + f"  ·  Chế độ: {service.PRIVACY_LABELS[priv]}")
        dlg = ScheduleDialog(self, [(t.key, t.label) for t in tgts], summary)
        if dlg.exec() != ScheduleDialog.Accepted:
            return
        jobs = [schedule.new_job(key, at, accs, priv) for key, at in dlg.result()]
        schedule.add_jobs(p, jobs)
        self.refresh_schedule()
        first = min(j["at"] for j in jobs)
        self.set_status(f"Đã hẹn giờ {len(jobs)} video, lượt đầu lúc {schedule.pretty(first)}. Hãy để Kevit mở đúng giờ.", "ok")

    def refresh_schedule(self) -> None:
        p = self.get_project()
        jobs = list(p.publish_queue) if p else []
        names = {t.key: t.label for t in service.targets(p)} if p else {}
        if p:
            names.setdefault("project", f"{p.name} (cả dự án)")
            for c in p.chapters:
                names.setdefault(c.id, c.name)
        keep = self.selected_job_id()
        self.sched_table.setRowCount(len(jobs))
        colors = {"done": theme.T["ok"], "error": theme.T["err"], "missed": theme.T["warn"], "running": theme.T["info"], "pending": theme.T["text"]}
        for r, j in enumerate(jobs):
            accs = ", ".join(publish.account_name(a) for a in j["accounts"])
            status = schedule.STATUS_LABELS.get(j["status"], j["status"])
            cells = [schedule.pretty(j["at"]), names.get(j["key"], j["key"]), f"{len(j['accounts'])} tài khoản", status]
            tips = [schedule.pretty(j["at"]), names.get(j["key"], j["key"]), accs, j.get("message") or status]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(tips[c])
                if c == 0:
                    it.setData(Qt.UserRole, j["id"])
                if c == 3:
                    it.setForeground(QColor(colors.get(j["status"], theme.T["text"])))
                    if j.get("message"):
                        it.setText(f"{status}: {j['message'][:60]}")
                self.sched_table.setItem(r, c, it)
        if keep:
            for r in range(self.sched_table.rowCount()):
                if self.sched_table.item(r, 0).data(Qt.UserRole) == keep:
                    self.sched_table.selectRow(r)
        self.sched_empty.setVisible(not jobs)
        self.sched_table.setVisible(bool(jobs))
        nxt = schedule.next_pending(p) if p else None
        self.sched_next.setText(f"Lượt kế tiếp: {schedule.pretty(nxt['at'])} · {names.get(nxt['key'], nxt['key'])}" if nxt else "")
        self.sched_next.setVisible(bool(nxt))
        self.update_job_buttons()

    def selected_job_id(self) -> str | None:
        r = self.sched_table.currentRow()
        it = self.sched_table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def selected_job(self) -> dict | None:
        p, jid = self.get_project(), self.selected_job_id()
        return schedule.get_job(p, jid) if (p and jid) else None

    def update_job_buttons(self) -> None:
        j = self.selected_job()
        idle = j is not None and j["status"] != "running"
        busy = bool(self.scheduler and self.scheduler.busy) or self._worker is not None
        self.b_job_now.setEnabled(idle and j["status"] != "done" and not busy)
        self.b_job_time.setEnabled(idle and j["status"] != "done")
        self.b_job_del.setEnabled(idle)
        p = self.get_project()
        self.b_job_clear.setEnabled(bool(p and any(x["status"] == "done" for x in p.publish_queue)))

    def job_now(self) -> None:
        p, j = self.get_project(), self.selected_job()
        if not (p and j and self.scheduler):
            return
        if not self.scheduler.run_now(p, j["id"]):
            self.set_status("Đang đăng một lượt khác, hãy thử lại sau ít phút.", "warn")
        self.refresh_schedule()

    def job_retime(self) -> None:
        p, j = self.get_project(), self.selected_job()
        if not (p and j):
            return
        at = ask_datetime(self, "Đổi giờ đăng", schedule.parse(j["at"]))
        if at:
            schedule.reschedule(p, j["id"], at)
            self.refresh_schedule()
            self.set_status(f"Đã đổi giờ đăng sang {schedule.pretty(schedule.fmt(at))}.", "ok")

    def job_remove(self) -> None:
        p, j = self.get_project(), self.selected_job()
        if p and j and schedule.remove_job(p, j["id"]):
            self.refresh_schedule()
            self.set_status("Đã huỷ lượt hẹn giờ.", "info")

    def job_clear(self) -> None:
        p = self.get_project()
        if p:
            n = schedule.clear_finished(p)
            self.refresh_schedule()
            self.set_status(f"Đã dọn {n} mục đã xong.", "info")

"""Bộ chạy lịch đăng: mỗi ~20 giây xem các dự án còn lượt chờ, lượt nào đến giờ thì đăng (mỗi lúc một lượt, ở luồng nền).
Chạy cho MỌI dự án có lịch, không chỉ dự án đang mở; dự án đang mở dùng chung đối tượng với giao diện để không ghi đè lẫn nhau."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from .models import Project
from .publish import schedule
from .workers import Worker

INTERVAL_MS = 20_000
FIRST_TICK_MS = 4_000


class Scheduler(QObject):
    changed = Signal(str)          # tên dự án có hàng đợi vừa đổi (giao diện làm mới nếu là dự án đang mở)
    log = Signal(str)
    activity = Signal(str, str)    # (nội dung, mức) -> thanh trạng thái

    def __init__(self, get_open_project, parent=None, now=datetime.now, interval_ms: int = INTERVAL_MS):
        super().__init__(parent)
        self.get_open_project, self.now = get_open_project, now
        self._worker: Worker | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        self._timer.start()
        QTimer.singleShot(FIRST_TICK_MS, self.tick)

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def _load(self, name: str) -> Project | None:
        cur = self.get_open_project()
        if cur is not None and cur.name == name:
            return cur
        try:
            return Project.load(name)
        except Exception:  # noqa: BLE001 - dự án đã bị xoá/đổi tên: bỏ khỏi chỉ mục
            schedule.drop_from_index(name)
            return None

    def tick(self) -> None:
        if self.busy:
            return
        for name in schedule.projects_with_pending():
            p = self._load(name)
            if p is None:
                continue
            if schedule.recover_interrupted(p):
                self.changed.emit(name)
            due, changed = schedule.take_due(p, self.now())
            if changed:
                self.changed.emit(name)
                self.activity.emit(f"Có lượt đăng đã lỡ giờ ở dự án «{name}». Mở tab Đăng video để xử lý.", "warn")
            if due:
                self._start(p, due[0])
                return

    def run_now(self, p: Project, job_id: str) -> bool:
        """Đăng ngay một lượt trong lịch (kể cả lượt lỡ giờ/lỗi). False nếu đang bận lượt khác."""
        j = schedule.get_job(p, job_id)
        if self.busy or j is None or j["status"] == "running":
            return False
        j.update(at=schedule.fmt(self.now()), status="pending", message="")
        self._start(p, j)
        return True

    def _start(self, p: Project, job: dict) -> None:
        t = schedule.target_for(p, job["key"])
        label = t.label if t else job["key"]
        self.log.emit(f"[Lịch] Đến giờ đăng «{label}» (dự án {p.name})…")
        self.activity.emit(f"Đang đăng theo lịch: {label}…", "info")
        job.update(status="running")
        self._worker = w = Worker(lambda log: schedule.run_job(p, job, log))
        w.log.connect(self.log)
        w.done.connect(lambda ok: self._finished(p, job, label, bool(ok)))
        w.failed.connect(lambda e: (job.update(status="error", message=str(e)[:300]), self._finished(p, job, label, False)))
        w.finished.connect(lambda: setattr(self, "_worker", None))
        self.changed.emit(p.name)
        w.start()

    def _finished(self, p: Project, job: dict, label: str, ok: bool) -> None:
        if ok:
            self.log.emit(f"[Lịch] Đã đăng «{label}».")
            self.activity.emit(f"Đã đăng theo lịch: {label}.", "ok")
        else:
            self.log.emit(f"[Lịch] LỖI khi đăng «{label}»: {job.get('message', '')}")
            self.activity.emit(f"Đăng theo lịch lỗi: {label}. {job.get('message', '')[:160]}", "error")
        self.changed.emit(p.name)

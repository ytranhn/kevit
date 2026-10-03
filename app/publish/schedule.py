"""Lịch đăng: hàng đợi các lượt đăng hẹn giờ của dự án (lưu trong project.json) và logic chọn/chạy lượt đến hạn.
Giờ là giờ địa phương của máy. Lượt đến hạn quá GRACE_MIN phút (vd. Kevit đã đóng) KHÔNG tự đăng bù mà chuyển sang 'missed' để người dùng quyết định."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta

from .. import models
from ..models import Project
from . import account_name, service
from .base import PublishError

FMT = "%Y-%m-%d %H:%M"
GRACE_MIN = 30
WEEKDAY_LABELS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")
STATUS_LABELS = {"pending": "Chờ đăng", "running": "Đang đăng…", "done": "Đã đăng", "error": "Lỗi", "missed": "Lỡ giờ"}
OPEN = ("pending", "running", "missed", "error")        # còn việc phải làm / cần người dùng xem


def parse(s: str) -> datetime:
    return datetime.strptime(s, FMT)


def fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def pretty(s: str) -> str:
    """Giờ hiển thị cho người dùng: 'Th 6, 02/10/2026 20:00' từ chuỗi lưu '2026-10-02 20:00'."""
    try:
        d = parse(s)
    except ValueError:
        return s
    return f"{('T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN')[d.weekday()]}, {d.strftime('%d/%m/%Y %H:%M')}"


def occurrences(start: datetime, n: int, every_days: int = 1, weekdays: set[int] | None = None) -> list[datetime]:
    """n thời điểm cùng giờ-phút với `start`: bắt đầu từ ngày của `start`, cứ `every_days` ngày một lần, chỉ lấy các ngày trong `weekdays`
    (0=Thứ hai … 6=Chủ nhật; None = mọi ngày). Mỗi thời điểm dùng cho MỘT video."""
    every_days = max(1, int(every_days))
    out, k = [], 0
    while len(out) < n and k < 4000:
        day = start.date() + timedelta(days=k)
        if k % every_days == 0 and (weekdays is None or day.weekday() in weekdays):
            out.append(datetime.combine(day, start.time()))
        k += 1
    return out


def new_job(key: str, at: datetime, accounts: list[str], privacy: str) -> dict:
    return dict(id=uuid.uuid4().hex[:8], key=key, at=fmt(at), accounts=list(accounts), privacy=privacy, status="pending", message="", ran_at="")


# ---- chỉ mục các dự án còn lượt đang chờ (để bộ lập lịch không phải đọc mọi dự án) ----
def _index_file():
    return models.DATA_DIR / "publish_schedule.json"


def _read_index() -> list[str]:
    try:
        return list(json.loads(_index_file().read_text(encoding="utf-8")).get("projects", []))
    except Exception:  # noqa: BLE001
        return []


def sync_index(p: Project) -> None:
    names = set(_read_index())
    (names.add if any(j["status"] in ("pending", "running") for j in p.publish_queue) else names.discard)(p.name)
    try:
        _index_file().parent.mkdir(parents=True, exist_ok=True)
        _index_file().write_text(json.dumps({"projects": sorted(names)}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def projects_with_pending() -> list[str]:
    return _read_index()


def drop_from_index(name: str) -> None:
    names = [n for n in _read_index() if n != name]
    _index_file().write_text(json.dumps({"projects": names}, ensure_ascii=False), encoding="utf-8")


# ---- thao tác trên hàng đợi ----
def add_jobs(p: Project, jobs: list[dict]) -> None:
    p.publish_queue.extend(jobs)
    p.publish_queue.sort(key=lambda j: j["at"])
    p.save()
    sync_index(p)


def get_job(p: Project, job_id: str) -> dict | None:
    return next((j for j in p.publish_queue if j["id"] == job_id), None)


def remove_job(p: Project, job_id: str) -> bool:
    j = get_job(p, job_id)
    if not j or j["status"] == "running":
        return False
    p.publish_queue.remove(j)
    p.save()
    sync_index(p)
    return True


def reschedule(p: Project, job_id: str, at: datetime) -> bool:
    j = get_job(p, job_id)
    if not j or j["status"] == "running":
        return False
    j.update(at=fmt(at), status="pending", message="")
    p.publish_queue.sort(key=lambda x: x["at"])
    p.save()
    sync_index(p)
    return True


def clear_finished(p: Project) -> int:
    n = len(p.publish_queue)
    p.publish_queue[:] = [j for j in p.publish_queue if j["status"] != "done"]
    p.save()
    sync_index(p)
    return n - len(p.publish_queue)


def next_pending(p: Project) -> dict | None:
    return next((j for j in p.publish_queue if j["status"] == "pending"), None)


def recover_interrupted(p: Project) -> int:
    """Lượt còn ghi 'đang đăng' mà không có tiến trình nào chạy (Kevit bị đóng giữa chừng): đánh dấu lỗi để người dùng kiểm tra lịch sử."""
    n = 0
    for j in p.publish_queue:
        if j["status"] == "running":
            j.update(status="error", message="Bị gián đoạn khi đang đăng (Kevit đóng giữa chừng). Xem lịch sử đăng rồi đăng lại nếu cần.")
            n += 1
    if n:
        p.save()
        sync_index(p)
    return n


def take_due(p: Project, now: datetime | None = None) -> tuple[list[dict], bool]:
    """(các lượt đến hạn còn trong thời gian chấp nhận, có thay đổi không). Lượt quá hạn lâu → 'missed'."""
    now = now or datetime.now()
    due, changed = [], False
    for j in p.publish_queue:
        if j["status"] != "pending" or parse(j["at"]) > now:
            continue
        if now - parse(j["at"]) > timedelta(minutes=GRACE_MIN):
            j.update(status="missed", message=f"Lỡ giờ hẹn {pretty(j['at'])} (Kevit không chạy lúc đó).")
            changed = True
        else:
            due.append(j)
    if changed:
        p.save()
        sync_index(p)
    return due, changed


def target_for(p: Project, key: str) -> service.Target | None:
    if key == "project":
        return service.Target("project", f"{p.name} (cả dự án)", None)
    ch = next((c for c in p.chapters if c.id == key), None)
    return service.Target(ch.id, ch.name, ch) if ch else None


def run_job(p: Project, job: dict, log=print, cancel=None) -> bool:
    """Chạy một lượt: ghép + viết mô tả (nếu thiếu) + đăng lên các tài khoản của lượt. Ghi kết quả vào lượt và lưu dự án."""
    job.update(status="running", message="")
    p.save()
    sync_index(p)
    try:
        t = target_for(p, job["key"])
        if t is None:
            raise PublishError("Không còn video này trong dự án (chương đã bị xoá).")
        results = service.run(p, [t], job["accounts"], job["privacy"], log, cancel)
        bad = [r for r in results if not r.ok]
        if bad:
            raise PublishError("; ".join(f"{account_name(r.account)}: {r.message}" for r in bad)[:500])
        job.update(status="done", message="" if results else "Đã đăng từ trước (bỏ qua).")
        ok = True
    except Exception as e:  # noqa: BLE001 - mọi lỗi được ghi vào lượt để hiện cho người dùng
        job.update(status="error", message=str(e)[:500])
        ok = False
    job["ran_at"] = time.strftime(FMT)
    p.save()
    sync_index(p)
    return ok

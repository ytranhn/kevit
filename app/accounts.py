"""Nhiều tài khoản Google Flow. Mỗi tài khoản = một hồ sơ Chrome riêng (đăng nhập Google riêng, credit riêng) + một cổng debug riêng,
nên các tài khoản mở song song được và đổi qua lại không phải đăng nhập lại. Tài khoản "default" là hồ sơ `flow_profile` cũ (cổng 9222),
nhờ vậy người dùng cũ không phải đăng nhập lại. Mỗi dự án gắn một tài khoản (Project.flow_account)."""
from __future__ import annotations

import json
import re
import socket
import time
import unicodedata
from dataclasses import asdict, dataclass

from . import models

DEFAULT_ID = "default"
BASE_PORT = 9222


@dataclass
class Account:
    id: str
    name: str
    port: int
    profile: str                 # đường dẫn tương đối so với thư mục dữ liệu (đổi thư mục dữ liệu vẫn đúng)
    credits: int | None = None   # credit Flow còn lại lần đọc gần nhất (None = chưa biết)
    daily: int | None = None     # credit tặng hằng ngày còn lại (nếu đọc được)
    plan_total: int | None = None    # credit gói cấp mỗi kỳ (vd. 1000 credit/tháng), nếu trang Google One nêu
    daily_grant: int | None = None   # credit tặng thêm mỗi ngày (vd. 50), nếu trang Google One nêu
    renew: str = ""              # ghi chú thời gian gia hạn/làm mới (vd. "30 Oct 2026" hoặc "làm mới hằng tháng")
    email: str = ""              # email Google của tài khoản (đọc từ Flow)
    checked_at: float = 0.0      # thời điểm đọc credit (epoch giây)

    @property
    def cdp_url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def profile_dir(self):
        return models.DATA_DIR / self.profile


_active_id = DEFAULT_ID


def _file():
    return models.DATA_DIR / "accounts.json"


def _default() -> Account:
    return Account(DEFAULT_ID, "Tài khoản chính", BASE_PORT, "flow_profile")


CREDIT_STALE = 6 * 3600          # credit đọc quá lâu thì chỉ là gợi ý, không dùng để CHẶN việc chạy


def _read() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        accs = [Account(**a) for a in d.get("accounts", [])]
    except Exception:  # noqa: BLE001 - chưa có file hoặc hỏng: bắt đầu lại từ tài khoản chính
        d, accs = {}, []
    if not any(a.id == DEFAULT_ID for a in accs):
        accs.insert(0, _default())
    default_new = d.get("default_new", DEFAULT_ID)
    if not any(a.id == default_new for a in accs):
        default_new = DEFAULT_ID
    return {"accounts": accs, "default_new": default_new, "auto_switch": bool(d.get("auto_switch", False))}


def _write(accs: list[Account], default_new: str, auto_switch: bool | None = None) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    if auto_switch is None:
        auto_switch = _read()["auto_switch"]
    f.write_text(json.dumps({"accounts": [asdict(a) for a in accs], "default_new": default_new, "auto_switch": auto_switch},
                            ensure_ascii=False, indent=2), encoding="utf-8")


def all_accounts() -> list[Account]:
    return _read()["accounts"]


def get(acc_id: str | None) -> Account:
    """Tài khoản theo id; id rỗng hoặc đã bị xoá thì trả về tài khoản chính."""
    accs = {a.id: a for a in all_accounts()}
    return accs.get(acc_id or DEFAULT_ID) or accs[DEFAULT_ID]


def default_new_id() -> str:
    """Tài khoản gắn cho dự án MỚI."""
    return _read()["default_new"]


def set_default_new(acc_id: str) -> None:
    d = _read()
    if any(a.id == acc_id for a in d["accounts"]):
        _write(d["accounts"], acc_id)


def _slug(name: str) -> str:
    t = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:24] or "acc"


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def add(name: str) -> Account:
    name = name.strip()
    if not name:
        raise ValueError("Tên tài khoản không được để trống.")
    d = _read()
    accs = d["accounts"]
    if any(a.name.casefold() == name.casefold() for a in accs):
        raise ValueError(f"Đã có tài khoản tên “{name}”.")
    base, acc_id, n = _slug(name), "", 1
    acc_id = base
    while any(a.id == acc_id for a in accs) or acc_id == DEFAULT_ID:
        n += 1
        acc_id = f"{base}-{n}"
    used = {a.port for a in accs}
    port = BASE_PORT + 1
    while port in used or not _port_free(port):
        port += 1
    acc = Account(acc_id, name, port, f"flow_profiles/{acc_id}")
    _write(accs + [acc], d["default_new"])
    return acc


def rename(acc_id: str, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Tên tài khoản không được để trống.")
    d = _read()
    if any(a.id != acc_id and a.name.casefold() == name.casefold() for a in d["accounts"]):
        raise ValueError(f"Đã có tài khoản tên “{name}”.")
    for a in d["accounts"]:
        if a.id == acc_id:
            a.name = name
    _write(d["accounts"], d["default_new"])


def remove(acc_id: str) -> list[str]:
    """Gỡ tài khoản khỏi danh sách (thư mục hồ sơ Chrome được giữ nguyên trên ổ đĩa). Không gỡ được tài khoản chính.
    Các dự án đang gắn tài khoản này được chuyển về tài khoản chính; trả về tên các dự án đó."""
    if acc_id == DEFAULT_ID:
        raise ValueError("Không gỡ được tài khoản chính.")
    moved = usage(acc_id)
    for name in moved:
        p = models.Project.load(name)
        p.use_flow_account(DEFAULT_ID)
        p.save()
    d = _read()
    accs = [a for a in d["accounts"] if a.id != acc_id]
    _write(accs, d["default_new"] if d["default_new"] != acc_id else DEFAULT_ID)
    return moved


def usage(acc_id: str) -> list[str]:
    """Tên các dự án đang gắn với tài khoản này."""
    out = []
    for name in models.Project.list_names():
        try:
            d = json.loads((models.PROJ_DIR / name / "project.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if (d.get("flow_account") or DEFAULT_ID) == acc_id:
            out.append(name)
    return out


def active() -> Account:
    return get(_active_id)


def activate(acc_id: str | None) -> Account:
    """Đặt tài khoản đang dùng cho mọi thao tác Flow tiếp theo (cổng debug + hồ sơ Chrome)."""
    global _active_id
    acc = get(acc_id)
    _active_id = acc.id
    from . import flow_auto
    flow_auto.use_account(acc)
    return acc


# ---------------------------------------------------------------- credit + tự chuyển tài khoản
def auto_switch() -> bool:
    """Tự chuyển sang tài khoản khác còn credit khi tài khoản hiện tại không đủ (cài đặt chung, mặc định tắt)."""
    return _read()["auto_switch"]


def set_auto_switch(on: bool) -> None:
    d = _read()
    _write(d["accounts"], d["default_new"], bool(on))


def save_credits(acc_id: str, credits: int | None, daily: int | None = None, renew: str = "", email: str = "",
                 plan_total: int | None = None, daily_grant: int | None = None) -> None:
    """Ghi kết quả đọc credit của một tài khoản. Không ghi đè thông tin cũ bằng chỗ trống (vd. lần đọc nhanh không có ngày gia hạn)."""
    d = _read()
    for a in d["accounts"]:
        if a.id == acc_id:
            if credits is not None:
                a.credits, a.checked_at = int(credits), time.time()
            if daily is not None:
                a.daily = int(daily)
            if plan_total is not None:
                a.plan_total = int(plan_total)
            if daily_grant is not None:
                a.daily_grant = int(daily_grant)
            if renew:
                a.renew = renew
            if email:
                a.email = email
    _write(d["accounts"], d["default_new"])


def known_credits(a: Account, max_age: float = CREDIT_STALE) -> int | None:
    """Credit đã biết và còn mới; quá cũ hoặc chưa đọc thì None (không đủ tin cậy để chặn việc chạy)."""
    if a.credits is None or time.time() - a.checked_at > max_age:
        return None
    return a.credits


def fmt_credits(n: int | None) -> str:
    return "chưa rõ" if n is None else f"{n:,}".replace(",", ".")


def fmt_age(ts: float) -> str:
    if not ts:
        return "chưa cập nhật"
    d = max(0, int(time.time() - ts))
    return "vừa xong" if d < 60 else f"{d // 60} phút trước" if d < 3600 else f"{d // 3600} giờ trước" if d < 86400 else f"{d // 86400} ngày trước"


def describe_credits(a: Account) -> str:
    """Một dòng mô tả credit cho giao diện, vd. '1.002 credit · còn 2 credit ngày · gia hạn: 30 Oct 2026 · cập nhật 5 phút trước'."""
    if a.credits is None:
        return "credit: chưa đọc"
    parts = [f"{fmt_credits(a.credits)} credit"]
    if a.daily is not None:
        parts.append(f"còn {a.daily} credit ngày")
    if a.renew:
        parts.append(f"gia hạn: {a.renew}")
    parts.append(f"cập nhật {fmt_age(a.checked_at)}")
    return " · ".join(parts)


def capacity(a: Account) -> int | None:
    """Mức credit tối đa tham chiếu để vẽ thanh tiến độ (credit gói + credit tặng hằng ngày); chưa biết thì None."""
    if a.plan_total is None and a.daily_grant is None:
        return None
    return (a.plan_total or 0) + (a.daily_grant or 0)


def used_percent(a: Account) -> int | None:
    """% credit đã dùng so với mức tối đa (0-100); không đủ dữ liệu thì None."""
    cap = capacity(a)
    if a.credits is None or not cap:
        return None
    return max(0, min(100, round(100 * (1 - a.credits / cap))))


def split_by_credits(costs: list[int], budget: int | None) -> tuple[list[int], list[int]]:
    """Chia danh sách scene (theo chi phí từng scene, giữ thứ tự) thành (nhóm làm vừa ngân sách, phần còn lại).
    budget None = chưa biết credit: coi như đủ cho tất cả."""
    if budget is None:
        return list(range(len(costs))), []
    take, used = [], 0
    for i, c in enumerate(costs):
        if used + c > budget:
            break
        take.append(i)
        used += c
    return take, list(range(len(take), len(costs)))


def check_budget(need: int, current_id: str, auto: bool | None = None) -> dict:
    """Kiểm tra nhanh bằng credit đã lưu (không mở Chrome) trước khi chạy.
    ok=True: đủ; ok=False: chắc chắn thiếu (nên chặn); ok=None: chưa biết (cho chạy, job sẽ đọc lại credit thật).
    Trả về {ok, current, cur_credits, need, others:[(Account, credits)], total}."""
    auto = auto_switch() if auto is None else auto
    accs = all_accounts()
    cur = next((a for a in accs if a.id == current_id), accs[0])
    cur_c = known_credits(cur)
    others = [(a, known_credits(a)) for a in accs if a.id != cur.id]
    out = {"ok": None, "current": cur, "cur_credits": cur_c, "need": need, "others": others, "auto": auto, "total": None}
    if cur_c is None:
        return out
    if cur_c >= need:
        out["ok"] = True
        return out
    if not auto:
        out["ok"] = False
        return out
    if any(c is None for _, c in others):               # còn tài khoản chưa biết credit: có thể đủ, để job kiểm tra thật
        out["ok"] = None
        return out
    total = cur_c + sum(c for _, c in others)
    out["total"] = total
    out["ok"] = total >= need
    return out


def order_candidates(exclude: set[str]) -> list[Account]:
    """Thứ tự thử khi tự chuyển tài khoản: nhiều credit (đã biết) trước, chưa biết credit sau."""
    cands = [a for a in all_accounts() if a.id not in exclude]
    return sorted(cands, key=lambda a: (known_credits(a) is None, -(known_credits(a) or 0)))

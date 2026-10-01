"""Nhiều tài khoản Google Flow. Mỗi tài khoản = một hồ sơ Chrome riêng (đăng nhập Google riêng, credit riêng) + một cổng debug riêng,
nên các tài khoản mở song song được và đổi qua lại không phải đăng nhập lại. Tài khoản "default" là hồ sơ `flow_profile` cũ (cổng 9222),
nhờ vậy người dùng cũ không phải đăng nhập lại. Mỗi dự án gắn một tài khoản (Project.flow_account)."""
from __future__ import annotations

import json
import re
import socket
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
    return {"accounts": accs, "default_new": default_new}


def _write(accs: list[Account], default_new: str) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"accounts": [asdict(a) for a in accs], "default_new": default_new}, ensure_ascii=False, indent=2), encoding="utf-8")


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

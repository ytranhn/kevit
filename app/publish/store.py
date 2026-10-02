"""Lưu khoá ứng dụng (client id/secret, theo nền tảng) và các TÀI KHOẢN đã kết nối (nhiều tài khoản cho mỗi nền tảng).
Cùng nơi với API key khác (QSettings của app)."""
from __future__ import annotations

import json
import threading

from .. import models

_LOCK = threading.RLock()


def _load(key: str) -> dict:
    try:
        v = json.loads(str(models.qsettings().value(key, "") or "{}"))
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001 - dữ liệu hỏng thì coi như chưa có
        return {}


def _save(key: str, data: dict) -> None:
    models.qsettings().setValue(key, json.dumps(data, ensure_ascii=False))


# ---- khoá ứng dụng: một bộ cho mỗi nhóm (youtube, tiktok, meta); mọi tài khoản của nhóm dùng chung ----
def get_creds(group: str) -> dict:
    return dict(_load("publish_creds").get(group, {}))


def set_creds(group: str, creds: dict) -> None:
    with _LOCK:
        all_ = _load("publish_creds")
        all_[group] = {k: str(v).strip() for k, v in creds.items()}
        _save("publish_creds", all_)


# ---- tài khoản đã kết nối: id ổn định theo tài khoản thật (vd. youtube:<channel>, facebook:<page>) nên kết nối lại không tạo bản trùng ----
def list_accounts(platform: str | None = None) -> list[dict]:
    accs = _load("publish_accounts")
    out = [dict(a, id=i) for i, a in accs.items() if not platform or a.get("platform") == platform]
    return sorted(out, key=lambda a: (a.get("platform", ""), a.get("label", "").lower()))


def get_account(account_id: str) -> dict:
    a = _load("publish_accounts").get(account_id)
    return dict(a, id=account_id) if a else {}


def set_account(account_id: str, data: dict) -> None:
    with _LOCK:
        accs = _load("publish_accounts")
        accs[account_id] = {k: v for k, v in data.items() if k != "id"}
        _save("publish_accounts", accs)


def remove_account(account_id: str) -> None:
    with _LOCK:
        accs = _load("publish_accounts")
        accs.pop(account_id, None)
        _save("publish_accounts", accs)

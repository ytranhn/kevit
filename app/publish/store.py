"""Lưu khoá ứng dụng (client id/secret) và token đăng nhập của từng nền tảng. Cùng nơi với API key khác (QSettings của app)."""
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


def get_creds(platform: str) -> dict:
    """Khoá ứng dụng do người dùng tự đăng ký trên trang nhà phát triển của nền tảng: client_id, client_secret, redirect_uri."""
    return dict(_load("publish_creds").get(platform, {}))


def set_creds(platform: str, creds: dict) -> None:
    with _LOCK:
        all_ = _load("publish_creds")
        all_[platform] = {k: str(v).strip() for k, v in creds.items()}
        _save("publish_creds", all_)


def get_token(platform: str) -> dict:
    return dict(_load("publish_tokens").get(platform, {}))


def set_token(platform: str, token: dict) -> None:
    with _LOCK:
        all_ = _load("publish_tokens")
        all_[platform] = token
        _save("publish_tokens", all_)


def clear_token(platform: str) -> None:
    with _LOCK:
        all_ = _load("publish_tokens")
        all_.pop(platform, None)
        _save("publish_tokens", all_)

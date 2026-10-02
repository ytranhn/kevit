"""Đăng video lên các nền tảng, hoàn toàn qua API chính thức (không điều khiển trình duyệt)."""
from . import store
from .base import Platform, Post, PublishError, Result
from .meta import FacebookReels, InstagramReels
from .tiktok import TikTok
from .youtube import YouTube

PLATFORMS: dict[str, type[Platform]] = {c.key: c for c in (YouTube, TikTok, FacebookReels, InstagramReels)}


def get(account_id: str) -> Platform:
    """Đối tượng đăng cho một tài khoản đã kết nối (id dạng "youtube:<kênh>", "facebook:<trang>"…)."""
    return PLATFORMS[account_id.split(":", 1)[0]](account_id)


def accounts() -> list[dict]:
    """Mọi tài khoản đã kết nối, kèm tên nền tảng."""
    return [a for a in store.list_accounts() if a.get("platform") in PLATFORMS]


def account_name(account_id: str) -> str:
    a = store.get_account(account_id)
    cls = PLATFORMS.get(account_id.split(":", 1)[0])
    return f"{cls.label if cls else account_id} · {a['label']}" if a else f"{cls.label if cls else '?'} · (đã xoá {account_id.split(':', 1)[-1][:8]})"


__all__ = ["PLATFORMS", "Platform", "Post", "PublishError", "Result", "accounts", "account_name", "get", "store"]

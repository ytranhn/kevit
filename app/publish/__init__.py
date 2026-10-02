"""Đăng video lên các nền tảng, hoàn toàn qua API chính thức (không điều khiển trình duyệt)."""
from .base import Platform, Post, PublishError, Result
from .meta import FacebookReels, InstagramReels
from .tiktok import TikTok
from .youtube import YouTube

PLATFORMS: dict[str, type[Platform]] = {c.key: c for c in (YouTube, TikTok, FacebookReels, InstagramReels)}


def get(key: str) -> Platform:
    return PLATFORMS[key]()


__all__ = ["PLATFORMS", "Platform", "Post", "PublishError", "Result", "get"]

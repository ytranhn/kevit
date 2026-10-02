"""Khung chung của các nền tảng đăng video: bài đăng, kết quả, lỗi, tiện ích HTTP/đọc file theo khúc."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import store

HTTP_TIMEOUT = httpx.Timeout(60.0, connect=20.0, read=300.0, write=300.0)
RETRY_STATUS = (429, 500, 502, 503, 504)


class PublishError(RuntimeError):
    """Lỗi đăng bài đã được diễn giải bằng tiếng Việt (hiển thị thẳng cho người dùng)."""


class NotConnected(PublishError):
    pass


@dataclass
class Post:
    """Nội dung chung của một bài đăng; mỗi nền tảng tự ghép lại theo giới hạn của nó (compose)."""
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)      # không có dấu #
    privacy: str = "private"                                # private | unlisted | public (nền tảng tự ánh xạ sang mức gần nhất)
    vertical: bool = True                                   # video dọc 9:16 (Shorts/Reels)
    duration: float = 0.0                                   # giây

    @property
    def tags_text(self) -> str:
        return " ".join("#" + h.lstrip("#") for h in self.hashtags if h.strip("# "))


@dataclass
class Result:
    platform: str
    ok: bool
    post_id: str = ""
    url: str = ""
    message: str = ""
    privacy: str = ""


def clean_tags(tags: list[str] | str, limit: int = 30) -> list[str]:
    """Chuẩn hoá hashtag: bỏ #, khoảng trắng và ký tự lạ, bỏ trùng (không phân biệt hoa thường), giữ thứ tự."""
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[\s,;#]+", tags) if t]
    out, seen = [], set()
    for t in tags:
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"), flags=re.UNICODE)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:limit]


def truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - len(ellipsis)].rstrip() + ellipsis


def read_chunks(path: Path, size: int):
    """Sinh (offset, bytes) theo từng khúc `size` byte, không nạp cả file vào bộ nhớ."""
    with open(path, "rb") as f:
        off = 0
        while True:
            data = f.read(size)
            if not data:
                return
            yield off, data
            off += len(data)


def read_stream(path: Path, size: int = 1024 * 1024):
    """Luồng byte của file (đi kèm header Content-Length) để gửi cả file một lần mà không nạp hết vào bộ nhớ."""
    for _, data in read_chunks(path, size):
        yield data


def request(client: httpx.Client, method: str, url: str, *, retries: int = 3, ok=(200, 201, 202, 204, 206, 308), **kw) -> httpx.Response:
    """Gọi HTTP, tự thử lại khi lỗi mạng/quá tải tạm thời. Mã không thuộc `ok` thì báo lỗi kèm nội dung máy chủ trả về."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = client.request(method, url, **kw)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = e
        else:
            if r.status_code in ok:
                return r
            if r.status_code not in RETRY_STATUS or attempt == retries:
                raise PublishError(f"{method} {url.split('?')[0]} → HTTP {r.status_code}: {r.text[:400]}")
            last = PublishError(f"HTTP {r.status_code}")
        if attempt < retries:
            time.sleep(min(2 ** attempt * 2, 20))
    raise PublishError(f"Lỗi mạng khi gọi {url.split('?')[0]}: {last}")


class Platform:
    """Một nền tảng. Lớp con định nghĩa key/label/fields và các hàm connect/upload."""
    key = ""
    label = ""
    # (khoá, nhãn, gợi ý, là bí mật)
    fields: tuple[tuple[str, str, str, bool], ...] = (("client_id", "Client ID", "", False), ("client_secret", "Client secret", "", True))
    default_redirect = "http://127.0.0.1:53682/callback"
    setup_url = ""
    setup_note = ""
    max_title = 100

    def __init__(self, client: httpx.Client | None = None, sleep=time.sleep):
        self._client = client
        self.sleep = sleep

    # ---- cấu hình / kết nối ----
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)
        return self._client

    @property
    def store_key(self) -> str:
        """Khoá lưu cấu hình/token; Facebook và Instagram dùng chung một kết nối Meta."""
        return self.key

    def creds(self) -> dict:
        c = store.get_creds(self.store_key)
        c.setdefault("redirect_uri", self.default_redirect)
        if not c["redirect_uri"].strip():
            c["redirect_uri"] = self.default_redirect
        return c

    def token(self) -> dict:
        return store.get_token(self.store_key)

    def is_connected(self) -> bool:
        return bool(self.token().get("access_token"))

    def account_label(self) -> str:
        return self.token().get("account", "")

    def require_creds(self) -> dict:
        c = self.creds()
        missing = [lbl for k, lbl, _, _ in self.fields if not c.get(k)]
        if missing:
            raise PublishError(f"{self.label}: thiếu {', '.join(missing)}. Nhập ở Cài đặt → Đăng video.")
        return c

    def require_connected(self) -> dict:
        t = self.token()
        if not t.get("access_token"):
            raise NotConnected(f"{self.label}: chưa kết nối tài khoản. Bấm Kết nối ở Cài đặt → Đăng video.")
        return t

    def connect(self, log=print) -> str:
        raise NotImplementedError

    def disconnect(self) -> None:
        store.clear_token(self.store_key)

    # ---- đăng ----
    def compose(self, post: Post) -> dict:
        """Ghép tiêu đề/mô tả/hashtag của bài thành nội dung đúng giới hạn của nền tảng."""
        raise NotImplementedError

    def upload(self, video: Path, post: Post, log=print) -> Result:
        raise NotImplementedError

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
    platform: str                       # nền tảng (youtube, tiktok, facebook, instagram)
    ok: bool
    post_id: str = ""
    url: str = ""
    message: str = ""
    privacy: str = ""
    account: str = ""                   # id tài khoản đã đăng


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
    """Một nền tảng, gắn với MỘT tài khoản đã kết nối (account_id). Lớp con định nghĩa key/label/fields và connect/upload."""
    key = ""
    label = ""
    creds_key = ""                      # nhóm khoá ứng dụng dùng chung (mặc định = key)
    # (khoá, nhãn, gợi ý, là bí mật)
    fields: tuple[tuple[str, str, str, bool], ...] = (("client_id", "Client ID", "", False), ("client_secret", "Client secret", "", True))
    default_redirect = "http://127.0.0.1:53682/callback"
    setup_url = ""
    setup_note = ""
    max_title = 100

    def __init__(self, account_id: str = "", client: httpx.Client | None = None, sleep=time.sleep):
        self.account_id = account_id
        self._client = client
        self.sleep = sleep

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)
        return self._client

    # ---- khoá ứng dụng ----
    def creds(self) -> dict:
        c = store.get_creds(self.creds_key or self.key)
        if not c.get("redirect_uri", "").strip():
            c["redirect_uri"] = self.default_redirect
        return c

    def require_creds(self) -> dict:
        c = self.creds()
        missing = [lbl for k, lbl, _, _ in self.fields if not c.get(k)]
        if missing:
            raise PublishError(f"{self.label}: thiếu {', '.join(missing)}. Nhập ở Cài đặt → Đăng video.")
        return c

    # ---- tài khoản ----
    def account(self) -> dict:
        return store.get_account(self.account_id) if self.account_id else {}

    token = account                     # tên cũ: các hàm đọc/ghi token dùng chung một bản ghi tài khoản

    def save_account(self, data: dict) -> None:
        store.set_account(self.account_id, data)

    def is_connected(self) -> bool:
        return bool(self.account().get("access_token"))

    def account_label(self) -> str:
        return self.account().get("label", "")

    def require_connected(self) -> dict:
        a = self.account()
        if not a.get("access_token"):
            raise NotConnected(f"{self.label}: tài khoản này chưa kết nối hoặc đã bị xoá. Kết nối ở Cài đặt → Đăng video.")
        return a

    def connect(self, log=print) -> list[dict]:
        """Đăng nhập một tài khoản mới (hoặc kết nối lại); lưu và trả về danh sách tài khoản vừa kết nối."""
        raise NotImplementedError

    def disconnect(self) -> None:
        store.remove_account(self.account_id)

    def check(self) -> str:
        """Kiểm tra tài khoản còn dùng được; trả về mô tả ngắn, lỗi thì ném PublishError."""
        self.require_connected()
        return "đã kết nối"

    # ---- đăng ----
    def compose(self, post: Post) -> dict:
        """Ghép tiêu đề/mô tả/hashtag của bài thành nội dung đúng giới hạn của nền tảng."""
        raise NotImplementedError

    def upload(self, video: Path, post: Post, log=print) -> Result:
        raise NotImplementedError

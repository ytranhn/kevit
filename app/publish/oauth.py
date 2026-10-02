"""Đăng nhập OAuth 2.0 cho ứng dụng máy tính: mở trình duyệt, nhận mã ở địa chỉ loopback 127.0.0.1, đổi lấy token (có PKCE)."""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser

_CANCEL = threading.Event()


def cancel_login() -> None:
    """Dừng việc chờ đăng nhập đang chạy (khi trình duyệt báo lỗi và người dùng không muốn đợi hết giờ)."""
    _CANCEL.set()


LOGIN_TIMEOUT = 240      # giây chờ người dùng đăng nhập xong trên trình duyệt

_PAGE = ("<!doctype html><meta charset='utf-8'><title>Kevit</title><body style='font-family:system-ui;text-align:center;margin-top:18vh'>"
         "<h2>{title}</h2><p>{msg}</p></body>")


def pkce_pair(hex_challenge: bool = False) -> tuple[str, str]:
    """(verifier, challenge). TikTok yêu cầu challenge là SHA-256 dạng hex, các nơi khác dùng base64url."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = digest.hex() if hex_challenge else base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def redirect_port(redirect_uri: str) -> int:
    u = urllib.parse.urlparse(redirect_uri)
    return u.port or 80


def wait_for_code(redirect_uri: str, state: str, open_url: str, log=print, timeout: int = LOGIN_TIMEOUT,
                  opener=webbrowser.open, hint: str = "") -> str:
    """Mở `open_url` rồi chờ nền tảng chuyển hướng về `redirect_uri`; trả về `code`. Báo lỗi nếu người dùng từ chối, sai state hoặc quá hạn."""
    u = urllib.parse.urlparse(redirect_uri)
    if u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"Địa chỉ chuyển hướng phải là loopback (127.0.0.1 hoặc localhost), hiện là {redirect_uri}")
    result: dict = {}
    done = threading.Event()
    _CANCEL.clear()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != (u.path or "/"):
                self.send_response(404)
                self.end_headers()
                return
            if q.get("state", [""])[0] != state:
                result["error"] = "state không khớp (có thể là yêu cầu giả mạo)"
                title, msg = "Lỗi", "Phiên đăng nhập không hợp lệ. Hãy quay lại Kevit và thử lại."
            elif "error" in q:
                result["error"] = q.get("error_description", q["error"])[0]
                title, msg = "Đã từ chối", "Bạn đã từ chối cấp quyền. Có thể đóng tab này."
            elif "code" in q:
                result["code"] = q["code"][0]
                title, msg = "Đã kết nối", "Quay lại Kevit. Có thể đóng tab này."
            else:
                return self.send_error(400)
            body = _PAGE.format(title=title, msg=msg).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *a):  # im lặng
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", redirect_port(redirect_uri)), Handler)
    except OSError as e:
        raise RuntimeError(f"Cổng {redirect_port(redirect_uri)} đang bị chiếm ({e}). Đổi địa chỉ chuyển hướng ở Cài đặt → Đăng video.") from e
    server.timeout = 0.5
    log("Đang mở trình duyệt để đăng nhập…")
    opener(open_url)
    end = time.time() + timeout
    try:
        while not done.is_set() and time.time() < end and not _CANCEL.is_set():
            server.handle_request()
    finally:
        server.server_close()
    if _CANCEL.is_set() and "code" not in result:
        _CANCEL.clear()
        raise RuntimeError("Đã huỷ chờ đăng nhập.")
    if "error" in result:
        raise RuntimeError(f"Đăng nhập thất bại: {result['error']}")
    if "code" not in result:
        raise RuntimeError(f"Hết {timeout}s mà chưa đăng nhập xong." + (f" {hint}" if hint else ""))
    return result["code"]


def auth_url(base: str, **params) -> str:
    return base + ("&" if "?" in base else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})

"""TikTok qua Content Posting API (Direct Post, tải file lên theo khúc)."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import oauth, store
from .base import Platform, Post, PublishError, Result, clean_tags, request, truncate

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = "user.info.basic,video.publish"
CHUNK = 10 * 1024 * 1024               # mỗi khúc 5–64 MB; khúc cuối được lớn hơn tới 128 MB
CAPTION_MAX = 2200
PRIVACY = {"private": "SELF_ONLY", "unlisted": "MUTUAL_FOLLOW_FRIENDS", "public": "PUBLIC_TO_EVERYONE"}
POLL_EVERY, POLL_MAX = 5, 120          # giây giữa hai lần hỏi trạng thái, và số lần tối đa


def _check(r) -> dict:
    """Bóc lớp vỏ {data, error} của TikTok; mã lỗi khác "ok" thì báo lỗi."""
    j = r.json()
    err = j.get("error") or {}
    if isinstance(err, dict) and err.get("code", "ok") not in ("ok", ""):
        hint = {"spam_risk_too_many_posts": " (đã đăng quá số bài cho phép trong ngày)",
                "unaudited_client_can_only_post_to_private_accounts": " (ứng dụng chưa được TikTok duyệt: chỉ đăng được lên tài khoản riêng tư)",
                "access_token_invalid": " (hãy kết nối lại TikTok)"}.get(err["code"], "")
        raise PublishError(f"TikTok: {err.get('message') or err['code']}{hint}")
    return j.get("data") or {}


class TikTok(Platform):
    key = "tiktok"
    creds_key = "tiktok"
    label = "TikTok"
    fields = (("client_id", "Client key", "", False), ("client_secret", "Client secret", "", True))
    default_redirect = "http://localhost:53683/callback"
    setup_url = "https://developers.tiktok.com/apps/"
    setup_note = ("developers.tiktok.com → tạo app, thêm sản phẩm Login Kit và Content Posting API (bật Direct Post), "
                  "đăng ký ĐÚNG địa chỉ chuyển hướng bên dưới. Trước khi TikTok duyệt app, chỉ đăng được ở chế độ riêng tư (SELF_ONLY) "
                  "và tài khoản phải được thêm làm Target User.")

    def connect(self, log=print) -> list[dict]:
        c = self.require_creds()
        state = oauth.secrets.token_urlsafe(16)
        verifier, challenge = oauth.pkce_pair(hex_challenge=True)      # TikTok: challenge là SHA-256 dạng hex
        url = oauth.auth_url(AUTH_URL, client_key=c["client_id"], scope=SCOPES, response_type="code", redirect_uri=c["redirect_uri"],
                             state=state, code_challenge=challenge, code_challenge_method="S256", disable_auto_auth=1)
        code = oauth.wait_for_code(c["redirect_uri"], state, url, log)
        tok = self._token_call(dict(client_key=c["client_id"], client_secret=c["client_secret"], code=code,
                                    grant_type="authorization_code", redirect_uri=c["redirect_uri"], code_verifier=verifier))
        name = self._display_name(tok["access_token"])
        acc_id = f"tiktok:{tok.get('open_id') or hashlib.sha1(tok['refresh_token'].encode()).hexdigest()[:10]}"
        acc = dict(tok, platform=self.key, label=name or "Tài khoản TikTok")
        store.set_account(acc_id, acc)
        return [dict(acc, id=acc_id)]

    def _token_call(self, form: dict, old: dict | None = None) -> dict:
        r = request(self.client, "POST", f"{API}/v2/oauth/token/", data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}, ok=(200, 400))
        j = r.json()
        if "access_token" not in j:
            raise PublishError(f"TikTok từ chối đăng nhập: {j.get('error_description') or j.get('error') or r.text[:200]}")
        tok = dict(old or {})
        tok.update(access_token=j["access_token"], refresh_token=j.get("refresh_token", tok.get("refresh_token", "")),
                   expires_at=time.time() + int(j.get("expires_in", 86400)), open_id=j.get("open_id", tok.get("open_id", "")))
        return tok

    def _display_name(self, access: str) -> str:
        try:
            r = request(self.client, "GET", f"{API}/v2/user/info/", params={"fields": "display_name"},
                        headers={"Authorization": f"Bearer {access}"}, retries=1)
            return (_check(r).get("user") or {}).get("display_name", "")
        except Exception:  # noqa: BLE001
            return ""

    def access_token(self) -> str:
        t = self.require_connected()
        if t.get("expires_at", 0) > time.time() + 120:
            return t["access_token"]
        c = self.require_creds()
        if not t.get("refresh_token"):
            raise PublishError(f"TikTok «{t.get('label', '')}»: phiên đăng nhập hết hạn, hãy kết nối lại.")
        try:
            t = self._token_call(dict(client_key=c["client_id"], client_secret=c["client_secret"],
                                      grant_type="refresh_token", refresh_token=t["refresh_token"]), t)
        except PublishError as e:
            raise PublishError(f"{e}. Hãy kết nối lại tài khoản TikTok này.") from e
        self.save_account(t)
        return t["access_token"]

    def check(self) -> str:
        access = self.access_token()
        r = request(self.client, "GET", f"{API}/v2/user/info/", params={"fields": "display_name"}, headers={"Authorization": f"Bearer {access}"}, retries=1)
        return f"tài khoản “{(_check(r).get('user') or {}).get('display_name', '')}” dùng được"

    def compose(self, post: Post) -> dict:
        tags = clean_tags(post.hashtags, 8)
        head = post.title.strip()
        cap = head + ("\n" if head and tags else "") + " ".join("#" + t for t in tags)
        return dict(title=truncate(cap, CAPTION_MAX))

    def upload(self, video: Path, post: Post, log=print) -> Result:
        access = self.access_token()
        auth = {"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8"}
        info = _check(request(self.client, "POST", f"{API}/v2/post/publish/creator_info/query/", headers=auth, json={}))
        options = info.get("privacy_level_options") or ["SELF_ONLY"]
        want = PRIVACY.get(post.privacy, "SELF_ONLY")
        level, note = (want, "") if want in options else ("SELF_ONLY", f"TikTok không cho phép mức “{want}” cho tài khoản này, đăng ở chế độ riêng tư.")
        limit = info.get("max_video_post_duration_sec")
        if limit and post.duration and post.duration > limit:
            raise PublishError(f"TikTok: video dài {post.duration:.0f}s, tài khoản chỉ đăng tối đa {limit}s.")
        size = video.stat().st_size
        chunk = size if size < CHUNK else CHUNK
        count = max(1, size // chunk)                        # khúc cuối nhận phần dư
        body = {"post_info": {"title": self.compose(post)["title"], "privacy_level": level, "disable_duet": False,
                              "disable_comment": False, "disable_stitch": False},
                "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}}
        d = _check(request(self.client, "POST", f"{API}/v2/post/publish/video/init/", headers=auth, json=body))
        pub_id, url = d.get("publish_id"), d.get("upload_url")
        if not pub_id or not url:
            raise PublishError("TikTok không trả địa chỉ tải lên.")
        log(f"TikTok: đang tải lên {size / 1_048_576:.1f} MB ({count} khúc)…")
        with open(video, "rb") as f:
            for i in range(count):
                start = i * chunk
                n = chunk if i < count - 1 else size - start
                f.seek(start)
                data = f.read(n)
                request(self.client, "PUT", url, content=data, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(n), "Content-Range": f"bytes {start}-{start + n - 1}/{size}"},
                    ok=(200, 201, 206))
                log(f"TikTok: đã gửi khúc {i + 1}/{count}")
        post_id = self._wait(auth, pub_id, log)
        return Result(self.key, True, post_id or pub_id, "", note or "Đã đăng lên TikTok (có thể mất vài phút để hiện).", level, self.account_id)

    def _wait(self, auth: dict, pub_id: str, log) -> str:
        for _ in range(POLL_MAX):
            d = _check(request(self.client, "POST", f"{API}/v2/post/publish/status/fetch/", headers=auth, json={"publish_id": pub_id}))
            st = d.get("status", "")
            if st == "PUBLISH_COMPLETE":
                ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
                return str(ids[0]) if ids else ""
            if st == "FAILED":
                raise PublishError(f"TikTok từ chối video: {d.get('fail_reason', 'không rõ lý do')}")
            if st == "SEND_TO_USER_INBOX":
                return ""
            self.sleep(POLL_EVERY)
        raise PublishError("TikTok xử lý quá lâu; kiểm tra lại trong ứng dụng TikTok.")

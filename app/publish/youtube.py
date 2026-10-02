"""YouTube (Shorts và video thường) qua YouTube Data API v3: OAuth 2.0 + tải lên resumable."""
from __future__ import annotations

import time
from pathlib import Path

from . import oauth, store
from .base import Platform, Post, PublishError, Result, clean_tags, read_chunks, request, truncate

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
CHUNK = 8 * 1024 * 1024               # phải là bội của 256 KiB
SHORTS_MAX_SECONDS = 180
CATEGORY_ENTERTAINMENT = "24"


class YouTube(Platform):
    key = "youtube"
    label = "YouTube"
    default_redirect = "http://127.0.0.1:53682/callback"
    setup_url = "https://console.cloud.google.com/apis/credentials"
    setup_note = ("Google Cloud Console → bật YouTube Data API v3 → tạo OAuth client loại “Desktop app”. "
                  "Dự án API chưa được Google kiểm duyệt thì video đăng lên luôn ở chế độ riêng tư. Mỗi lượt tải lên tốn 1600/10.000 đơn vị hạn mức mỗi ngày.")
    max_title = 100

    # ---- kết nối ----
    def connect(self, log=print) -> str:
        c = self.require_creds()
        state = oauth.secrets.token_urlsafe(16)
        verifier, challenge = oauth.pkce_pair()
        url = oauth.auth_url(AUTH_URL, client_id=c["client_id"], redirect_uri=c["redirect_uri"], response_type="code", scope=SCOPES,
                             access_type="offline", prompt="consent", state=state, code_challenge=challenge, code_challenge_method="S256")
        code = oauth.wait_for_code(c["redirect_uri"], state, url, log)
        r = request(self.client, "POST", TOKEN_URL, data=dict(
            code=code, client_id=c["client_id"], client_secret=c["client_secret"], redirect_uri=c["redirect_uri"],
            grant_type="authorization_code", code_verifier=verifier))
        tok = self._store(r.json())
        if not tok.get("refresh_token"):
            raise PublishError("Google không trả refresh token. Hãy gỡ quyền của ứng dụng ở myaccount.google.com/permissions rồi kết nối lại.")
        tok["account"] = self._channel_name(tok["access_token"])
        store.set_token(self.key, tok)
        return tok["account"] or "YouTube"

    def _store(self, data: dict, old: dict | None = None) -> dict:
        tok = dict(old or {})
        tok["access_token"] = data["access_token"]
        tok["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        if data.get("refresh_token"):
            tok["refresh_token"] = data["refresh_token"]
        store.set_token(self.key, tok)
        return tok

    def _channel_name(self, access: str) -> str:
        try:
            r = request(self.client, "GET", CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                        headers={"Authorization": f"Bearer {access}"}, retries=1)
            items = r.json().get("items") or []
            return items[0]["snippet"]["title"] if items else ""
        except Exception:  # noqa: BLE001 - tên kênh chỉ để hiển thị
            return ""

    def access_token(self) -> str:
        t = self.require_connected()
        if t.get("expires_at", 0) > time.time() + 60:
            return t["access_token"]
        c = self.require_creds()
        if not t.get("refresh_token"):
            raise PublishError("YouTube: phiên đăng nhập hết hạn, hãy kết nối lại.")
        try:
            r = request(self.client, "POST", TOKEN_URL, data=dict(
                client_id=c["client_id"], client_secret=c["client_secret"], refresh_token=t["refresh_token"], grant_type="refresh_token"))
        except PublishError as e:
            if "invalid_grant" in str(e):
                store.clear_token(self.key)
                raise PublishError("YouTube: quyền truy cập đã bị thu hồi hoặc hết hạn, hãy kết nối lại.") from e
            raise
        return self._store(r.json(), t)["access_token"]

    # ---- nội dung ----
    def compose(self, post: Post) -> dict:
        title = truncate(post.title.replace("<", "").replace(">", ""), self.max_title) or "Video"
        tags = clean_tags(post.hashtags, 15)
        extra = "#Shorts" if post.vertical and 0 < post.duration <= SHORTS_MAX_SECONDS else ""
        parts = [post.description.strip(), " ".join(filter(None, [post.tags_text, extra]))]
        desc = truncate("\n\n".join(p for p in parts if p), 4900)
        kept, total = [], 0
        for t in tags:                        # tổng độ dài thẻ ≤ 500 ký tự
            total += len(t) + 1
            if total > 480:
                break
            kept.append(t)
        return dict(title=title, description=desc, tags=kept)

    # ---- tải lên ----
    def upload(self, video: Path, post: Post, log=print) -> Result:
        access = self.access_token()
        c = self.compose(post)
        size = video.stat().st_size
        body = {"snippet": {"title": c["title"], "description": c["description"], "tags": c["tags"], "categoryId": CATEGORY_ENTERTAINMENT},
                "status": {"privacyStatus": post.privacy if post.privacy in ("private", "unlisted", "public") else "private",
                           "selfDeclaredMadeForKids": False}}
        headers = {"Authorization": f"Bearer {access}", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}
        r = request(self.client, "POST", UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, headers=headers, json=body)
        session = r.headers.get("Location")
        if not session:
            raise PublishError("YouTube không trả địa chỉ tải lên.")
        log(f"YouTube: đang tải lên {size / 1_048_576:.1f} MB…")
        final = None
        for off, data in read_chunks(video, CHUNK):
            end = off + len(data) - 1
            r = request(self.client, "PUT", session, content=data, headers={
                "Content-Length": str(len(data)), "Content-Type": "video/mp4", "Content-Range": f"bytes {off}-{end}/{size}"})
            if r.status_code in (200, 201):
                final = r.json()
            else:
                log(f"YouTube: đã gửi {min(end + 1, size) * 100 // size}%")
        if not final or "id" not in final:
            raise PublishError("YouTube: tải lên xong nhưng không nhận được mã video.")
        vid = final["id"]
        got = (final.get("status") or {}).get("privacyStatus", "")
        note = ""
        if got and got != body["status"]["privacyStatus"]:
            note = f"YouTube giữ ở chế độ “{got}” thay vì “{body['status']['privacyStatus']}” (dự án API chưa được Google kiểm duyệt)."
        return Result(self.key, True, vid, f"https://youtu.be/{vid}", note, got or body["status"]["privacyStatus"])

"""Facebook Reels (Trang) và Instagram Reels qua Graph API. Hai nền tảng dùng chung một lần đăng nhập Meta và một ứng dụng."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from . import oauth, store
from .base import Platform, Post, PublishError, Result, clean_tags, read_stream, request, truncate

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = "https://rupload.facebook.com"
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
SCOPES = ("pages_show_list,pages_read_engagement,pages_manage_posts,publish_video,"
          "instagram_basic,instagram_content_publish,business_management")
CAPTION_MAX = 2200
IG_MAX_TAGS = 30
POLL_EVERY, POLL_MAX = 5, 120


def graph_error(r: httpx.Response) -> str:
    try:
        e = r.json()["error"]
    except Exception:  # noqa: BLE001
        return r.text[:300]
    code, sub, msg = e.get("code"), e.get("error_subcode"), e.get("message", "")
    if code == 190:
        return f"{msg} → phiên đăng nhập Meta đã hết hạn hoặc bị thu hồi, hãy kết nối lại."
    if code in (10, 200, 283):
        return f"{msg} → thiếu quyền; ứng dụng Meta cần được cấp quyền này (chế độ Development chỉ dùng được với tài khoản vai trò Admin/Tester)."
    if code in (4, 17, 32, 613):
        return f"{msg} → chạm giới hạn số lần gọi API, thử lại sau."
    return f"{msg} (code {code}{'/' + str(sub) if sub else ''})"


def graph(client: httpx.Client, method: str, path: str, **kw) -> dict:
    """Gọi Graph API: mọi mã lỗi đều diễn giải lại theo nội dung lỗi Meta trả về."""
    url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
    r = request(client, method, url, ok=tuple(range(200, 600)), **kw)
    if r.status_code in (429, 500, 502, 503, 504) or r.status_code >= 400 or ("error" in _safe_json(r)):
        raise PublishError(f"Meta: {graph_error(r)}")
    return _safe_json(r)


def _safe_json(r: httpx.Response) -> dict:
    try:
        j = r.json()
        return j if isinstance(j, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


class Meta(Platform):
    """Kết nối Meta chung: đăng nhập, lấy danh sách Trang (kèm tài khoản Instagram doanh nghiệp liên kết) và token của Trang."""
    fields = (("client_id", "App ID", "", False), ("client_secret", "App secret", "", True))
    default_redirect = "http://localhost:53684/callback"
    setup_url = "https://developers.facebook.com/apps/"
    setup_note = ("developers.facebook.com → tạo app loại Business, thêm Facebook Login for Business, thêm địa chỉ chuyển hướng bên dưới "
                  "vào Valid OAuth Redirect URIs. Ở chế độ Development chỉ tài khoản Admin/Tester của app đăng được. "
                  "Instagram phải là tài khoản Professional (Business/Creator) và liên kết với Trang Facebook.")

    @property
    def store_key(self) -> str:
        return "meta"

    def connect(self, log=print) -> str:
        c = self.require_creds()
        state = oauth.secrets.token_urlsafe(16)
        url = oauth.auth_url(DIALOG, client_id=c["client_id"], redirect_uri=c["redirect_uri"], state=state, scope=SCOPES, response_type="code")
        code = oauth.wait_for_code(c["redirect_uri"], state, url, log)
        short = graph(self.client, "GET", "oauth/access_token", params=dict(
            client_id=c["client_id"], client_secret=c["client_secret"], redirect_uri=c["redirect_uri"], code=code))
        long = graph(self.client, "GET", "oauth/access_token", params=dict(
            grant_type="fb_exchange_token", client_id=c["client_id"], client_secret=c["client_secret"], fb_exchange_token=short["access_token"]))
        tok = dict(access_token=long["access_token"], expires_at=time.time() + int(long.get("expires_in", 5_184_000)))
        tok["pages"] = self._pages(tok["access_token"])
        if not tok["pages"]:
            raise PublishError("Tài khoản này không quản lý Trang Facebook nào (hoặc chưa cấp quyền cho Trang). Hãy chọn Trang ở bước cấp quyền.")
        tok["page_id"] = tok["pages"][0]["id"]
        tok["account"] = tok["pages"][0]["name"]
        store.set_token("meta", tok)
        return tok["account"]

    def _pages(self, user_token: str) -> list[dict]:
        out, url, params = [], "me/accounts", dict(
            fields="id,name,access_token,instagram_business_account{id,username}", limit=100, access_token=user_token)
        while url and len(out) < 500:
            j = graph(self.client, "GET", url, params=params)
            for p in j.get("data", []):
                ig = p.get("instagram_business_account") or {}
                out.append(dict(id=p["id"], name=p.get("name", p["id"]), access_token=p["access_token"],
                                ig_id=ig.get("id", ""), ig_username=ig.get("username", "")))
            url, params = (j.get("paging") or {}).get("next"), None
        return out

    # ---- Trang đang dùng ----
    def pages(self) -> list[dict]:
        return list(self.token().get("pages", []))

    def set_page(self, page_id: str) -> None:
        t = self.require_connected()
        page = next((p for p in t.get("pages", []) if p["id"] == page_id), None)
        if not page:
            raise PublishError("Trang không nằm trong danh sách đã kết nối.")
        t["page_id"], t["account"] = page_id, page["name"]
        store.set_token("meta", t)

    def page(self) -> dict:
        t = self.require_connected()
        page = next((p for p in t.get("pages", []) if p["id"] == t.get("page_id")), None) or (t.get("pages") or [None])[0]
        if not page:
            raise PublishError("Chưa chọn Trang Facebook.")
        if t.get("expires_at", 0) < time.time():
            raise PublishError("Phiên đăng nhập Meta đã hết hạn, hãy kết nối lại.")
        return page

    # ---- tải lên dùng chung (rupload) ----
    def _rupload(self, url: str, token: str, video: Path, log, name: str) -> None:
        size = video.stat().st_size
        log(f"{name}: đang tải lên {size / 1_048_576:.1f} MB…")
        r = request(self.client, "POST", url, content=read_stream(video), headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size), "Content-Length": str(size),
            "Content-Type": "application/octet-stream"}, ok=tuple(range(200, 600)), retries=0)
        j = _safe_json(r)
        if r.status_code >= 400 or j.get("success") is False or "error" in j:
            raise PublishError(f"{name}: tải lên thất bại: {r.text[:300]}")


class FacebookReels(Meta):
    key = "facebook"
    label = "Facebook"

    def compose(self, post: Post) -> dict:
        tags = " ".join("#" + t for t in clean_tags(post.hashtags, 15))
        body = "\n\n".join(p for p in (post.title.strip(), post.description.strip(), tags) if p)
        return dict(description=truncate(body, CAPTION_MAX), title=truncate(post.title, 100))

    def upload(self, video: Path, post: Post, log=print) -> Result:
        page = self.page()
        tok, pid = page["access_token"], page["id"]
        c = self.compose(post)
        start = graph(self.client, "POST", f"{pid}/video_reels", data={"upload_phase": "start", "access_token": tok})
        vid, up = start.get("video_id"), start.get("upload_url") or f"{RUPLOAD}/video-upload/{GRAPH_VERSION}/{start.get('video_id')}"
        if not vid:
            raise PublishError("Facebook không trả mã video.")
        self._rupload(up, tok, video, log, "Facebook")
        state = "DRAFT" if post.privacy == "private" else "PUBLISHED"
        graph(self.client, "POST", f"{pid}/video_reels", data={
            "access_token": tok, "video_id": vid, "upload_phase": "finish", "video_state": state,
            "description": c["description"], "title": c["title"]})
        self._wait(vid, tok, log)
        note = "Facebook Reels không có chế độ riêng tư: video được lưu ở BẢN NHÁP của Trang, chưa đăng." if state == "DRAFT" else ""
        return Result(self.key, True, vid, "" if state == "DRAFT" else f"https://www.facebook.com/reel/{vid}", note,
                      "private" if state == "DRAFT" else "public")

    def _wait(self, vid: str, token: str, log) -> None:
        for _ in range(POLL_MAX):
            j = graph(self.client, "GET", vid, params={"fields": "status", "access_token": token})
            st = j.get("status") or {}
            phase = (st.get("video_status") or "").lower()
            if phase in ("ready", "published"):
                return
            if phase == "error":
                raise PublishError(f"Facebook từ chối video: {st.get('processing_phase', {}).get('error', {}).get('message') or st}")
            self.sleep(POLL_EVERY)
        log("Facebook: video còn đang xử lý, sẽ tự hiện khi xong.")


class InstagramReels(Meta):
    key = "instagram"
    label = "Instagram"

    def compose(self, post: Post) -> dict:
        tags = " ".join("#" + t for t in clean_tags(post.hashtags, IG_MAX_TAGS))
        body = "\n\n".join(p for p in (post.title.strip(), post.description.strip(), tags) if p)
        return dict(caption=truncate(body, CAPTION_MAX))

    def upload(self, video: Path, post: Post, log=print) -> Result:
        if post.privacy == "private":
            raise PublishError("Instagram không có chế độ riêng tư: chọn “Công khai” nếu muốn đăng Reels thật.")
        page = self.page()
        ig = page.get("ig_id")
        if not ig:
            raise PublishError(f"Trang “{page['name']}” chưa liên kết với tài khoản Instagram Professional.")
        tok = page["access_token"]
        c = self.compose(post)
        cont = graph(self.client, "POST", f"{ig}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": c["caption"], "share_to_feed": "true", "access_token": tok})
        cid = cont.get("id")
        if not cid:
            raise PublishError("Instagram không trả mã container.")
        self._rupload(cont.get("uri") or f"{RUPLOAD}/ig-api-upload/{GRAPH_VERSION}/{cid}", tok, video, log, "Instagram")
        for _ in range(POLL_MAX):
            st = graph(self.client, "GET", cid, params={"fields": "status_code,status", "access_token": tok})
            code = st.get("status_code", "")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram từ chối video: {st.get('status') or code}")
            self.sleep(POLL_EVERY)
        else:
            raise PublishError("Instagram xử lý quá lâu, hãy thử lại sau.")
        pub = graph(self.client, "POST", f"{ig}/media_publish", data={"creation_id": cid, "access_token": tok})
        mid = pub.get("id", "")
        url = ""
        try:
            url = graph(self.client, "GET", mid, params={"fields": "permalink", "access_token": tok}).get("permalink", "") if mid else ""
        except PublishError:
            pass
        return Result(self.key, True, mid, url, "", "public")

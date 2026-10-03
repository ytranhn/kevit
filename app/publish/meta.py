"""Facebook Reels (Trang) và Instagram Reels qua Graph API. Hai nền tảng dùng chung một lần đăng nhập Meta và một ứng dụng."""
from __future__ import annotations

from pathlib import Path

import httpx

from . import oauth, store
from .base import Platform, Post, PublishError, Result, clean_tags, read_stream, request, truncate

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = "https://rupload.facebook.com"
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
# Quyền xin theo nhu cầu. Mặc định chỉ xin quyền TỐI THIỂU để kết nối được: app Meta chưa thêm quyền nào mà vẫn xin thì Facebook từ chối cả hộp thoại
# ("Invalid Scopes"). Quyền đầy đủ cho Reels có thể điền ở ô “Quyền yêu cầu (nâng cao)” sau khi thêm vào app (use case Pages / Instagram).
FB_SCOPES = "pages_show_list,pages_manage_posts"
IG_SCOPES = FB_SCOPES + ",instagram_basic,instagram_content_publish"
FULL_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video,instagram_basic,instagram_content_publish"
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
    """Kết nối Meta: một lần đăng nhập tạo ra MỘT TÀI KHOẢN cho mỗi Trang Facebook, và thêm một tài khoản Instagram cho mỗi Trang có
    Instagram Professional liên kết. Mỗi tài khoản giữ token riêng của Trang."""
    creds_key = "meta"
    fields = (("client_id", "App ID", "", False), ("client_secret", "App secret", "", True))
    optional_fields = (("config_id", "Configuration ID", "Chỉ cần nếu app dùng “Facebook Login for Business” (loại này không nhận danh sách quyền)."),
                       ("scopes", "Quyền yêu cầu (nâng cao)", "Để trống = quyền tối thiểu (pages_show_list, pages_manage_posts; thêm quyền Instagram nếu kết nối từ thẻ Instagram). "
                                                  "Đã thêm quyền khác vào app thì liệt kê ở đây, cách nhau bằng dấu phẩy, vd. pages_show_list,pages_read_engagement,pages_manage_posts,publish_video."))
    default_redirect = "http://localhost:53684/callback"
    setup_url = "https://developers.facebook.com/apps/"
    setup_note = ("developers.facebook.com → tạo app, thêm sản phẩm “Facebook Login” (đăng nhập thường) và thêm địa chỉ chuyển hướng bên dưới "
                  "vào Valid OAuth Redirect URIs. Ở chế độ Development chỉ tài khoản Admin/Tester của app đăng được. "
                  "Instagram phải là tài khoản Professional (Business/Creator) và liên kết với Trang Facebook.")
    default_scopes = FB_SCOPES

    def auth_params(self, c: dict) -> dict:
        """Tham số hộp thoại đăng nhập: có Configuration ID (Facebook Login for Business) thì dùng config_id, nếu không thì dùng danh sách quyền."""
        if c.get("config_id", "").strip():
            return dict(config_id=c["config_id"].strip(), response_type="code", override_default_response_type="true")
        return dict(scope=(c.get("scopes") or "").strip() or self.default_scopes, response_type="code", auth_type="reauthenticate")

    def connect(self, log=print) -> list[dict]:
        c = self.require_creds()
        state = oauth.secrets.token_urlsafe(16)
        url = oauth.auth_url(DIALOG, client_id=c["client_id"], redirect_uri=c["redirect_uri"], state=state, **self.auth_params(c))
        code = oauth.wait_for_code(c["redirect_uri"], state, url, log, hint=(
            "Nếu trình duyệt báo lỗi trên trang Facebook: “Invalid Scopes” → app chưa có quyền đó, bỏ bớt quyền ở ô “Quyền yêu cầu (nâng cao)” hoặc thêm quyền vào app; "
            "“The domain of this URL isn't included in the app's domains” → thêm localhost vào App Domains (Settings → Basic) và địa chỉ chuyển hướng vào Valid OAuth Redirect URIs; "
            "tài khoản bạn phải là Admin/Tester của app; app dùng Facebook Login for Business thì nhập Configuration ID."))
        short = graph(self.client, "GET", "oauth/access_token", params=dict(
            client_id=c["client_id"], client_secret=c["client_secret"], redirect_uri=c["redirect_uri"], code=code))
        return self.accounts_from_token(short["access_token"], c, log)

    def connect_with_token(self, user_token: str, log=print) -> list[dict]:
        """Kết nối bằng token người dùng lấy sẵn (vd. từ Graph API Explorer): không cần đăng nhập chuyển hướng về máy.
        Token được đổi sang loại dài hạn rồi lấy token của từng Trang (token Trang từ token dài hạn không hết hạn)."""
        user_token = user_token.strip()
        if len(user_token) < 20 or " " in user_token:
            raise PublishError("Token không hợp lệ: hãy dán nguyên chuỗi token (bắt đầu bằng “EAA…”).")
        return self.accounts_from_token(user_token, self.require_creds(), log)

    def accounts_from_token(self, user_token: str, c: dict, log=print) -> list[dict]:
        try:
            long = graph(self.client, "GET", "oauth/access_token", params=dict(
                grant_type="fb_exchange_token", client_id=c["client_id"], client_secret=c["client_secret"], fb_exchange_token=user_token))
        except PublishError as e:      # token của Facebook Login for Business có thể đã là loại dài hạn và không đổi được nữa
            log(f"Meta: không đổi được sang token dài hạn ({str(e)[:120]}), dùng token vừa nhận.")
            long = {"access_token": user_token}
        pages = self._pages(long["access_token"])
        if not pages:
            raise PublishError("Token hợp lệ nhưng không thấy Trang Facebook nào. Khi tạo token hãy chọn ít nhất một Trang "
                               "(và tài khoản phải là quản trị viên của Trang), và token cần có quyền pages_show_list.")
        out = []
        for pg in pages:
            fb = dict(platform="facebook", label=pg["name"], access_token=pg["access_token"], page_id=pg["id"])
            store.set_account(f"facebook:{pg['id']}", fb)         # token Trang lấy từ token dài hạn: không hết hạn
            out.append(dict(fb, id=f"facebook:{pg['id']}"))
            if pg["ig_id"]:
                ig = dict(platform="instagram", label=f"@{pg['ig_username'] or pg['ig_id']}", access_token=pg["access_token"],
                          page_id=pg["id"], page_name=pg["name"], ig_id=pg["ig_id"])
                store.set_account(f"instagram:{pg['ig_id']}", ig)
                out.append(dict(ig, id=f"instagram:{pg['ig_id']}"))
        return out

    def _pages(self, user_token: str) -> list[dict]:
        """Danh sách Trang (kèm token Trang). Hỏi kèm tài khoản Instagram liên kết; nếu quyền Instagram chưa được cấp thì hỏi lại không kèm."""
        for with_ig in (True, False):
            fields = "id,name,access_token" + (",instagram_business_account{id,username}" if with_ig else "")
            out, url, params = [], "me/accounts", dict(fields=fields, limit=100, access_token=user_token)
            try:
                while url and len(out) < 500:
                    j = graph(self.client, "GET", url, params=params)
                    for p in j.get("data", []):
                        ig = p.get("instagram_business_account") or {}
                        if p.get("access_token"):
                            out.append(dict(id=p["id"], name=p.get("name", p["id"]), access_token=p["access_token"],
                                            ig_id=ig.get("id", ""), ig_username=ig.get("username", "")))
                    url, params = (j.get("paging") or {}).get("next"), None
                return out
            except PublishError:
                if not with_ig:
                    raise
        return []

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

    def check(self) -> str:
        acc = self.require_connected()
        j = graph(self.client, "GET", acc["page_id"], params={"fields": "name", "access_token": acc["access_token"]})
        return f"Trang “{j.get('name', acc['label'])}” dùng được"

    def compose(self, post: Post) -> dict:
        tags = " ".join("#" + t for t in clean_tags(post.hashtags, 15))
        body = "\n\n".join(p for p in (post.title.strip(), post.description.strip(), tags) if p)
        return dict(description=truncate(body, CAPTION_MAX), title=truncate(post.title, 100))

    def upload(self, video: Path, post: Post, log=print) -> Result:
        acc = self.require_connected()
        tok, pid = acc["access_token"], acc["page_id"]
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
                      "private" if state == "DRAFT" else "public", self.account_id)

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
    default_scopes = IG_SCOPES

    def check(self) -> str:
        acc = self.require_connected()
        j = graph(self.client, "GET", acc["ig_id"], params={"fields": "username", "access_token": acc["access_token"]})
        return f"Instagram @{j.get('username', '')} dùng được"

    def compose(self, post: Post) -> dict:
        tags = " ".join("#" + t for t in clean_tags(post.hashtags, IG_MAX_TAGS))
        body = "\n\n".join(p for p in (post.title.strip(), post.description.strip(), tags) if p)
        return dict(caption=truncate(body, CAPTION_MAX))

    def upload(self, video: Path, post: Post, log=print) -> Result:
        if post.privacy == "private":
            raise PublishError("Instagram không có chế độ riêng tư: chọn “Công khai” nếu muốn đăng Reels thật.")
        acc = self.require_connected()
        ig, tok = acc.get("ig_id"), acc["access_token"]
        if not ig:
            raise PublishError(f"Tài khoản “{acc.get('label', '')}” chưa có Instagram Professional liên kết.")
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
        return Result(self.key, True, mid, url, "", "public", self.account_id)

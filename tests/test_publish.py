from . import _env  # noqa: F401  (phải import trước mọi module của app)

import json
import subprocess
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

import httpx
import imageio_ffmpeg

from app import models
from app.publish import PLATFORMS, base, meta, oauth, service, store, tiktok, youtube
from app.publish.base import Post, PublishError, clean_tags


YT, TT, FB, IG = "youtube:UC1", "tiktok:OP1", "facebook:PG", "instagram:IG"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def fake_video(tmp: Path, name="v.mp4", size=2500) -> Path:
    f = tmp / name
    f.write_bytes(bytes(range(256)) * (size // 256 + 1))
    f.write_bytes(f.read_bytes()[:size])
    return f


class Tmp(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._d.name)
        for k in ("publish_creds", "publish_accounts"):
            models.qsettings().remove(k)
        self.addCleanup(self._d.cleanup)


class TestBase(Tmp):
    def test_clean_tags(self):
        self.assertEqual(clean_tags(["#Truyện ma", "truyện ma", "a-b", "", "  "]), ["Truyệnma", "ab"])
        self.assertEqual(clean_tags("#một #hai, ba#bốn"), ["một", "hai", "ba", "bốn"])
        self.assertEqual(len(clean_tags([f"t{i}" for i in range(50)], 30)), 30)

    def test_oauth_loopback_roundtrip(self):
        uri = "http://127.0.0.1:53691/callback"

        def opener(url):
            def go():
                time.sleep(0.3)
                httpx.get(uri, params={"code": "abc", "state": "S1"})
            threading.Thread(target=go).start()
        self.assertEqual(oauth.wait_for_code(uri, "S1", "x", opener=opener, timeout=10), "abc")

    def test_oauth_state_mismatch_and_denied(self):
        uri = "http://127.0.0.1:53692/callback"

        def hit(params):
            def opener(url):
                threading.Thread(target=lambda: (time.sleep(0.3), httpx.get(uri, params=params))).start()
            return opener
        with self.assertRaisesRegex(RuntimeError, "state"):
            oauth.wait_for_code(uri, "S1", "x", opener=hit({"code": "abc", "state": "EVIL"}), timeout=10)
        with self.assertRaisesRegex(RuntimeError, "thất bại"):
            oauth.wait_for_code(uri, "S1", "x", opener=hit({"error": "access_denied", "state": "S1"}), timeout=10)

    def test_oauth_rejects_non_loopback(self):
        with self.assertRaises(RuntimeError):
            oauth.wait_for_code("https://evil.example/cb", "s", "x")

    def test_pkce_hex(self):
        v, c = oauth.pkce_pair(hex_challenge=True)
        import hashlib
        self.assertEqual(c, hashlib.sha256(v.encode()).hexdigest())
        self.assertTrue(43 <= len(v) <= 128)


class TestYouTube(Tmp):
    def test_compose_shorts_and_limits(self):
        yt = youtube.YouTube(YT)
        c = yt.compose(Post("T" * 150 + "<b>", "mô tả", ["a", "b"], vertical=True, duration=60))
        self.assertLessEqual(len(c["title"]), 100)
        self.assertNotIn("<", c["title"])
        self.assertIn("#Shorts", c["description"])
        self.assertIn("#a #b", c["description"])
        self.assertNotIn("#Shorts", yt.compose(Post("t", "d", [], vertical=False, duration=60))["description"])
        self.assertNotIn("#Shorts", yt.compose(Post("t", "d", [], vertical=True, duration=600))["description"])

    def test_resumable_upload_chunks(self):
        old = youtube.CHUNK
        youtube.CHUNK = 1000
        self.addCleanup(setattr, youtube, "CHUNK", old)
        store.set_creds("youtube", {"client_id": "i", "client_secret": "s"})
        store.set_account(YT, {"platform": "youtube", "label": "Kênh A", "access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3600})
        video = fake_video(self.tmp)
        seen = []

        def h(req: httpx.Request):
            if req.url.path == "/upload/youtube/v3/videos":
                body = json.loads(req.content)
                self.assertEqual(body["status"]["privacyStatus"], "private")
                self.assertEqual(req.headers["authorization"], "Bearer AT")
                self.assertEqual(req.headers["x-upload-content-length"], "2500")
                return httpx.Response(200, headers={"Location": "https://up.example/session"})
            seen.append((req.headers["content-range"], len(req.content)))
            if req.headers["content-range"].endswith("2499/2500"):
                return httpx.Response(200, json={"id": "VID", "status": {"privacyStatus": "private"}})
            return httpx.Response(308)
        r = youtube.YouTube(YT, client(h)).upload(video, Post("t", "d", ["x"]))
        self.assertEqual(seen, [("bytes 0-999/2500", 1000), ("bytes 1000-1999/2500", 1000), ("bytes 2000-2499/2500", 500)])
        self.assertTrue(r.ok)
        self.assertEqual(r.url, "https://youtu.be/VID")
        self.assertEqual(r.message, "")

    def test_unverified_project_note(self):
        store.set_account(YT, {"platform": "youtube", "label": "Kênh A", "access_token": "AT", "expires_at": time.time() + 3600})

        def h(req):
            if "upload/youtube" in str(req.url):
                return httpx.Response(200, headers={"Location": "https://up.example/s"})
            return httpx.Response(200, json={"id": "V", "status": {"privacyStatus": "private"}})
        r = youtube.YouTube(YT, client(h)).upload(fake_video(self.tmp, size=100), Post("t", privacy="public"))
        self.assertIn("giữ ở chế độ", r.message)

    def test_refresh_token(self):
        store.set_creds("youtube", {"client_id": "i", "client_secret": "s"})
        store.set_account(YT, {"platform": "youtube", "label": "Kênh A", "access_token": "OLD", "refresh_token": "RT", "expires_at": 0})

        def h(req):
            form = dict(urllib.parse.parse_qsl(req.content.decode()))
            self.assertEqual(form["grant_type"], "refresh_token")
            return httpx.Response(200, json={"access_token": "NEW", "expires_in": 3600})
        self.assertEqual(youtube.YouTube(YT, client(h)).access_token(), "NEW")
        self.assertEqual(store.get_account(YT)["refresh_token"], "RT")      # giữ refresh token cũ

    def test_revoked_refresh_clears_token(self):
        store.set_creds("youtube", {"client_id": "i", "client_secret": "s"})
        store.set_account(YT, {"platform": "youtube", "label": "Kênh A", "access_token": "OLD", "refresh_token": "RT", "expires_at": 0})
        h = lambda req: httpx.Response(400, json={"error": "invalid_grant"})  # noqa: E731
        with self.assertRaisesRegex(PublishError, "kết nối lại"):
            youtube.YouTube(YT, client(h)).access_token()
        self.assertFalse(store.get_account(YT))

    def test_not_connected(self):
        with self.assertRaisesRegex(PublishError, "chưa kết nối"):
            youtube.YouTube(YT).upload(fake_video(self.tmp), Post("t"))


class TestTikTok(Tmp):
    def setUp(self):
        super().setUp()
        store.set_creds("tiktok", {"client_id": "ck", "client_secret": "cs"})
        store.set_account(TT, {"platform": "tiktok", "label": "T", "access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 9999})
        old = tiktok.CHUNK
        tiktok.CHUNK = 1000
        self.addCleanup(setattr, tiktok, "CHUNK", old)

    def handler(self, privacy_options, puts, inits, status="PUBLISH_COMPLETE"):
        def h(req: httpx.Request):
            p = req.url.path
            if p.endswith("creator_info/query/"):
                return httpx.Response(200, json={"data": {"privacy_level_options": privacy_options, "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
            if p.endswith("video/init/"):
                inits.append(json.loads(req.content))
                return httpx.Response(200, json={"data": {"publish_id": "P1", "upload_url": "https://up.tiktok.example/u"}, "error": {"code": "ok"}})
            if req.url.host == "up.tiktok.example":
                puts.append((req.headers["content-range"], len(req.content)))
                return httpx.Response(201 if req.headers["content-range"].endswith("2499/2500") else 206)
            if p.endswith("status/fetch/"):
                return httpx.Response(200, json={"data": {"status": status, "publicaly_available_post_id": ["777"], "fail_reason": "bad"}, "error": {"code": "ok"}})
            raise AssertionError(p)
        return h

    def test_chunking_last_chunk_takes_remainder(self):
        puts, inits = [], []
        r = tiktok.TikTok(TT, client(self.handler(["SELF_ONLY", "PUBLIC_TO_EVERYONE"], puts, inits))).upload(
            fake_video(self.tmp), Post("Tiêu đề", hashtags=["a", "b"], privacy="public", duration=30))
        self.assertEqual(inits[0]["source_info"], {"source": "FILE_UPLOAD", "video_size": 2500, "chunk_size": 1000, "total_chunk_count": 2})
        self.assertEqual(puts, [("bytes 0-999/2500", 1000), ("bytes 1000-2499/2500", 1500)])
        self.assertEqual(inits[0]["post_info"]["privacy_level"], "PUBLIC_TO_EVERYONE")
        self.assertEqual(inits[0]["post_info"]["title"], "Tiêu đề\n#a #b")
        self.assertEqual((r.ok, r.post_id), (True, "777"))

    def test_small_video_single_chunk(self):
        puts, inits = [], []
        tiktok.TikTok(TT, client(self.handler(["SELF_ONLY"], puts, inits))).upload(fake_video(self.tmp, size=500), Post("t"))
        self.assertEqual(inits[0]["source_info"]["total_chunk_count"], 1)
        self.assertEqual(inits[0]["source_info"]["chunk_size"], 500)

    def test_privacy_falls_back_to_self_only(self):
        puts, inits = [], []
        r = tiktok.TikTok(TT, client(self.handler(["SELF_ONLY"], puts, inits))).upload(fake_video(self.tmp), Post("t", privacy="public"))
        self.assertEqual(inits[0]["post_info"]["privacy_level"], "SELF_ONLY")
        self.assertEqual(r.privacy, "SELF_ONLY")
        self.assertIn("riêng tư", r.message)

    def test_failed_status(self):
        with self.assertRaisesRegex(PublishError, "bad"):
            tiktok.TikTok(TT, client(self.handler(["SELF_ONLY"], [], [], status="FAILED"))).upload(fake_video(self.tmp), Post("t"))

    def test_api_error_envelope(self):
        h = lambda req: httpx.Response(200, json={"data": {}, "error": {"code": "spam_risk_too_many_posts", "message": "m"}})  # noqa: E731
        with self.assertRaisesRegex(PublishError, "quá số bài"):
            tiktok.TikTok(TT, client(h)).upload(fake_video(self.tmp), Post("t"))

    def test_duration_over_limit(self):
        puts, inits = [], []
        with self.assertRaisesRegex(PublishError, "tối đa 600s"):
            tiktok.TikTok(TT, client(self.handler(["SELF_ONLY"], puts, inits))).upload(fake_video(self.tmp), Post("t", duration=900))

    def test_caption_limit(self):
        self.assertLessEqual(len(tiktok.TikTok(TT).compose(Post("x" * 5000, hashtags=["a"]))["title"]), 2200)


class TestMeta(Tmp):
    def setUp(self):
        super().setUp()
        store.set_creds("meta", {"client_id": "a", "client_secret": "s"})
        store.set_account(FB, {"platform": "facebook", "label": "Trang", "access_token": "PT", "page_id": "PG"})
        store.set_account(IG, {"platform": "instagram", "label": "@u", "access_token": "PT", "page_id": "PG", "ig_id": "IG"})

    def test_facebook_reels_flow(self):
        calls = []

        def h(req: httpx.Request):
            form = dict(urllib.parse.parse_qsl(req.content.decode())) if req.headers.get("content-type", "").startswith("application/x-www") else {}
            calls.append((req.url.host, req.url.path, form.get("upload_phase"), form.get("video_state")))
            if req.url.path.endswith("/PG/video_reels") and form["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/v21.0/V1"})
            if req.url.host == "rupload.facebook.com":
                self.assertEqual(req.headers["authorization"], "OAuth PT")
                self.assertEqual((req.headers["offset"], req.headers["file_size"], len(req.content)), ("0", "2500", 2500))
                return httpx.Response(200, json={"success": True})
            if form.get("upload_phase") == "finish":
                self.assertIn("Tiêu đề", form["description"])
                self.assertIn("#a", form["description"])
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"status": {"video_status": "ready"}})
        r = meta.FacebookReels(FB, client(h), sleep=lambda s: None).upload(fake_video(self.tmp), Post("Tiêu đề", "mô tả", ["a"], privacy="public"))
        self.assertEqual(r.url, "https://www.facebook.com/reel/V1")
        self.assertEqual([c[2] for c in calls if c[2]], ["start", "finish"])
        self.assertEqual([c[3] for c in calls if c[3]], ["PUBLISHED"])

    def test_facebook_private_is_draft(self):
        def h(req):
            form = dict(urllib.parse.parse_qsl(req.content.decode())) if req.headers.get("content-type", "").startswith("application/x-www") else {}
            if form.get("upload_phase") == "start":
                return httpx.Response(200, json={"video_id": "V1", "upload_url": "https://rupload.facebook.com/x"})
            if form.get("upload_phase") == "finish":
                self.assertEqual(form["video_state"], "DRAFT")
            return httpx.Response(200, json={"success": True, "status": {"video_status": "ready"}})
        r = meta.FacebookReels(FB, client(h), sleep=lambda s: None).upload(fake_video(self.tmp), Post("t", privacy="private"))
        self.assertEqual((r.url, r.privacy), ("", "private"))
        self.assertIn("BẢN NHÁP", r.message)

    def test_instagram_reels_flow_waits_for_finished(self):
        polls = []

        def h(req: httpx.Request):
            form = dict(urllib.parse.parse_qsl(req.content.decode())) if req.headers.get("content-type", "").startswith("application/x-www") else {}
            if req.url.path.endswith("/IG/media"):
                self.assertEqual((form["media_type"], form["upload_type"]), ("REELS", "resumable"))
                return httpx.Response(200, json={"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v21.0/C1"})
            if req.url.host == "rupload.facebook.com":
                return httpx.Response(200, json={"success": True})
            if req.url.path.endswith("/C1"):
                polls.append(1)
                return httpx.Response(200, json={"status_code": "FINISHED" if len(polls) >= 3 else "IN_PROGRESS"})
            if req.url.path.endswith("/IG/media_publish"):
                self.assertEqual(form["creation_id"], "C1")
                self.assertEqual(len(polls), 3)           # chỉ đăng sau khi xử lý xong
                return httpx.Response(200, json={"id": "M1"})
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/xyz"})
        r = meta.InstagramReels(IG, client(h), sleep=lambda s: None).upload(fake_video(self.tmp), Post("t", privacy="public", hashtags=["a"]))
        self.assertEqual(r.url, "https://instagram.com/reel/xyz")

    def test_instagram_refuses_private(self):
        with self.assertRaisesRegex(PublishError, "riêng tư"):
            meta.InstagramReels(IG).upload(fake_video(self.tmp), Post("t", privacy="private"))

    def test_instagram_needs_linked_account(self):
        a = store.get_account(IG)
        a["ig_id"] = ""
        store.set_account(IG, a)
        with self.assertRaisesRegex(PublishError, "chưa có Instagram"):
            meta.InstagramReels(IG).upload(fake_video(self.tmp), Post("t", privacy="public"))

    def test_graph_error_translated(self):
        h = lambda req: httpx.Response(400, json={"error": {"message": "Invalid OAuth", "code": 190}})  # noqa: E731
        with self.assertRaisesRegex(PublishError, "kết nối lại"):
            meta.graph(client(h), "GET", "me")

    def test_instagram_hashtag_cap(self):
        c = meta.InstagramReels(IG).compose(Post("t", hashtags=[f"t{i}" for i in range(60)]))
        self.assertLessEqual(c["caption"].count("#"), 30)

    def test_disconnect_removes_only_that_account(self):
        meta.FacebookReels(FB).disconnect()
        self.assertFalse(meta.FacebookReels(FB).is_connected())
        self.assertTrue(meta.InstagramReels(IG).is_connected())

    def test_removed_account_cannot_upload(self):
        meta.FacebookReels(FB).disconnect()
        with self.assertRaisesRegex(PublishError, "chưa kết nối"):
            meta.FacebookReels(FB).upload(fake_video(self.tmp), Post("t"))

    def test_connect_creates_account_per_page_and_instagram(self):
        old = oauth.wait_for_code
        oauth_code = lambda *a, **k: "CODE"  # noqa: E731
        meta.oauth.wait_for_code = oauth_code
        self.addCleanup(setattr, meta.oauth, "wait_for_code", old)
        for k in list(store._load("publish_accounts")):
            store.remove_account(k)

        def h(req: httpx.Request):
            if req.url.path.endswith("/me/accounts"):
                return httpx.Response(200, json={"data": [
                    {"id": "P1", "name": "Trang Một", "access_token": "T1", "instagram_business_account": {"id": "I1", "username": "mot"}},
                    {"id": "P2", "name": "Trang Hai", "access_token": "T2"}]})
            return httpx.Response(200, json={"access_token": "USER", "expires_in": 5000000})
        out = meta.FacebookReels("", client(h)).connect()
        self.assertEqual(sorted(a["id"] for a in out), ["facebook:P1", "facebook:P2", "instagram:I1"])
        self.assertEqual(store.get_account("facebook:P2")["access_token"], "T2")      # mỗi Trang giữ token riêng
        self.assertEqual(store.get_account("instagram:I1")["label"], "@mot")
        meta.FacebookReels("", client(h)).connect()                                     # kết nối lại không tạo bản trùng
        self.assertEqual(len(store.list_accounts()), 3)


class TestAccounts(Tmp):
    def test_two_youtube_accounts_are_independent(self):
        store.set_creds("youtube", {"client_id": "i", "client_secret": "s"})
        for acc, tok in (("youtube:A", "TOKEN_A"), ("youtube:B", "TOKEN_B")):
            store.set_account(acc, {"platform": "youtube", "label": acc, "access_token": tok, "expires_at": time.time() + 3600})
        used = []

        def h(req):
            if "upload/youtube" in str(req.url):
                used.append(req.headers["authorization"])
                return httpx.Response(200, headers={"Location": "https://up.example/s"})
            return httpx.Response(200, json={"id": "V", "status": {"privacyStatus": "private"}})
        v = fake_video(self.tmp, size=100)
        ra = youtube.YouTube("youtube:A", client(h)).upload(v, Post("t"))
        rb = youtube.YouTube("youtube:B", client(h)).upload(v, Post("t"))
        self.assertEqual(used, ["Bearer TOKEN_A", "Bearer TOKEN_B"])
        self.assertEqual((ra.account, rb.account), ("youtube:A", "youtube:B"))

    def test_youtube_connect_adds_account_keyed_by_channel(self):
        store.set_creds("youtube", {"client_id": "i", "client_secret": "s"})
        old = youtube.oauth.wait_for_code
        youtube.oauth.wait_for_code = lambda *a, **k: "CODE"
        self.addCleanup(setattr, youtube.oauth, "wait_for_code", old)

        def h(req):
            if "oauth2" in req.url.host or req.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
            return httpx.Response(200, json={"items": [{"id": "UCxyz", "snippet": {"title": "Kênh Truyện"}}]})
        youtube.YouTube("", client(h)).connect()
        youtube.YouTube("", client(h)).connect()
        accs = store.list_accounts("youtube")
        self.assertEqual([(a["id"], a["label"]) for a in accs], [("youtube:UCxyz", "Kênh Truyện")])

    def test_account_name_and_listing(self):
        from app import publish
        store.set_account("tiktok:1", {"platform": "tiktok", "label": "Bé Na", "access_token": "x"})
        store.set_account("youtube:1", {"platform": "youtube", "label": "Kênh", "access_token": "x"})
        self.assertEqual(publish.account_name("tiktok:1"), "TikTok · Bé Na")
        self.assertIn("đã xoá", publish.account_name("tiktok:gone"))
        self.assertEqual([a["id"] for a in publish.accounts()], ["tiktok:1", "youtube:1"])


class FakePlatform(base.Platform):
    key, label = "fake", "Giả"
    sent: list = []
    fail = False
    fail_accounts: set = set()

    def is_connected(self):
        return True

    def upload(self, video, post, log=print):
        if self.fail or self.account_id in self.fail_accounts:
            raise PublishError("hỏng")
        FakePlatform.sent.append((video, post, self.account_id))
        return base.Result(self.key, True, "id1", "https://x/1", "", post.privacy, self.account_id)


def make_clip(path: Path, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"color=c={color}:s=180x320:d=1:r=15", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   capture_output=True, check=True)


class TestService(Tmp):
    def setUp(self):
        super().setUp()
        models.DATA_DIR = self.tmp
        models.PROJ_DIR = self.tmp / "projects"
        PLATFORMS["fake"] = FakePlatform
        self.addCleanup(PLATFORMS.pop, "fake", None)
        FakePlatform.sent, FakePlatform.fail, FakePlatform.fail_accounts = [], False, set()
        p = models.Project("Truyện thử")
        for i in range(2):
            ch = p.new_chapter()
            ch.post_meta = {"title": f"Tập {i + 1}", "description": "d", "hashtags": ["x"]}
            for k in (1, 2):
                clip = p.chapter_dir(ch) / "clips" / f"scene_{k:02d}.mp4"
                make_clip(clip)
                ch.scenes.append(models.Scene(k, clip=str(clip), raw_clip=str(clip), status="done"))
        p.save()
        self.p = p

    def test_end_to_end_merge_and_publish_per_chapter(self):
        logs = []
        res = service.run(self.p, service.targets(self.p), ["fake:1"], "unlisted", logs.append)
        self.assertEqual([r.ok for r in res], [True, True])
        self.assertEqual(len(FakePlatform.sent), 2)
        video, post, _ = FakePlatform.sent[0]
        self.assertTrue(video.exists() and video.stat().st_size > 0)
        self.assertEqual((post.title, post.privacy, post.vertical), ("Tập 1", "unlisted", True))
        self.assertGreater(post.duration, 1.5)             # 2 clip × 1 giây
        self.assertEqual(len(self.p.publish_history), 2)
        # lần 2: đã đăng thì bỏ qua, không đăng trùng
        service.run(self.p, service.targets(self.p), ["fake:1"], "unlisted", logs.append)
        self.assertEqual(len(FakePlatform.sent), 2)
        self.assertTrue(any("bỏ qua" in m for m in logs))
        service.run(self.p, service.targets(self.p)[:1], ["fake:1"], "unlisted", logs.append, force=True)
        self.assertEqual(len(FakePlatform.sent), 3)
        # lịch sử lưu xuống đĩa
        self.assertEqual(len(models.Project.load("Truyện thử").publish_history), 3)

    def test_several_accounts_one_failing_does_not_block_others(self):
        FakePlatform.fail_accounts = {"fake:2"}
        t = service.targets(self.p)[:1]
        res = service.run(self.p, t, ["fake:1", "fake:2", "fake:3"], "private")
        self.assertEqual([(r.account, r.ok) for r in res], [("fake:1", True), ("fake:2", False), ("fake:3", True)])
        self.assertEqual([a for *_, a in FakePlatform.sent], ["fake:1", "fake:3"])
        # lần sau chỉ thử lại tài khoản đã lỗi
        FakePlatform.fail_accounts = set()
        service.run(self.p, t, ["fake:1", "fake:2", "fake:3"], "private")
        self.assertEqual([a for *_, a in FakePlatform.sent], ["fake:1", "fake:3", "fake:2"])

    def test_whole_project_video(self):
        self.p.publish_scope = "project"
        self.p.post_meta = {"title": "Cả bộ", "hashtags": []}
        service.run(self.p, service.targets(self.p), ["fake:1"], "private")
        video, post, _ = FakePlatform.sent[0]
        self.assertEqual(video, self.p.full_path)
        self.assertGreater(post.duration, 3.5)

    def test_failure_isolated_and_recorded(self):
        FakePlatform.fail = True
        res = service.run(self.p, service.targets(self.p), ["fake:1"], "private")
        self.assertEqual([r.ok for r in res], [False, False])
        self.assertEqual(res[0].message, "hỏng")
        self.assertFalse(service.history_ok(self.p, service.targets(self.p)[0], "fake:1"))   # lỗi thì lần sau được thử lại

    def test_incomplete_chapter_is_skipped_not_fatal(self):
        self.p.chapters[0].scenes[1].status = "error"
        res = service.run(self.p, service.targets(self.p), ["fake:1"], "private")
        self.assertEqual([r.ok for r in res], [False, True])
        self.assertIn("chưa gen xong", res[0].message)

    def test_stale_video_is_remerged(self):
        t = service.targets(self.p)[0]
        service.ensure_video(self.p, t)
        self.assertFalse(service.is_stale(self.p, t))
        time.sleep(0.05)
        Path(self.p.chapters[0].scenes[0].clip).touch()
        self.assertTrue(service.is_stale(self.p, t))

    def test_metadata_generated_when_missing(self):
        from unittest import mock
        self.p.chapters[0].post_meta = {}
        gen = {"title": "AI", "description": "d", "hashtags": ["h"]}
        with mock.patch("app.publish.describe.generate", return_value=gen):
            service.run(self.p, service.targets(self.p)[:1], ["fake:1"], "private")
        self.assertEqual(FakePlatform.sent[0][1].title, "AI")
        self.assertEqual(self.p.chapters[0].post_meta["title"], "AI")

    def test_no_platform_selected(self):
        with self.assertRaises(PublishError):
            service.run(self.p, service.targets(self.p), [], "private")

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        self.assertEqual(service.run(self.p, service.targets(self.p), ["fake:1"], "private", cancel=ev), [])


class TestDescribe(Tmp):
    def test_prompt_and_normalize(self):
        from app.publish import describe
        p = models.Project("Truyện", synopsis="Tóm tắt")
        ch = p.new_chapter()
        ch.scenes.append(models.Scene(1, narration="Lời dẫn một"))
        pr = describe.build_prompt(p, ch)
        self.assertIn("Lời dẫn một", pr)
        self.assertIn("Vietnamese", pr)
        out = describe.normalize({"title": '"Tựa" ' + "x" * 200, "description": " d ", "hashtags": ["#A b", "a", "Bb"] + ["z"] * 30})
        self.assertLessEqual(len(out["title"]), 100)
        self.assertEqual(out["hashtags"][:3], ["Ab", "a", "Bb"])
        self.assertLessEqual(len(out["hashtags"]), describe.MAX_TAGS)
        self.assertIn("Truyện", describe.build_prompt(p, None))


if __name__ == "__main__":
    unittest.main()


class TestThumbnail(Tmp):
    def test_thumbnail_and_duration(self):
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        p = models.Project("Ảnh")
        ch = p.new_chapter()
        for k in (1, 2):
            clip = p.chapter_dir(ch) / "clips" / f"s{k}.mp4"
            make_clip(clip)
            ch.scenes.append(models.Scene(k, clip=str(clip), status="done"))
        t = service.targets(p)[0]
        img = service.thumbnail(p, t)
        self.assertTrue(img and img.exists() and img.stat().st_size > 0)
        self.assertEqual(service.thumbnail(p, t), img)                      # dùng lại ảnh đã tạo
        self.assertAlmostEqual(service.video_seconds(p, t), 2.0, delta=0.3)  # chưa ghép: cộng thời lượng clip
        self.assertGreater(service.created_at(p, t), 0)
        ch.scenes[1].status = "error"
        self.assertTrue(service.thumbnail(p, t))                             # chỉ cần clip đầu


class TestCheck(Tmp):
    def test_youtube_check_ok_and_no_channel(self):
        store.set_account(YT, {"platform": "youtube", "label": "K", "access_token": "AT", "expires_at": time.time() + 3600})
        ok = lambda req: httpx.Response(200, json={"items": [{"id": "UC", "snippet": {"title": "Kênh Thử"}}]})  # noqa: E731
        self.assertIn("Kênh Thử", youtube.YouTube(YT, client(ok)).check())
        empty = lambda req: httpx.Response(200, json={"items": []})  # noqa: E731
        with self.assertRaisesRegex(PublishError, "chưa có kênh"):
            youtube.YouTube(YT, client(empty)).check()

    def test_tiktok_check(self):
        store.set_account(TT, {"platform": "tiktok", "label": "T", "access_token": "AT", "expires_at": time.time() + 9999})
        h = lambda req: httpx.Response(200, json={"data": {"user": {"display_name": "Bé Na"}}, "error": {"code": "ok"}})  # noqa: E731
        self.assertIn("Bé Na", tiktok.TikTok(TT, client(h)).check())
        bad = lambda req: httpx.Response(200, json={"data": {}, "error": {"code": "access_token_invalid", "message": "x"}})  # noqa: E731
        with self.assertRaisesRegex(PublishError, "kết nối lại"):
            tiktok.TikTok(TT, client(bad)).check()

    def test_meta_check(self):
        store.set_account(FB, {"platform": "facebook", "label": "Trang", "access_token": "PT", "page_id": "PG"})
        store.set_account(IG, {"platform": "instagram", "label": "@u", "access_token": "PT", "page_id": "PG", "ig_id": "IG"})
        h = lambda req: httpx.Response(200, json={"name": "Trang Một", "username": "mot"})  # noqa: E731
        self.assertIn("Trang Một", meta.FacebookReels(FB, client(h)).check())
        self.assertIn("@mot", meta.InstagramReels(IG, client(h)).check())
        bad = lambda req: httpx.Response(400, json={"error": {"message": "Invalid", "code": 190}})  # noqa: E731
        with self.assertRaisesRegex(PublishError, "kết nối lại"):
            meta.FacebookReels(FB, client(bad)).check()

    def test_disconnected_account_check_fails(self):
        with self.assertRaisesRegex(PublishError, "chưa kết nối"):
            youtube.YouTube("youtube:gone").check()


class TestMetaConnect(Tmp):
    def _run(self, plat, creds, pages_handler=None):
        """Chạy connect() với đăng nhập giả; trả (URL hộp thoại đăng nhập, danh sách tài khoản, các lần gọi me/accounts)."""
        store.set_creds("meta", creds)
        seen = {}
        calls = []
        old = meta.oauth.wait_for_code
        meta.oauth.wait_for_code = lambda redirect, state, url, *a, **k: (seen.setdefault("url", url), "CODE")[1]
        self.addCleanup(setattr, meta.oauth, "wait_for_code", old)

        def h(req: httpx.Request):
            if req.url.path.endswith("/me/accounts"):
                calls.append(req.url.params.get("fields"))
                return (pages_handler or (lambda r: httpx.Response(200, json={"data": [{"id": "P1", "name": "Trang Một", "access_token": "T1"}]})))(req)
            return httpx.Response(200, json={"access_token": "USER", "expires_in": 5000000})
        out = plat("", client(h)).connect()
        return urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query), out, calls

    def test_facebook_tab_asks_only_page_permissions(self):
        q, out, _ = self._run(meta.FacebookReels, {"client_id": "a", "client_secret": "s"})
        scopes = q["scope"][0].split(",")
        self.assertEqual(scopes, ["pages_show_list", "pages_manage_posts"])      # chỉ quyền tối thiểu: quyền app chưa có sẽ làm Facebook báo Invalid Scopes
        self.assertNotIn("instagram_content_publish", scopes)        # không đòi quyền Instagram khi chỉ muốn Facebook
        self.assertNotIn("business_management", scopes)
        self.assertNotIn("config_id", q)
        self.assertEqual([a["id"] for a in out], ["facebook:P1"])

    def test_instagram_tab_asks_instagram_permissions_too(self):
        q, _, _ = self._run(meta.InstagramReels, {"client_id": "a", "client_secret": "s"})
        self.assertIn("instagram_content_publish", q["scope"][0].split(","))

    def test_custom_scopes_override(self):
        q, _, _ = self._run(meta.FacebookReels, {"client_id": "a", "client_secret": "s", "scopes": "pages_show_list,pages_manage_posts"})
        self.assertEqual(q["scope"][0], "pages_show_list,pages_manage_posts")

    def test_config_id_used_instead_of_scope_for_login_for_business(self):
        q, _, _ = self._run(meta.FacebookReels, {"client_id": "a", "client_secret": "s", "config_id": " 999 "})
        self.assertEqual(q["config_id"], ["999"])
        self.assertNotIn("scope", q)
        self.assertEqual(q["override_default_response_type"], ["true"])

    def test_pages_retried_without_instagram_field_when_permission_missing(self):
        def handler(req):
            if "instagram_business_account" in req.url.params.get("fields", ""):
                return httpx.Response(400, json={"error": {"message": "(#10) Requires instagram_basic", "code": 10}})
            return httpx.Response(200, json={"data": [{"id": "P1", "name": "Trang Một", "access_token": "T1"}]})
        _, out, calls = self._run(meta.FacebookReels, {"client_id": "a", "client_secret": "s"}, handler)
        self.assertEqual(len(calls), 2)
        self.assertEqual([a["id"] for a in out], ["facebook:P1"])

    def test_no_pages_gives_actionable_message(self):
        with self.assertRaisesRegex(PublishError, "không thấy Trang"):
            self._run(meta.FacebookReels, {"client_id": "a", "client_secret": "s"}, lambda r: httpx.Response(200, json={"data": []}))

    def test_login_timeout_message_carries_troubleshooting_hint(self):
        with self.assertRaisesRegex(RuntimeError, "Valid OAuth Redirect URIs"):
            oauth.wait_for_code("http://127.0.0.1:53697/callback", "S", "x", opener=lambda u: None, timeout=1, hint="… Valid OAuth Redirect URIs …")

    def test_cancel_login_stops_waiting_immediately(self):
        def opener(url):
            threading.Thread(target=lambda: (time.sleep(0.4), oauth.cancel_login())).start()
        t0 = time.time()
        with self.assertRaisesRegex(RuntimeError, "huỷ chờ"):
            oauth.wait_for_code("http://127.0.0.1:53698/callback", "S", "x", opener=opener, timeout=30)
        self.assertLess(time.time() - t0, 5)

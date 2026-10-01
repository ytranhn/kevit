"""Tự động hoá Google Flow qua Chrome thật (CDP localhost:9222) của người dùng.
Chỉ thao tác trên tab flow.google.com, không chạm tab khác. Gặp CAPTCHA/đăng nhập thì dừng báo lỗi."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import credits
from . import flow_selectors as S
from .models import DATA_DIR, Character, Project, Scene
from .veo_client import build_prompt

PROFILE_DIR = DATA_DIR / "flow_profile"
FLOW_URL = "https://labs.google/fx/tools/flow"
DOWNLOAD_DIR = DATA_DIR / "flow_downloads"      # nơi Chrome Flow tự lưu file tải, không bật hộp thoại chọn chỗ lưu


ERROR_RE = r"(lỗi|không thành công|thất bại|failed)"
OVERLOAD_RE = r"(high demand|nhu cầu cao|quá tải|retried at a later time|try again later)"


def _ranges(nums: list[int]) -> str:
    """[2,3,4,7,9,10] -> 'scene 2-4, 7, 9-10'."""
    nums, out, i = sorted(nums), [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return "scene " + ", ".join(out)


class FlowError(RuntimeError):
    pass


def _cdp_up() -> bool:
    try:
        urllib.request.urlopen(S.CDP_URL + "/json/version", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def _chrome_exe() -> str | None:
    """Đường dẫn Chrome thật trên Windows/Linux (macOS dùng `open -a`)."""
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for r in filter(None, roots):
            f = Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if f.exists():
                return str(f)
        return shutil.which("chrome") or shutil.which("chrome.exe")
    return next(filter(None, (shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))), None)


def chrome_command() -> list[str]:
    """Lệnh mở Chrome BÌNH THƯỜNG (không qua Playwright) với profile riêng + cổng debug."""
    flags = [f"--remote-debugging-port={S.CDP_URL.rsplit(':', 1)[-1]}", f"--user-data-dir={PROFILE_DIR}",
             "--no-first-run", FLOW_URL]
    if sys.platform == "darwin":
        return ["open", "-na", "Google Chrome", "--args", *flags]
    exe = _chrome_exe()
    if not exe:
        raise FlowError("Không tìm thấy Google Chrome. Hãy cài Chrome rồi thử lại.")
    return [exe, *flags]


FLOW_ORIGINS = ("https://flow.google.com:443,*", "https://labs.google:443,*")


def seed_download_prefs(profile: Path | None = None) -> bool:
    """Cho phép tải nhiều file tự động cho riêng flow.google.com / labs.google trong profile Chrome của tool, và tắt hỏi nơi lưu,
    để Chrome không bật popup 'trang web muốn tải xuống nhiều tệp' mỗi lần tool tải clip. Chỉ ghi khi Chrome CHƯA chạy bằng profile này
    (Chrome ghi đè file khi thoát). Trả True nếu đã ghi."""
    prof = Path(profile or PROFILE_DIR)
    f = prof / "Default" / "Preferences"
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        ex = d.setdefault("profile", {}).setdefault("content_settings", {}).setdefault("exceptions", {}).setdefault("automatic_downloads", {})
        for o in FLOW_ORIGINS:
            ex[o] = {"setting": 1}
        dl = d.setdefault("download", {})
        dl["prompt_for_download"] = False
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dl["default_directory"] = str(DOWNLOAD_DIR)
        d.setdefault("savefile", {})["default_directory"] = str(DOWNLOAD_DIR)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 - tuỳ chọn tiện lợi, không để làm hỏng việc mở Chrome
        return False


def launch_chrome() -> None:
    if not _cdp_up():
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        seed_download_prefs()
        subprocess.Popen(chrome_command())
        for _ in range(20):
            if _cdp_up():
                return
            time.sleep(1)
        raise FlowError("Không mở được Chrome (cổng 9222). Nếu Chrome đang mở sẵn, hãy đóng hết cửa sổ Chrome rồi bấm lại.")


class FlowAuto:
    def __init__(self, log=print, dry_run: bool = False):
        self.log, self.dry_run = log, dry_run
        self.pw = self.browser = self.page = None

    # ---- kết nối ----
    def __enter__(self):
        launch_chrome()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(S.CDP_URL)
        ctx = self.browser.contexts[0]
        try:   # trang không được gọi hộp thoại lưu file của hệ điều hành: buộc dùng đường tải xuống thường để tool nhận file
            ctx.add_init_script("try { delete window.showSaveFilePicker; } catch (e) {} "
                                "try { Object.defineProperty(window, 'showSaveFilePicker', {value: undefined, configurable: true}); } catch (e) {}")
        except Exception as e:  # noqa: BLE001
            self.log(f"Không đặt được chặn hộp thoại lưu file: {e}")
        self.page = next((p for p in ctx.pages if "flow.google.com" in p.url), None) or ctx.new_page()
        self.page.set_default_timeout(30000)
        return self

    def __exit__(self, *a):
        if self.pw:
            self.pw.stop()  # chỉ ngắt kết nối, không đóng Chrome

    def _check_login(self):
        u = self.page.url
        if "accounts.google.com" in u or "signin" in u:
            raise FlowError("Chrome chưa đăng nhập Google Flow. Đăng nhập trong cửa sổ Chrome rồi chạy lại.")

    def _goto(self, url: str):
        self.page.keyboard.press("Escape")
        self.page.goto(url)
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(3000)
        self._check_login()

    # ---- dự án ----
    def ensure_project(self, p: Project) -> str:
        pg = self.page
        if p.flow_project_url:
            self._goto(p.flow_project_url)
            if "/project/" in pg.url and pg.locator(S.PROMPT_EDITOR).count():
                self.log(f"Dùng lại project Flow: {p.name}")
                return p.flow_project_url
            self.log("Project Flow đã lưu không còn truy cập được, tìm lại theo tên...")
        self._goto(S.HOME_URL)
        for link in pg.get_by_role("link", name=S.LINK_OPEN_PROJECT).all():
            card = link.locator(f"xpath=ancestor::*[.//button[@aria-label='{S.BTN_EDIT_TITLE}']][1]")
            if card.count() and p.name in card.first.inner_text():
                url = "https://flow.google.com" + link.get_attribute("href")
                self._goto(url)
                p.flow_project_url = url
                p.save()
                self.log(f"Tìm thấy project Flow cùng tên: {p.name}")
                return url
        self.log(f"Tạo project Flow mới: {p.name}")
        pg.get_by_role("button", name=S.BTN_NEW_PROJECT).click()
        pg.wait_for_url(re.compile(r"/project/[0-9a-f-]+$"), timeout=30000)
        pg.wait_for_timeout(2500)
        box = pg.get_by_role("textbox", name=S.TITLE_BOX).first
        box.click()
        pg.keyboard.press("ControlOrMeta+A")
        pg.keyboard.type(p.name)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1000)
        p.flow_project_url = pg.url
        p.save()
        return p.flow_project_url

    # ---- cấu hình tạo video ----
    def configure(self, model: str, aspect: str, res: str = "720p", dur: int = 8):
        pg = self.page
        pg.get_by_role("button", name=S.BTN_SETTINGS_PILL).click()
        pg.wait_for_timeout(800)
        pg.get_by_role("radio", name=S.RADIO_VIDEO, exact=True).click()
        pg.wait_for_timeout(600)
        pg.get_by_role("radio", name=S.RADIO_INGREDIENTS, exact=True).click()
        if aspect != "flow":   # "flow": giữ nguyên khổ đang chọn trong Flow
            pg.get_by_role("radio", name=aspect, exact=True).click()
        pg.get_by_role("button", name=S.BTN_MODEL).click()
        pg.wait_for_timeout(400)
        pg.locator("[role=menuitem]").filter(has_text=model).first.click()
        pg.wait_for_timeout(600)
        pg.get_by_role("radio", name="x1", exact=True).click()
        pg.wait_for_timeout(500)
        for opt in (res, f"{dur} giây"):  # chỉ Omni có tuỳ chọn này; Veo cố định
            r = pg.get_by_role("radio", name=re.compile(rf"^{opt}"))
            if r.count():
                r.first.click()
                pg.wait_for_timeout(600)   # chờ Flow cập nhật giá sau mỗi lần đổi
        cost = self._stable_cost()
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        self.log(f"Cấu hình: {model}, {'khổ theo Flow' if aspect == 'flow' else aspect}, x1" + (f", {res}, {dur}s" if model == credits.OMNI else "") + f". {cost}")

    def _stable_cost(self) -> str:
        """Đọc dòng giá khi nó đã ổn định (Flow cập nhật chậm sau khi đổi tuỳ chọn, đọc sớm sẽ ra giá cũ)."""
        pg, last = self.page, None
        for _ in range(12):
            txt = pg.get_by_text(S.TXT_COST).first.locator("xpath=..").inner_text().replace("\n", " ")
            if txt == last:
                return txt
            last = txt
            pg.wait_for_timeout(400)
        return last or ""

    # ---- ảnh nhân vật ----
    def add_ingredient(self, img: Path):
        pg = self.page
        pg.get_by_role("button", name=S.BTN_ADD_INGREDIENT).click()
        pg.wait_for_timeout(1000)
        pg.get_by_role("textbox", name=S.SEARCH_ASSET).fill(img.name)
        pg.wait_for_timeout(1800)
        ov = pg.locator(".cdk-overlay-container").last
        opt = ov.get_by_role("option").filter(has_text=img.name)
        if not opt.count():
            self.log(f"Tải ảnh nhân vật lên Flow: {img.name}")
            with pg.expect_file_chooser() as fc:
                ov.get_by_role("button", name=S.BTN_UPLOAD).click()
            fc.value.set_files(str(img))
            pg.wait_for_timeout(2000)
            for _ in range(40):
                o = ov.get_by_role("option").filter(has_text=img.name)
                if o.count() and S.TXT_UPLOADING not in o.first.inner_text() and ov.get_by_role("progressbar").count() == 0:
                    break
                pg.wait_for_timeout(1500)
            opt = ov.get_by_role("option").filter(has_text=img.name)
            if not opt.count():
                raise FlowError(f"Upload ảnh {img.name} thất bại")
        else:
            self.log(f"Dùng lại ảnh đã có trên Flow: {img.name}")
        opt.first.click()  # click option = thêm vào câu lệnh
        pg.wait_for_timeout(1200)

    # ---- 1 scene ----
    def submit_scene(self, p: Project, s: Scene, chars: dict[str, Character]) -> int:
        """Cấu hình + điền prompt + bấm tạo, rồi chờ tới khi Flow nhận (xuất hiện thêm 1 ô clip). KHÔNG chờ render xong.
        Trả về số ô clip trước khi gửi. Trả None-clip khi dry_run."""
        pg = self.page
        self._goto(p.flow_project_url)
        n0 = pg.locator(S.TILE).count()
        dur = credits.pick_duration(s.narration) if (p.flow_auto_duration and p.flow_model == credits.OMNI) else 8
        s.duration = dur
        self.configure(p.flow_model, p.aspect_ratio, p.flow_resolution, dur)
        for n in s.characters[:3]:
            c = chars.get(n)
            if c and c.image and Path(c.image).exists():
                self.add_ingredient(Path(c.image))
        pg.locator(S.PROMPT_EDITOR).click()
        pg.keyboard.insert_text(build_prompt(p, s, chars))
        pg.wait_for_timeout(800)
        if self.dry_run:
            self.log(f"[dry-run] scene {s.index}: đã điền sẵn, không bấm tạo.")
            return -1
        gen = pg.get_by_role("button", name=S.BTN_GENERATE)
        if not gen.is_enabled():
            raise FlowError("Nút tạo bị khoá (prompt trống hoặc hết credit?)")
        gen.click()
        if not self._wait_new_tile(n0):
            why = self._overload_text()
            if why:
                raise FlowError("Flow đang quá tải (high demand) nên không nhận yêu cầu; credit được hoàn. "
                                f"Thử lại sau ít phút. Thông báo của Flow: {why[:110]}")
            raise FlowError("Flow không nhận yêu cầu (không thấy clip mới xuất hiện sau khi bấm tạo). "
                            "Kiểm tra cửa sổ Chrome Flow xem có thông báo lỗi không.")
        self.log(f"Scene {s.index}: đã gửi lên Flow.")
        return n0

    def _overload_text(self) -> str:
        """Đoạn thông báo quá tải/lỗi của Flow nếu đang hiện trên trang (banner 'high demand'...), không có thì ''."""
        try:
            body = self.page.locator("body").inner_text(timeout=3000)
        except Exception:  # noqa: BLE001
            return ""
        m = re.search(OVERLOAD_RE, body, re.I)
        if not m:
            return ""
        i = m.start()
        return " ".join(body[max(0, i - 60): i + 160].split())

    def _wait_new_tile(self, n0: int, polls: int = 20) -> bool:
        """Sau khi bấm tạo, Flow phải thêm một ô clip mới (đang render). Chờ tối đa ~polls*1.5 giây; sớm bỏ cuộc nếu trang báo quá tải."""
        pg = self.page
        for i in range(polls):
            pg.wait_for_timeout(1500)
            if pg.locator(S.TILE).count() > n0:
                return True
            if i in (5, 11) and self._overload_text():
                return False
        return False

    def generate_scene(self, p: Project, s: Scene, chars: dict[str, Character], out_dir: Path) -> Path | None:
        n0 = self.submit_scene(p, s, chars)
        if n0 < 0:
            return None
        pg = self.page
        self.log(f"Scene {s.index}: chờ Flow render...")
        t0 = time.time()
        while time.time() - t0 < 900:
            pg.wait_for_timeout(6000)
            self._check_login()
            tiles = pg.locator(S.TILE)
            if tiles.count() > n0:
                txt = tiles.first.inner_text()
                if re.search(ERROR_RE, txt, re.I):
                    raise FlowError(f"Flow báo lỗi scene {s.index}: {txt[:120]}")
                if "%" not in txt:
                    break
        else:
            raise FlowError(f"Scene {s.index}: quá 15 phút chưa xong.")
        return self._download_first(s, out_dir)

    # ---- gen song song kiểu cửa sổ trượt ----
    def generate_sliding(self, p: Project, scenes: list[Scene], chars: dict[str, Character], out_dir_for, window: int,
                         on_event=None, on_clip=None, cancel=None, max_wait: int = 1500) -> None:
        """Luôn giữ tối đa `window` scene đang render trên Flow: clip nào xong thì tải về và gửi ngay scene kế tiếp vào chỗ trống.
        on_event(scene, "sent"|"error", thông_báo): scene vừa được gửi / vừa lỗi. on_clip(scene): scene đã có clip gốc (làm giọng...).
        Scene gửi lỗi không chặn scene khác, trừ khi Flow báo quá tải: khi đó dừng gửi và báo lỗi các scene còn lại.
        Huỷ (`cancel`): ngừng gửi và ngừng chờ; scene đã gửi vẫn render trên Flow (lấy lại bằng Đồng bộ Flow, không tốn credit)."""
        pg = self.page
        notify = on_event or (lambda *a: None)
        pending, inflight = list(scenes), []          # inflight: [(scene, thời điểm gửi)]
        base: int | None = None                       # số ô clip trước khi gửi scene đầu tiên
        halt, quiet = "", 0
        cancelled = lambda: cancel is not None and cancel.is_set()

        def fail(s, msg):
            notify(s, "error", msg)
            self.log(f"Scene {s.index}: {msg}")

        def refill():
            nonlocal base, halt
            while pending and len(inflight) < window and not halt and not cancelled():
                s = pending.pop(0)
                try:
                    n0 = self.submit_scene(p, s, chars)
                    if n0 < 0:
                        continue
                    base = n0 if base is None else base
                    inflight.append((s, time.time()))
                    notify(s, "sent", "")
                    pg.wait_for_timeout(1500)
                except Exception as e:  # noqa: BLE001
                    fail(s, str(e)[:500])
                    if "quá tải" in str(e):
                        halt = str(e)[:500]
            if halt:
                while pending:
                    fail(pending.pop(0), halt)

        refill()
        while inflight and not cancelled():
            pg.wait_for_timeout(6000)
            self._check_login()
            texts = pg.locator(S.TILE).all_inner_texts()
            rendering = sum(1 for t in texts if "%" in t)
            self.log(f"Flow đang render {rendering} clip · chờ {len(inflight)} · còn {len(pending)} scene chưa gửi.")
            got: list[Scene] = []
            if rendering < len(inflight):              # có clip đã xong (hoặc lỗi): đối chiếu và tải về
                got = self.sync_clips(p, [s for s, _ in inflight], out_dir_for,
                                      limit=len(texts) - (base or 0), skip_rendering=True)
                inflight[:] = [(s, t) for s, t in inflight if s not in got]
                quiet = quiet + 1 if (not got and rendering == 0) else 0
                if quiet >= 2:                         # không còn gì render mà vẫn thiếu clip: Flow đã từ chối/lỗi
                    why = self._overload_text()
                    for s, _ in inflight:
                        fail(s, "Đã gửi lên Flow nhưng chưa thấy clip" + (f" (Flow báo: {why[:80]})" if why else "")
                                + ". Bấm ⟳ Đồng bộ Flow để thử lấy lại, không tốn credit.")
                    inflight.clear()
                    quiet = 0
            for s, t in list(inflight):
                if time.time() - t > max_wait:
                    fail(s, f"Quá {max_wait // 60} phút chưa thấy clip (chưa thấy clip). Bấm ⟳ Đồng bộ Flow để thử lấy lại.")
                    inflight.remove((s, t))
            refill()                                   # lấp chỗ trống TRƯỚC, rồi mới xử lý clip vừa tải (làm giọng)
            for s in got:
                if on_clip:
                    on_clip(s)
        if cancelled():
            for s, _ in inflight:
                fail(s, "Đã huỷ khi đang chờ render (chưa thấy clip). Bấm ⟳ Đồng bộ Flow để lấy clip nếu Flow đã render xong.")

    def _open_first_tile(self):
        pg = self.page
        pg.locator(S.TILE).first.click()
        pg.wait_for_url(re.compile(r"/edit/"), timeout=20000)
        pg.wait_for_timeout(2000)

    def _download_current(self, dst: Path, label: str) -> Path:
        """Đang ở màn hình clip (/edit/): tải bản gốc. Chỉ nhấn Escape khi có menu mở (Escape ở màn clip sẽ thoát)."""
        pg = self.page
        dst.parent.mkdir(parents=True, exist_ok=True)
        last = None
        for attempt in range(1, 9):  # clip vừa xong đôi khi chưa cho tải
            try:
                if "/edit/" not in pg.url:
                    self._open_first_tile()
                if pg.locator("[role=menu]").count():
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(500)
                btn = pg.get_by_role("button", name=S.BTN_DOWNLOAD)
                btn.wait_for(state="visible", timeout=20000)
                btn.click()
                pg.wait_for_timeout(1200)
                with pg.expect_download(timeout=90000) as d:
                    pg.locator("[role=menuitem]").filter(has_text=S.MENU_ORIGINAL).first.click()
                d.value.save_as(str(dst))
                return dst
            except Exception as e:  # noqa: BLE001
                last = e
                self.log(f"{label}: tải clip lần {attempt} chưa được ({type(e).__name__}), thử lại...")
                pg.wait_for_timeout(8000)
        raise FlowError(f"Không tải được clip {label} sau 8 lần: {str(last)[:300]}")

    def _download_first(self, s: Scene, out_dir: Path) -> Path:
        self.page.wait_for_timeout(2000)
        return self._download_current(out_dir / f"scene_{s.index:02d}_raw.mp4", f"scene {s.index}")

    # ---- đồng bộ: lấy lại clip đã render trên Flow mà app chưa tải ----
    @staticmethod
    def _norm(x: str) -> str:
        return re.sub(r"\s+", " ", x).strip().lower()

    def sync_clips(self, p: Project, scenes: list[Scene], out_dir_for, limit: int | None = None,
                   skip_rendering: bool = False) -> list[Scene]:
        """Duyệt các clip trong project Flow (mới -> cũ), khớp với scene theo nội dung prompt (đầu prompt = visual),
        tải về scene nào chưa có clip. Không tạo clip mới nên không tốn credit."""
        pg = self.page
        url = self.ensure_project(p)
        remaining = list(scenes)
        got: list[Scene] = []
        self._goto(url)
        total = pg.locator(S.TILE).count()
        if limit is not None:        # chỉ quét các ô mới nhất (clip vừa gửi), không lội qua cả lịch sử cũ
            total = min(total, max(limit, 0))
        self.log(f"Project Flow có {total} clip video, đang đối chiếu với {len(remaining)} scene...")
        for i in range(total):
            if not remaining:
                break
            self._goto(url)
            tiles = pg.locator(S.TILE)
            if i >= tiles.count():
                break
            if skip_rendering and "%" in tiles.nth(i).inner_text():
                continue                                  # còn đang render: chưa tải được
            tiles.nth(i).click()
            pg.wait_for_url(re.compile(r"/edit/"), timeout=20000)
            text = ""
            for _ in range(20):  # chờ prompt hiện ra thay vì đoán thời gian
                raw = pg.locator("main").inner_text()
                # prompt của CHÍNH clip này nằm ngay sau thanh phát (nút "repeat"); phía sau còn panel nhật ký
                # liệt kê prompt các clip khác nên chỉ xét cửa sổ đầu để không khớp nhầm.
                after = raw.split("repeat\n", 1)[1] if "repeat\n" in raw else ""
                if len(after.strip()) > 40:
                    text = self._norm(after)[:300]
                    break
                pg.wait_for_timeout(500)
            self.log(f"Clip #{i + 1}: {text[:70] or '(không đọc được prompt)'}")
            for s in remaining:
                key = self._norm(s.visual)[:60]
                if key and key in text:
                    dst = out_dir_for(s) / f"scene_{s.index:02d}_raw.mp4"
                    self._download_current(dst, f"scene {s.index}")
                    s.raw_clip = str(dst)
                    remaining.remove(s)
                    got.append(s)
                    self.log(f"Scene {s.index}: đã lấy lại clip từ Flow.")
                    break
        if remaining:
            self.log(f"Chưa thấy trên Flow (chưa gen, đã xoá, hoặc đã sửa prompt sau khi gen): "
                     f"{_ranges([s.index for s in remaining])}.")
        return got

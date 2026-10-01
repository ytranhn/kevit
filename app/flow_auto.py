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
from .models import DATA_DIR, Chapter, Character, Project, Scene
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


class NoCreditError(FlowError):
    """Tài khoản Flow không đủ credit cho scene tiếp theo (đã biết trước khi bấm tạo). Scene chưa gửi KHÔNG bị tính là lỗi: để chuyển tài khoản."""


class Budget:
    """Credit còn lại của tài khoản đang dùng trong một lượt gen: trừ dần theo giá thật của từng scene đã gửi. left=None: chưa biết, không chặn."""

    def __init__(self, left: int | None = None):
        self.left = left

    def can_afford(self, cost: int | None) -> bool:
        return self.left is None or cost is None or cost <= self.left

    def spend(self, cost: int | None) -> None:
        if self.left is not None and cost:
            self.left = max(0, self.left - cost)


def use_account(acc) -> None:
    """Chuyển mọi thao tác Flow sang tài khoản `acc` (accounts.Account): cổng debug và hồ sơ Chrome riêng của nó."""
    global PROFILE_DIR
    S.CDP_URL = acc.cdp_url
    PROFILE_DIR = acc.profile_dir
    _cdp_cache.update(up=False, t=0.0)                # trạng thái Chrome của tài khoản trước không còn đúng


def _cdp_up(url: str | None = None) -> bool:
    """Chrome Flow có đang mở cổng gỡ lỗi không (mặc định: tài khoản đang dùng). CHẶN tới 2s nếu Chrome treo: chỉ gọi ở luồng nền; giao diện dùng cdp_state()."""
    try:
        urllib.request.urlopen((url or S.CDP_URL) + "/json/version", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


_cdp_cache = {"up": False, "t": 0.0, "busy": False}


def cdp_state(max_age: float = 3.0) -> bool:
    """Trạng thái Chrome Flow cho giao diện: trả về NGAY giá trị đã biết, và nếu cũ hơn max_age giây thì kiểm tra lại ở luồng nền
    (Chrome treo không còn làm đứng giao diện 2 giây mỗi lần)."""
    import threading
    now = time.time()
    if now - _cdp_cache["t"] > max_age and not _cdp_cache["busy"]:
        _cdp_cache["busy"] = True

        def run():
            try:
                _cdp_cache["up"] = _cdp_up()
            finally:
                _cdp_cache["t"], _cdp_cache["busy"] = time.time(), False
        threading.Thread(target=run, name="cdp-check", daemon=True).start()
    return _cdp_cache["up"]


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


def chrome_command(acc=None) -> list[str]:
    """Lệnh mở Chrome BÌNH THƯỜNG (không qua Playwright) với profile riêng + cổng debug (của `acc`, mặc định tài khoản đang dùng)."""
    port = acc.port if acc else S.CDP_URL.rsplit(":", 1)[-1]
    flags = [f"--remote-debugging-port={port}", f"--user-data-dir={acc.profile_dir if acc else PROFILE_DIR}",
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


def launch_chrome(acc=None) -> None:
    """Mở Chrome Flow của tài khoản `acc` (mặc định: tài khoản đang dùng) nếu chưa mở; chờ tới khi cổng debug sẵn sàng."""
    url = acc.cdp_url if acc else S.CDP_URL
    prof = Path(acc.profile_dir) if acc else PROFILE_DIR
    if not _cdp_up(url):
        prof.mkdir(parents=True, exist_ok=True)
        seed_download_prefs(prof)
        subprocess.Popen(chrome_command(acc))
        for _ in range(20):
            if _cdp_up(url):
                return
            time.sleep(1)
        raise FlowError(f"Không mở được Chrome (cổng {url.rsplit(':', 1)[-1]}). Nếu Chrome đang mở sẵn bằng hồ sơ khác, "
                        "hãy đóng hết cửa sổ Chrome của tool rồi bấm lại.")


class FlowAuto:
    def __init__(self, log=print, dry_run: bool = False, acc=None):
        """acc: tài khoản (accounts.Account) cần điều khiển; None = tài khoản đang dùng. Mỗi tài khoản có Chrome/cổng riêng nên
        có thể điều khiển tài khoản khác với tài khoản đang dùng mà không phải đổi cài đặt chung."""
        self.log, self.dry_run, self.acc = log, dry_run, acc
        self.last_cost: int | None = None            # giá credit thật Flow báo cho cấu hình vừa chọn
        self.pw = self.browser = self.page = None

    # ---- kết nối ----
    def __enter__(self):
        launch_chrome(self.acc)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(self.acc.cdp_url if self.acc else S.CDP_URL)
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

    # ---- tìm phần tử không phụ thuộc ngôn ngữ giao diện ----
    def _first(self, css: str, role: str | None = None, name=None, scope=None):
        """Phần tử đầu tiên theo CSS (class/icon: không đổi theo ngôn ngữ Flow); không có thì tìm theo vai trò + nhãn chữ (đa ngôn ngữ)."""
        root = scope or self.page
        loc = root.locator(css)
        if loc.count() or not role or name is None:
            return loc.first
        return root.get_by_role(role, name=name).first

    # ---- dự án ----
    @staticmethod
    def project_title(p: Project, ch: Chapter | None) -> str:
        return f"{p.name} · {ch.name}" if ch else p.name

    @staticmethod
    def url_of(p: Project, ch: Chapter | None) -> str:
        return (ch.flow_project_url if ch else p.flow_project_url) or ""

    @staticmethod
    def _store_url(p: Project, ch: Chapter | None, url: str) -> None:
        if ch:
            ch.flow_project_url = url
        else:
            p.flow_project_url = url
        p.save()

    def ensure_project(self, p: Project, ch: Chapter | None = None) -> str:
        """Trả về địa chỉ project Flow đã mở sẵn. ch=None: project chung của dự án (ảnh nhân vật). ch=chương: project RIÊNG của chương
        (đặt tên «Dự án · Chương»), để mỗi project chỉ chứa clip của một chương -> Flow nhẹ, đối soát nhanh.
        Mọi chương đều có project riêng, kể cả chương đã gen từ trước: clip cũ nằm ở project chung của dự án vẫn tìm lại được
        qua Đồng bộ (sync_clips quét thêm project chung), còn clip MỚI luôn vào project của chương."""
        pg = self.page
        title = self.project_title(p, ch)
        if ch and ch.flow_project_url and ch.flow_project_url == p.flow_project_url:
            # bản trước (v1.2.x) cho chương đã gen "mượn" project chung của dự án: gỡ ra để chương có project riêng theo tên chương
            self.log(f"{ch.name}: trước đây dùng project chung của dự án, chuyển sang project riêng «{title}».")
            ch.flow_project_url = ""
            p.save()
        url = self.url_of(p, ch)
        if url:
            self._goto(url)
            if "/project/" in pg.url and pg.locator(S.PROMPT_EDITOR).count():
                self.log(f"Dùng lại project Flow: {title}")
                return url
            self.log("Project Flow đã lưu không còn truy cập được, tìm lại theo tên...")
        self._goto(S.HOME_URL)
        for link in pg.locator(S.CSS_PROJECT_LINK).all():
            cond = " or ".join([f"normalize-space(.)='{S.ICON_EDIT_TITLE}'"] + [f"@aria-label='{a}'" for a in S.EDIT_TITLE_LABELS])
            card = link.locator(f"xpath=ancestor::*[.//button[{cond}]][1]")
            if not card.count():
                continue
            text = card.first.inner_text()
            # chương: khớp NGUYÊN dòng tiêu đề (tránh «… Chương 1» khớp nhầm «… Chương 10»); dự án: chứa tên như trước
            hit = (title in [ln.strip() for ln in text.splitlines()]) if ch else (p.name in text)
            if hit:
                url = "https://flow.google.com" + link.get_attribute("href")
                self._goto(url)
                self._store_url(p, ch, url)
                self.log(f"Tìm thấy project Flow cùng tên: {title}")
                return url
        self.log(f"Tạo project Flow mới: {title}")
        self._first(S.CSS_NEW_PROJECT, "button", S.BTN_NEW_PROJECT).click()
        pg.wait_for_url(re.compile(r"/project/[0-9a-f-]+$"), timeout=30000)
        pg.wait_for_timeout(2500)
        box = self._first(S.CSS_TITLE_INPUT, "textbox", S.TITLE_BOX)
        box.click()
        pg.keyboard.press("ControlOrMeta+A")
        pg.keyboard.type(title)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1000)
        self._store_url(p, ch, pg.url)
        return pg.url

    # ---- credit và gói của tài khoản ----
    def read_credits(self, deep: bool = False) -> dict | None:
        """Đọc credit Flow còn lại ở hộp thoại tài khoản (góc phải Flow). deep=True: mở thêm trang Google One để lấy credit tặng hằng ngày
        và thông tin làm mới/gia hạn. Trả {credits, email, daily, renew} hoặc None nếu không đọc được (đổi giao diện, chưa đăng nhập...).
        Không bao giờ chặn việc chạy chỉ vì không đọc được. Chỉ gọi khi không có tác vụ nào khác đang dùng tab Flow (nó điều hướng tab)."""
        pg = self.page
        try:
            self._goto(S.HOME_URL)
            btn = pg.locator(", ".join([S.CSS_ACCOUNT] + [f"[aria-label='{a}']" for a in S.ACCOUNT_LABELS])).first
            btn.wait_for(state="visible", timeout=15000)
            btn.click()
            pg.wait_for_timeout(1500)
            text = pg.evaluate("document.querySelector('.cdk-overlay-container')?.innerText || ''")
            pg.keyboard.press("Escape")
        except FlowError:
            raise
        except Exception as e:  # noqa: BLE001
            self.log(f"Không đọc được credit trên Flow: {type(e).__name__}")
            return None
        amount = self._credit_from_text(text)
        if amount is None:
            self.log("Không thấy dòng credit trong hộp thoại tài khoản Flow.")
            return None
        email = (re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text) or [None])[0] or ""
        info = {"credits": amount, "email": email, "daily": None, "renew": ""}
        if deep:
            info.update(self._read_plan_details())
        return info

    @staticmethod
    def _credit_from_text(text: str) -> int | None:
        """Số credit trong hộp thoại tài khoản, theo thứ tự tin cậy: (1) dòng ngay sau icon 'movie_filter_auto' (tên icon không đổi theo ngôn ngữ);
        (2) dòng khớp chữ 'credits' của các ngôn ngữ đã biết; (3) dòng ngắn có số, không phải email/tên tài khoản."""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines[:-1]):
            if ln == S.ICON_CREDITS and credits.parse_amount(lines[i + 1]) is not None:
                return credits.parse_amount(lines[i + 1])
        for ln in lines:
            if S.CREDIT_LINE.search(ln) and credits.parse_amount(ln) is not None:
                return credits.parse_amount(ln)
        for ln in lines:
            if "@" not in ln and len(ln) < 50 and re.fullmatch(r"\D{0,25}\d[\d.,\u202f\u00a0 ]*\D{0,40}", ln) and any(c.isdigit() for c in ln):
                return credits.parse_amount(ln)
        return None

    def _read_plan_details(self) -> dict:
        """Trang Google One → Google Flow activity: credit tặng hằng ngày còn lại và thời gian làm mới/gia hạn (nếu trang có ghi). Lỗi thì trả rỗng."""
        out = {"daily": None, "renew": ""}
        page = None
        try:
            page = self.page.context.new_page()
            page.goto(S.ONE_ACTIVITY_URL)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(4500)
            txt = page.evaluate("document.body.innerText || ''")
        except Exception as e:  # noqa: BLE001
            self.log(f"Không đọc được trang gói Google One: {type(e).__name__}")
            return out
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
        m = re.search(r"(\d[\d.,]*)\s+daily\s+(?:Google\s+)?Flow\s+credits?\s+remaining", txt, re.I) \
            or re.search(r"còn\s+(\d[\d.,]*)\s+(?:tín dụng|credit)[^\n]{0,30}(?:hằng ngày|mỗi ngày)", txt, re.I)
        if m:
            out["daily"] = credits.parse_amount(m.group(1))
        d = re.search(r"(?:renews?|refreshes|resets?|expires?|gia hạn|làm mới)[^\n\d]{0,30}"
                      r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", txt, re.I)
        if d:
            out["renew"] = d.group(1)
        elif re.search(r"refresh\s+monthly|làm mới hằng tháng|làm mới mỗi tháng", txt, re.I):
            out["renew"] = "làm mới hằng tháng" + (" (+50/ngày)" if re.search(r"\b50\b[^\n]{0,40}daily|daily[^\n]{0,40}\b50\b", txt, re.I) else "")
        return out

    # ---- cấu hình tạo video ----
    def _ensure_classic_mode(self) -> None:
        """Giao diện Flow mới mở sẵn chế độ 'Agent' trong ô nhập: nút cài đặt tạo clip bị ẩn. Bấm vào nhãn Agent để tắt rồi mới cấu hình."""
        pg = self.page
        trigger = self._first(S.CSS_SETTINGS_PILL, "button", S.BTN_SETTINGS_PILL)
        for _ in range(2):
            if trigger.count() and trigger.is_visible():
                return
            chip = pg.locator(S.BTN_AGENT_CHIP)
            if not chip.count():
                break
            self.log("Tắt chế độ Agent trong ô nhập để dùng tạo clip thông thường...")
            chip.first.click()
            pg.wait_for_timeout(1200)

    def _open_settings(self) -> None:
        """Mở bảng cài đặt trong ô nhập (nếu chưa mở). Bảng đang đóng dở (hiệu ứng) thì lần bấm đầu có thể không mở: thử lại một lần."""
        pg = self.page
        self._ensure_classic_mode()
        for attempt in range(2):
            if pg.locator("[role=radio]:visible").count():       # đã mở sẵn: bấm nữa sẽ ĐÓNG bảng
                break
            self._first(S.CSS_SETTINGS_PILL, "button", S.BTN_SETTINGS_PILL).click()
            try:
                pg.locator("[role=radio]").first.wait_for(state="visible", timeout=5000)
                break
            except Exception:  # noqa: BLE001
                if attempt:
                    raise FlowError("Không mở được bảng cài đặt tạo clip trên Flow (giao diện Flow có thể đã đổi).")
                pg.wait_for_timeout(800)
        pg.wait_for_timeout(400)

    def configure(self, model: str, aspect: str, res: str = "720p", dur: int = 8):
        pg = self.page
        self._open_settings()
        vid = self._radio(S.RADIO_VIDEO)
        vid.click()
        self._wait_checked(vid)
        pg.wait_for_timeout(600)
        self._radio(S.RADIO_INGREDIENTS).click()
        if aspect != "flow":   # "flow": giữ nguyên khổ đang chọn trong Flow
            self._radio(aspect).click()
        self._pick_model(model)
        pg.wait_for_timeout(600)
        self._radio(re.compile(r"^\s*x1\s*$")).click()
        pg.wait_for_timeout(500)
        for opt in (re.escape(res), rf"{dur}(?!\d)"):  # chỉ Omni có tuỳ chọn này (độ phân giải '720p', thời lượng '8s'/'8 giây'); Veo cố định
            r = pg.locator("[role=radio]").filter(has_text=re.compile(rf"^\s*{opt}"))
            if r.count():
                r.first.click()
                pg.wait_for_timeout(600)   # chờ Flow cập nhật giá sau mỗi lần đổi
        cost = self._stable_cost()
        self.last_cost = credits.parse_amount(cost)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        self.log(f"Cấu hình: {model}, {'khổ theo Flow' if aspect == 'flow' else aspect}, x1" + (f", {res}, {dur}s" if model == credits.OMNI else "") + f". {cost}")

    def _wait_checked(self, radio, timeout: int = 4000) -> None:
        """Đợi nút chọn (radio) thật sự ở trạng thái đã chọn: Flow đổi danh sách model/tuỳ chọn sau khi chuyển chế độ Ảnh <-> Video."""
        try:
            self.page.wait_for_function("e => e.getAttribute('aria-checked') === 'true'", arg=radio.element_handle(), timeout=timeout)
        except Exception:  # noqa: BLE001 - không đọc được trạng thái thì vẫn đi tiếp
            self.page.wait_for_timeout(800)

    def _pick_model(self, model: str) -> None:
        """Mở danh sách model (nút có icon mũi tên xổ, không phụ thuộc ngôn ngữ) và chọn `model`. Danh sách có thể chưa kịp đổi sau khi
        chuyển chế độ: không thấy model thì đóng, đợi rồi thử lại một lần."""
        pg = self.page
        for attempt in range(2):
            ov = pg.locator(".cdk-overlay-container").last
            menu_btn = ov.locator("button").filter(has_text=S.ICON_MODEL_MENU)
            (menu_btn.first if menu_btn.count() else pg.get_by_role("button", name=S.BTN_MODEL).first).click()
            pg.wait_for_timeout(500)
            item = pg.locator("[role=menuitem]").filter(has_text=model)
            try:
                item.first.wait_for(state="visible", timeout=4000 if attempt == 0 else 8000)
                item.first.click()
                return
            except Exception:  # noqa: BLE001
                if attempt:
                    raise FlowError(f"Không thấy model «{model}» trong danh sách model của Flow (tài khoản có thể chưa được dùng model này).")
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(1200)

    def _cost_text(self) -> str:
        """Dòng giá trong bảng cài đặt. Không phụ thuộc ngôn ngữ: dòng CUỐI có chữ số của bảng cài đặt đang mở (vd. 'Generating will use 20 credits',
        'Quá trình tạo sẽ tốn 20 tín dụng'); không có bảng thì tìm theo chữ của các ngôn ngữ đã biết."""
        pg = self.page
        try:
            ov = pg.locator(".cdk-overlay-container").last
            if ov.count():
                lines = [ln.strip() for ln in ov.inner_text(timeout=3000).splitlines() if ln.strip()]
                priced = [ln for ln in lines if any(c.isdigit() for c in ln) and credits.parse_amount(ln) is not None and not re.fullmatch(r"x\d|\d+p|\d+s|\d+:\d+", ln, re.I)
                          and len(ln) > 8]
                if priced:
                    return priced[-1]
        except Exception:  # noqa: BLE001
            pass
        return pg.get_by_text(S.TXT_COST).first.locator("xpath=..").inner_text().replace("\n", " ")

    def _stable_cost(self) -> str:
        """Đọc dòng giá khi nó đã ổn định (Flow cập nhật chậm sau khi đổi tuỳ chọn, đọc sớm sẽ ra giá cũ)."""
        pg, last = self.page, None
        for _ in range(12):
            txt = self._cost_text()
            if txt == last:
                return txt
            last = txt
            pg.wait_for_timeout(400)
        return last or ""

    # ---- ảnh nhân vật ----
    def add_ingredient(self, img: Path):
        pg = self.page
        self._first(S.CSS_ADD_MENU, "button", S.BTN_ADD_INGREDIENT).click()
        pg.wait_for_timeout(1000)
        ov = pg.locator(".cdk-overlay-container").last
        search = ov.locator("input")
        (search.first if search.count() else pg.get_by_role("textbox", name=S.SEARCH_ASSET)).fill(img.name)
        pg.wait_for_timeout(1800)
        opt = ov.get_by_role("option").filter(has_text=img.name)
        if not opt.count():
            self.log(f"Tải ảnh nhân vật lên Flow: {img.name}")
            with pg.expect_file_chooser() as fc:
                self._first(S.CSS_UPLOAD, "button", S.BTN_UPLOAD, scope=ov).click()
            fc.value.set_files(str(img))
            pg.wait_for_timeout(2000)
            for _ in range(40):
                o = ov.get_by_role("option").filter(has_text=img.name)
                if o.count() and not S.text_in(S.UPLOADING_LABELS, o.first.inner_text()) and ov.get_by_role("progressbar").count() == 0:
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
    def submit_scene(self, p: Project, ch: Chapter, s: Scene, chars: dict[str, Character], budget: "Budget | None" = None) -> int:
        """Cấu hình + điền prompt + bấm tạo, rồi chờ tới khi Flow nhận (xuất hiện thêm 1 ô clip). KHÔNG chờ render xong.
        Trả về số ô clip trước khi gửi. Trả None-clip khi dry_run."""
        pg = self.page
        self._goto(self.url_of(p, ch))
        n0 = pg.locator(S.TILE).count()
        dur = credits.pick_duration(s.narration, p.narration_lang) if (p.flow_auto_duration and p.flow_model == credits.OMNI) else 8
        s.duration = dur
        self.configure(p.flow_model, p.aspect_ratio, p.flow_resolution, dur)
        if budget is not None and not budget.can_afford(self.last_cost):
            raise NoCreditError(f"Không đủ credit cho scene {s.index}: cần {self.last_cost}, tài khoản còn {budget.left}.")
        for n in s.characters[:3]:
            c = chars.get(n)
            if c and c.image and Path(c.image).exists():
                self.add_ingredient(Path(c.image))
        if self.dry_run:
            pg.locator(S.PROMPT_EDITOR).click()
            pg.keyboard.press("ControlOrMeta+A")
            pg.keyboard.insert_text(build_prompt(p, s, chars))
            self.log(f"[dry-run] scene {s.index}: đã điền sẵn, không bấm tạo.")
            return -1
        self._type_prompt(build_prompt(p, s, chars))
        gen = self._first(S.CSS_GENERATE, "button", S.BTN_GENERATE)
        gen.click()
        if not self._wait_new_tile(n0):
            why = self._overload_text()
            if why:
                raise FlowError("Flow đang quá tải (high demand) nên không nhận yêu cầu; credit được hoàn. "
                                f"Thử lại sau ít phút. Thông báo của Flow: {why[:110]}")
            raise FlowError("Flow không nhận yêu cầu (không thấy clip mới xuất hiện sau khi bấm tạo). "
                            "Kiểm tra cửa sổ Chrome Flow xem có thông báo lỗi không.")
        if budget is not None:
            budget.spend(self.last_cost)
        self.log(f"Scene {s.index}: đã gửi lên Flow." + (f" (còn ~{budget.left} credit)" if budget is not None and budget.left is not None else ""))
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

    def generate_scene(self, p: Project, ch: Chapter, s: Scene, chars: dict[str, Character], out_dir: Path,
                       budget: "Budget | None" = None) -> Path | None:
        n0 = self.submit_scene(p, ch, s, chars, budget)
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
    def generate_sliding(self, p: Project, ch: Chapter, scenes: list[Scene], chars: dict[str, Character], out_dir_for, window: int,
                         on_event=None, on_clip=None, cancel=None, max_wait: int = 1500, budget: "Budget | None" = None) -> list[Scene]:
        """Luôn giữ tối đa `window` scene đang render trên Flow: clip nào xong thì tải về và gửi ngay scene kế tiếp vào chỗ trống.
        on_event(scene, "sent"|"error", thông_báo): scene vừa được gửi / vừa lỗi. on_clip(scene): scene đã có clip gốc (làm giọng...).
        Scene gửi lỗi không chặn scene khác, trừ khi Flow báo quá tải: khi đó dừng gửi và báo lỗi các scene còn lại.
        Huỷ (`cancel`): ngừng gửi và ngừng chờ; scene đã gửi vẫn render trên Flow (lấy lại bằng Đồng bộ Flow, không tốn credit).
        budget: credit còn lại của tài khoản; scene nào không đủ credit thì KHÔNG gửi và KHÔNG báo lỗi mà trả về trong danh sách kết quả
        (các scene chưa gửi vì hết credit) để nơi gọi chuyển sang tài khoản khác."""
        pg = self.page
        notify = on_event or (lambda *a: None)
        pending, inflight = list(scenes), []          # inflight: [(scene, thời điểm gửi)]
        base: int | None = None                       # số ô clip trước khi gửi scene đầu tiên
        halt, quiet = "", 0
        unsent: list[Scene] = []                      # chưa gửi vì không đủ credit
        cancelled = lambda: cancel is not None and cancel.is_set()

        def fail(s, msg):
            notify(s, "error", msg)
            self.log(f"Scene {s.index}: {msg}")

        def refill():
            nonlocal base, halt
            while pending and len(inflight) < window and not halt and not cancelled():
                s = pending.pop(0)
                try:
                    n0 = self.submit_scene(p, ch, s, chars, budget)
                    if n0 < 0:
                        continue
                    base = n0 if base is None else base
                    inflight.append((s, time.time()))
                    notify(s, "sent", "")
                    pg.wait_for_timeout(1500)
                except NoCreditError as e:
                    self.log(str(e))
                    unsent.append(s)
                    unsent.extend(pending)                # mọi scene sau cũng chưa gửi: nhường tài khoản khác
                    pending.clear()
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
                got = self.sync_clips(p, ch, [s for s, _ in inflight], out_dir_for,
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
        return unsent

    def _type_prompt(self, text: str) -> None:
        """Nhập prompt (thay hẳn chữ cũ nếu có) rồi chờ nút 'Bắt đầu tạo' sáng lên (Flow cần vài giây để nhận prompt)."""
        pg = self.page
        pg.locator(S.PROMPT_EDITOR).click()
        pg.keyboard.press("ControlOrMeta+A")
        pg.keyboard.insert_text(text)
        gen = self._first(S.CSS_GENERATE, "button", S.BTN_GENERATE)
        for _ in range(16):
            pg.wait_for_timeout(500)
            if gen.is_enabled():
                return
        raise FlowError("Nút tạo bị khoá (prompt trống hoặc hết credit?)")

    # ---- tạo ảnh (chế độ "Hình ảnh" của Flow: Nano Banana, thường 0 tín dụng) ----
    def _close_overlays(self) -> None:
        """Đóng mọi lớp phủ của Flow (bảng cài đặt, menu, thông báo) đang che trang: chúng chặn mọi cú bấm vào ô ảnh/clip ('Timeout waiting for
        navigation'). Thử Escape, không được thì bấm vào vùng trống ngoài lớp phủ."""
        pg = self.page
        for _ in range(3):
            try:
                if not pg.locator(".cdk-overlay-backdrop").count():
                    return
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(500)
                if pg.locator(".cdk-overlay-backdrop").count():
                    pg.mouse.click(4, 4)
                    pg.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                return

    def _radio(self, text):
        """Nút chọn (radio) theo chữ hiển thị (chuỗi hoặc biểu thức đa ngôn ngữ). Bên trong có tên icon (vd. 'videocam Video') nên khớp theo has_text."""
        return self.page.locator("[role=radio]").filter(has_text=text).first

    def _set_image_mode(self, aspect: str = "3:4") -> str:
        """Mở bảng cài đặt, chọn Hình ảnh + khổ + x1. Trả về dòng giá hiện trên Flow."""
        pg = self.page
        self._open_settings()
        self._radio(S.RADIO_IMAGE).click()
        pg.wait_for_timeout(1000)
        self._radio(aspect).click()
        self._radio(re.compile(r"^\s*x1\s*$")).click()
        pg.wait_for_timeout(500)
        cost = self._stable_cost()
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        self._close_overlays()
        return cost

    def generate_image(self, p: Project, prompt: str, aspect: str = "3:4", timeout: int = 240) -> bytes:
        """Tạo 1 ảnh trong dự án Flow bằng chế độ Hình ảnh rồi tải về, trả về dữ liệu ảnh. Không dùng Gemini API nên không cần key riêng."""
        pg = self.page
        url = self.ensure_project(p)
        self._goto(url)
        n0 = pg.locator(S.IMAGE_TILE).count()
        before = {self._url_key(u) for u in self._tile_image_urls()}      # ảnh có sẵn: để nhận ra đâu là ảnh MỚI
        cost = self._set_image_mode(aspect)
        self.log(f"Tạo ảnh trên Flow ({aspect}). {cost}")
        self._close_overlays()
        self._type_prompt(prompt)
        self._first(S.CSS_GENERATE, "button", S.BTN_GENERATE).click()
        t0, seen_new = time.time(), False
        while time.time() - t0 < timeout:
            pg.wait_for_timeout(4000)
            self._check_login()
            now = pg.locator(S.IMAGE_TILE).count()
            pending = pg.locator(S.PENDING_TILE).count()
            seen_new = seen_new or now > n0 or pending > 0
            if now > n0 and not pending:
                break
            if not seen_new and time.time() - t0 > 30:          # 30s mà Flow không hiện ô mới: không nhận yêu cầu
                why = self._overload_text()
                raise FlowError("Flow đang quá tải (high demand) nên không nhận yêu cầu; thử lại sau ít phút." if why else
                                "Flow không nhận yêu cầu tạo ảnh (không thấy ô mới). Kiểm tra cửa sổ Chrome Flow xem có thông báo lỗi không.")
        else:
            raise FlowError(f"Quá {timeout}s chưa có ảnh trên Flow.")
        # ưu tiên: lấy thẳng ảnh mới từ ô ảnh trên trang dự án (không cần mở trang chi tiết nên không phụ thuộc vào việc bấm trúng ô)
        for _ in range(10):
            fresh = [u for u in self._tile_image_urls() if self._url_key(u) not in before]
            data = self._fetch_image(fresh[0]) if fresh else None
            if data:
                return data
            pg.wait_for_timeout(2000)
        # dự phòng: mở ảnh mới nhất rồi lấy từ màn hình chi tiết
        self.log("Không thấy ảnh mới trên trang dự án, thử mở ảnh để lấy...")
        try:
            self._close_overlays()
            pg.locator(S.IMAGE_TILE).first.scroll_into_view_if_needed(timeout=5000)
            pg.locator(S.IMAGE_TILE).first.click(timeout=10000)
            pg.wait_for_url(re.compile(r"/edit/"), timeout=20000)
        except Exception as e:  # noqa: BLE001
            raise FlowError("Flow đã tạo ảnh nhưng tool không lấy được nó (không mở được ảnh để tải). Mở dự án trên Flow, tải ảnh thủ công "
                            f"rồi gắn vào nhân vật bằng nút “Chọn ảnh…”. Chi tiết: {type(e).__name__}") from e
        pg.wait_for_timeout(2000)
        data = self._download_image()
        self._goto(url)                                             # thoát màn hình ảnh về dự án
        return data

    @staticmethod
    def _url_key(u: str) -> str:
        return u.split("?", 1)[0]

    def _tile_image_urls(self) -> list[str]:
        """Địa chỉ ảnh của các ô ảnh trên trang dự án (theo thứ tự hiển thị)."""
        try:
            return self.page.evaluate("""() => [...document.querySelectorAll('flow-image-tile img')]
                .filter(i => i.naturalWidth > 100).map(i => i.currentSrc || i.src).filter(u => u && u.startsWith('http'))""")
        except Exception:  # noqa: BLE001
            return []

    def _fetch_image(self, url: str) -> bytes | None:
        """Tải dữ liệu ảnh từ địa chỉ CDN đã ký của Flow (không qua trình duyệt nên không dính quyền tải/hộp thoại lưu)."""
        import urllib.request
        try:
            ua = self.page.evaluate("navigator.userAgent")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=30) as r:
                data = r.read()
            return data if len(data) > 2000 else None
        except Exception as e:  # noqa: BLE001
            self.log(f"Lấy ảnh trực tiếp chưa được ({type(e).__name__}).")
            return None

    def _image_bytes_from_page(self) -> bytes | None:
        """Ở màn hình ảnh (/edit/): lấy dữ liệu ảnh lớn nhất đang hiển thị (địa chỉ CDN đã ký)."""
        try:
            imgs = self.page.evaluate("""() => [...document.images].map(i => ({src: i.currentSrc || i.src, area: i.naturalWidth * i.naturalHeight}))
                                       .filter(i => i.area > 200 * 200 && i.src.startsWith('http')).sort((a, b) => b.area - a.area)""")
        except Exception:  # noqa: BLE001
            return None
        for im in imgs[:2]:
            data = self._fetch_image(im["src"])
            if data:
                return data
        return None

    def _download_image(self) -> bytes:
        """Đang ở màn hình ảnh (/edit/): lấy ảnh. Ưu tiên lấy thẳng từ địa chỉ ảnh; không được thì dùng nút tải xuống (mở menu cỡ ảnh,
        chọn 'Kích thước gốc'; cần Chrome cho phép tải tự động nên chỉ là phương án dự phòng)."""
        import tempfile
        pg = self.page
        for _ in range(5):                       # ảnh vừa tạo đôi khi cần vài giây để hiện đủ độ phân giải
            data = self._image_bytes_from_page()
            if data:
                return data
            pg.wait_for_timeout(2000)
        tmp = Path(tempfile.mkdtemp(prefix="kevit-img-")) / "image"
        last = None
        for attempt in range(1, 3):
            try:
                if pg.locator("[role=menu]").count():
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(500)
                btn = self._first(S.CSS_DOWNLOAD, "button", S.BTN_DOWNLOAD)
                btn.wait_for(state="visible", timeout=15000)
                with pg.expect_download(timeout=25000) as d:
                    btn.first.click()
                    pg.wait_for_timeout(1200)
                    item = pg.locator("[role=menuitem]").filter(has_text=S.MENU_IMAGE_ORIGINAL)
                    if item.count():
                        item.first.click()
                d.value.save_as(str(tmp))
                data = tmp.read_bytes()
                if data:
                    return data
                last = "file tải về rỗng"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {str(e)[:120]}"
            self.log(f"Tải ảnh lần {attempt} chưa được ({last}), thử lại...")
        raise FlowError(f"Không lấy được ảnh vừa tạo: {last}. Nếu Chrome hiện hộp thoại xin phép tải nhiều tệp, hãy bấm Cho phép.")

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
                btn = self._first(S.CSS_DOWNLOAD, "button", S.BTN_DOWNLOAD)
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

    def _scan_tiles(self, url: str, remaining: list[Scene], got: list[Scene], out_dir_for, limit: int | None, skip_rendering: bool) -> None:
        """Duyệt các ô clip của một project Flow (mới -> cũ), khớp prompt với `remaining`, tải clip khớp (chuyển scene từ remaining sang got)."""
        pg = self.page
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

    def sync_clips(self, p: Project, ch: Chapter, scenes: list[Scene], out_dir_for, limit: int | None = None,
                   skip_rendering: bool = False) -> list[Scene]:
        """Duyệt các clip trong project Flow (mới -> cũ), khớp với scene theo nội dung prompt (đầu prompt = visual),
        tải về scene nào chưa có clip. Không tạo clip mới nên không tốn credit."""
        url = self.ensure_project(p, ch)
        remaining = list(scenes)
        got: list[Scene] = []
        self._scan_tiles(url, remaining, got, out_dir_for, limit, skip_rendering)
        legacy = p.flow_project_url
        if remaining and limit is None and legacy and legacy != url:
            # clip gen từ bản cũ (mọi chương dùng chung một project của dự án) vẫn nằm ở project chung: quét thêm để lấy lại, không tốn credit
            self.log("Quét thêm project chung của dự án (clip gen từ bản cũ)...")
            self._scan_tiles(legacy, remaining, got, out_dir_for, None, skip_rendering)
        if remaining:
            self.log(f"Chưa thấy trên Flow (chưa gen, đã xoá, hoặc đã sửa prompt sau khi gen): "
                     f"{_ranges([s.index for s in remaining])}.")
        return got

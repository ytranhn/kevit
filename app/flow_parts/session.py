"""Điều khiển Flow: kết nối Chrome, mở/tạo project Flow, đọc credit/gói, cấu hình model-tỉ lệ, thao tác trang cơ bản."""
from __future__ import annotations

import re

from playwright.sync_api import sync_playwright

from .. import credits
from .. import flow_selectors as S
from ..models import Chapter, Project
from ..flow_common import FlowError, launch_chrome


class FlowSessionMixin:
    """Điều khiển Flow: kết nối Chrome, mở/tạo project Flow, đọc credit/gói, cấu hình model-tỉ lệ, thao tác trang cơ bản."""

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
        info = {"credits": amount, "email": email, "daily": None, "renew": "", "plan_total": None, "daily_grant": None}
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
        out = {"daily": None, "renew": "", "plan_total": None, "daily_grant": None}
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
        m = re.search(r"(\d[\d.,]*)\s+(?:Google\s+)?Flow\s+credits\s+are\s+included", txt, re.I)
        if m:
            out["plan_total"] = credits.parse_amount(m.group(1))
        m = re.search(r"additional\s+(\d[\d.,]*)\s+(?:Google\s+)?Flow\s+credits\s+daily", txt, re.I)
        if m:
            out["daily_grant"] = credits.parse_amount(m.group(1))
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

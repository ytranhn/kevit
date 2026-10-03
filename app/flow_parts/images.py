"""Điều khiển Flow: gen ảnh (nhân vật/ảnh phụ) và lấy ảnh về."""
from __future__ import annotations

import re
import time
from pathlib import Path

from .. import flow_selectors as S
from ..models import Chapter, Project
from ..flow_common import FlowError


class FlowImageMixin:
    """Điều khiển Flow: gen ảnh (nhân vật/ảnh phụ) và lấy ảnh về."""

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

    def generate_image(self, p: Project, prompt: str, aspect: str = "3:4", timeout: int = 240, ch: Chapter | None = None) -> bytes:
        """Tạo 1 ảnh trong dự án Flow bằng chế độ Hình ảnh rồi tải về, trả về dữ liệu ảnh. Không dùng Gemini API nên không cần key riêng.
        ch: tạo trong project Flow RIÊNG của chương này (cùng project với clip video của chương); None = project chung của dự án."""
        pg = self.page
        url = self.ensure_project(p, ch)
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

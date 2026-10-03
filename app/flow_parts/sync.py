"""Điều khiển Flow: quét các ô video trong project Flow, tải clip về và đồng bộ với scene."""
from __future__ import annotations

import re
from pathlib import Path

from .. import flow_selectors as S
from ..models import Chapter, Project, Scene
from ..flow_common import _ranges, FlowError


class FlowSyncMixin:
    """Điều khiển Flow: quét các ô video trong project Flow, tải clip về và đồng bộ với scene."""

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

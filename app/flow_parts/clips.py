"""Điều khiển Flow: gửi prompt và gen clip cho scene (từng scene hoặc cuốn chiếu), kèm ảnh nhân vật."""
from __future__ import annotations

import re
import time
from pathlib import Path

from .. import credits
from .. import flow_selectors as S
from ..models import Chapter, Character, Project, Scene
from ..veo_client import build_prompt
from ..flow_common import Budget, ERROR_RE, OVERLOAD_RE, FlowError, NoCreditError


class FlowClipMixin:
    """Điều khiển Flow: gửi prompt và gen clip cho scene (từng scene hoặc cuốn chiếu), kèm ảnh nhân vật."""

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
        self.download_res = p.flow_resolution if p.flow_model == credits.OMNI else None
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

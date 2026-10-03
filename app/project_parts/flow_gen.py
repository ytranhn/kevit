"""Tab Dự án: gen clip qua Flow, đồng bộ Flow, tạo giọng, ghép video, nhập/xuất thủ công."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .. import credits, flow, flow_auto, pipeline, veo_client
from ..merger import merge
from .. import accounts


class FlowGenMixin:
    """Tab Dự án: gen clip qua Flow, đồng bộ Flow, tạo giọng, ghép video, nhập/xuất thủ công."""

    def flow_auto_run(self, mode: str = "pending", silent: bool = False, then=None):
        """Gen scene của chương đang xem. silent=True (dùng khi gen nhiều chương): không hỏi xác nhận/cổng credit từng chương (đã hỏi một lần
        cho cả lô) và không bật hộp thoại; `then` được gọi khi chương xong (sau tự đồng bộ/ghép)."""
        if not self.need_project():
            return self._skip_run(then)
        if not self.scenes:
            if silent:
                self.log(f"[{self.chapter.name}] Chưa có scene, bỏ qua.")
                return self._skip_run(then)
            QMessageBox.warning(self, "Thiếu scene", "Bấm ① Tạo scene trước.")
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        todo = self.pick_todo(mode)
        if not todo:
            self.log(f"[{ch.name}] Không có scene nào để gen ({self.MODE_LABEL[mode]}).")
            return self._skip_run(then, ch) if silent else None
        redo = sum(1 for s in todo if s.status == "done")
        est = credits.estimate(p.flow_model, p.flow_resolution, todo, p.flow_auto_duration, p.narration_lang)
        if not silent and not self.credit_gate(est, lambda: self.flow_auto_run(mode)):
            return
        cfg = p.flow_model + (f" {p.flow_resolution}" if p.flow_model == credits.OMNI else "")
        acc_now = accounts.get(p.account_id)
        auto = accounts.auto_switch()
        if not silent and QMessageBox.question(self, "Xác nhận trừ credit",
                                f"Sẽ tạo {len(todo)} clip ({ch.name}: {self.MODE_LABEL[mode]}) bằng {cfg} trên Google Flow.\n"
                                + (f"⚠ {redo} scene đã xong sẽ bị gen LẠI và ghi đè clip cũ.\n" if redo else "") +
                                f"Ước tính khoảng {est} credit (giá thật hiện trong nhật ký).\n"
                                f"Tài khoản Flow: «{acc_now.name}» ({accounts.describe_credits(acc_now)})"
                                + ("; tự chuyển sang tài khoản khác khi hết credit.\n" if auto and len(accounts.all_accounts()) > 1 else ".\n")
                                + (f"Gửi song song {p.flow_parallel} scene mỗi lượt (đổi trong Cài đặt dự án).\n" if p.flow_parallel > 1 and len(todo) > 1 else "") +
                                "Chrome Flow phải đã đăng nhập. Tiếp tục?") != QMessageBox.Yes:
            return
        chars = {c.name: c for c in self.chars_tab.chars}
        prev = {id(s): (s.status, s.error) for s in todo}     # trạng thái trước khi xếp hàng đợi
        self._queue_prev = dict(prev)
        for s in todo:                      # hiện ngay hàng đợi trên bảng, không đợi tới lượt
            s.status, s.error = "queued", ""
        self._prog = (0, len(todo))
        self.fill_table(self._row)
        acc_start = accounts.active()
        costs_of = lambda scs: credits.scene_costs(p.flow_model, p.flow_resolution, scs, p.flow_auto_duration, p.narration_lang)

        def job(log):
            # Làm giọng + ghép là việc chạy trên máy, không phụ thuộc Flow: cho chạy nền nhiều luồng để không chặn việc
            # gửi/nhận clip tiếp theo. (Phần điều khiển Flow vẫn một luồng cho mỗi tài khoản vì chỉ có một trình duyệt.)
            lock = threading.Lock()
            pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong")
            finished = 0
            par = max(1, p.flow_parallel)

            def save():
                with lock:
                    p.save()

            def count_done():
                nonlocal finished
                with lock:
                    finished += 1
                    self._prog = (finished, len(todo))

            def finish_work(s):
                try:
                    s.status = "raw"
                    pipeline.apply_voice(p, ch, s, log)
                    s.status = "done"
                    log(f"[{ch.name}] Scene {s.index} xong.")
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                save()
                count_done()

            def finish(s):
                pool.submit(finish_work, s)

            def run_batch(f, batch, budget):
                """Gen `batch` trên phiên Flow `f`. Trả về các scene CHƯA gửi vì không đủ credit (để chuyển tài khoản)."""
                if par > 1 and len(batch) > 1:
                    log(f"Gen song song: luôn giữ {par} scene đang render trên Flow, clip nào xong thì gửi scene kế tiếp.")
                    out_dir = lambda s: p.chapter_dir(ch) / "clips"
                    overloaded = []

                    def on_event(s, kind, msg):     # giao diện cập nhật từng scene ngay khi gửi / lỗi
                        if kind == "sent":
                            s.status, s.error = "generating", ""
                        else:
                            s.status, s.error = "error", msg
                            count_done()
                            if "quá tải" in msg:
                                overloaded.append(s)
                        save()

                    unsent = f.generate_sliding(p, ch, batch, chars, out_dir, par, on_event, finish, self._cancel, budget=budget) or []
                    if overloaded:
                        log("Flow đang quá tải: đã dừng gen các scene còn lại, hãy thử lại sau ít phút (credit của yêu cầu lỗi được Flow hoàn).")
                    elif self._cancel.is_set():
                        log("Đã dừng theo yêu cầu. Các scene còn lại giữ nguyên trạng thái trước đó.")
                    return unsent
                unsent = []
                for i, s in enumerate(batch):
                    if self._cancel.is_set():
                        log("Đã dừng theo yêu cầu. Các scene còn lại giữ nguyên trạng thái trước đó.")
                        break
                    s.status, s.error = "generating", ""
                    save()
                    try:
                        s.raw_clip = str(f.generate_scene(p, ch, s, chars, p.chapter_dir(ch) / "clips", budget))
                    except flow_auto.NoCreditError as e:
                        log(str(e))
                        s.status, s.error = "queued", ""
                        unsent = batch[i:]
                        break
                    except Exception as e:  # noqa: BLE001
                        s.status, s.error = "error", str(e)[:1500]
                        log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                        save()
                        count_done()
                        continue
                    finish(s)               # làm giọng chạy nền trong lúc Flow gen scene kế tiếp
                return unsent

            def out_of_credit(scs, why):
                for s in scs:
                    s.status, s.error = "error", why
                    count_done()
                save()

            acc, used, first, remaining = acc_start, set(), True, list(todo)
            try:
                while remaining and not self._cancel.is_set():
                    # ---- mở phiên Flow của tài khoản `acc` và đọc credit thật ----
                    try:
                        f_ctx = flow_auto.FlowAuto(log, acc=acc)
                        f = f_ctx.__enter__()
                    except Exception as e:  # noqa: BLE001
                        if first:
                            raise
                        log(f"Không dùng được tài khoản «{acc.name}» ({str(e)[:100]}), thử tài khoản khác.")
                        used.add(acc.id)
                        acc = next_account(used)
                        if acc is None:
                            break
                        continue
                    try:
                        left = None
                        try:
                            info = f.read_credits(deep=False)
                        except flow_auto.FlowError as e:
                            if first:
                                raise
                            log(f"Tài khoản «{acc.name}» chưa đăng nhập Flow: {str(e)[:100]}")
                            info = False
                        if info:
                            left = info["credits"]
                            accounts.save_credits(acc.id, left, email=info.get("email", ""))
                            log(f"Tài khoản «{acc.name}»: còn {accounts.fmt_credits(left)} credit.")
                        elif info is None:
                            log(f"Không đọc được credit của «{acc.name}»: cứ chạy, Flow sẽ báo nếu thiếu.")
                        usable = info is not False
                        if usable:
                            f.ensure_project(p, ch)
                            if first:
                                # scene lỗi do ngắt/tải thất bại có thể đã render xong trên Flow: lấy lại thay vì trả credit lần nữa
                                maybe = [s for s in todo if prev[id(s)][0] == "error"
                                         and any(k in (prev[id(s)][1] or "") for k in ("Bị ngắt", "Không tải được", "quá 15 phút", "chưa thấy clip"))]
                                if maybe:
                                    log("Kiểm tra clip đã render sẵn trên Flow trước khi gen lại (tránh trả credit trùng)...")
                                    recovered = f.sync_clips(p, ch, maybe, lambda s: p.chapter_dir(ch) / "clips")
                                    for s in recovered:
                                        try:
                                            s.status, s.error = "raw", ""
                                            pipeline.apply_voice(p, ch, s, log)
                                            s.status = "done"
                                        except Exception as e:  # noqa: BLE001
                                            s.status, s.error = "error", str(e)[:1500]
                                        p.save()
                                    finished = len(recovered)
                                    self._prog = (finished, len(todo))
                                    remaining = [x for x in remaining if x not in recovered]
                                if not remaining:
                                    break
                            take_i, rest_i = accounts.split_by_credits(costs_of(remaining), left)
                            if first and rest_i and not auto:
                                need = sum(costs_of(remaining))
                                raise flow_auto.NoCreditError(
                                    f"Không đủ credit: tài khoản «{acc.name}» còn {accounts.fmt_credits(left)}, cần khoảng {accounts.fmt_credits(need)}. "
                                    "Chưa gen gì. Nạp thêm credit, đổi tài khoản (chip Flow) hoặc bật tự chuyển tài khoản.")
                            batch = [remaining[i] for i in take_i]
                            later = [remaining[i] for i in rest_i]
                            if first and auto and (rest_i or left is None):
                                warm = [a for a in accounts.order_candidates({acc.id}) if not flow_auto._cdp_up(a.cdp_url)][:1]
                                for a in warm:                      # mở sẵn Chrome của tài khoản dự phòng để chuyển không phải chờ
                                    threading.Thread(target=lambda a=a: _quiet(flow_auto.launch_chrome, a), daemon=True).start()
                            if batch:
                                if len(batch) < len(remaining):
                                    log(f"Tài khoản «{acc.name}» đủ credit cho {len(batch)}/{len(remaining)} scene; phần còn lại sẽ chuyển tài khoản khác.")
                                unsent = run_batch(f, batch, flow_auto.Budget(left))
                            else:
                                unsent = []
                            remaining = unsent + later
                            try:                                    # cập nhật credit còn lại sau lượt gen (không bắt buộc)
                                info2 = f.read_credits(deep=False)
                                if info2:
                                    accounts.save_credits(acc.id, info2["credits"], email=info2.get("email", ""))
                            except Exception:  # noqa: BLE001
                                pass
                    finally:
                        f_ctx.__exit__(None, None, None)
                    first = False
                    if not remaining or self._cancel.is_set():
                        break
                    # ---- còn scene chưa gen vì hết credit: chuyển tài khoản (nếu bật) ----
                    used.add(acc.id)
                    nxt = next_account(used) if auto else None
                    if nxt is None:
                        why = (f"Hết credit trên tài khoản «{acc.name}»" + ("" if not auto else " và không tài khoản nào khác đủ credit")
                               + ". Nạp thêm credit/đổi tài khoản rồi gen lại các scene còn lại.")
                        log(why)
                        out_of_credit(remaining, why)
                        remaining = []
                        break
                    log(f"Chuyển tài khoản Flow: «{acc.name}» → «{nxt.name}» (còn {len(remaining)} scene).")
                    p.use_flow_account(nxt.id)
                    p.save()
                    accounts.activate(nxt.id)
                    self.account_changed.emit(nxt.id)
                    acc = nxt
            finally:
                pool.shutdown(wait=True)        # đợi các scene còn đang làm giọng/ghép

        def next_account(used):
            """Tài khoản kế tiếp để thử: nhiều credit (đã biết) trước; bỏ qua tài khoản đã biết là không đủ cho scene rẻ nhất."""
            cheapest = min(costs_of(todo) or [0])
            for a in accounts.order_candidates(used):
                c = accounts.known_credits(a)
                if c is None or c >= cheapest:
                    return a
            return None

        def _quiet(fn, *a):
            try:
                fn(*a)
            except Exception:  # noqa: BLE001
                pass

        def on_fail(msg):
            if "Không đủ credit" in msg:
                if silent and getattr(self, "_batch", None):
                    self._batch["stop"] = True          # hết credit giữa lô: dừng cả lô, thông báo ở cuối
                    self._batch["why"] = msg
                else:
                    QMessageBox.warning(self, "Không đủ credit", msg)
        self._prog = (0, len(todo))
        # sau lượt gen: tự đồng bộ Flow nếu còn scene lỗi, tự ghép nếu chương đã đủ scene, rồi mới báo hoàn tất (tab Đăng video dùng tín hiệu này)
        after = then if then is not None else self.generation_done.emit
        self.run(job, lambda _: None, cancelable=True, on_fail=on_fail, then=lambda: self._finish_chapter(ch, after))

    def flow_sync(self, chapters=None, then=None, quiet: bool = False) -> bool:
        """Đối soát với Flow, không tốn credit: (1) scene đã có clip gốc nhưng chưa xong -> tạo giọng + ghép;
        (2) scene chưa có clip -> tìm clip đã render trên Flow theo nội dung prompt và tải về. `chapters`: chỉ đối soát các chương này
        (mặc định tất cả). `then`: gọi khi xong; mặc định tự ghép các chương đã đủ scene. Trả True nếu đã bắt đầu một tiến trình nền."""
        if not self.need_project():
            return False
        self.save_edits()
        p = self.project
        scope = set(map(id, chapters)) if chapters is not None else None
        owner = {id(s): ch for ch, s in p.all_scenes()}
        pairs = [(ch, s) for ch, s in p.all_scenes() if scope is None or id(ch) in scope]
        has_raw = lambda s: bool(s.raw_clip and Path(s.raw_clip).exists())
        heal = [s for _, s in pairs if s.status != "done" and has_raw(s)]
        missing = [s for _, s in pairs if s.status != "done" and not has_raw(s)]
        if then is None:
            then = self._merge_ready_chapters
        if not heal and not missing:
            if not quiet:
                self.log("Mọi scene đều đã xong, không có gì để đồng bộ.")
            return False

        def finish(s, log):
            try:
                s.status, s.error = "raw", ""
                pipeline.apply_voice(p, owner[id(s)], s, log)
                s.status = "done"
                log(f"[{owner[id(s)].name}] Scene {s.index}: xong.")
            except Exception as e:  # noqa: BLE001
                s.status, s.error = "error", str(e)[:1500]
                log(f"[{owner[id(s)].name}] Scene {s.index} lỗi: {e}")
            p.save()

        def job(log):
            if heal:
                log(f"{len(heal)} scene đã có clip nhưng chưa hoàn tất: đang tạo giọng và ghép (3 luồng song song)...")
                done_n = [0]

                def one(s):
                    if self._cancel.is_set():
                        return
                    finish(s, log)
                    done_n[0] += 1
                    self._prog = (done_n[0], len(heal))
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="giong") as pool:
                    list(pool.map(one, heal))
                if self._cancel.is_set():
                    log("Đã dừng theo yêu cầu.")
                    return
            if missing:
                with flow_auto.FlowAuto(log) as f:
                    by_ch: dict[str, list] = {}               # mỗi chương có project Flow riêng: đối soát lần lượt từng chương
                    for s in missing:
                        by_ch.setdefault(owner[id(s)].id, []).append(s)
                    for cid, group in by_ch.items():
                        if self._cancel.is_set():
                            break
                        c = owner[id(group[0])]
                        log(f"[{c.name}] đối soát {len(group)} scene với project Flow của chương...")
                        for s in f.sync_clips(p, c, group, lambda s: p.chapter_dir(owner[id(s)]) / "clips"):
                            finish(s, log)
                    for s in missing:  # kẹt "đang gen" mà Flow không có clip -> trả về chờ gen
                        if s.status == "generating":
                            s.status, s.error = "pending", ""
                    p.save()
            log("Đồng bộ xong.")
        self.run(job, lambda _: None, cancelable=bool(heal), then=then)
        return True

    def generate(self, mode: str = "pending"):
        """Đường Gemini API (Veo) — chỉ dùng khi có key."""
        if not self.need_project(need_key=True):
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        chars = {c.name: c for c in self.chars_tab.chars}
        todo = self.pick_todo(mode)
        if not todo:
            self.log(f"Không có scene nào để gen ({self.MODE_LABEL[mode]}).")
            return

        self._queue_prev = {id(s): (s.status, s.error) for s in todo}
        for s in todo:
            s.status, s.error = "queued", ""
        self.fill_table(self._row)

        def job(log):
            for k, s in enumerate(todo):
                if self._cancel.is_set():
                    log("Đã dừng theo yêu cầu.")
                    break
                self._prog = (k, len(todo))
                s.status, s.error = "generating", ""
                p.save()
                try:
                    s.raw_clip = str(veo_client.generate_clip(p, s, chars, p.chapter_dir(ch) / "clips", log))
                    pipeline.apply_voice(p, ch, s, log)
                    s.status = "done"
                    log(f"[{ch.name}] Scene {s.index} xong.")
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"[{ch.name}] Scene {s.index} lỗi: {e}")
                p.save()
            self._prog = (len(todo), len(todo))
        self.run(job, lambda _: None, cancelable=True)

    def revoice(self, only_current: bool):
        """Đổi giọng/lời: chỉ tạo lại TTS + ghép trên clip gốc, không tốn credit Flow."""
        if not self.need_project():
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        pool = self.pick_todo("current") if only_current else ch.scenes
        todo = [s for s in pool if s.raw_clip and Path(s.raw_clip).exists()]
        if not todo:
            self.log("Chưa có clip gốc để áp dụng giọng.")
            return

        def job(log):
            for k, s in enumerate(todo):
                if self._cancel.is_set():
                    log("Đã dừng theo yêu cầu.")
                    break
                self._prog = (k, len(todo))
                try:
                    pipeline.apply_voice(p, ch, s, log)
                    s.status, s.error = "done", ""
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"Scene {s.index} lỗi: {e}")
                p.save()
            self._prog = (len(todo), len(todo))
        self.run(job, lambda _: None, cancelable=True)

    def merge_all(self):
        """Ghép các scene của CHƯƠNG đang chọn thành 1 video chương."""
        if not self.need_project():
            return
        self.save_edits()
        p, ch = self.project, self.chapter
        clips = [Path(s.clip) for s in ch.scenes if s.status == "done" and s.clip and Path(s.clip).exists()]
        if not clips or len(clips) != len(ch.scenes):
            QMessageBox.warning(self, "Chưa đủ clip",
                                f"Cần gen xong tất cả scene của {ch.name} (trạng thái Xong) trước khi ghép.")
            return
        out = p.merged_path(ch)
        self.log(f"[{ch.name}] Đang ghép video...")

        def done(o):
            self.log(f"Xong: {o}")
            self.set_view_mode(1)
            self.preview.load(str(o), autoplay=True)
        self._keep_preview = True
        self.run(lambda log: merge(clips, out), done)

    def merge_project(self):
        """Ghép tất cả chương (theo thứ tự) thành 1 video của cả dự án."""
        if not self.need_project():
            return
        self.save_edits()
        p = self.project
        missing = [ch.name for ch in p.chapters
                   if not ch.scenes or any(not (s.status == "done" and s.clip and Path(s.clip).exists()) for s in ch.scenes)]
        if missing:
            QMessageBox.warning(self, "Chưa đủ clip", "Các chương chưa gen xong hết scene:\n- " + "\n- ".join(missing))
            return
        clips = [Path(s.clip) for _, s in p.all_scenes()]
        out = p.full_path
        self.log(f"Đang ghép toàn bộ {len(p.chapters)} chương ({len(clips)} scene)...")

        def done(o):
            self.log(f"Xong: {o}")
            self.set_view_mode(1)
            self.preview.load(str(o), autoplay=True)
        self._keep_preview = True
        self.run(lambda log: merge(clips, out), done)


    # ================= Flow thủ công =================
    def flow_export(self):
        if not self.need_project() or not self.scenes:
            return
        self.save_edits()
        chars = {c.name: c for c in self.chars_tab.chars}
        d = flow.export_all(self.project, self.chapter, chars)
        self.log(f"Đã xuất prompt + ảnh nhân vật vào {d}.")
        flow.reveal(d)

    def flow_copy(self):
        if not 0 <= self._row < len(self.scenes):
            return
        self.save_edits()
        s = self.scenes[self._row]
        chars = {c.name: c for c in self.chars_tab.chars}
        QApplication.clipboard().setText(veo_client.build_prompt(self.project, s, chars))
        flow.export_scene(self.project, self.chapter, s, chars)
        self.log(f"Đã copy prompt scene {s.index}.")

    def flow_import(self):
        if not self.need_project() or not self.scenes:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn clip tải từ Flow", "", "Video (*.mp4 *.mov)")
        if not files:
            return
        p, ch = self.project, self.chapter
        got = flow.import_clips(p, ch, files, max(self._row, 0))
        p.save()
        self.log(f"Nhập {len(got)} clip từ scene {got[0].index}. Đang thêm giọng đọc...")

        def job(log):
            for s in got:
                try:
                    pipeline.apply_voice(p, ch, s, log)
                    s.status = "done"
                except Exception as e:  # noqa: BLE001
                    s.status, s.error = "error", str(e)[:1500]
                    log(f"Scene {s.index} lỗi: {e}")
                p.save()
        self.run(job, lambda _: None)

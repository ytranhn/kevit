"""Tab Dự án: đồng bộ clip từ Flow, tạo lại giọng, ghép video, nhập/xuất thủ công."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .. import flow, flow_auto, pipeline, veo_client
from ..merger import merge


class ClipToolsMixin:
    """Tab Dự án: đồng bộ clip từ Flow, tạo lại giọng, ghép video, nhập/xuất thủ công."""

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

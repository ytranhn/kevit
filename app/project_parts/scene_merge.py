"""Tab Dự án: ghép scene thủ công và gợi ý ghép bằng AI."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QVBoxLayout)

from .. import credits, llm, scene_ops, scene_planner
from ..models import nfc
from ..theme import SP


class SceneMergeMixin:
    """Tab Dự án: ghép scene thủ công và gợi ý ghép bằng AI."""

    # ---- gộp scene ----
    def _merge_data_job(self, groups: list[list]):
        """Tạo nội dung gộp cho từng nhóm: dùng LLM nếu đã cấu hình, lỗi hoặc chưa có thì dùng cách gộp đơn giản."""
        chars = self.chars_tab.chars
        use_llm = llm.is_configured()[0]
        lang = self.project.narration_lang if self.project else "vi"

        def job(log):
            out = []
            for g in groups:
                data, how = None, "đơn giản"
                if use_llm:
                    try:
                        data, how = scene_planner.merge_scenes_llm(g, chars, log, lang), "LLM"
                    except Exception as e:  # noqa: BLE001
                        log(f"LLM gộp lỗi ({str(e)[:80]}), dùng cách gộp đơn giản.")
                out.append((g, data or scene_ops.merge_heuristic(g), how))
            return out
        return job

    def merge_selected(self):
        sel = self.selected_scenes()
        if len(sel) < 2 or not scene_ops.is_contiguous(self.chapter, sel):
            QMessageBox.information(self, "Gộp scene", "Hãy chọn từ 2 scene liền kề nhau (Shift+click).")
            return
        self.save_edits()
        self.log(f"[{self.chapter.name}] Đang gộp scene {', '.join(str(s.index) for s in sel)}...")

        def done(res):
            group, data, how = res[0]
            self.show_merge_preview(group, data, how)
        self.run(self._merge_data_job([sel]), done)

    def show_merge_preview(self, group, data, how):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Gộp scene {', '.join(str(s.index) for s in group)} ({how})")
        dlg.resize(620, 520)
        title = QLineEdit(data["title"])
        chars = QLineEdit(", ".join(data["characters"]))
        narr = QPlainTextEdit(data["narration"])
        visual = QPlainTextEdit(data["visual"])
        count = QLabel("")
        count.setProperty("caption", True)

        def upd():
            w = len(narr.toPlainText().split())
            count.setText(f"{w} từ ≈ {w / 3.3:.0f}s" + ("  ⚠ dài hơn clip 8-10s" if w > 34 else ""))
        narr.textChanged.connect(upd)
        upd()
        lost = sum(len(scene_ops.scene_files(s)) for s in group)
        note = QLabel((f"⚠ {lost} file video/giọng của các scene này sẽ chuyển vào thùng rác (nội dung đã đổi nên phải gen lại). "
                       if lost else "") + "Bạn có thể sửa nội dung trước khi gộp.")
        note.setWordWrap(True)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(note)
        for cap, w in (("Tiêu đề", title), ("Nhân vật (tối đa 3)", chars)):
            lay.addWidget(QLabel(cap))
            lay.addWidget(w)
        head = QHBoxLayout()
        head.addWidget(QLabel("Thuyết minh"))
        head.addStretch()
        head.addWidget(count)
        lay.addLayout(head)
        lay.addWidget(narr, 2)
        lay.addWidget(QLabel("Visual (prompt gửi Flow)"))
        lay.addWidget(visual, 2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Gộp")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        valid = {nfc(c.name): c.name for c in self.chars_tab.chars}
        names = [valid[nfc(n.strip())] for n in chars.text().split(",") if nfc(n.strip()) in valid]
        merged = {"title": title.text().strip() or data["title"], "narration": narr.toPlainText().strip(),
                  "visual": visual.toPlainText().strip(), "characters": names[:3]}
        new = scene_ops.merge_group(self.project, self.chapter, group, merged)
        self.log(f"[{self.chapter.name}] Đã gộp {len(group)} scene thành scene {new.index}. Cần gen video cho scene này.")
        self._row = -1
        self._after_edit(new.index - 1)

    def suggest_merge_dialog(self):
        if not self.chapter:
            return
        self.save_edits()
        groups = scene_ops.suggest_merges(self.chapter)
        if not groups:
            QMessageBox.information(self, "Gợi ý gộp",
                                    "Không có nhóm scene ngắn liền kề nào phù hợp (mỗi scene ≤14 từ, tổng ≤32 từ, chưa có video).")
            return
        p = self.project
        per = credits.scene_cost(p.flow_model, p.flow_resolution, 8)
        dlg = QDialog(self)
        dlg.setWindowTitle("Gợi ý gộp scene ngắn")
        dlg.resize(640, 420)
        lst = QListWidget()
        for g in groups:
            txt = " / ".join(s.narration for s in g)
            it = QListWidgetItem(f"Scene {' + '.join(str(s.index) for s in g)}  ({sum(scene_ops.words(s) for s in g)} từ)  —  {txt[:90]}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            lst.addItem(it)
        info = QLabel("")
        info.setProperty("caption", True)

        def upd():
            chosen = [g for i, g in enumerate(groups) if lst.item(i).checkState() == Qt.Checked]
            saved = sum(len(g) - 1 for g in chosen)
            info.setText(f"Gộp {len(chosen)} nhóm: bớt {saved} scene, tiết kiệm khoảng {saved * per} credit Flow ({p.flow_model}).")
        lst.itemChanged.connect(upd)
        upd()
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.xl)
        lay.setSpacing(SP.m)
        lay.addWidget(QLabel("Các scene liền kề đều ngắn có thể gộp thành 1 clip. Bỏ tick nhóm nào bạn không muốn gộp."))
        lay.addWidget(lst, 1)
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Gộp các nhóm đã chọn")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = [g for i, g in enumerate(groups) if lst.item(i).checkState() == Qt.Checked]
        if not chosen:
            return
        ch = self.chapter
        self.log(f"[{ch.name}] Đang gộp {len(chosen)} nhóm scene...")

        def done(results):
            n = 0
            for g, data, how in results:
                scene_ops.merge_group(self.project, ch, g, data)
                n += len(g) - 1
            self.log(f"[{ch.name}] Đã gộp {len(results)} nhóm, bớt {n} scene. Cần gen video cho các scene mới gộp.")
            self._row = -1
            self._after_edit(0)
        self.run(self._merge_data_job(chosen), done)

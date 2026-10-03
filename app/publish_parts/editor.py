"""Tab Đăng video: chọn tài khoản/nền tảng, soạn tiêu đề-mô tả-hashtag, tuỳ chọn đăng."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .. import publish
from ..publish import base, describe, service
from .widgets import HashChip, AccountPick, MAX_TAGS


class EditorMixin:
    """Tab Đăng video: chọn tài khoản/nền tảng, soạn tiêu đề-mô tả-hashtag, tuỳ chọn đăng."""

    # ================= tài khoản / nền tảng =================
    def refresh_accounts(self) -> None:
        """Dựng lại các thẻ nền tảng từ tài khoản đã kết nối; tích sẵn những tài khoản dự án đã chọn."""
        p = self.get_project()
        chosen = set(p.publish_accounts) if p else set()
        was = self._loading
        self._loading = True
        try:
            while self.tiles_row.count():
                w = self.tiles_row.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self._tiles = []
            self.acc_checks = {}
            accs = publish.accounts()
            for a in accs:
                row = AccountPick(a, a["id"] in chosen)
                row.check.toggled.connect(self.save_options)
                self.acc_checks[a["id"]] = row.check
                self._tiles.append(row)
            if not accs:
                empty = QLabel("Chưa kết nối tài khoản nào.")
                empty.setProperty("caption", True)
                self._tiles.append(empty)
            missing = [cls.label for key, cls in publish.PLATFORMS.items() if not any(a["platform"] == key for a in accs)]
            self.not_connected.setText(("Chưa kết nối: " + ", ".join(missing) + ".") if missing and accs else "")
            self.not_connected.setVisible(bool(self.not_connected.text()))
            self.reflow_tiles()
        finally:
            self._loading = was
        self.update_buttons()
        self.update_steps()
        self.update_counters()

    def reflow_tiles(self) -> None:
        """Danh sách tài khoản: 2 cột khi đủ rộng, hẹp thì 1 cột."""
        cols = 2 if self.right_scroll.viewport().width() >= 760 else 1
        while self.tiles_row.count():
            self.tiles_row.takeAt(0)
        for i, t in enumerate(self._tiles):
            self.tiles_row.addWidget(t, i // cols, i % cols)
        for c in range(2):
            self.tiles_row.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._tiles:
            self.reflow_tiles()

    def chosen_accounts(self) -> list[str]:
        return [k for k, cb in self.acc_checks.items() if cb.isChecked()]

    # ================= nội dung đang soạn =================
    def limits(self) -> tuple[int, int, int]:
        """(tiêu đề, mô tả, hashtag) tối đa theo các nền tảng đang chọn: lấy mức chặt nhất."""
        plats = {a.split(":", 1)[0] for a in self.chosen_accounts()}
        return (100 if (plats & {"youtube", "facebook"} or not plats) else 200,
                2200 if plats & {"tiktok", "facebook", "instagram"} else 5000, MAX_TAGS)

    def update_counters(self) -> None:
        lt, ld, lh = self.limits()
        self._set_counter(self.cnt_title, len(self.edit_title.text()), lt)
        self._set_counter(self.cnt_desc, len(self.edit_desc.toPlainText()), ld)
        self._set_counter(self.cnt_tags, len(self._tags), lh)

    def on_text_changed(self) -> None:
        self.update_counters()
        self.update_steps()

    def show_target(self, t: service.Target | None) -> None:
        p = self.get_project()
        self._shown = t
        meta = service.meta_of(p, t) if (p and t) else {}
        self.edit_title.blockSignals(True)
        self.edit_desc.blockSignals(True)
        self.edit_title.setText(meta.get("title", ""))
        self.edit_desc.setPlainText(meta.get("description", ""))
        self.edit_title.blockSignals(False)
        self.edit_desc.blockSignals(False)
        self._tags = list(meta.get("hashtags", []))
        self.render_tags()
        pm, secs = self._thumbs.get(t.key, (None, 0.0)) if t else (None, 0.0)
        self.big_thumb.set(pm, secs)
        for w in (self.edit_title, self.edit_desc, self.tag_input, self.b_ai, self.b_save_meta, self.b_more, self.b_open_video):
            w.setEnabled(t is not None and self._worker is None)
        self.update_counters()
        self.update_steps()

    def render_tags(self) -> None:
        while self.tag_flow.count():
            it = self.tag_flow.takeAt(0)
            w = it.widget()
            if w is self.tag_input:
                w.setParent(None)
            elif w:
                w.deleteLater()
        for t in self._tags:
            chip = HashChip(t)
            chip.removed.connect(self.remove_tag)
            self.tag_flow.addWidget(chip)
        self.tag_flow.addWidget(self.tag_input)
        self.tag_input.show()
        self.tag_area.updateGeometry()
        self.update_counters()

    def add_tag_from_input(self) -> None:
        new = base.clean_tags(self.tag_input.text(), MAX_TAGS)
        self.tag_input.clear()
        if not new:
            return
        self._tags = base.clean_tags(self._tags + new, MAX_TAGS)
        self.render_tags()
        self.commit_editor(quiet=True)

    def remove_tag(self, tag: str) -> None:
        self._tags = [t for t in self._tags if t != tag]
        self.render_tags()
        self.commit_editor(quiet=True)

    def commit_editor(self, quiet: bool = False) -> None:
        p, t = self.get_project(), self._shown
        if not p or not t:
            return
        meta = dict(title=self.edit_title.text().strip(), description=self.edit_desc.toPlainText().strip(), hashtags=list(self._tags))
        old = service.meta_of(p, t)
        if meta != {k: old.get(k, [] if k == "hashtags" else "") for k in meta}:
            service.set_meta(p, t, meta)
            p.save()
            if not quiet:
                self.set_status("Đã lưu nội dung.", "ok")
            if t.key in self._rows:
                self._rows[t.key].update_info(self.row_info(p, t))
        elif not quiet:
            self.set_status("Nội dung không thay đổi.", "info")

    def ai_for_current(self) -> None:
        t, p = self._shown, self.get_project()
        if not (t and p):
            return
        self.commit_editor(quiet=True)

        def job(log):
            return describe.generate(p, t.chapter, log)

        def done(meta):
            service.set_meta(p, t, meta)
            p.save()
            if self._shown and self._shown.key == t.key:
                self.show_target(t)
            if t.key in self._rows:
                self._rows[t.key].update_info(self.row_info(p, t))
            self.set_status("AI đã viết xong, hãy xem lại rồi lưu/đăng.", "ok")
        self.start(job, done, "Đang nhờ AI viết mô tả…")

    # ================= tuỳ chọn =================
    def save_options(self, *_) -> None:
        p = self.get_project()
        if not p or self._loading:
            return
        p.publish_accounts = self.chosen_accounts() + [a for a in p.publish_accounts if a not in self.acc_checks]    # giữ lựa chọn của tài khoản đang ẩn
        p.publish_privacy = self.privacy.currentData() or "private"
        p.publish_auto = self.auto.isChecked()
        p.save()
        self.update_buttons()
        self.update_steps()
        self.update_counters()
        for t in self._targets:
            if t.key in self._rows:
                self._rows[t.key].update_info(self.row_info(p, t))

    def on_scope(self, *_) -> None:
        p = self.get_project()
        if not p:
            return
        self.commit_editor(quiet=True)
        p.publish_scope = self.scope.currentData()
        p.save()
        self._clear_rows()
        self._targets, self._shown = [], None
        self.rebuild_list()
        self.update_buttons()

    def update_steps(self) -> None:
        has_content = bool(self.edit_title.text().strip())
        has_accounts = bool(self.chosen_accounts())
        self.stepper.set_done([has_content, has_content and has_accounts, False])

    def update_buttons(self) -> None:
        idle = self._worker is None
        any_t = bool(self.checked_targets())
        self.b_prepare.setEnabled(idle and any_t)
        self.b_publish.setEnabled(idle and any_t and bool(self.chosen_accounts()))
        self.b_schedule.setEnabled(idle and any_t and bool(self.chosen_accounts()))
        self.b_stop.setVisible(not idle)

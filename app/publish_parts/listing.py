"""Danh sách video của tab Đăng video: nạp dữ liệu, lọc/sắp xếp, ảnh thumbnail, lịch sử."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

from .. import flow, icons, publish, theme
from ..publish import service
from ..workers import Worker
from .widgets import _fmt_date, VideoRow, FILTERS


class ListingMixin:
    """Danh sách video của tab Đăng video: nạp dữ liệu, lọc/sắp xếp, ảnh thumbnail, lịch sử."""

    # ================= nạp dữ liệu =================
    def showEvent(self, e):
        super().showEvent(e)
        self.reload()

    def reload(self) -> None:
        p = self.get_project()
        self._loading = True
        try:
            self.setEnabled(bool(p))
            if not p:
                self._clear_rows()
                self._targets, self._project_name = [], ""
                self.empty.setText("Hãy mở một dự án ở tab Dự án.")
                self.lbl_count.setText("Video sẽ đăng (0)")
                self.show_target(None)
                self.history.setRowCount(0)
                return
            if p.name != self._project_name:             # đổi dự án: bỏ lựa chọn/mục đang xem/ảnh của dự án trước
                self._clear_rows()
                self._project_name, self._targets, self._shown, self._thumbs = p.name, [], None, {}
            self.scope.setCurrentIndex(max(0, self.scope.findData(p.publish_scope)))
            self.privacy.setCurrentIndex(max(0, self.privacy.findData(p.publish_privacy)))
            self.auto.setChecked(p.publish_auto)
            self.refresh_accounts()
            self.rebuild_list()
            self.fill_history()
            self.refresh_schedule()
        finally:
            self._loading = False
        self.update_buttons()
        self.update_steps()

    # ---------- danh sách video ----------
    def _clear_rows(self) -> None:
        for r in self._rows.values():
            self.list_box.removeWidget(r)
            r.deleteLater()
        self._rows = {}

    def row_info(self, p, t: service.Target) -> dict:
        n, done = service.scene_count(p, t), len(service.clips_of(p, t))
        accounts = [a["id"] for a in publish.accounts()]
        target_accs = [a for a in p.publish_accounts if a in accounts] or accounts
        posted_any = any(service.history_ok(p, t, a) for a in accounts)
        posted_all = bool(target_accs) and all(service.history_ok(p, t, a) for a in target_accs)
        ready = service.is_ready(p, t)
        if posted_all:
            status, kind = "Đã đăng", "info"
        elif ready:
            status, kind = "Sẵn sàng", "ok"
        elif not n:
            status, kind = "Chưa có scene", "warn"
        else:
            status, kind = f"Gen {done}/{n} scene", "warn"
        name = service.meta_of(p, t).get("title") or t.label
        created = service.created_at(p, t)
        return dict(title=f"{t.key} - {name}" if t.chapter else name, status=status, kind=kind, ready=ready, posted_any=posted_any,
                    posted_all=posted_all, created=created, date=f"Tạo: {_fmt_date(created)}" if created else "Chưa có clip")

    def rebuild_list(self) -> None:
        p = self.get_project()
        keep_checked = {k for k, r in self._rows.items() if r.check.isChecked()}
        first = not self._rows
        keep_shown = self._shown.key if self._shown else None
        self._clear_rows()
        self._targets = service.targets(p)
        for t in self._targets:
            info = self.row_info(p, t)
            row = VideoRow(info)
            row.check.setChecked(not info["posted_all"] and (t.key in keep_checked or (first and info["ready"])))
            row.check.setEnabled(not info["posted_all"])
            row.clicked.connect(lambda k=t.key: self.select_key(k))
            row.checked.connect(self.on_checks_changed)
            row.action.connect(lambda a, k=t.key: self.row_action(k, a))
            pm, secs = self._thumbs.get(t.key, (None, 0.0))
            row.thumb.set(pm, secs)
            self._rows[t.key] = row
            self.list_box.insertWidget(self.list_box.count() - 1, row)
        self.apply_filter()
        self.select_key(keep_shown if keep_shown in self._rows else (self._targets[0].key if self._targets else None))
        self.load_thumbs()

    def apply_filter(self) -> None:
        p = self.get_project()
        if not p:
            return
        q = self.search.text().strip().lower()
        by_key = {t.key: t for t in self._targets}
        infos = {k: r.info for k, r in self._rows.items()}
        counts = dict(all=len(infos), ready=sum(1 for i in infos.values() if i["ready"] and not i["posted_all"]),
                      posted=sum(1 for i in infos.values() if i["posted_any"]))
        for k, label in FILTERS:
            b = self.tab_btns[k]
            b.setText(f"{label}  {counts[k]}")
            b.setMinimumWidth(b.sizeHint().width())      # không để nhóm lọc bị ép hẹp làm cụt chữ

        def show(k: str) -> bool:
            i = infos[k]
            ok = {"all": True, "ready": i["ready"] and not i["posted_all"], "posted": i["posted_any"]}[self._filter]
            return ok and (not q or q in i["title"].lower() or q in by_key[k].label.lower())
        order = self.sort.currentData() or "newest"
        keys = list(self._rows)
        if order == "newest":
            keys.sort(key=lambda k: -infos[k]["created"])
        elif order == "oldest":
            keys.sort(key=lambda k: infos[k]["created"])
        for r in self._rows.values():
            self.list_box.removeWidget(r)
        for i, k in enumerate(keys):
            self.list_box.insertWidget(i, self._rows[k])
            self._rows[k].setVisible(show(k))
        if not self._targets:
            self.empty.setText("Dự án chưa có chương nào.")
        else:
            self.empty.setText("" if any(show(k) for k in keys) else "Không có video nào khớp bộ lọc.")
        self.on_checks_changed()

    def set_filter(self, key: str) -> None:
        self._filter = key
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        self.apply_filter()

    def on_checks_changed(self, *_) -> None:
        vis = [r for r in self._rows.values() if not r.isHidden()]
        self.lbl_count.setText(f"Video sẽ đăng ({len(self.checked_targets())})")
        self.all_check.blockSignals(True)
        self.all_check.setChecked(bool(vis) and all(r.check.isChecked() for r in vis if not r.info["posted_all"]))
        self.all_check.blockSignals(False)
        self.update_buttons()

    def check_all(self, on: bool) -> None:
        for r in self._rows.values():
            if not r.isHidden() and not r.info["posted_all"]:
                r.check.setChecked(on)

    def checked_targets(self) -> list[service.Target]:
        return [t for t in self._targets if (r := self._rows.get(t.key)) and r.check.isChecked() and not r.isHidden() and not r.info["posted_all"]]

    def select_key(self, key: str | None) -> None:
        if key is not None and key not in self._rows:
            key = None
        self.commit_editor(quiet=True)
        for k, r in self._rows.items():
            r.set_selected(k == key)
        self.show_target(next((t for t in self._targets if t.key == key), None))

    # ---------- ảnh đại diện (tạo ở luồng nền) ----------
    def load_thumbs(self) -> None:
        p = self.get_project()
        if not p or self._thumb_worker is not None:
            return
        todo = [t for t in self._targets if service.clips_of(p, t) and t.key not in self._thumbs]
        if not todo:
            return

        def job(log):
            out = {}
            for t in todo:
                try:
                    out[t.key] = (service.thumbnail(p, t), service.video_seconds(p, t))
                except Exception:  # noqa: BLE001 - thiếu ảnh không phải lỗi nghiêm trọng
                    out[t.key] = (None, 0.0)
            return out

        def done(res):
            for k, (path, secs) in res.items():
                pm = QPixmap(str(path)) if path else None
                self._thumbs[k] = (pm, secs)
                if k in self._rows:
                    self._rows[k].thumb.set(pm, secs)
                if self._shown and self._shown.key == k:
                    self.big_thumb.set(pm, secs)
        self._thumb_worker = w = Worker(job)
        w.done.connect(done)
        w.finished.connect(lambda: setattr(self, "_thumb_worker", None))
        w.start()

    def row_action(self, key: str, action: str) -> None:
        p = self.get_project()
        t = next((x for x in self._targets if x.key == key), None)
        if not (p and t):
            return
        if action == "reveal":
            f = service.video_path(p, t)
            if f.exists():
                flow.reveal_file(f)
            else:
                QMessageBox.information(self, "Chưa có video ghép", "Video chưa được ghép. Bấm “Ghép + viết mô tả” trước.")
        elif action == "remerge":
            service.video_path(p, t).unlink(missing_ok=True)
            self.set_status("Đã xoá video ghép cũ, lần đăng sau sẽ ghép lại.", "info")
            self.rebuild_list()
        elif action == "clear":
            service.set_meta(p, t, {})
            p.save()
            self.rebuild_list()
            self.show_target(self._shown)

    # ---------- lịch sử ----------
    def fill_history(self) -> None:
        p = self.get_project()
        names = {t.key: t.label for t in service.targets(p)} if p else {}
        rows = list(reversed(p.publish_history[-100:])) if p else []
        expanded = self.hist_toggle.isChecked()
        self.history.setRowCount(len(rows))
        self.hist_empty.setVisible(not rows and expanded)
        self.history.setVisible(bool(rows) and expanded)
        for r, h in enumerate(rows):
            who = publish.account_name(h["account"]) if h.get("account") else h.get("platform", "")
            if h.get("ok"):
                result = "✓ " + (service.PRIVACY_LABELS.get(h.get("privacy"), h.get("privacy", "")) or "Thành công") + (f" — {h['message']}" if h.get("message") else "")
                color = theme.T["ok"]
            else:
                result, color = "✗ " + (h.get("message") or "Lỗi"), theme.T["err"]
            link = f"Xem trên {publish.PLATFORMS[h['platform']].label}" if h.get("url") and h.get("platform") in publish.PLATFORMS else ""
            cells = [h.get("time", ""), f"{names.get(h.get('chapter'), h.get('chapter', ''))} · {who}", result, link]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(h.get("message") or text)
                if c == 2:
                    it.setForeground(QColor(color))
                if c == 3 and link:
                    it.setData(Qt.UserRole, h["url"])
                    it.setForeground(QColor(theme.T["info"]))
                self.history.setItem(r, c, it)

    def toggle_history(self, on: bool) -> None:
        icons.attach(self.hist_caret, "up" if on else "down", 18, role="muted")
        self.fill_history()

    def open_history_link(self, row: int, col: int) -> None:
        it = self.history.item(row, 3)
        url = it.data(Qt.UserRole) if it else None
        if url:
            QDesktopServices.openUrl(QUrl(url))

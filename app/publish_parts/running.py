"""Tab Đăng video: chạy nền (ghép, viết mô tả, đăng) và tự đăng sau khi gen xong."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from .. import publish
from ..publish import service
from ..widgets import repolish
from ..workers import Worker


class RunMixin:
    """Tab Đăng video: chạy nền (ghép, viết mô tả, đăng) và tự đăng sau khi gen xong."""

    # ================= chạy nền =================
    def set_status(self, text: str, kind: str = "info") -> None:
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.setVisible(bool(text))
        repolish(self.status)
        self.activity.emit(text, {"ok": "ok", "err": "error", "warn": "warn"}.get(kind, "info"))

    def log(self, msg: str) -> None:
        self.log_sink(msg)
        low = msg.lower()
        self.activity.emit(msg.splitlines()[0][:300] if msg else "", "error" if "lỗi" in low else "info")

    def _lockables(self) -> list[QWidget]:
        return [self.scope, self.sort, self.search, self.privacy, self.auto, self.force, self.edit_title, self.edit_desc, self.tag_input,
                self.b_ai, self.b_save_meta, self.b_more, self.b_refresh, self.all_check,
                *self.acc_checks.values(), *(r.check for r in self._rows.values())]

    def start(self, fn, on_done, message: str) -> None:
        self._cancel.clear()
        self._worker = w = Worker(fn)
        self.set_status(message, "info")
        self.b_stop.setEnabled(True)
        w.log.connect(self.log)
        w.done.connect(on_done)
        w.failed.connect(lambda e: (self.log(f"LỖI: {e}"), self.set_status(f"Lỗi: {e[:240]}", "err")))
        w.finished.connect(self._finished)
        self.update_buttons()
        w.start()
        for wd in self._lockables():
            wd.setEnabled(False)

    def _finished(self) -> None:
        self._worker = None
        status, kind = self.status.text(), self.status.property("kind")
        self.reload()
        for wd in self._lockables():
            wd.setEnabled(True)
        self.set_status(status, kind or "info")

    def prepare(self) -> None:
        p, tgts = self.get_project(), self.checked_targets()
        if not (p and tgts):
            return
        self.commit_editor(quiet=True)

        def job(log):
            out = []
            for t in tgts:
                if self._cancel.is_set():
                    break
                try:
                    service.ensure_video(p, t, log)
                    service.ensure_meta(p, t, log)
                    out.append((t.label, ""))
                except Exception as e:  # noqa: BLE001
                    log(f"[{t.label}] LỖI: {e}")
                    out.append((t.label, str(e)))
            return out

        def done(out):
            bad = [f"{n}: {e}" for n, e in out if e]
            self.set_status(f"Đã chuẩn bị {len(out) - len(bad)}/{len(out)} video." + (f" Lỗi: {bad[0][:200]}" if bad else ""), "warn" if bad else "ok")
        self.start(job, done, "Đang ghép video và viết mô tả…")

    def publish(self) -> None:
        p, tgts = self.get_project(), self.checked_targets()
        accs = self.chosen_accounts()
        if not (p and tgts and accs):
            return
        self.commit_editor(quiet=True)
        priv = self.privacy.currentData()
        lines = [f"• {len(tgts)} video × {len(accs)} tài khoản:\n   " + "\n   ".join(publish.account_name(a) for a in accs),
                 f"• Chế độ: {service.PRIVACY_LABELS[priv]}"]
        if priv == "public":
            lines.append("\n⚠ Công khai: bài sẽ hiện ngay với mọi người và không thể thu hồi từ Kevit.")
        if self.force.isChecked():
            lines.append("\n⚠ Sẽ đăng LẠI cả những mục đã đăng (có thể trùng bài).")
        if QMessageBox.question(self, "Xác nhận đăng", "\n".join(lines) + "\n\nTiếp tục?") != QMessageBox.Yes:
            return
        self.run_publish(p, tgts, accs, priv, self.force.isChecked())

    def run_publish(self, p, tgts, accs, priv, force=False) -> None:
        def job(log):
            return service.run(p, tgts, accs, priv, log, self._cancel, force)

        def done(results):
            ok = sum(1 for r in results if r.ok)
            bad = [r for r in results if not r.ok]
            text = f"Đã đăng {ok}/{len(results)} bài." + (f" Lỗi {publish.account_name(bad[0].account)}: {bad[0].message[:200]}" if bad else "")
            self.set_status(text, "warn" if bad else "ok")
        self.start(job, done, "Đang đăng…")


    # ================= tự động =================
    def on_generation_done(self) -> None:
        """Một đợt gen clip vừa xong: nếu bật tự động thì ghép + viết mô tả + đăng các chương đã đủ clip và chưa đăng."""
        p = self.get_project()
        if not p or not p.publish_auto or self._worker is not None:
            return
        accs = [a for a in p.publish_accounts if publish.store.get_account(a).get("access_token")]
        for a in set(p.publish_accounts) - set(accs):
            self.log(f"Tự động đăng: bỏ qua {publish.account_name(a)} vì tài khoản chưa kết nối hoặc đã bị xoá.")
        todo = [t for t in service.targets(p) if service.is_ready(p, t) and any(not service.history_ok(p, t, a) for a in accs)]
        if not (accs and todo):
            return
        self.log(f"Tự động đăng: {len(todo)} video lên {', '.join(publish.account_name(a) for a in accs)} ({service.PRIVACY_LABELS[p.publish_privacy]}).")
        self.run_publish(p, todo, accs, p.publish_privacy)

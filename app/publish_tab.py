"""Tab Đăng video: chọn chương, ghép video, viết mô tả/hashtag bằng AI rồi đăng lên các nền tảng qua API."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import icons
from .llm_panel import card, field
from .publish import PLATFORMS, base, describe, service
from .shell import PageHeader
from .theme import SP
from .widgets import repolish
from .workers import Worker

PRIVACY = (("private", "Riêng tư (an toàn để thử)"), ("unlisted", "Không công khai (có link)"), ("public", "Công khai"))
SCOPES = (("chapters", "Mỗi chương một video"), ("project", "Một video cho cả dự án"))


def _tags_text(tags: list[str]) -> str:
    return " ".join("#" + t for t in tags)


class PublishTab(QWidget):
    activity = Signal(str, str)           # (nội dung, mức) -> thanh trạng thái

    def __init__(self, get_project, log=print):
        super().__init__()
        self.get_project, self.log_sink = get_project, log
        self._worker: Worker | None = None
        self._cancel = threading.Event()
        self._shown: service.Target | None = None
        self._project_name = ""
        self._targets: list[service.Target] = []
        self._loading = False

        # ---- cột trái: danh sách video ----
        self.scope = QComboBox()
        for k, label in SCOPES:
            self.scope.addItem(label, k)
        self.scope.activated.connect(self.on_scope)
        self._checks: list[QCheckBox] = []
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Mục", "Video", "Mô tả", "Đã đăng"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_target(r))
        b_all = QPushButton("Chọn tất cả")
        b_all.setProperty("ghost", True)
        b_all.clicked.connect(lambda: self.check_all(True))
        b_none = QPushButton("Bỏ chọn")
        b_none.setProperty("ghost", True)
        b_none.clicked.connect(lambda: self.check_all(False))
        left = card("Video sẽ đăng", "Tích chọn các mục muốn đăng. Video ghép được tạo/ghép lại tự động khi cần.")
        top = QHBoxLayout()
        top.addWidget(self.scope, 1)
        top.addWidget(b_all)
        top.addWidget(b_none)
        left.layout().addLayout(top)
        left.layout().addWidget(self.table, 1)
        self.empty = QLabel("")
        self.empty.setProperty("caption", True)
        self.empty.setWordWrap(True)
        left.layout().addWidget(self.empty)

        # ---- cột phải: nội dung, nền tảng, thao tác, lịch sử ----
        self.edit_title = QLineEdit()
        self.edit_desc = QPlainTextEdit()
        self.edit_desc.setMinimumHeight(110)
        self.edit_tags = QLineEdit()
        self.edit_tags.setPlaceholderText("#kểchuyện #truyệnma …")
        self.lbl_target = QLabel("")
        self.lbl_target.setProperty("caption", True)
        self.b_ai = QPushButton("Viết bằng AI")
        icons.attach(self.b_ai, "sparkle", 18)
        self.b_ai.clicked.connect(self.ai_for_current)
        self.b_save_meta = QPushButton("Lưu nội dung")
        self.b_save_meta.clicked.connect(self.commit_editor)
        content = card("Nội dung đăng", "Tiêu đề, mô tả và hashtag của video đang chọn. Tool tự chỉnh theo giới hạn từng nền tảng.")
        content.layout().addWidget(self.lbl_target)
        content.layout().addWidget(field("Tiêu đề", "", self.edit_title))
        content.layout().addWidget(field("Mô tả", "", self.edit_desc))
        content.layout().addWidget(field("Hashtag", "Cách nhau bằng dấu cách.", self.edit_tags))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.b_ai)
        row.addWidget(self.b_save_meta)
        content.layout().addLayout(row)

        self.plat_checks: dict[str, QCheckBox] = {}
        self.plat_status: dict[str, QLabel] = {}
        plats = card("Đăng lên", "Kết nối tài khoản ở Cài đặt → Đăng video.")
        for k, cls in PLATFORMS.items():
            cb = QCheckBox(cls.label)
            cb.toggled.connect(self.save_options)
            st = QLabel("")
            r = QHBoxLayout()
            r.addWidget(cb, 1)
            r.addWidget(st)
            plats.layout().addLayout(r)
            self.plat_checks[k], self.plat_status[k] = cb, st
        self.privacy = QComboBox()
        for k, label in PRIVACY:
            self.privacy.addItem(label, k)
        self.privacy.activated.connect(self.save_options)
        plats.layout().addWidget(field("Chế độ hiển thị", "YouTube/TikTok chưa được duyệt ứng dụng có thể tự ép về riêng tư. "
                                       "Facebook lưu bản nháp nếu chọn riêng tư; Instagram chỉ đăng công khai.", self.privacy))
        self.auto = QCheckBox("Tự động đăng khi một chương gen xong")
        self.auto.setToolTip("Sau khi gen clip xong cả chương, tự ghép, viết mô tả và đăng không cần xác nhận lại.")
        self.auto.toggled.connect(self.save_options)
        self.force = QCheckBox("Đăng lại cả những mục đã đăng")
        plats.layout().addWidget(self.auto)
        plats.layout().addWidget(self.force)

        self.b_prepare = QPushButton("Ghép + viết mô tả")
        self.b_prepare.clicked.connect(self.prepare)
        self.b_publish = QPushButton("Đăng ngay")
        self.b_publish.setProperty("primary", True)
        self.b_publish.clicked.connect(self.publish)
        self.b_stop = QPushButton("Dừng")
        self.b_stop.clicked.connect(self._cancel.set)
        self.b_stop.hide()
        for b in (self.b_prepare, self.b_publish, self.b_stop):
            b.setFixedHeight(40)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        actions = QHBoxLayout()
        actions.addWidget(self.status, 1)
        actions.addWidget(self.b_stop)
        actions.addWidget(self.b_prepare)
        actions.addWidget(self.b_publish)

        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(["Lúc", "Mục · nền tảng", "Kết quả", "Liên kết"])
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history.setShowGrid(False)
        self.history.setMinimumHeight(170)
        hh = self.history.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.history.cellDoubleClicked.connect(self.open_history_link)
        hist = card("Lịch sử đăng", "Bấm đúp một dòng có liên kết để mở bài đã đăng.")
        hist.layout().addWidget(self.history)

        col = QVBoxLayout()
        col.setSpacing(SP.l)
        col.addWidget(content)
        col.addWidget(plats)
        col.addLayout(actions)
        col.addWidget(hist)
        col.addStretch(1)
        holder = QWidget()
        holder.setLayout(col)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)

        body = QHBoxLayout()
        body.setSpacing(SP.l)
        body.addWidget(left, 5)
        body.addWidget(scroll, 6)
        root = QVBoxLayout(self)
        root.setContentsMargins(SP.xl, SP.xl, SP.xl, SP.l)
        root.setSpacing(SP.l)
        root.addWidget(PageHeader("Đăng video", "Tự ghép video, viết mô tả và hashtag rồi đăng lên YouTube, TikTok, Facebook, Instagram qua API chính thức."))
        root.addLayout(body, 1)
        self.update_buttons()

    # ================= nạp dữ liệu =================
    def showEvent(self, e):
        super().showEvent(e)
        self.reload()

    def reload(self) -> None:
        p = self.get_project()
        self._loading = True
        try:
            self.setEnabled(bool(p) and not self._worker)
            if not p:
                self.table.setRowCount(0)
                self.empty.setText("Hãy mở một dự án ở tab Dự án.")
                self._shown = None
                return
            if p.name != self._project_name:             # đổi dự án: bỏ lựa chọn/mục đang xem của dự án trước
                self._project_name, self._targets, self._shown = p.name, [], None
            self.scope.setCurrentIndex(max(0, self.scope.findData(p.publish_scope)))
            self.privacy.setCurrentIndex(max(0, self.privacy.findData(p.publish_privacy)))
            self.auto.setChecked(p.publish_auto)
            for k, cb in self.plat_checks.items():
                cb.setChecked(k in p.publish_platforms)
            self.refresh_platforms()
            self.fill_table()
            self.fill_history()
        finally:
            self._loading = False
        self.update_buttons()

    def refresh_platforms(self) -> None:
        for k, cls in PLATFORMS.items():
            plat = cls()
            ok = plat.is_connected()
            lab = self.plat_status[k]
            lab.setText(f"Đã kết nối · {plat.account_label()}" if ok else "Chưa kết nối")
            lab.setProperty("pill", "ok" if ok else "warn")
            repolish(lab)

    def fill_table(self) -> None:
        p = self.get_project()
        keep = self._shown.key if self._shown else None
        checked = {t.key for t in self.checked_targets()}
        first = not self._targets
        self._targets = service.targets(p)
        self._checks = []
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._targets))
        for r, t in enumerate(self._targets):
            n, done = service.scene_count(p, t), len(service.clips_of(p, t))
            if not n:
                video = "Chưa có scene"
            elif done < n:
                video = f"Gen {done}/{n} scene"
            elif service.is_stale(p, t):
                video = "Cần ghép"
            else:
                video = "Đã ghép"
            posted = [PLATFORMS[k].label for k in PLATFORMS if service.history_ok(p, t, k)]
            cells = [t.label, video, "✓" if service.meta_of(p, t).get("title") else "—", ", ".join(posted) or "—"]
            for c, text in enumerate(cells, 1):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                self.table.setItem(r, c, it)
            cb = QCheckBox()
            cb.setChecked(t.key in checked or (first and service.is_ready(p, t)))
            cb.toggled.connect(lambda *_: self.update_buttons())
            self._checks.append(cb)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(12, 0, 0, 0)
            hl.addWidget(cb)
            self.table.setCellWidget(r, 0, holder)
        self.table.blockSignals(False)
        self.empty.setText("" if self._targets else "Dự án chưa có chương nào.")
        row = next((i for i, t in enumerate(self._targets) if t.key == keep), 0 if self._targets else -1)
        self._shown = None
        self.table.blockSignals(True)
        self.table.setCurrentCell(row, 0)
        self.table.blockSignals(False)
        self.show_target(row)

    def fill_history(self) -> None:
        p = self.get_project()
        names = {t.key: t.label for t in service.targets(p)} if p else {}
        rows = list(reversed(p.publish_history[-100:])) if p else []
        self.history.setRowCount(len(rows))
        for r, h in enumerate(rows):
            plat = PLATFORMS.get(h.get("platform"))
            result = ("✓ " + (service.PRIVACY_LABELS.get(h.get("privacy"), h.get("privacy", "")) or "Đã đăng")
                      + (f" — {h['message']}" if h.get("message") else "")) if h.get("ok") else "✗ " + (h.get("message") or "Lỗi")
            cells = [h.get("time", ""), f"{names.get(h.get('chapter'), h.get('chapter', ''))} · {plat.label if plat else h.get('platform')}",
                     result, h.get("url", "")]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(text)
                self.history.setItem(r, c, it)

    def open_history_link(self, row: int, _col: int) -> None:
        it = self.history.item(row, 3)
        if it and it.text().startswith("http"):
            QDesktopServices.openUrl(QUrl(it.text()))

    # ================= nội dung đang sửa =================
    def current_target(self) -> service.Target | None:
        r = self.table.currentRow()
        return self._targets[r] if 0 <= r < len(self._targets) else None

    def show_target(self, row: int) -> None:
        self.commit_editor(quiet=True)
        p = self.get_project()
        t = self._targets[row] if 0 <= row < len(self._targets) else None
        self._shown = t
        meta = service.meta_of(p, t) if (p and t) else {}
        self.edit_title.setText(meta.get("title", ""))
        self.edit_desc.setPlainText(meta.get("description", ""))
        self.edit_tags.setText(_tags_text(meta.get("hashtags", [])))
        self.lbl_target.setText(t.label if t else "Chọn một mục ở danh sách bên trái.")
        for w in (self.edit_title, self.edit_desc, self.edit_tags, self.b_ai, self.b_save_meta):
            w.setEnabled(t is not None and not self._worker)

    def commit_editor(self, quiet: bool = False) -> None:
        p, t = self.get_project(), self._shown
        if not p or not t:
            return
        meta = dict(title=self.edit_title.text().strip(), description=self.edit_desc.toPlainText().strip(),
                    hashtags=base.clean_tags(self.edit_tags.text(), 30))
        if meta != {k: service.meta_of(p, t).get(k, [] if k == "hashtags" else "") for k in meta}:
            service.set_meta(p, t, meta)
            p.save()
            if not quiet:
                self.set_status("Đã lưu nội dung.", "ok")
            self.fill_row_meta(t)

    def fill_row_meta(self, t: service.Target) -> None:
        p = self.get_project()
        if t in self._targets:
            it = self.table.item(self._targets.index(t), 3)
            if it:
                it.setText("✓" if service.meta_of(p, t).get("title") else "—")

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
            if self._shown is t:
                self.show_target(self.table.currentRow())
            self.fill_row_meta(t)
            self.set_status("AI đã viết xong, hãy xem lại rồi lưu/đăng.", "ok")
        self.start(job, done, "Đang nhờ AI viết mô tả…")

    # ================= tuỳ chọn =================
    def save_options(self, *_) -> None:
        p = self.get_project()
        if not p or self._loading:
            return
        p.publish_platforms = [k for k, cb in self.plat_checks.items() if cb.isChecked()]
        p.publish_privacy = self.privacy.currentData() or "private"
        p.publish_auto = self.auto.isChecked()
        p.save()
        self.update_buttons()

    def on_scope(self, *_) -> None:
        p = self.get_project()
        if not p:
            return
        self.commit_editor(quiet=True)
        p.publish_scope = self.scope.currentData()
        p.save()
        self._targets, self._shown = [], None
        self.fill_table()
        self.update_buttons()

    def check_all(self, on: bool) -> None:
        for cb in self._checks:
            cb.setChecked(on)

    def checked_targets(self) -> list[service.Target]:
        return [t for t, cb in zip(self._targets, self._checks) if cb.isChecked()]

    def update_buttons(self) -> None:
        idle = self._worker is None
        any_t = bool(self.checked_targets())
        self.b_prepare.setEnabled(idle and any_t)
        self.b_publish.setEnabled(idle and any_t and any(cb.isChecked() for cb in self.plat_checks.values()))
        self.b_stop.setVisible(not idle)

    # ================= chạy nền =================
    def set_status(self, text: str, kind: str = "info") -> None:
        self.status.setText(text)
        self.status.setProperty("pill", kind)
        repolish(self.status)
        self.activity.emit(text, {"ok": "ok", "err": "error", "warn": "warn"}.get(kind, "info"))

    def log(self, msg: str) -> None:
        self.log_sink(msg)
        low = msg.lower()
        self.activity.emit(msg.splitlines()[0][:300] if msg else "", "error" if "lỗi" in low else "info")

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
        for wd in (self.table, self.scope, self.edit_title, self.edit_desc, self.edit_tags, self.b_ai, self.b_save_meta, self.privacy,
                   self.auto, self.force, *self.plat_checks.values()):
            wd.setEnabled(False)

    def _finished(self) -> None:
        self._worker = None
        for wd in (self.table, self.scope, self.privacy, self.auto, self.force, *self.plat_checks.values()):
            wd.setEnabled(True)
        self.reload()

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
        plats = [k for k, cb in self.plat_checks.items() if cb.isChecked()]
        if not (p and tgts and plats):
            return
        self.commit_editor(quiet=True)
        priv = self.privacy.currentData()
        lines = [f"• {len(tgts)} video × {len(plats)} nền tảng ({', '.join(PLATFORMS[k].label for k in plats)})",
                 f"• Chế độ: {service.PRIVACY_LABELS[priv]}"]
        if priv == "public":
            lines.append("\n⚠ Công khai: bài sẽ hiện ngay với mọi người và không thể thu hồi từ Kevit.")
        if self.force.isChecked():
            lines.append("\n⚠ Sẽ đăng LẠI cả những mục đã đăng (có thể trùng bài).")
        if QMessageBox.question(self, "Xác nhận đăng", "\n".join(lines) + "\n\nTiếp tục?") != QMessageBox.Yes:
            return
        self.run_publish(p, tgts, plats, priv, self.force.isChecked())

    def run_publish(self, p, tgts, plats, priv, force=False) -> None:
        def job(log):
            return service.run(p, tgts, plats, priv, log, self._cancel, force)

        def done(results):
            ok = sum(1 for r in results if r.ok)
            bad = [r for r in results if not r.ok]
            text = f"Đã đăng {ok}/{len(results)} bài." + (f" Lỗi {PLATFORMS[bad[0].platform].label}: {bad[0].message[:200]}" if bad else "")
            self.set_status(text, "warn" if bad else "ok")
        self.start(job, done, "Đang đăng…")

    # ================= tự động =================
    def on_generation_done(self) -> None:
        """Một đợt gen clip vừa xong: nếu bật tự động thì ghép + viết mô tả + đăng các chương đã đủ clip và chưa đăng."""
        p = self.get_project()
        if not p or not p.publish_auto or self._worker is not None:
            return
        plats = [k for k in p.publish_platforms if PLATFORMS[k]().is_connected()]
        for k in set(p.publish_platforms) - set(plats):
            self.log(f"Tự động đăng: bỏ qua {PLATFORMS[k].label} vì chưa kết nối.")
        todo = [t for t in service.targets(p) if service.is_ready(p, t) and any(not service.history_ok(p, t, k) for k in plats)]
        if not (plats and todo):
            return
        self.log(f"Tự động đăng: {len(todo)} video lên {', '.join(PLATFORMS[k].label for k in plats)} ({service.PRIVACY_LABELS[p.publish_privacy]}).")
        self.run_publish(p, todo, plats, p.publish_privacy)

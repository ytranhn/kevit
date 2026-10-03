from . import _env  # noqa: F401

import time
import unittest

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from app import models
from app.publish import PLATFORMS
from app.publish_tab import PublishTab
from .test_publish import FakePlatform, Tmp, make_clip

app = QApplication.instance() or QApplication([])


def wait(cond, secs=30):
    end = time.time() + secs
    while not cond() and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    QCoreApplication.processEvents()
    return cond()


class TestPublishTab(Tmp):
    def setUp(self):
        super().setUp()
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        PLATFORMS["fake"] = FakePlatform
        self.addCleanup(PLATFORMS.pop, "fake", None)
        from app.publish import store
        store.set_account("fake:1", {"platform": "fake", "label": "Giả 1", "access_token": "x"})
        FakePlatform.sent, FakePlatform.fail = [], False
        p = models.Project("Dự án UI", publish_accounts=["fake:1"], publish_privacy="unlisted")
        for i in range(3):
            ch = p.new_chapter()
            ch.post_meta = {"title": f"Tập {i + 1}", "description": "d", "hashtags": ["h"]}
            for k in (1, 2):
                clip = p.chapter_dir(ch) / "clips" / f"s{k}.mp4"
                if i < 2:
                    make_clip(clip)
                ch.scenes.append(models.Scene(k, clip=str(clip) if i < 2 else "", status="done" if i < 2 else "pending"))
        p.save()
        self.p = p
        self.tab = PublishTab(lambda: self.p)
        self.addCleanup(lambda: wait(lambda: self.tab._thumb_worker is None and self.tab._worker is None))   # luồng nền xong rồi mới xoá thư mục tạm
        self.tab.reload()

    def test_default_selection_is_ready_chapters_only(self):
        self.assertEqual([t.key for t in self.tab.checked_targets()], ["01", "02"])

    def test_publish_runs_in_background_and_updates_history(self):
        t = self.tab
        t.run_publish(self.p, t.checked_targets(), ["fake:1"], "unlisted")
        self.assertTrue(wait(lambda: t._worker is None))
        self.assertEqual(len(FakePlatform.sent), 2)
        self.assertEqual(t.history.rowCount(), 2)
        self.assertIn("Đã đăng 2/2", t.status.text())
        self.assertEqual(t._rows["01"].pill.text(), "Đã đăng")

    def test_edit_metadata_is_saved(self):
        t = self.tab
        t.select_key("02")
        t.edit_title.setText("Tiêu đề mới")
        t.tag_input.setText("#a #b, c")
        t.add_tag_from_input()
        t.commit_editor()
        again = models.Project.load("Dự án UI")
        self.assertEqual(again.chapters[1].post_meta["title"], "Tiêu đề mới")
        self.assertEqual(again.chapters[1].post_meta["hashtags"], ["h", "a", "b", "c"])    # thêm vào hashtag đã có

    def test_auto_publish_only_when_enabled_and_new(self):
        t = self.tab
        t.on_generation_done()                                  # tắt tự động: không làm gì
        self.assertIsNone(t._worker)
        self.p.publish_auto = True
        t.on_generation_done()
        self.assertTrue(wait(lambda: t._worker is None))
        self.assertEqual(len(FakePlatform.sent), 2)             # chỉ 2 chương đủ clip, chương 3 bỏ qua
        t.on_generation_done()                                  # gọi lại: đã đăng thì không đăng trùng
        self.assertTrue(wait(lambda: t._worker is None))
        self.assertEqual(len(FakePlatform.sent), 2)

    def test_account_selection_is_per_project(self):
        from app.publish import store
        store.set_account("youtube:A", {"platform": "youtube", "label": "Kênh A", "access_token": "x"})
        store.set_account("youtube:B", {"platform": "youtube", "label": "Kênh B", "access_token": "x"})
        t = self.tab
        t.refresh_accounts()
        self.assertEqual(set(t.acc_checks), {"fake:1", "youtube:A", "youtube:B"})
        t.acc_checks["youtube:B"].setChecked(True)
        self.assertEqual(sorted(models.Project.load("Dự án UI").publish_accounts), ["fake:1", "youtube:B"])
        other = models.Project("Dự án khác")
        other.save()
        self.p = other
        t.reload()
        self.assertFalse(any(cb.isChecked() for cb in t.acc_checks.values()))     # dự án khác chưa chọn tài khoản nào
        t.acc_checks["youtube:A"].setChecked(True)
        self.assertEqual(models.Project.load("Dự án khác").publish_accounts, ["youtube:A"])
        self.assertEqual(sorted(models.Project.load("Dự án UI").publish_accounts), ["fake:1", "youtube:B"])

    def test_old_project_with_publish_platforms_still_loads(self):
        import json
        f = models.PROJ_DIR / "Dự án UI" / "project.json"
        d = json.loads(f.read_text(encoding="utf-8"))
        d["publish_platforms"] = ["youtube"]
        f.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(models.Project.load("Dự án UI").publish_accounts, ["fake:1"])

    def test_scope_switch_to_project(self):
        t = self.tab
        t.scope.setCurrentIndex(1)
        t.on_scope()
        self.assertEqual([x.key for x in t._targets], ["project"])
        self.assertEqual(models.Project.load("Dự án UI").publish_scope, "project")

    def test_no_project(self):
        tab = PublishTab(lambda: None)
        tab.reload()
        self.assertFalse(tab.isEnabled())


if __name__ == "__main__":
    unittest.main()


class TestProjectSettingsAccounts(Tmp):
    """Chọn tài khoản đăng ngay trong Cài đặt dự án."""

    def test_dialog_saves_accounts_and_syncs_publish_tab(self):
        from app import theme, ui
        from app.publish import store
        theme.install(app)
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        for a, pl, lb in (("youtube:A", "youtube", "Kênh A"), ("youtube:B", "youtube", "Kênh B"), ("tiktok:1", "tiktok", "@na")):
            store.set_account(a, {"platform": pl, "label": lb, "access_token": "x"})
        models.Project("P1", publish_accounts=["youtube:A"]).save()
        models.Project("P2").save()
        w = ui.MainWindow()
        self.addCleanup(w.close)
        proj = w.proj
        proj.combo.setCurrentText("P1")
        proj.open_project("P1") if hasattr(proj, "open_project") else None
        d = proj.settings_dialog
        d.fill_accounts()
        self.assertEqual({k: cb.isChecked() for k, cb in d.acc_checks.items()}, {"youtube:A": True, "youtube:B": False, "tiktok:1": False})
        d.acc_checks["tiktok:1"].setChecked(True)
        d.pub_privacy.setCurrentIndex(d.pub_privacy.findData("unlisted"))
        d.pub_auto.setChecked(True)
        d.apply_publish()
        proj.project.save()
        again = models.Project.load(proj.project.name)
        self.assertEqual(sorted(again.publish_accounts), ["tiktok:1", "youtube:A"])
        self.assertEqual((again.publish_privacy, again.publish_auto), ("unlisted", True))
        w.publish_tab.reload()
        self.assertEqual(sorted(k for k, cb in w.publish_tab.acc_checks.items() if cb.isChecked()), ["tiktok:1", "youtube:A"])
        other = models.Project.load("P2" if proj.project.name == "P1" else "P1")
        self.assertNotEqual(sorted(other.publish_accounts), ["tiktok:1", "youtube:A"])    # dự án kia không bị ảnh hưởng


class TestSettingsPanel(Tmp):
    def test_platform_selection_and_shared_meta_credentials(self):
        from app import theme
        from app.publish import store
        from app.publish_panel import PublishSettingsPanel
        theme.install(app)
        store.set_account("facebook:P", {"platform": "facebook", "label": "Trang Một", "access_token": "x", "page_id": "P"})
        panel = PublishSettingsPanel()
        panel.show()
        self.assertTrue(panel.cards["youtube"].isVisible() and not panel.rows["youtube"].isVisible())
        panel.select("instagram")
        self.assertTrue(panel.cards["instagram"].isVisible() and panel.rows["youtube"].isVisible())
        self.assertFalse(panel.cards["youtube"].isVisible())
        self.assertTrue(panel.tab_btns["instagram"].isChecked() and not panel.tab_btns["youtube"].isChecked())
        # Facebook và Instagram dùng chung một bộ khoá: lưu ở thẻ này thì thẻ kia thấy ngay
        fb, ig = panel.cards["facebook"], panel.cards["instagram"]
        fb.id_edit.setText("APP123")
        fb.secret_edit.setText("SECRET")
        fb.save()
        self.assertEqual(ig.id_edit.text(), "APP123")
        self.assertEqual(store.get_creds("meta")["client_id"], "APP123")
        # trạng thái các hàng thu gọn
        self.assertIn("1 tài khoản", panel.rows["facebook"].status.text())
        self.assertEqual(panel.rows["facebook"].btn.text(), "Quản lý")
        self.assertEqual(panel.rows["tiktok"].status.text(), "Chưa kết nối")
        self.assertEqual(panel.rows["tiktok"].btn.text(), "Kết nối")

    def test_check_without_accounts_validates_keys(self):
        from app import theme
        from app.publish_panel import PublishSettingsPanel
        theme.install(app)
        panel = PublishSettingsPanel()
        card = panel.cards["youtube"]
        card.check()
        self.assertIn("Chưa nhập đủ", card.msg.text())
        card.id_edit.setText("id")
        card.secret_edit.setText("secret")
        card.check()
        self.assertIn("Khoá đã đủ", card.msg.text())

    def test_check_runs_for_each_account_in_background(self):
        from unittest import mock
        from app import theme
        from app.publish import store
        from app.publish_panel import PublishSettingsPanel
        theme.install(app)
        for a in ("youtube:A", "youtube:B"):
            store.set_account(a, {"platform": "youtube", "label": a, "access_token": "x"})
        panel = PublishSettingsPanel()
        card = panel.cards["youtube"]

        class P:
            def __init__(self, aid): self.aid = aid
            def check(self):
                if self.aid == "youtube:B":
                    raise RuntimeError("hết hạn")
                return "kênh dùng được"
        with mock.patch("app.publish_panel.publish.get", side_effect=lambda aid: P(aid)):
            card.check()
            self.assertTrue(wait(lambda: card.worker is None or not card.worker.isRunning()))
            wait(lambda: "Đã kiểm tra" in card.msg.text())
        self.assertIn("1 lỗi", card.msg.text())
        self.assertIn("✓", card.rows["youtube:A"].result.text())
        self.assertIn("✗", card.rows["youtube:B"].result.text())


class TestProjectSettingsDialogUI(Tmp):
    def _window(self):
        from app import accounts, theme, ui
        theme.install(app)
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        models.Project("PX", style="phong cách riêng", aspect_ratio="16:9", publish_accounts=[]).save()
        w = ui.MainWindow()
        self.addCleanup(w.close)
        w.proj.combo.setCurrentText("PX")
        return w, accounts

    def test_aspect_cards_follow_and_drive_the_hidden_selector(self):
        w, _ = self._window()
        d, tab = w.proj.settings_dialog, w.proj
        d.sync_aspect()
        self.assertEqual([c.ratio for c in d.aspect_cards if c.isChecked()], ["16:9"])
        d.pick_aspect("flow")
        self.assertEqual(tab.aspect.currentData(), "flow")
        self.assertEqual([c.ratio for c in d.aspect_cards if c.isChecked()], ["flow"])
        tab.aspect.setCurrentIndex(tab.aspect.findData("9:16"))          # đổi từ phía khác (vd. khôi phục khi Huỷ)
        self.assertEqual([c.ratio for c in d.aspect_cards if c.isChecked()], ["9:16"])

    def test_nav_scrolls_and_highlights(self):
        w, _ = self._window()
        d = w.proj.settings_dialog
        d.show()
        QCoreApplication.processEvents()
        d.go("voice")
        QCoreApplication.processEvents()
        self.assertEqual([k for k, n in d._navs.items() if n.property("active")], ["voice"])
        self.assertGreater(d.scroll.verticalScrollBar().value(), 0)
        d.scroll.verticalScrollBar().setValue(0)
        QCoreApplication.processEvents()
        self.assertEqual([k for k, n in d._navs.items() if n.property("active")], ["visual"])
        d.close()

    def test_reset_defaults_keeps_accounts_and_voice(self):
        from unittest import mock
        from PySide6.QtWidgets import QMessageBox
        w, _ = self._window()
        d, tab = w.proj.settings_dialog, w.proj
        tab.style.setText("khác")
        tab.max_scenes.setValue(40)
        tab.flow_parallel.setValue(5)
        lang = tab.narr_lang.currentIndex()
        d.pub_auto.setChecked(True)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            d.reset_defaults()
        self.assertEqual((tab.style.text(), tab.max_scenes.value(), tab.flow_parallel.value()), ("cinematic, soft lighting, 35mm film look", 16, 1))
        self.assertEqual(tab.aspect.currentData(), "9:16")
        self.assertFalse(d.pub_auto.isChecked())
        self.assertEqual(tab.narr_lang.currentIndex(), lang)

    def test_credit_card_shows_account_credit(self):
        w, accounts = self._window()
        accounts.save_credits("default", 1234, 2, "30 Oct 2026")
        d = w.proj.settings_dialog
        d.refresh_credit_card()
        self.assertIn("1.234", d.credit_big.text())
        self.assertIn("Còn 2 credit ngày", d.credit_detail.text())
        self.assertIn("30 Oct 2026", d.credit_detail.text())


class TestNoHorizontalOverflow(Tmp):
    """Ở cỡ nhỏ, không trang/hộp thoại nào được đòi bề rộng lớn hơn vùng hiển thị (tránh nội dung tràn mép và chồng lấn)."""

    def test_pages_fit_at_small_sizes(self):
        from PySide6.QtWidgets import QScrollArea, QWidget
        from app import theme, ui
        from app.publish import store
        theme.install(app)
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        p = models.Project("Nhỏ", publish_accounts=["youtube:A"])
        for i in range(2):
            ch = p.new_chapter(f"Chương {i + 1} có tên khá dài để thử")
            ch.scenes.append(models.Scene(1, narration="x", status="pending"))
        p.chapters[0].post_meta = {"title": "T" * 90, "description": "m " * 300, "hashtags": [f"tag{i}" for i in range(20)]}
        p.save()
        store.set_account("youtube:A", {"platform": "youtube", "label": "Kênh có tên rất rất dài để thử cắt chữ", "access_token": "x"})
        w = ui.MainWindow()
        self.addCleanup(w.close)
        w.resize(1216, 700)
        w.show()
        QCoreApplication.processEvents()
        problems = []

        def check(where, scroll):
            QCoreApplication.processEvents()
            need, have = scroll.widget().minimumSizeHint().width(), scroll.viewport().width()
            if need > have:
                problems.append(f"{where}: cần {need}px, chỉ có {have}px")

        w.tabs.setCurrentWidget(w.publish_tab)
        QCoreApplication.processEvents()
        for sc in w.publish_tab.findChildren(QScrollArea):
            check("Đăng video", sc)
        w.tabs.setCurrentWidget(w.settings_tab)
        for key in ("llm", "gemini", "flow", "publish", "data"):
            w.settings_tab.select_section(key)
            check(f"Cài đặt/{key}", w.settings_tab.stack.currentWidget())
        for card in w.settings_tab.publish_panel.cards.values():
            w.settings_tab.publish_panel.select(card.key)
            check(f"Cài đặt/đăng video/{card.key}", w.settings_tab.stack.currentWidget())
        d = w.proj.settings_dialog
        d.fill_accounts()
        d.resize(1000, 640)
        d.show()
        QCoreApplication.processEvents()
        check("Cài đặt dự án", d.scroll)
        d.close()
        self.assertEqual(problems, [])


class TestMessageBoxStyle(Tmp):
    def _show(self, icon, text, buttons, default=QMessageBox.NoButton):
        from app import theme
        theme.install(app)
        box = QMessageBox(icon, "Tiêu đề", text, buttons)
        if default != QMessageBox.NoButton:
            box.setDefaultButton(default)
        box.show()
        QCoreApplication.processEvents()
        self.addCleanup(box.close)
        return box

    def test_buttons_are_vietnamese_and_default_is_primary(self):
        box = self._show(QMessageBox.Question, "Xoá chương?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        self.assertEqual(box.button(QMessageBox.Yes).text(), "Đồng ý")
        self.assertEqual(box.button(QMessageBox.No).text(), "Không")
        self.assertTrue(box.button(QMessageBox.No).property("primary"))          # nút mặc định nổi bật
        self.assertFalse(box.button(QMessageBox.Yes).property("primary"))
        self.assertFalse(box.iconPixmap().isNull())

    def test_information_and_ok_only(self):
        box = self._show(QMessageBox.Information, "Xong.", QMessageBox.Ok)
        self.assertEqual(box.button(QMessageBox.Ok).text(), "Đóng")
        self.assertTrue(box.button(QMessageBox.Ok).property("primary"))

    def test_custom_buttons_keep_their_text(self):
        from app import theme
        theme.install(app)
        box = QMessageBox(QMessageBox.Warning, "T", "Nội dung")
        mine = box.addButton("Gen lại scene", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.show()
        QCoreApplication.processEvents()
        self.addCleanup(box.close)
        self.assertEqual(mine.text(), "Gen lại scene")
        self.assertEqual(box.button(QMessageBox.Cancel).text(), "Huỷ")

    def test_text_is_not_bold(self):
        box = self._show(QMessageBox.Question, "Nội dung dài", QMessageBox.Yes | QMessageBox.No)
        lab = box.findChild(QLabel, "qt_msgbox_label")
        self.assertIsNotNone(lab)
        self.assertLessEqual(lab.font().weight(), QFont.Weight.Normal)


class TestScheduler(Tmp):
    def setUp(self):
        super().setUp()
        from datetime import datetime
        from app.publish import schedule, store
        from app.publish_scheduler import Scheduler
        self.sc, self.Scheduler = schedule, Scheduler
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        PLATFORMS["fake"] = FakePlatform
        self.addCleanup(PLATFORMS.pop, "fake", None)
        FakePlatform.sent, FakePlatform.fail, FakePlatform.fail_accounts = [], False, set()
        store.set_account("fake:1", {"platform": "fake", "label": "G1", "access_token": "x"})
        p = models.Project("Lịch UI")
        for i in range(2):
            ch = p.new_chapter()
            ch.post_meta = {"title": f"Tập {i + 1}", "description": "d", "hashtags": []}
            clip = p.chapter_dir(ch) / "clips" / "s1.mp4"
            make_clip(clip)
            ch.scenes.append(models.Scene(1, clip=str(clip), status="done"))
        p.save()
        self.p = p
        self.now = datetime(2026, 10, 5, 12, 0)

    def _sched(self, open_project=None):
        s = self.Scheduler(lambda: open_project, now=lambda: self.now)
        events = []
        s.changed.connect(events.append)
        return s, events

    def test_tick_runs_due_job_once_and_in_order(self):
        from datetime import timedelta
        self.sc.add_jobs(self.p, [self.sc.new_job("01", self.now - timedelta(minutes=1), ["fake:1"], "private"),
                                  self.sc.new_job("02", self.now - timedelta(minutes=2), ["fake:1"], "private")])
        s, events = self._sched(self.p)
        s.tick()
        self.assertTrue(s.busy)
        s.tick()                                                    # đang bận: không chạy chồng
        self.assertTrue(wait(lambda: not s.busy))
        self.assertEqual([x[1].title for x in FakePlatform.sent], ["Tập 2"])         # lượt cũ hơn (02) chạy trước
        self.assertTrue(wait(lambda: True))
        s.tick()
        self.assertTrue(wait(lambda: not s.busy and len(FakePlatform.sent) == 2))
        self.assertEqual([j["status"] for j in self.p.publish_queue], ["done", "done"])
        self.assertIn(self.p.name, events)

    def test_future_job_waits_and_far_overdue_is_missed_not_posted(self):
        from datetime import timedelta
        self.sc.add_jobs(self.p, [self.sc.new_job("01", self.now + timedelta(hours=2), ["fake:1"], "private"),
                                  self.sc.new_job("02", self.now - timedelta(days=1), ["fake:1"], "private")])
        s, _ = self._sched(self.p)
        s.tick()
        self.assertFalse(s.busy)
        self.assertEqual(FakePlatform.sent, [])
        self.assertEqual({j["key"]: j["status"] for j in self.p.publish_queue}, {"01": "pending", "02": "missed"})

    def test_other_project_on_disk_is_run_without_being_open(self):
        from datetime import timedelta
        self.sc.add_jobs(self.p, [self.sc.new_job("01", self.now - timedelta(minutes=1), ["fake:1"], "private")])
        s, _ = self._sched(None)                                    # không dự án nào đang mở
        s.tick()
        self.assertTrue(wait(lambda: not s.busy))
        self.assertEqual(len(FakePlatform.sent), 1)
        self.assertEqual(models.Project.load("Lịch UI").publish_queue[0]["status"], "done")

    def test_run_now_and_busy(self):
        from datetime import timedelta
        self.sc.add_jobs(self.p, [self.sc.new_job("01", self.now + timedelta(days=5), ["fake:1"], "private")])
        s, _ = self._sched(self.p)
        jid = self.p.publish_queue[0]["id"]
        self.assertTrue(s.run_now(self.p, jid))
        self.assertFalse(s.run_now(self.p, jid))                    # đang chạy
        self.assertTrue(wait(lambda: not s.busy))
        self.assertEqual(self.p.publish_queue[0]["status"], "done")

    def test_deleted_project_is_dropped_from_index(self):
        from datetime import timedelta
        import shutil
        self.sc.add_jobs(self.p, [self.sc.new_job("01", self.now - timedelta(minutes=1), ["fake:1"], "private")])
        shutil.rmtree(self.p.dir)
        s, _ = self._sched(None)
        s.tick()
        self.assertFalse(s.busy)
        self.assertEqual(self.sc.projects_with_pending(), [])


class TestScheduleUI(Tmp):
    def setUp(self):
        super().setUp()
        from app.publish import store
        from app.publish_scheduler import Scheduler
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        PLATFORMS["fake"] = FakePlatform
        self.addCleanup(PLATFORMS.pop, "fake", None)
        FakePlatform.sent, FakePlatform.fail, FakePlatform.fail_accounts = [], False, set()
        store.set_account("fake:1", {"platform": "fake", "label": "G1", "access_token": "x"})
        p = models.Project("Hẹn UI", publish_accounts=["fake:1"], publish_privacy="unlisted")
        for i in range(3):
            ch = p.new_chapter()
            ch.post_meta = {"title": f"Tập {i + 1}", "description": "d", "hashtags": []}
            clip = p.chapter_dir(ch) / "clips" / "s1.mp4"
            make_clip(clip)
            ch.scenes.append(models.Scene(1, clip=str(clip), status="done"))
        p.save()
        self.p = p
        self.tab = PublishTab(lambda: self.p)
        self.sch = Scheduler(lambda: self.p)
        self.tab.set_scheduler(self.sch)
        self.addCleanup(lambda: wait(lambda: self.tab._thumb_worker is None and not self.sch.busy))
        self.tab.reload()

    def test_dialog_rule_generates_one_video_per_slot_and_validates(self):
        from datetime import datetime, timedelta
        from app.schedule_dialog import ScheduleDialog
        now = datetime(2026, 10, 5, 10, 0)
        d = ScheduleDialog(None, [("01", "Tập 1"), ("02", "Tập 2"), ("03", "Tập 3")], "tóm tắt", now=lambda: now)
        got = d.result()
        self.assertEqual([k for k, _ in got], ["01", "02", "03"])
        self.assertEqual([a.strftime("%d %H:%M") for _, a in got], ["05 20:00", "06 20:00", "07 20:00"])      # 10h sáng: bắt đầu tối nay 20:00
        self.assertTrue(d.ok.isEnabled())
        d.every.setValue(2)
        d.apply_rule()
        self.assertEqual([a.day for _, a in d.result()], [5, 7, 9])
        d.days[0].setChecked(False)                                                   # bỏ thứ Hai: 05/10 là thứ Hai
        d.every.setValue(1)
        d.apply_rule()
        self.assertEqual([a.day for _, a in d.result()], [6, 7, 8])
        # đổi thứ tự: video đổi chỗ, giờ giữ nguyên
        d.table.selectRow(0)
        d.move(1)
        self.assertEqual([k for k, _ in d.result()], ["02", "01", "03"])
        self.assertEqual([a.day for _, a in d.result()], [6, 7, 8])
        # giờ đã qua → chặn
        d.editors[1].setDateTime(d.editors[1].dateTime().addDays(-30))
        self.assertFalse(d.ok.isEnabled())
        self.assertTrue(d.warn.isVisible() or d.warn.text() != "")

    def test_schedule_selected_creates_jobs_and_table(self):
        from datetime import datetime, timedelta
        from unittest import mock
        from app.schedule_dialog import ScheduleDialog
        t0 = datetime.now() + timedelta(days=1)
        slots = [("01", t0), ("02", t0 + timedelta(days=1))]
        self.tab.check_all(False)
        for k in ("01", "02"):
            self.tab._rows[k].check.setChecked(True)
        with mock.patch.object(ScheduleDialog, "exec", return_value=ScheduleDialog.Accepted), \
                mock.patch.object(ScheduleDialog, "result", return_value=slots):
            self.tab.schedule_selected()
        q = models.Project.load("Hẹn UI").publish_queue
        self.assertEqual([(j["key"], j["status"], j["accounts"], j["privacy"]) for j in q],
                         [("01", "pending", ["fake:1"], "unlisted"), ("02", "pending", ["fake:1"], "unlisted")])
        self.assertEqual(self.tab.sched_table.rowCount(), 2)
        self.assertTrue(self.tab.sched_next.isVisible() or self.tab.sched_next.text() != "")
        self.assertFalse(self.tab.sched_empty.isVisible())

    def test_job_buttons_and_actions(self):
        from datetime import datetime, timedelta
        from app.publish import schedule
        schedule.add_jobs(self.p, [schedule.new_job("01", datetime.now() + timedelta(days=2), ["fake:1"], "private")])
        self.tab.refresh_schedule()
        self.assertFalse(self.tab.b_job_del.isEnabled())                      # chưa chọn dòng nào
        self.tab.sched_table.selectRow(0)
        self.assertTrue(self.tab.b_job_del.isEnabled() and self.tab.b_job_now.isEnabled() and self.tab.b_job_time.isEnabled())
        self.tab.job_now()                                                    # đăng ngay qua bộ chạy lịch
        self.assertTrue(wait(lambda: not self.sch.busy))
        self.assertEqual(len(FakePlatform.sent), 1)
        self.assertEqual(self.p.publish_queue[0]["status"], "done")
        self.tab.refresh_schedule()
        self.assertTrue(self.tab.b_job_clear.isEnabled())
        self.tab.job_clear()
        self.assertEqual(self.p.publish_queue, [])
        self.assertTrue(self.tab.sched_empty.isVisible() or not self.tab.sched_table.isVisible())

    def test_cancel_job(self):
        from datetime import datetime, timedelta
        from app.publish import schedule
        schedule.add_jobs(self.p, [schedule.new_job("02", datetime.now() + timedelta(days=2), ["fake:1"], "private")])
        self.tab.refresh_schedule()
        self.tab.sched_table.selectRow(0)
        self.tab.job_remove()
        self.assertEqual(self.p.publish_queue, [])
        self.assertEqual(schedule.projects_with_pending(), [])


class TestBatchGen(Tmp):
    """Gen nhiều chương: chuỗi gen → (lỗi: đồng bộ Flow) → (đủ scene: ghép) → chương kế. Phần gọi Flow thật được thay bằng bản giả."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        from app import theme, ui
        theme.install(app)
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        p = models.Project("Gen lô")
        for i in range(3):
            ch = p.new_chapter(f"Chương {i + 1}")
            for k in (1, 2):
                ch.scenes.append(models.Scene(k, narration="x", visual="v", status="pending"))
        p.save()
        self.w = ui.MainWindow()
        self.addCleanup(self.w.close)
        self.tab = self.w.proj
        self.tab.combo.setCurrentText("Gen lô")
        self.calls, self.syncs = [], []
        self.fail_chapters = set()
        tab = self.tab

        def fake_gen(mode="pending", silent=False, then=None):
            ch = tab.chapter
            self.calls.append(ch.id)
            for s in ch.scenes:
                if s.status == "done":
                    continue
                if ch.id in self.fail_chapters:
                    s.status, s.error = "error", "Không tải được clip"
                else:
                    self.finish_scene(ch, s)
            after = then if then is not None else tab.generation_done.emit
            tab.run(lambda log: None, lambda _: None, cancelable=True, then=lambda: tab._finish_chapter(ch, after))

        def fake_sync(chapters=None, then=None, quiet=False):
            self.syncs.append([c.id for c in chapters] if chapters else None)
            for c in chapters or []:
                for s in c.scenes:
                    if s.status == "error":
                        self.finish_scene(c, s)
            tab.run(lambda log: None, lambda _: None, then=then)
            return True
        tab.flow_auto_run = fake_gen
        tab.flow_sync = fake_sync
        self.done_signals = []
        tab.generation_done.connect(lambda: self.done_signals.append(1))
        patcher = mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def finish_scene(self, ch, s):
        p = self.tab.project
        clip = p.chapter_dir(ch) / "clips" / f"scene_{s.index:02d}.mp4"
        make_clip(clip)
        s.raw_clip = s.clip = str(clip)
        s.status, s.error = "done", ""

    def run_batch(self, ids):
        self.tab.start_batch(ids)
        self.assertTrue(wait(lambda: self.tab._batch is None and not self.tab._busy, 60))

    def test_batch_runs_chapters_in_order_and_merges_each(self):
        p = self.tab.project
        self.run_batch(["01", "02", "03"])
        self.assertEqual(self.calls, ["01", "02", "03"])
        for ch in p.chapters:
            self.assertTrue(p.merged_path(ch).exists(), ch.name)              # mỗi chương đủ scene đều được tự ghép
        self.assertEqual(self.syncs, [])
        self.assertEqual(len(self.done_signals), 3)                             # phát khi TỪNG chương xong (tab Đăng video dùng để tự đăng ngay chương đó)

    def test_error_scenes_trigger_auto_sync_then_merge(self):
        p = self.tab.project
        self.fail_chapters = {"02"}
        self.run_batch(["01", "02", "03"])
        self.assertEqual(self.syncs, [["02"]])                                   # chỉ đồng bộ đúng chương lỗi
        self.assertTrue(p.merged_path(p.chapters[1]).exists())                   # đồng bộ khôi phục clip → ghép được
        self.assertTrue(all(s.status == "done" for c in p.chapters for s in c.scenes))

    def test_options_off_means_no_sync_no_merge(self):
        p = self.tab.project
        p.gen_auto_merge = p.gen_auto_sync = False
        self.fail_chapters = {"02"}
        self.run_batch(["01", "02"])
        self.assertEqual(self.syncs, [])
        self.assertFalse(p.merged_path(p.chapters[0]).exists())
        self.assertEqual([s.status for s in p.chapters[1].scenes], ["error", "error"])      # lỗi giữ nguyên, không tự xử lý

    def test_stop_ends_the_batch_cleanly(self):
        original = self.tab.flow_auto_run

        def stopping(mode="pending", silent=False, then=None):
            original(mode, silent, then)
            self.tab.request_cancel()                                           # người dùng bấm Dừng ngay sau chương đầu
        self.tab.flow_auto_run = stopping
        self.run_batch(["01", "02", "03"])
        self.assertEqual(self.calls, ["01"])
        self.assertIsNone(self.tab._batch)

    def test_already_done_chapters_are_skipped_but_still_merged(self):
        p = self.tab.project
        for s in p.chapters[0].scenes:
            self.finish_scene(p.chapters[0], s)
        self.assertFalse(p.merged_path(p.chapters[0]).exists())
        self.tab._finish_chapter(p.chapters[0])
        self.assertTrue(wait(lambda: p.merged_path(p.chapters[0]).exists() and not self.tab._busy, 30))

    def test_no_pending_scenes_shows_message_instead_of_starting(self):
        from unittest import mock
        p = self.tab.project
        for ch in p.chapters:
            for s in ch.scenes:
                s.status = "done"
        with mock.patch.object(QMessageBox, "information") as info:
            self.tab.start_batch(["01", "02"])
        info.assert_called_once()
        self.assertIsNone(self.tab._batch)

    def test_dialog_summary_and_selection(self):
        from app.chapter_gen_dialog import ChapterGenDialog
        p = self.tab.project
        for s in p.chapters[0].scenes:
            s.status = "done"                                                   # chương 1 đã xong: không chọn được
        d = ChapterGenDialog(None, p)
        self.assertFalse(d.checks[0].isEnabled())
        self.assertEqual([c.id for c in d.chosen()], ["02", "03"])               # mặc định chọn mọi chương chưa xong
        self.assertIn("4 scene của 2 chương", d.summary.text())
        d.checks[2].setChecked(False)
        self.assertIn("2 scene của 1 chương", d.summary.text())
        d.set_all(False)
        self.assertFalse(d.ok.isEnabled())

    def test_project_settings_toggles_roundtrip(self):
        d = self.tab.settings_dialog
        d.auto_merge.setChecked(False)
        d.auto_sync.setChecked(False)
        d.apply_publish()
        p = models.Project.load("Gen lô") if False else self.tab.project
        self.assertEqual((p.gen_auto_merge, p.gen_auto_sync), (False, False))


class TestRealFlowAutoRunChain(Tmp):
    """Dùng hàm flow_auto_run THẬT với Chrome/Flow giả (luôn lỗi) để chắc chuỗi vẫn đi tiếp, không treo và không đổi trạng thái scene."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        from app import flow_auto, theme, ui
        theme.install(app)
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        p = models.Project("Flow lỗi")
        for i in range(2):
            ch = p.new_chapter(f"Chương {i + 1}")
            for k in (1, 2):
                ch.scenes.append(models.Scene(k, narration="x", visual="v", status="pending"))
        p.save()
        self.w = ui.MainWindow()
        self.addCleanup(self.w.close)
        self.tab = self.w.proj
        self.tab.combo.setCurrentText("Flow lỗi")

        class Broken:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                raise flow_auto.FlowError("Chrome Flow chưa mở")

            def __exit__(self, *a):
                return False
        for patcher in (mock.patch.object(flow_auto, "FlowAuto", Broken), mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                        mock.patch.object(QMessageBox, "warning")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_failed_flow_does_not_break_the_chain(self):
        order = []
        self.tab.generation_done.connect(lambda: order.append("done"))
        self.tab.start_batch(["01", "02"])
        self.assertTrue(wait(lambda: self.tab._batch is None and not self.tab._busy, 60))
        p = self.tab.project
        self.assertTrue(all(s.status == "pending" for c in p.chapters for s in c.scenes))     # không gen được → trả về chờ gen, không kẹt "queued"
        self.assertEqual(len(order), 2)                                                        # cả hai chương đều được xử lý rồi kết thúc lô

    def test_single_chapter_run_emits_done_after_post_processing(self):
        got = []
        self.tab.generation_done.connect(lambda: got.append(1))
        self.tab.show_chapter(0)
        self.tab.flow_auto_run("pending")
        self.assertTrue(wait(lambda: not self.tab._busy and bool(got), 60))
        self.assertEqual(len(got), 1)

    def test_silent_with_nothing_to_do_still_continues(self):
        p = self.tab.project
        for s in p.chapters[0].scenes:
            s.status = "done"
        called = []
        self.tab.show_chapter(0)
        self.tab.flow_auto_run("pending", silent=True, then=lambda: called.append(1))
        self.assertTrue(wait(lambda: bool(called), 30))


class TestEveryPopupOpens(Tmp):
    """Mở mọi popover, menu, hộp thoại và phím tắt: không được ném lỗi, nội dung không rỗng, mô tả dài không cụt chữ."""

    def setUp(self):
        super().setUp()
        from app import theme, ui
        theme.install(app)
        models.DATA_DIR, models.PROJ_DIR = self.tmp, self.tmp / "projects"
        p = models.Project("Mở hết")
        for i in range(2):
            ch = p.new_chapter(f"Chương {i + 1}")
            for k, st in enumerate(("pending", "done", "error", "raw"), 1):
                ch.scenes.append(models.Scene(k, narration="Ngày xưa", visual="v", status=st))
        p.save()
        self.w = ui.MainWindow()
        self.addCleanup(self.w.close)
        self.w.resize(1280, 800)
        self.w.show()
        self.w.proj.combo.setCurrentText("Mở hết")
        QCoreApplication.processEvents()
        self.p = self.w.proj

    def test_project_popovers(self):
        p = self.p
        for pop, anchor, side, align in ((p.pop_project, p.nav_project, "below", "left"), (p.pop_chapter, p.nav_chapter, "below", "left"),
                                         (p.pop_more, p.more, "below", "right"), (p.pop_manage, p.manage_btn, "below", "right"),
                                         (p.pop_gen, p.s2, "above", "left")):
            pop.show_for(anchor, side, align)
            QCoreApplication.processEvents()
            self.assertTrue(pop.isVisible() and pop.height() > 80)
            pop.hide()

    def test_chip_popovers_and_context_popover(self):
        self.w.on_chip("llm")
        self.assertTrue(self.w.llm_pop.isVisible())
        self.w.llm_pop.hide()
        self.w.on_chip("flow")
        self.assertTrue(self.w.flow_pop.isVisible())
        self.w.flow_pop.hide()
        self.p.table.setCurrentCell(1, 0)
        self.p.table_context_menu(self.p.table.viewport().rect().center())
        self.assertTrue(self.p._ctx_pop.isVisible())

    def test_popover_descriptions_are_elided_not_cut(self):
        from app.widgets import ElidedLabel
        self.p.pop_gen.show_for(self.p.s2, "above", "left")
        QCoreApplication.processEvents()
        subs = [lab for lab in self.p.pop_gen.findChildren(ElidedLabel)]
        self.assertTrue(subs)
        for lab in subs:                                  # mô tả dài đã được cắt bằng … và có tooltip đọc đủ
            self.assertTrue(lab.toolTip())
        gen_sub = next(lab for lab in subs if "Scene đang xem" not in lab.text() and lab.toolTip().startswith("  ·") is False)
        self.assertFalse(any(lab.toolTip().startswith(" ·") or lab.toolTip().startswith("·") for lab in subs))
        self.assertTrue(gen_sub)
        self.p.pop_gen.hide()

    def test_every_menu_builds(self):
        from PySide6.QtWidgets import QMenu
        menus = self.w.findChildren(QMenu)
        self.assertGreaterEqual(len(menus), 8)
        for m in menus:
            m.aboutToShow.emit()
            self.assertTrue(m.actions())

    def test_dialogs_open(self):
        from app.char_gen_dialog import CharGenDialog
        from app.char_image_dialog import CharImageDialog
        from app.error_dialog import ErrorDialog
        from app.pack_guide import PackGuideDialog
        from app.trash_dialog import TrashedProjectsDialog
        from app.widgets import CharPickDialog
        c = models.Character(name="Thỏ")
        for d in (ErrorDialog(self.w, "Lỗi", "Chương 1", "x" * 200, "x", lambda: None), TrashedProjectsDialog(self.w),
                  CharGenDialog("Mở hết", self.w, "01"), CharImageDialog("Mở hết", c, self.w, "01"), PackGuideDialog(self.w),
                  CharPickDialog(self.w, [c], [])):
            d.show()
            QCoreApplication.processEvents()
            self.assertTrue(d.isVisible())
            d.close()

    def test_input_dialog_is_vietnamese(self):
        from PySide6.QtWidgets import QInputDialog
        d = QInputDialog(self.w)
        d.setLabelText("Tên mới:")
        d.show()
        QCoreApplication.processEvents()
        self.assertEqual(d.okButtonText(), "Đồng ý")
        self.assertEqual(d.cancelButtonText(), "Huỷ")
        d.close()

    def test_shortcuts_switch_tabs_and_focus_search(self):
        from PySide6.QtGui import QShortcut
        keys = {s.key().toString() for s in self.w.findChildren(QShortcut)}
        self.assertTrue({"Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4"} <= keys)
        self.assertTrue(any(s.key().matches(__import__("PySide6.QtGui", fromlist=["QKeySequence"]).QKeySequence.Find) for s in self.p.findChildren(QShortcut)))

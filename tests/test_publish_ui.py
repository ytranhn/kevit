from . import _env  # noqa: F401

import os
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from app import models
from app.publish import PLATFORMS, service
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
        self.assertIn("Giả", t.table.item(0, 4).text())

    def test_edit_metadata_is_saved(self):
        t = self.tab
        t.table.setCurrentCell(1, 1)
        t.edit_title.setText("Tiêu đề mới")
        t.edit_tags.setText("#a #b, c")
        t.commit_editor()
        again = models.Project.load("Dự án UI")
        self.assertEqual(again.chapters[1].post_meta["title"], "Tiêu đề mới")
        self.assertEqual(again.chapters[1].post_meta["hashtags"], ["a", "b", "c"])

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
        self.assertEqual([k for k, cb in w.publish_tab.acc_checks.items() if cb.isChecked()], ["tiktok:1", "youtube:A"])
        other = models.Project.load("P2" if proj.project.name == "P1" else "P1")
        self.assertNotEqual(sorted(other.publish_accounts), ["tiktok:1", "youtube:A"])    # dự án kia không bị ảnh hưởng

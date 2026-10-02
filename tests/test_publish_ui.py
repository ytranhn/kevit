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
        FakePlatform.sent, FakePlatform.fail = [], False
        p = models.Project("Dự án UI", publish_platforms=["fake"], publish_privacy="unlisted")
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
        self.assertTrue(self.tab.plat_checks["fake"].isChecked() if "fake" in self.tab.plat_checks else True)

    def test_publish_runs_in_background_and_updates_history(self):
        t = self.tab
        t.run_publish(self.p, t.checked_targets(), ["fake"], "unlisted")
        self.assertTrue(wait(lambda: t._worker is None))
        self.assertEqual(len(FakePlatform.sent), 2)
        self.assertEqual(t.history.rowCount(), 2)
        self.assertIn("Đã đăng 2/2", t.status.text())
        self.assertEqual(t.table.item(0, 4).text(), "Giả")

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

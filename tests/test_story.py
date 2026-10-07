from . import _env  # noqa: F401  (phải import trước mọi module của app)

import json
import unittest
from unittest import mock

from app import story_writer
from app.models import Chapter, Project, Scene


def fake_llm(prompt, schema, log=print, profile=None):
    if "chapters" in schema["properties"]:
        n = int(prompt.split("đúng ")[1].split(" chương")[0])
        return json.dumps({"chapters": [{"title": f"Tựa {i}", "summary": f"Tóm tắt {i}"} for i in range(1, n + 1)]})
    k = prompt.split("TRỌN chương ")[1].split(" ")[0]
    return json.dumps({"story": f"Nội dung chương {k}."})


class TestStoryWriter(unittest.TestCase):
    def run_gen(self, p, n=3, cancelled=lambda: False):
        with mock.patch.object(story_writer.llm, "generate_json", fake_llm), \
             mock.patch.object(story_writer.llm, "describe", return_value="giả"), mock.patch.object(Project, "save"):
            return story_writer.generate(p, "Làng chài thời xưa", n, 500, "cổ tích", lambda *_: None, cancelled)

    def test_writes_n_chapters_and_reuses_empty_first(self):
        p = Project(name="t_story")
        p.new_chapter()
        made = self.run_gen(p, 3)
        self.assertEqual(len(made), 3)
        self.assertEqual(len(p.chapters), 3)                    # chương trống ban đầu được dùng lại
        self.assertEqual([c.title for c in p.chapters], ["Tựa 1", "Tựa 2", "Tựa 3"])
        self.assertTrue(all(c.story.startswith("Nội dung chương") for c in p.chapters))

    def test_appends_after_existing_chapters(self):
        p = Project(name="t_story2")
        old = p.new_chapter("Mở đầu")
        old.story, old.scenes = "Truyện cũ.", [Scene(index=1)]
        self.run_gen(p, 2)
        self.assertEqual(len(p.chapters), 3)
        self.assertEqual(p.chapters[0].story, "Truyện cũ.")

    def test_cancel_keeps_written_chapters(self):
        p = Project(name="t_story3")
        calls = []
        made = self.run_gen(p, 3, cancelled=lambda: bool(calls) or calls.append(1) and False)
        self.assertEqual(len(made), 1)
        self.assertEqual(len(p.chapters), 1)

    def test_empty_outline_raises(self):
        p = Project(name="t_story4")
        with mock.patch.object(story_writer.llm, "generate_json", return_value='{"chapters": []}'), \
             mock.patch.object(story_writer.llm, "describe", return_value="giả"):
            with self.assertRaises(ValueError):
                story_writer.generate(p, "x", 2, 500, "", lambda *_: None)

    def test_chapter_count_is_clamped(self):
        p = Project(name="t_story5")
        self.assertEqual(len(self.run_gen(p, 999)), story_writer.MAX_CHAPTERS)


if __name__ == "__main__":
    unittest.main()

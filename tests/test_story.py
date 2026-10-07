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


class TestSseResponse(unittest.TestCase):
    def test_parse_sse_joins_content_and_ignores_reasoning(self):
        from app import llm
        sse = "\n\n".join([
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"nghĩ..."}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"{\\"a\\":"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":" 1}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":4}}',
            "data: [DONE]"])
        got, stop, usage = llm.parse_sse(sse)
        self.assertEqual(got, '{"a": 1}')
        self.assertEqual(stop, "stop")
        self.assertEqual(usage["completion_tokens"], 4)

    def test_openai_accepts_sse_body(self):
        import httpx
        from app import llm
        body = 'data: {"choices":[{"delta":{"reasoning_content":"x"}}]}\n\ndata: {"choices":[{"delta":{"content":"{\\"ok\\": true}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n'
        real = httpx.Client
        with mock.patch.object(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body)), **kw)):
            out = llm._openai("hi", {"type": "object"}, lambda *_: None, mock.MagicMock(effective_base_url="http://x/v1", effective_key="k",
                                                                                      effective_model="m", proxy=""))
        self.assertEqual(json.loads(out), {"ok": True})


class TestPlanMany(unittest.TestCase):
    def setUp(self):
        self.p = Project(name="t_planmany")
        for i in range(1, 5):
            self.p.new_chapter().story = "" if i == 3 else f"Truyện {i}."

    def run_plan(self, cancelled=lambda: False, boom_on="Truyện 2."):
        from app import scene_planner

        def fake(story, chars, n, syn, log, lang):
            if story == boom_on:
                raise ValueError("LLM hỏng")
            return [Scene(index=1, narration=story)], ["ghi chú"]
        saved = []
        with mock.patch.object(scene_planner, "plan_scenes", fake):
            res = scene_planner.plan_many(self.p.chapters, [], 16, "", lambda *_: None, "vi", cancelled, lambda: saved.append(1))
        return res, saved

    def test_error_and_empty_do_not_block_others(self):
        (ok, failed), saved = self.run_plan()
        self.assertEqual([c.id for c in ok], ["01", "04"])
        self.assertEqual({c.id for c, _ in failed}, {"02", "03"})
        self.assertEqual(len(saved), 2)                        # lưu ngay sau từng chương xong
        self.assertTrue(self.p.chapters[0].scenes and not self.p.chapters[1].scenes)

    def test_cancel_stops_batch(self):
        n = []
        (ok, _), _ = self.run_plan(cancelled=lambda: bool(n) or n.append(1) or False, boom_on="")
        self.assertEqual(len(ok), 1)


class TestSceneBatchDialog(unittest.TestCase):
    def test_default_selects_chapters_with_story_and_no_scenes(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from app.scene_batch_dialog import SceneBatchDialog
        p = Project(name="t_dlg")
        for story, scenes in (("A.", []), ("B.", [Scene(index=1)]), ("", []), ("D.", [])):
            ch = p.new_chapter()
            ch.story, ch.scenes = story, scenes
        with mock.patch("app.llm.describe", return_value="giả"):
            dlg = SceneBatchDialog(None, p)
        self.assertEqual([c.id for c in dlg.chosen()], ["01", "04"])
        dlg.checks[1].setChecked(True)
        self.assertEqual([c.id for c in dlg.replaced()], ["02"])
        self.assertIn("⚠", dlg.summary.text())
        self.assertFalse(dlg.checks[2].isEnabled())            # chương trống không chọn được

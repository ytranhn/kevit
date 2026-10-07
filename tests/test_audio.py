from . import _env  # noqa: F401  (phải import trước mọi module của app)

import unittest
from unittest import mock

from app import pipeline, veo_client
from app.models import Chapter, Project, Scene


class TestAudioOptions(unittest.TestCase):
    def test_defaults_keep_old_behaviour(self):
        p = Project(name="x")
        self.assertTrue(p.narration_enabled)
        self.assertFalse(p.bgm_enabled)
        self.assertIn("NO music", veo_client.build_prompt(p, Scene(index=1, visual="a cat"), {}))

    def test_bgm_goes_into_prompt(self):
        p = Project(name="x", bgm_enabled=True, bgm_style="soft piano")
        txt = veo_client.build_prompt(p, Scene(index=1, visual="a cat"), {})
        self.assertNotIn("NO music", txt)
        self.assertIn("soft piano", txt)
        self.assertIn("NO speech", txt)

    def test_narration_off_keeps_raw_clip(self):
        import tempfile
        from pathlib import Path
        raw = Path(tempfile.mkdtemp()) / "r.mp4"
        raw.write_bytes(b"x")
        s = Scene(index=1, narration="Xin chào", raw_clip=str(raw), audio="cu.mp3")
        with mock.patch.object(pipeline, "mux_voice") as mux:
            pipeline.apply_voice(Project(name="x", narration_enabled=False), Chapter(id="01"), s)
        mux.assert_not_called()
        self.assertEqual((s.clip, s.audio), (str(raw), ""))


if __name__ == "__main__":
    unittest.main()

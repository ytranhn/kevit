"""Hoàn thiện 1 scene: clip Veo gốc + giọng đọc cố định của dự án -> clip cuối."""
import hashlib
from pathlib import Path

from . import tts
from .merger import mux_voice
from .models import Chapter, Project, Scene


def apply_voice(p: Project, ch: Chapter, s: Scene, log=print) -> None:
    raw = Path(s.raw_clip)
    if not raw.exists():
        raise RuntimeError("Chưa có clip Veo gốc")
    if not p.narration_enabled or not s.narration.strip():   # tắt thuyết minh: giữ nguyên âm thanh Veo gốc
        s.clip, s.audio = str(raw), ""
        return
    key = hashlib.sha1(f"{p.tts_provider}|{p.voice}|{p.voice_style}|{s.narration}".encode()).hexdigest()[:10]
    wav = p.chapter_dir(ch) / "audio" / f"scene_{s.index:02d}_{key}.{'mp3' if p.tts_provider == 'edge' else 'wav'}"
    if not tts.is_valid_audio(wav):  # cache: đổi giọng/lời mới gen lại TTS; file rỗng/hỏng cũng gen lại
        log(f"Scene {s.index}: tạo giọng đọc ({p.tts_provider}/{p.voice})...")
        wav.unlink(missing_ok=True)
        tts.synthesize(s.narration, p.tts_provider, p.voice, p.voice_style, wav)
    s.audio = str(wav)
    s.clip = str(mux_voice(raw, wav, p.chapter_dir(ch) / "clips" / f"scene_{s.index:02d}.mp4",
                           ambient_vol=p.bgm_volume / 100 if p.bgm_enabled else 0.25))


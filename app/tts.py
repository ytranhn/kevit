"""Gemini TTS: cùng 1 voice + style cho mọi scene trong dự án -> giọng đọc nhất quán."""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import wave
from pathlib import Path

from google import genai
from google.genai import types

from .settings import TTS_MODEL, get_api_key

VOICES = ["Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr", "Callirrhoe",
          "Achird", "Sulafat", "Algenib", "Gacrux", "Umbriel", "Despina", "Erinome"]


MIN_AUDIO_BYTES = 2000   # ~0.1s mp3; nhỏ hơn là chắc chắn rỗng/hỏng
EDGE_MIN_GAP = 4.0       # giây giữa 2 yêu cầu Edge: gọi dồn dập bị dịch vụ từ chối ("No audio was received")
EDGE_RETRY_WAIT = (4, 8, 15, 25)
_edge_lock = threading.Lock()
_edge_last = 0.0


def is_valid_audio(path: Path) -> bool:
    return path.exists() and path.stat().st_size >= MIN_AUDIO_BYTES


EDGE_VOICES = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
PROVIDERS = {"edge": "Edge TTS (miễn phí, không cần key)", "gemini": "Gemini TTS (cần API key)"}
VOICES_BY_PROVIDER = {"edge": EDGE_VOICES, "gemini": VOICES}


def synthesize(text: str, provider: str, voice: str, style: str, dst: Path) -> Path:
    return (_edge if provider == "edge" else _gemini)(text, voice, style, dst)


def _edge_wait_turn() -> None:
    """Giữ nhịp tối thiểu giữa các yêu cầu Edge (đo thực tế: cách <=0s thất bại ~40%, cách >=2s thành công 100%)."""
    global _edge_last
    with _edge_lock:
        wait = _edge_last + EDGE_MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _edge_last = time.monotonic()


def _edge(text: str, voice: str, style: str, dst: Path) -> Path:
    import edge_tts  # style bị bỏ qua: Edge TTS không nhận chỉ dẫn phong cách
    dst.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(len(EDGE_RETRY_WAIT) + 1):
        _edge_wait_turn()
        try:
            # Luồng riêng: luồng gọi có thể đang giữ event loop (Playwright) nên không dùng asyncio.run trực tiếp
            with ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(lambda: asyncio.run(edge_tts.Communicate(text, voice).save(str(dst)))).result()
            if is_valid_audio(dst):
                return dst
            last = RuntimeError("dịch vụ trả về file âm thanh rỗng/hỏng")
        except Exception as e:  # noqa: BLE001
            last = e
        dst.unlink(missing_ok=True)   # không để lại file hỏng làm bộ đệm
        if attempt < len(EDGE_RETRY_WAIT):
            time.sleep(EDGE_RETRY_WAIT[attempt])
    raise RuntimeError(f"Edge TTS không phản hồi sau {len(EDGE_RETRY_WAIT) + 1} lần thử ({last}). "
                       "Thường do gọi quá dồn: đợi vài phút rồi bấm lại, file đã tạo trước đó không bị mất.")


def _gemini(text: str, voice: str, style: str, dst: Path) -> Path:
    client = genai.Client(api_key=get_api_key())
    prompt = f"{style}:\n{text}" if style.strip() else text
    resp = client.models.generate_content(
        model=TTS_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
    pcm = resp.candidates[0].content.parts[0].inline_data.data  # PCM s16le 24kHz mono
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(pcm)
    return dst

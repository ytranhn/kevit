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

# Danh mục giọng Edge TTS (miễn phí, ~320 giọng cho ~75 ngôn ngữ). Lấy từ dịch vụ rồi lưu đệm 7 ngày; không có mạng thì dùng bảng dự phòng dưới.
_FALLBACK = {
    "vi": ["vi-VN-NamMinhNeural:M", "vi-VN-HoaiMyNeural:F"],
    "en": ["en-US-AndrewMultilingualNeural:M", "en-US-AvaMultilingualNeural:F", "en-US-GuyNeural:M", "en-US-JennyNeural:F",
           "en-GB-RyanNeural:M", "en-GB-SoniaNeural:F", "en-AU-WilliamMultilingualNeural:M", "en-AU-NatashaNeural:F"],
    "zh": ["zh-CN-YunxiNeural:M", "zh-CN-XiaoxiaoNeural:F", "zh-CN-YunjianNeural:M", "zh-CN-XiaoyiNeural:F", "zh-TW-HsiaoChenNeural:F", "zh-HK-WanLungNeural:M"],
    "ja": ["ja-JP-KeitaNeural:M", "ja-JP-NanamiNeural:F"],
    "ko": ["ko-KR-InJoonNeural:M", "ko-KR-SunHiNeural:F", "ko-KR-HyunsuMultilingualNeural:M"],
    "fr": ["fr-FR-HenriNeural:M", "fr-FR-DeniseNeural:F", "fr-CA-AntoineNeural:M", "fr-CA-SylvieNeural:F"],
    "de": ["de-DE-ConradNeural:M", "de-DE-KatjaNeural:F", "de-DE-FlorianMultilingualNeural:M"],
    "es": ["es-ES-AlvaroNeural:M", "es-ES-ElviraNeural:F", "es-MX-JorgeNeural:M", "es-MX-DaliaNeural:F"],
    "pt": ["pt-BR-AntonioNeural:M", "pt-BR-FranciscaNeural:F", "pt-PT-DuarteNeural:M", "pt-PT-RaquelNeural:F"],
    "it": ["it-IT-DiegoNeural:M", "it-IT-ElsaNeural:F", "it-IT-IsabellaNeural:F"],
    "ru": ["ru-RU-DmitryNeural:M", "ru-RU-SvetlanaNeural:F"],
    "id": ["id-ID-ArdiNeural:M", "id-ID-GadisNeural:F"],
    "hi": ["hi-IN-MadhurNeural:M", "hi-IN-SwaraNeural:F"],
    "ar": ["ar-SA-HamedNeural:M", "ar-SA-ZariyahNeural:F", "ar-EG-ShakirNeural:M", "ar-EG-SalmaNeural:F"],
    "th": ["th-TH-NiwatNeural:M", "th-TH-PremwadeeNeural:F"],
}
_PREFERRED = {"vi": "vi-VN-NamMinhNeural", "en": "en-US-AndrewMultilingualNeural", "zh": "zh-CN-YunxiNeural", "ja": "ja-JP-KeitaNeural",
              "ko": "ko-KR-InJoonNeural", "fr": "fr-FR-HenriNeural", "de": "de-DE-ConradNeural", "es": "es-ES-AlvaroNeural",
              "pt": "pt-BR-AntonioNeural", "it": "it-IT-DiegoNeural", "ru": "ru-RU-DmitryNeural", "id": "id-ID-ArdiNeural",
              "hi": "hi-IN-MadhurNeural", "ar": "ar-SA-HamedNeural", "th": "th-TH-NiwatNeural"}
_catalog: dict[str, list[tuple[str, str]]] | None = None


def _cache_file() -> Path:
    from . import models
    return models.DATA_DIR / "edge_voices.json"


def _fetch_edge_voices() -> list[dict]:
    import edge_tts
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(edge_tts.list_voices())).result(timeout=12)


def edge_catalog(refresh: bool = False) -> dict[str, list[tuple[str, str]]]:
    """{mã ngôn ngữ: [(tên giọng, 'M'|'F'), ...]} cho toàn bộ giọng Edge. Thứ tự: giọng ưu tiên trước, rồi nam/nữ theo vùng."""
    global _catalog
    if _catalog is not None and not refresh:
        return _catalog
    import json
    raw = None
    f = _cache_file()
    try:
        if f.exists() and not refresh and time.time() - f.stat().st_mtime < 7 * 86400:
            raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        try:
            raw = [{"n": v["ShortName"], "g": v["Gender"][0], "l": v["Locale"]} for v in _fetch_edge_voices()]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001 - không có mạng: dùng bảng dự phòng
            raw = [{"n": x.split(":")[0], "g": x.split(":")[1], "l": "-".join(x.split("-")[:2])} for xs in _FALLBACK.values() for x in xs]
    cat: dict[str, list[tuple[str, str]]] = {}
    for v in raw:
        cat.setdefault(v["l"].split("-")[0].lower(), []).append((v["n"], v["g"]))
    for lang, voices in cat.items():
        pref = _PREFERRED.get(lang)
        voices.sort(key=lambda x: (x[0] != pref, x[1] != "M", x[0]))
    _catalog = cat
    return cat


def voices_for(provider: str, lang: str = "vi") -> list[tuple[str, str]]:
    """Danh sách giọng [(mã giọng, nhãn hiển thị)] theo nhà cung cấp và ngôn ngữ thuyết minh."""
    if provider == "edge":
        out = []
        for name, g in edge_catalog().get(lang, []) or [(v, "M") for v in EDGE_VOICES if lang == "vi"]:
            nice = name.replace("Neural", "").replace("Multilingual", " Đa ngữ")
            out.append((name, f"{nice}  ·  {'Nam' if g == 'M' else 'Nữ'}"))
        return out
    return [(v, v) for v in VOICES]               # Gemini TTS: giọng dùng được cho mọi ngôn ngữ


def default_voice(provider: str, lang: str = "vi") -> str:
    vs = [v for v, _ in voices_for(provider, lang)]
    if provider == "edge":
        pref = _PREFERRED.get(lang)
        return pref if pref in vs else (vs[0] if vs else EDGE_VOICES[1])
    return vs[0]


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

"""Ngôn ngữ thuyết minh: tên hiển thị, cách đếm đơn vị (từ hoặc ký tự), tốc độ đọc và ngân sách cho clip ~8 giây.
Tiếng Việt là ngôn ngữ của truyện gốc; các ngôn ngữ khác được dịch từ đoạn truyện gốc."""
from __future__ import annotations

import re

TEMPO = 1.3                  # tối đa tăng tốc giọng khi ghép (xem merger.mux_voice)
CLIP_SECONDS = 8

# mã -> (tên hiển thị, đơn vị đếm, số đơn vị đọc được mỗi giây ở tốc độ bình thường)
LANGS: dict[str, tuple[str, str, float]] = {
    "vi": ("Tiếng Việt", "từ", 3.3),
    "en": ("English (Anh)", "words", 2.6),
    "zh": ("中文 (Trung)", "chữ", 4.2),
    "ja": ("日本語 (Nhật)", "chữ", 5.5),
    "ko": ("한국어 (Hàn)", "chữ", 4.8),
    "fr": ("Français (Pháp)", "words", 2.7),
    "de": ("Deutsch (Đức)", "words", 2.3),
    "es": ("Español (Tây Ban Nha)", "words", 3.0),
    "pt": ("Português (Bồ Đào Nha)", "words", 2.9),
    "it": ("Italiano (Ý)", "words", 2.8),
    "ru": ("Русский (Nga)", "words", 2.4),
    "id": ("Bahasa Indonesia", "words", 2.6),
    "hi": ("हिन्दी (Hindi)", "words", 2.5),
    "ar": ("العربية (Ả Rập)", "words", 2.3),
    "th": ("ไทย (Thái)", "chữ", 6.5),
}
CJK = {"zh", "ja", "ko", "th"}           # không tách từ bằng dấu cách: đếm theo ký tự


def name(lang: str) -> str:
    return LANGS.get(lang, LANGS["vi"])[0]


def english_name(lang: str) -> str:
    """Tên tiếng Anh của ngôn ngữ, dùng trong prompt cho AI."""
    return {"vi": "Vietnamese", "en": "English", "zh": "Chinese (Simplified)", "ja": "Japanese", "ko": "Korean", "fr": "French",
            "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "ru": "Russian", "id": "Indonesian",
            "hi": "Hindi", "ar": "Arabic", "th": "Thai"}.get(lang, "Vietnamese")


def unit_label(lang: str) -> str:
    return LANGS.get(lang, LANGS["vi"])[1]


def rate(lang: str) -> float:
    return LANGS.get(lang, LANGS["vi"])[2]


def count_units(text: str, lang: str = "vi") -> int:
    """Số từ (hoặc số ký tự với tiếng Trung/Nhật/Hàn/Thái) của một đoạn thuyết minh."""
    if lang in CJK:
        return len(re.findall(r"[^\W_]", text, re.UNICODE))
    return len(text.split())


def seconds(text: str, lang: str = "vi") -> float:
    return count_units(text, lang) / rate(lang)


def budget(lang: str = "vi", seconds_: int = CLIP_SECONDS) -> int:
    """Số đơn vị tối đa đọc kịp trong một clip (có tăng tốc nhẹ tới TEMPO)."""
    return int(rate(lang) * seconds_ * TEMPO)

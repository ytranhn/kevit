"""Giá credit Google Flow cho 1 clip (x1), đo trực tiếp trên giao diện Flow ngày 2026-10-01.
Chỉ để ước tính trước khi gen; giá thật đọc từ Flow và ghi vào nhật ký, có thể đổi theo thời gian."""
from __future__ import annotations

OMNI = "Omni 1.1 Flash"
OMNI_COST = {"360p": {4: 4, 6: 5, 8: 6, 10: 7}, "720p": {4: 7, 6: 10, 8: 12, 10: 15}}
FIXED_COST = {"Veo 3.1 - Lite": 10, "Veo 3.1 - Fast": 20, "Veo 3.1 - Quality": 100}   # luôn 8 giây
DURATIONS = (4, 6, 8, 10)
WORDS_PER_SEC = 3.3
MAX_TEMPO = 1.15          # cho phép đọc nhanh nhẹ để chọn clip ngắn hơn (tối đa 1.3 ở bước ghép)


def pick_duration(narration: str, lang: str = "vi") -> int:
    """Thời lượng clip Omni ngắn nhất vẫn đủ chỗ đọc hết thuyết minh (có đệm 0.3s, tăng tốc nhẹ tối đa 1.15x).
    Tốc độ đọc phụ thuộc ngôn ngữ (tiếng Anh ~2.6 từ/giây, tiếng Trung ~4.2 chữ/giây...)."""
    from . import langs
    sec = langs.seconds(narration, lang) + 0.3
    return next((d for d in DURATIONS if d * MAX_TEMPO >= sec), DURATIONS[-1])


def scene_cost(model: str, resolution: str, duration: int) -> int:
    if model == OMNI:
        return OMNI_COST.get(resolution, OMNI_COST["720p"]).get(duration, OMNI_COST["720p"][8])
    return FIXED_COST.get(model, 20)


def estimate(model: str, resolution: str, scenes: list, auto_duration: bool, lang: str = "vi") -> int:
    total = 0
    for s in scenes:
        d = pick_duration(s.narration, lang) if (auto_duration and model == OMNI) else 8
        total += scene_cost(model, resolution, d)
    return total


def scene_costs(model: str, resolution: str, scenes: list, auto_duration: bool, lang: str = "vi") -> list[int]:
    """Chi phí ước tính từng scene (giữ thứ tự), dùng để chia scene theo credit từng tài khoản."""
    out = []
    for s in scenes:
        d = pick_duration(s.narration, lang) if (auto_duration and model == OMNI) else 8
        out.append(scene_cost(model, resolution, d))
    return out


def parse_amount(text: str) -> int | None:
    """Số credit trong một đoạn chữ của Flow ('Generating will use 20 credits', '1,002 Google Flow credits', '1.002 ...'): gộp mọi chữ số
    (dấu phẩy/chấm/khoảng trắng là ngăn cách hàng nghìn). Không có số thì None."""
    import re
    m = re.search(r"\d[\d.,\u202f\u00a0 ]*", text or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return int(digits) if digits else None

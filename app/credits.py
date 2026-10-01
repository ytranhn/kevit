"""Giá credit Google Flow cho 1 clip (x1), đo trực tiếp trên giao diện Flow ngày 2026-10-01.
Chỉ để ước tính trước khi gen; giá thật đọc từ Flow và ghi vào nhật ký, có thể đổi theo thời gian."""
from __future__ import annotations

OMNI = "Omni 1.1 Flash"
OMNI_COST = {"360p": {4: 4, 6: 5, 8: 6, 10: 7}, "720p": {4: 7, 6: 10, 8: 12, 10: 15}}
FIXED_COST = {"Veo 3.1 - Lite": 10, "Veo 3.1 - Fast": 20, "Veo 3.1 - Quality": 100}   # luôn 8 giây
DURATIONS = (4, 6, 8, 10)
WORDS_PER_SEC = 3.3
MAX_TEMPO = 1.15          # cho phép đọc nhanh nhẹ để chọn clip ngắn hơn (tối đa 1.3 ở bước ghép)


def pick_duration(narration: str) -> int:
    """Thời lượng clip Omni ngắn nhất vẫn đủ chỗ đọc hết thuyết minh (có đệm 0.3s, tăng tốc nhẹ tối đa 1.15x)."""
    sec = len(narration.split()) / WORDS_PER_SEC + 0.3
    return next((d for d in DURATIONS if d * MAX_TEMPO >= sec), DURATIONS[-1])


def scene_cost(model: str, resolution: str, duration: int) -> int:
    if model == OMNI:
        return OMNI_COST.get(resolution, OMNI_COST["720p"]).get(duration, OMNI_COST["720p"][8])
    return FIXED_COST.get(model, 20)


def estimate(model: str, resolution: str, scenes: list, auto_duration: bool) -> int:
    total = 0
    for s in scenes:
        d = pick_duration(s.narration) if (auto_duration and model == OMNI) else 8
        total += scene_cost(model, resolution, d)
    return total

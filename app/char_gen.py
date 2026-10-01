"""Tạo nhân vật phù hợp với dự án: (1) AI đọc truyện đề xuất danh sách nhân vật kèm mô tả ngoại hình (prompt tiếng Anh) theo phong cách
của dự án; (2) tuỳ chọn tạo ảnh tham chiếu bằng Gemini (cần Gemini API key)."""
from __future__ import annotations

import json
import time

from . import llm, models, settings
from .models import Character, Project, nfc, safe_dirname

STORY_BUDGET = 28000          # tổng số ký tự truyện gửi cho AI (chia đều giữa các chương) để tiết kiệm token

SCHEMA = {
    "type": "object",
    "properties": {"characters": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "role": {"type": "string"},
            "appearance_en": {"type": "string"},
            "description_vi": {"type": "string"},
        },
        "required": ["name", "aliases", "role", "appearance_en", "description_vi"],
    }}},
    "required": ["characters"],
}


def _fold(x: str) -> str:
    from .scene_planner import _fold as f
    return f(x)


def story_digest(p: Project, budget: int = STORY_BUDGET) -> str:
    """Gom bối cảnh + truyện các chương, cắt đều theo ngân sách ký tự."""
    chs = [c for c in p.chapters if c.story.strip()]
    per = max(1500, budget // max(len(chs), 1))
    parts = [f"BỐI CẢNH CHUNG: {p.synopsis.strip()}"] if p.synopsis.strip() else []
    for c in chs:
        t = c.story.strip()
        if len(t) > per:                                  # giữ đầu và cuối chương: nhân vật thường xuất hiện ở cả hai nơi
            t = t[: per * 2 // 3] + "\n[...lược bớt...]\n" + t[-per // 3:]
        parts.append(f"=== {c.name} ===\n{t}")
    return "\n\n".join(parts)


def suggest_characters(p: Project, existing: list[Character], max_n: int = 10, log=print) -> list[dict]:
    """Trả về các nhân vật đề xuất (chưa có trong dự án): [{name, aliases, role, appearance_en, description_vi}]."""
    digest = story_digest(p)
    if not digest.strip():
        raise ValueError("Dự án chưa có nội dung truyện hay bối cảnh. Dán truyện vào tab Truyện (hoặc điền Bối cảnh) trước.")
    have = ", ".join(c.name for c in existing) or "(chưa có)"
    prompt = f"""Bạn giúp đạo diễn dựng video kể chuyện: đọc truyện dưới đây rồi đề xuất danh sách nhân vật cần có ảnh tham chiếu.

Yêu cầu:
- Chọn tối đa {max_n} nhân vật có TÊN RIÊNG hoặc vai trò rõ ràng và xuất hiện đáng kể (ưu tiên nhân vật chính, người nói nhiều, người xuất hiện lặp lại). Bỏ vai quần chúng.
- KHÔNG đề xuất nhân vật đã có: {have}.
- "name": tên chuẩn đúng như trong truyện (một cách gọi duy nhất). "aliases": các cách gọi khác, chức danh, biệt danh có trong truyện.
- "role": vai trò ngắn gọn bằng tiếng Việt (vd. "Nhân vật chính", "Sư tỷ", "Phản diện").
- "appearance_en": mô tả NGOẠI HÌNH bằng TIẾNG ANH, 25-45 từ, dùng làm prompt ảnh: giới tính, tuổi, vóc dáng, tóc, gương mặt, trang phục, đặc điểm nổi bật. Phù hợp thể loại/bối cảnh truyện và phong cách hình ảnh "{p.style or 'cinematic'}". Chỉ dựa vào truyện; truyện không tả thì suy ra hợp lý theo bối cảnh. Không có lời thoại, không có tên riêng.
- "description_vi": 1-2 câu tiếng Việt về nhân vật (tính cách, vai trò trong truyện); nếu ngoại hình do bạn tự suy ra, ghi "(gợi ý)".

{digest}"""
    data = json.loads(llm.generate_json(prompt, SCHEMA, log))
    taken = {_fold(nfc(c.name)) for c in existing} | {_fold(nfc(a)) for c in existing for a in c.aliases}
    out, seen = [], set()
    for it in data.get("characters", []):
        name = nfc(it.get("name", "")).strip()
        key = _fold(name)
        if not name or key in taken or key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "aliases": [nfc(a).strip() for a in it.get("aliases", []) if a.strip() and _fold(nfc(a)) not in taken],
                    "role": it.get("role", "").strip(), "appearance_en": it.get("appearance_en", "").strip(),
                    "description_vi": it.get("description_vi", "").strip()})
    return out[:max_n]


def image_prompt(p: Project, appearance: str) -> str:
    """Prompt tạo ảnh tham chiếu: một nhân vật, nền trơn, đúng phong cách dự án (để ảnh dùng được làm 'ingredient' cho Flow)."""
    style = (p.style or "").strip().rstrip(".")
    return (f"Character reference portrait: {appearance.strip().rstrip('.')}. "
            + (f"Visual style: {style}. " if style else "")
            + "Single character, upper body, facing the camera, centered, plain neutral background, soft even lighting, "
              "highly detailed face, no text, no watermark, no other people. Vertical 3:4 composition.")


def generate_image(prompt: str, model: str | None = None, log=print) -> bytes:
    """Tạo 1 ảnh bằng Gemini; trả về dữ liệu ảnh thô (PNG/JPEG)."""
    key = settings.get_api_key()
    if not key:
        raise ValueError("Chưa có Gemini API key: nhập ở Cài đặt → Gemini API để tạo ảnh. "
                         "(Hoặc dùng nút “Copy prompt ảnh” rồi tạo ảnh bằng công cụ khác, sau đó gắn ảnh vào nhân vật.)")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    model = model or settings.image_model()
    resp = None
    for attempt in range(3):
        for modalities in (["IMAGE"], ["TEXT", "IMAGE"]):
            try:
                resp = client.models.generate_content(model=model, contents=prompt,
                                                      config=types.GenerateContentConfig(response_modalities=modalities))
                break
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "modalit" in msg.lower() and modalities == ["IMAGE"]:
                    continue                       # model này đòi cả TEXT+IMAGE: thử lại kiểu đó
                if attempt < 2 and any(m in msg for m in llm.RETRY_MARKERS):
                    log(f"Gemini bận ({msg[:60]}), thử lại sau {5 * (attempt + 1)}s...")
                    time.sleep(5 * (attempt + 1))
                    break
                raise
        if resp is not None:
            break
    parts = []
    for cand in (getattr(resp, "candidates", None) or []):
        parts += list(getattr(getattr(cand, "content", None), "parts", None) or [])
    for part in parts:
        data = getattr(getattr(part, "inline_data", None), "data", None)
        if data:
            return data
    said = " ".join(getattr(p_, "text", "") or "" for p_ in parts).strip()
    block = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
    raise ValueError("Model không trả về ảnh" + (f" (bị chặn: {block})" if block else "") + (f". Model nói: {said[:160]}" if said else "")
                     + ". Thử đổi mô tả hoặc đổi model tạo ảnh trong Cài đặt.")


def save_character_image(project: str, name: str, data: bytes) -> str:
    """Lưu ảnh (bytes bất kỳ định dạng) thành PNG trong thư mục nhân vật của dự án; trả về đường dẫn."""
    from PySide6.QtGui import QImage
    img = QImage()
    if not img.loadFromData(data):
        raise ValueError("Dữ liệu ảnh nhận về không đọc được.")
    d = models.char_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{safe_dirname(name)}.png"
    if not img.save(str(dst), "PNG"):
        raise ValueError(f"Không ghi được ảnh vào {dst}")
    return str(dst)


def add_characters(project: str, cands: list[dict]) -> list[str]:
    """Thêm các nhân vật đã duyệt vào dự án (cand có thể kèm 'image_bytes'). Trùng tên thì cập nhật. Trả về tên đã thêm."""
    chars = models.load_characters(project)
    by = {nfc(c.name): c for c in chars}
    added = []
    for it in cands:
        name = nfc(it["name"]).strip()
        img = save_character_image(project, name, it["image_bytes"]) if it.get("image_bytes") else (by[name].image if name in by else "")
        c = Character(name, it["appearance_en"].strip(), [nfc(a) for a in it.get("aliases", [])], img, it.get("role", ""), "",
                      it.get("description_vi", ""))
        if name in by:
            chars[chars.index(by[name])] = c
        else:
            chars.append(c)
        by[name] = c
        added.append(name)
    models.save_characters(project, chars)
    return added

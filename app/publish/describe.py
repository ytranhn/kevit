"""Sinh tiêu đề, mô tả và hashtag cho video bằng mô hình AI đang chọn (cùng ngôn ngữ với thuyết minh)."""
from __future__ import annotations

import json

from .. import langs, llm
from ..models import Chapter, Project
from .base import clean_tags, truncate

SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "description": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
}, "required": ["title", "description", "hashtags"]}

STORY_CHARS = 5000        # phần truyện đưa cho AI (đủ nắm nội dung, không tốn token thừa)
MAX_TAGS = 12


def _context(p: Project, ch: Chapter | None) -> str:
    if ch is not None:
        narr = " ".join(s.narration.strip() for s in ch.scenes if s.narration.strip())
        body = truncate(narr or ch.story, STORY_CHARS, "")
        return f"Tên truyện: {p.name}\nTóm tắt truyện: {p.synopsis.strip() or '(không có)'}\nTập/chương: {ch.name}\nNội dung chương:\n{body}"
    parts = []
    for c in p.chapters:
        first = next((s.narration.strip() for s in c.scenes if s.narration.strip()), c.story.strip()[:200])
        parts.append(f"- {c.name}: {truncate(first, 200)}")
    return (f"Tên truyện: {p.name}\nTóm tắt truyện: {p.synopsis.strip() or '(không có)'}\nVideo gồm {len(p.chapters)} chương:\n"
            + truncate("\n".join(parts), STORY_CHARS, ""))


def build_prompt(p: Project, ch: Chapter | None) -> str:
    lang = langs.english_name(p.narration_lang)
    return (
        f"Bạn là biên tập viên mạng xã hội cho kênh kể chuyện bằng video ngắn. Hãy viết nội dung đăng cho video sau, bằng {lang}.\n\n"
        f"{_context(p, ch)}\n\n"
        "Yêu cầu:\n"
        "- title: tiêu đề gây tò mò, tối đa 70 ký tự, không tiết lộ cái kết, không clickbait sai sự thật, không emoji thừa.\n"
        "- description: 2–4 câu giới thiệu nội dung, câu cuối mời xem tiếp/theo dõi; không chèn hashtag trong này; không bịa chi tiết ngoài nội dung đã cho.\n"
        f"- hashtags: 8–{MAX_TAGS} hashtag liên quan (thể loại, nhân vật, chủ đề), KHÔNG kèm dấu #, không khoảng trắng, không trùng; "
        "xen lẫn hashtag chung (vd. kể chuyện, video ngắn) và hashtag riêng của truyện.\n"
        "Chỉ trả JSON đúng schema."
    )


def normalize(data: dict) -> dict:
    return dict(title=truncate(str(data.get("title", "")).strip().strip('"“”'), 100),
                description=str(data.get("description", "")).strip(),
                hashtags=clean_tags(data.get("hashtags") or [], MAX_TAGS))


def generate(p: Project, ch: Chapter | None, log=print, profile=None) -> dict:
    """ch=None: mô tả cho video ghép cả dự án. Trả {title, description, hashtags}."""
    ok, why = llm.is_configured(profile)
    if not ok:
        raise RuntimeError(why)
    log(f"Đang viết mô tả/hashtag bằng {llm.describe(profile)}…")
    out = normalize(json.loads(llm.generate_json(build_prompt(p, ch), SCHEMA, log, profile)))
    if not out["title"]:
        raise ValueError("AI không trả tiêu đề.")
    return out

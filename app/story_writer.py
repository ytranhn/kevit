"""Sinh truyện từ bối cảnh bằng LLM: dàn ý các chương, rồi viết từng chương nối tiếp nhau. Không phụ thuộc giao diện."""
from __future__ import annotations

import json

from . import llm
from .models import Chapter, Project

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {"chapters": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["title", "summary"]}}},
    "required": ["chapters"],
}
STORY_SCHEMA = {"type": "object", "properties": {"story": {"type": "string"}}, "required": ["story"]}

MAX_CHAPTERS = 30
TAIL_CHARS = 1500            # đoạn cuối chương trước đưa vào ngữ cảnh để chương sau nối liền mạch


def _rules(genre: str) -> str:
    return ("Viết bằng TIẾNG VIỆT có dấu, văn kể chuyện ngôi thứ ba, giàu hình ảnh để dựng thành video (cảnh, hành động, nhân vật cụ thể), "
            "lời thoại đặt trong ngoặc kép “ ”. Không dùng dấu \" thẳng bên trong nội dung, không tiêu đề, không chú thích ngoài truyện."
            + (f" Thể loại/giọng văn: {genre.strip()}." if genre.strip() else ""))


def _context(p: Project, brief: str) -> str:
    return f"BỐI CẢNH: {brief.strip()}"


def outline(p: Project, brief: str, n: int, genre: str, previous: list[Chapter], log=print) -> list[dict]:
    """Dàn ý n chương tiếp theo. `previous`: các chương đã có (viết tiếp, không lặp lại)."""
    have = "\n".join(f"- {c.name}: {c.story.strip()[:300]}" for c in previous if c.story.strip())
    prompt = (f"Bạn là biên kịch truyện. {_rules(genre)}\n\n{_context(p, brief)}\n\n"
              + (f"CÁC CHƯƠNG ĐÃ CÓ (viết tiếp sau đó, không lặp lại):\n{have}\n\n" if have else "")
              + f"Hãy lập dàn ý đúng {n} chương tiếp theo, có mở đầu - phát triển - cao trào/kết thúc hợp lý và các nhân vật nhất quán. "
              "Mỗi chương gồm title (ngắn) và summary (3-5 câu: sự kiện chính, nhân vật, nơi chốn).")
    items = json.loads(llm.generate_json(prompt, OUTLINE_SCHEMA, log)).get("chapters", [])
    items = [i for i in items if str(i.get("summary", "")).strip()][:n]
    if not items:
        raise ValueError("AI không trả về dàn ý nào. Thử lại hoặc mô tả bối cảnh rõ hơn.")
    return items


def write_chapter(p: Project, brief: str, genre: str, plan: list[dict], k: int, words: int, prev_tail: str = "", log=print) -> str:
    """Viết chương thứ k (0-based) trong `plan`, khoảng `words` từ, nối tiếp `prev_tail`."""
    outline_txt = "\n".join(f"{i + 1}. {c['title']}: {c['summary']}" for i, c in enumerate(plan))
    prompt = (f"Bạn là nhà văn. {_rules(genre)}\n\n{_context(p, brief)}\n\nDÀN Ý CÁC CHƯƠNG:\n{outline_txt}\n\n"
              + (f"ĐOẠN CUỐI CHƯƠNG TRƯỚC (viết nối liền mạch, không nhắc lại):\n{prev_tail[-TAIL_CHARS:]}\n\n" if prev_tail.strip() else "")
              + f"Hãy viết TRỌN chương {k + 1} «{plan[k]['title']}» theo đúng dàn ý, khoảng {words} từ, "
              "chia đoạn ngắn rõ ràng. Giữ tên và tính cách nhân vật nhất quán; chỉ kể phần của chương này.")
    story = json.loads(llm.generate_json(prompt, STORY_SCHEMA, log)).get("story", "").strip()
    if not story:
        raise ValueError(f"AI trả về chương {k + 1} rỗng.")
    return story


def generate(p: Project, brief: str, n: int, words: int, genre: str, log=print, cancelled=lambda: False) -> list[Chapter]:
    """Viết n chương mới. Chương đầu dùng lại chương hiện tại nếu nó còn trống; mỗi chương xong được lưu ngay (hỏng giữa chừng vẫn giữ phần đã viết)."""
    n = max(1, min(MAX_CHAPTERS, n))
    reuse = next((c for c in p.chapters if not c.story.strip() and not c.scenes), None)
    previous = [c for c in p.chapters if c.story.strip()]
    log(f"Lập dàn ý {n} chương bằng {llm.describe()}...")
    plan = outline(p, brief, n, genre, previous, log)
    for i, c in enumerate(plan, 1):
        log(f"  {i}. {c['title']}")
    made: list[Chapter] = []
    tail = previous[-1].story if previous else ""
    for k, item in enumerate(plan):
        if cancelled():
            log("Đã dừng viết truyện.")
            break
        log(f"Đang viết chương {k + 1}/{len(plan)}: {item['title']}...")
        story = write_chapter(p, brief, genre, plan, k, words, tail, log)
        ch = reuse if (k == 0 and reuse) else p.new_chapter()
        ch.title, ch.story = item["title"].strip() or ch.title, story
        p.save()
        made.append(ch)
        tail = story
    return made

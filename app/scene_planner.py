"""LLM (Gemini hoặc Claude): tách chapter -> scene BÁM SÁT văn bản gốc (đúng thứ tự, phủ toàn chapter), định danh nhân vật."""
from __future__ import annotations

import json
import math
import re
import unicodedata

from . import langs, llm
from .models import Character, Scene, nfc

# Ràng buộc "trung thành với truyện": thuyết minh giữ >= KEEP số từ của đoạn gốc, nhưng một clip 8s chỉ đọc được
# ~NARR_MAX từ (3.3 từ/giây, tăng tốc tối đa ~1.3x). Vậy mỗi scene chỉ nên chứa <= SRC_MAX từ gốc, nhắm SRC_TARGET.
KEEP = 0.7
NARR_MAX = 34
SRC_TARGET = 40
SRC_MAX = int(NARR_MAX / KEEP)   # 48


def _wc(text: str) -> int:
    return len(text.split())


def _units(text: str, lang: str = "vi") -> int:
    return len(text.split()) if lang == "vi" else langs.count_units(text, lang)


def _lang_note(lang: str) -> str:
    """Đoạn dặn thêm khi thuyết minh KHÔNG phải tiếng Việt: ưu tiên hơn các quy tắc số từ tiếng Việt ở trên."""
    if lang == "vi":
        return ""
    hi = langs.budget(lang)
    return (f"\n\nQUY TẮC NGÔN NGỮ THUYẾT MINH (ƯU TIÊN HƠN mọi quy tắc về số từ và tỉ lệ giữ nội dung ở trên): trường \"narration\" phải là "
            f"BẢN DỊCH sang {langs.english_name(lang)} ({langs.name(lang)}) của nội dung các đoạn truyện tiếng Việt của scene đó, viết thành lời kể "
            f"ngôi thứ ba tự nhiên cho người bản ngữ nghe, giữ đúng thứ tự ý và sự kiện chính; tên người/địa danh viết theo cách quen thuộc "
            f"của {langs.english_name(lang)} (phiên âm tên Hán-Việt sang dạng thông dụng); thoại trong ngoặc kép. TỐI ĐA {hi} {langs.unit_label(lang)} "
            f"mỗi scene để đọc kịp trong 8 giây (được rút gọn ý phụ, không thêm chi tiết mới). KHÔNG viết thuyết minh bằng tiếng Việt. "
            f"Các trường khác (title bằng tiếng Việt, visual bằng tiếng Anh) giữ nguyên quy tắc cũ.")


def _bounds(src_words: int, lang: str = "vi") -> tuple[int, int]:
    """(tối thiểu, tối đa) số từ thuyết minh cho đoạn gốc có src_words từ."""
    if lang != "vi":                 # bản dịch: không so số từ với tiếng Việt, chỉ giới hạn để đọc kịp 8s
        return 0, langs.budget(lang)
    hi = NARR_MAX
    return min(math.ceil(KEEP * src_words), hi), hi

SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "first_segment": {"type": "integer"},
            "last_segment": {"type": "integer"},
            "narration": {"type": "string"},
            "visual": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "first_segment", "last_segment", "narration", "visual", "characters"],
    }}},
    "required": ["scenes"],
}

def _fold(x: str) -> str:
    """Bỏ dấu + hạ chữ thường để so khớp tên nhân vật bất kể cách đặt dấu."""
    x = unicodedata.normalize("NFD", x.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in x if unicodedata.category(c) != "Mn").lower()


def _roster_line(c: Character) -> str:
    """1 dòng ngắn cho LLM nhận diện nhân vật: vai trò + loại (cụm đầu của mô tả) + tên gọi khác. Không gửi mô tả dài."""
    kind = c.description.split(",")[0].strip()
    bits = [b for b in (c.role, kind) if b]
    alias = f" (tên khác: {', '.join(c.aliases)})" if c.aliases else ""
    return f"- {c.name}{alias}: {'; '.join(bits)}" if bits else f"- {c.name}{alias}"


def guess_characters(chars: list[Character], scene: Scene, limit: int = 3) -> list[str]:
    """Đoán nhân vật của scene theo văn bản (không dùng LLM): ưu tiên tên trong `visual`, không có thì xét đoạn gốc và
    thuyết minh. Xếp theo thứ tự xuất hiện. Dùng để vá scene bị mất nhân vật hoặc khi chưa có LLM."""
    def scan(text: str) -> list[str]:
        t, hits = _fold(text), []
        for c in chars:
            syl = _fold(c.name).split()
            keys = {_fold(c.name)} | {_fold(a) for a in c.aliases} | ({" ".join(syl[-2:])} if len(syl) >= 3 else set())
            pos = [m.start() for k in keys if k for m in [re.search(rf"(?<!\w){re.escape(k)}(?!\w)", t)] if m]
            if pos:
                hits.append((min(pos), c.name))
        return [n for _, n in sorted(hits)]
    return (scan(scene.visual) or scan(f"{scene.source_text} {scene.narration}"))[:limit]


def attach_characters(chars: list[Character], scenes: list[Scene], only: list[str] | None = None, limit: int = 3) -> list[tuple[Scene, list[str]]]:
    """Gắn nhân vật vào scene ĐÃ CÓ theo văn bản (không dùng LLM, không tốn credit): nhân vật (chỉ trong `only` nếu có) được nhắc trong
    hình ảnh / đoạn gốc / thuyết minh của scene mà chưa nằm trong danh sách nhân vật của scene, tối đa `limit` mỗi scene.
    Trả về [(scene, [tên vừa thêm])] cho các scene đã đổi. Dùng khi nhân vật được tạo SAU khi đã tách scene."""
    allow = {nfc(n) for n in only} if only is not None else None
    pool = [c for c in chars if allow is None or nfc(c.name) in allow]
    out = []
    for sc in scenes:
        have = {nfc(n) for n in sc.characters}
        if len(sc.characters) >= limit:
            continue
        found = guess_characters(pool, Scene(index=sc.index, visual=sc.visual), limit=limit)          # ưu tiên nhân vật hiện trong hình ảnh
        for n in guess_characters(pool, Scene(index=sc.index, source_text=sc.source_text, narration=sc.narration), limit=limit):
            if n not in found:
                found.append(n)
        new = [n for n in found if nfc(n) not in have][:limit - len(sc.characters)]
        if new:
            sc.characters = sc.characters + new
            out.append((sc, new))
    return out


def relevant_chars(story: str, chars: list[Character]) -> list[Character]:
    """Chỉ giữ nhân vật được nhắc trong chương (tên, tên gọi khác, hoặc 2 âm cuối của tên dài như "Tinh Quân").
    Danh sách 50 nhân vật kèm mô tả là phần tốn token nhất ngoài chính chương truyện. Không khớp ai thì gửi hết."""
    text = _fold(story)
    keep = []
    for c in chars:
        keys = {_fold(c.name)} | {_fold(a) for a in c.aliases}
        syl = _fold(c.name).split()
        if len(syl) >= 3:
            keys.add(" ".join(syl[-2:]))
        if any(k and k in text for k in keys):
            keep.append(c)
    return keep or chars


_SENT = re.compile(r'(?<=[.!?…”"»])\s+')


def _clauses(sentence: str) -> list[str]:
    """Câu quá dài (> ~40 từ) thì tách thêm theo dấu phẩy/chấm phẩy để mỗi đoạn đánh số đủ nhỏ."""
    if _wc(sentence) <= 40:
        return [sentence]
    out, buf = [], ""
    for piece in re.split(r"(?<=[,;:，；])\s+", sentence):
        buf = f"{buf} {piece}".strip() if buf else piece
        if _wc(buf) >= 18:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def split_segments(story: str) -> list[str]:
    """Chia chapter thành các đoạn đánh số (theo dòng, đoạn dài thì tách theo câu, gộp câu quá ngắn).
    LLM chỉ cần trả số đoạn đầu/cuối của mỗi scene -> đầu ra nhỏ, và đoạn gốc luôn là nguyên văn."""
    segs: list[str] = []
    for line in story.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in _SENT.split(line) if x.strip()] if len(line) > 200 else [line]
        parts = [y for x in parts for y in _clauses(x)]
        buf = ""
        for part in parts:
            buf = f"{buf} {part}".strip() if buf else part
            if len(buf) >= 70:
                segs.append(buf)
                buf = ""
        if buf:
            segs.append(buf)
    return segs


def _fix_ranges(raw: list[tuple[int, int]], total: int) -> tuple[list[tuple[int, int]], bool]:
    """Ép các khoảng liền mạch, tăng dần, phủ 1..total (không chồng lấn, không sót). Trả (khoảng, có_phải_sửa)."""
    n, ends, prev = len(raw), [], 0
    for k, (_, b) in enumerate(raw):
        b = min(max(int(b), prev + 1), total - (n - k - 1))
        ends.append(total if k == n - 1 else b)
        prev = ends[-1]
    fixed, start = [], 1
    for e in ends:
        fixed.append((start, e))
        start = e + 1
    return fixed, any((int(a), int(b)) != f for (a, b), f in zip(raw, fixed))


def _split_range(segs: list[str], a: int, b: int) -> list[tuple[int, int]]:
    """Khoảng đoạn [a..b] có quá nhiều từ gốc (> SRC_MAX) thì cắt tại ranh giới đoạn thành các phần ~SRC_TARGET từ."""
    w = [_wc(segs[i - 1]) for i in range(a, b + 1)]
    total = sum(w)
    if total <= SRC_MAX or a == b:
        return [(a, b)]
    out, start, acc = [], a, 0
    for off, x in enumerate(w):
        i = a + off
        if acc and (acc + x > SRC_MAX or acc >= SRC_TARGET):    # đóng phần hiện tại trước đoạn i
            out.append((start, i - 1))
            start, acc = i, 0
        acc += x
    out.append((start, b))
    return out


def _norm(x: str) -> str:
    return re.sub(r"\s+", " ", x).strip().lower()


FIX_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {"k": {"type": "integer"}, "narration": {"type": "string"}, "visual": {"type": "string"}},
        "required": ["k", "narration", "visual"],
    }}},
    "required": ["scenes"],
}


def _fix_narrations(todo: list[tuple[int, Scene, bool]], log, lang: str = "vi") -> dict[int, dict]:
    """Một lượt LLM viết lại thuyết minh cho các scene không đạt (quá ngắn so với truyện, quá dài cho 8s, hoặc vừa bị tách).
    todo = [(k, scene, cần_visual)]. Trả {k: {narration, visual}}."""
    parts = []
    for k, sc, need_visual in todo:
        w = _wc(sc.source_text)
        lo, hi = _bounds(w, lang)
        parts.append(f"[Scene {k}] ({w} từ gốc) Thuyết minh phải dài {lo}–{hi} {langs.unit_label(lang)}."
                     + (" Viết thêm visual (tiếng Anh) cho đoạn này." if need_visual else " visual: để chuỗi rỗng.")
                     + f"\nTiêu đề: {sc.title}\nĐoạn truyện gốc:\n{sc.source_text}")
    prompt = f"""Viết (lại) thuyết minh của người dẫn truyện cho các scene dưới đây.

Quy tắc cho "narration" của mỗi scene:
- Viết TỪ đúng đoạn truyện gốc của scene đó, giữ nguyên tên riêng, đúng thứ tự ý, giọng kể ngôi thứ ba liền mạch,
  thoại trong ngoặc kép kèm "X nói".
- GIỮ LẠI ít nhất {int(KEEP * 100)}% nội dung/chi tiết của đoạn gốc: chỉ bỏ từ thừa, lặp, trạng từ; KHÔNG bỏ sự kiện hay
  chi tiết quan trọng. Số từ nằm trong khoảng yêu cầu của từng scene. Không thêm ý mới.
- Nếu scene yêu cầu "visual": mô tả bối cảnh, hành động, góc máy bằng TIẾNG ANH, không chứa lời thoại, chỉ gồm những gì
  có trong đoạn gốc, dùng tên không dấu của nhân vật.

{chr(10).join(parts)}{_lang_note(lang)}"""
    data = json.loads(llm.generate_json(prompt, FIX_SCHEMA, log))
    return {int(x["k"]): x for x in data["scenes"]}


def coverage_note(scenes: list[Scene]) -> str:
    """Tỉ lệ nội dung gốc còn lại trong thuyết minh (theo số từ), tổng và scene thấp nhất."""
    pairs = [(_wc(s.narration), _wc(s.source_text)) for s in scenes if s.source_text.strip()]
    if not pairs:
        return ""
    tot_n, tot_s = sum(n for n, _ in pairs), sum(w for _, w in pairs)
    low = min(pairs, key=lambda x: x[0] / max(x[1], 1))
    return (f"Thuyết minh giữ khoảng {min(100, tot_n * 100 // max(tot_s, 1))}% số từ của truyện gốc "
            f"(scene thấp nhất {min(100, low[0] * 100 // max(low[1], 1))}%; mục tiêu ≥{int(KEEP * 100)}%).")


def plan_scenes(story: str, chars: list[Character], max_scenes: int = 16,
                synopsis: str = "", log=print, lang: str = "vi") -> tuple[list[Scene], list[str]]:
    """Trả về (scenes, ghi_chú/cảnh_báo). `lang`: ngôn ngữ thuyết minh (vi = giữ ≥70% nội dung; ngôn ngữ khác = bản dịch ngắn gọn). Số scene tính theo độ dài chương để thuyết minh giữ >= 70% nội dung gốc
    trong ~8 giây/scene; `max_scenes` chỉ còn dùng để cảnh báo chi phí, không cắt bớt nội dung."""
    segs = split_segments(story)
    total = len(segs)
    if not total:
        raise ValueError("Chương chưa có nội dung.")
    words = len(story.split())
    n = max(1, min(math.ceil(words / SRC_TARGET), total))
    notes = [f"Chapter ~{words} từ ({total} đoạn) -> khoảng {n} scene (~{SRC_TARGET} từ gốc/scene, "
             + (f"thuyết minh ≤{NARR_MAX} từ, giữ ≥{int(KEEP * 100)}% nội dung)." if lang == "vi" else
                f"thuyết minh {langs.name(lang)} ≤{langs.budget(lang)} {langs.unit_label(lang)}/scene)." )]
    if n > max_scenes:
        notes.append(f"Lưu ý: {n} scene vượt mức {max_scenes} đặt trong Cài đặt dự án -> tốn nhiều credit Flow hơn. "
                     f"Muốn ít scene hơn thì chia chương ngắn lại (nếu rút gọn thêm, thuyết minh sẽ bỏ >30% truyện).")
    if n == total and words / total > SRC_MAX:
        notes.append("Cảnh báo: có đoạn gốc rất dài không tách được thêm; thuyết minh của đoạn đó có thể bị rút gọn nhiều.")

    in_story = relevant_chars(story, chars)
    if len(in_story) < len(chars):
        notes.append(f"Chỉ gửi {len(in_story)}/{len(chars)} nhân vật có xuất hiện trong chương (tiết kiệm token).")
    roster = "\n".join(_roster_line(c) for c in in_story) or "(chưa có nhân vật)"
    numbered = "\n".join(f"[{i}] ({_wc(seg)} từ) {seg}" for i, seg in enumerate(segs, 1))
    prompt = f"""Bạn là đạo diễn chuyển thể một chapter truyện thành chuỗi clip 8 giây có người dẫn truyện.

Chapter dưới đây đã được chia sẵn thành {total} đoạn đánh số [1]..[{total}] (kèm số từ của từng đoạn).

NHIỆM VỤ: chia thành KHOẢNG {n} scene (không ít hơn {n}), theo ĐÚNG THỨ TỰ, nối tiếp nhau, KHÔNG chồng lấn,
phủ từ đoạn [1] đến đoạn [{total}]. Mỗi scene gồm các đoạn có TỔNG khoảng {SRC_TARGET} từ gốc, TUYỆT ĐỐI không quá {SRC_MAX} từ;
ưu tiên cắt ở chỗ đổi cảnh, đổi người nói hoặc đổi hành động.

Mỗi scene gồm:
- "first_segment", "last_segment": số đoạn đầu và đoạn cuối (gồm cả hai đầu) mà scene này diễn tả.
  KHÔNG chép lại nội dung truyện.
- "narration": lời người dẫn truyện, VIẾT TỪ các đoạn của chính scene đó: giữ nguyên tên riêng, đúng thứ tự ý,
  giọng kể ngôi thứ ba liền mạch, thoại để trong ngoặc kép kèm "X nói". PHẢI GIỮ LẠI ít nhất {int(KEEP * 100)}% số từ/chi tiết của
  các đoạn gốc (chỉ bỏ từ thừa, lặp, trạng từ; không bỏ sự kiện chính), nhưng tối đa {NARR_MAX} từ. Không thêm chi tiết mới,
  không lấy ý từ scene khác.
- "visual": mô tả bối cảnh, hành động, góc máy bằng TIẾNG ANH, chỉ gồm những gì xảy ra trong các đoạn đó.
  Không chứa lời thoại. Dùng tên không dấu của nhân vật.
- "characters": CHỈ dùng đúng tên chuẩn trong danh sách nhân vật dưới đây (map tên gọi khác, đại từ, chức danh
  về tên chuẩn), chỉ gồm nhân vật XUẤT HIỆN trong các đoạn đó, tối đa 3, ưu tiên người quan trọng nhất của cảnh.
- "title": tiêu đề ngắn (tiếng Việt) của scene.

Danh sách nhân vật:
{roster}

Bối cảnh chung của bộ truyện (chỉ để hiểu ngữ cảnh, KHÔNG đưa vào scene nếu chapter không nhắc):
{synopsis or '(không có)'}

CHAPTER (đã đánh số đoạn):
{numbered}{_lang_note(lang)}"""
    data = json.loads(llm.generate_json(prompt, SCHEMA, log))
    items = data["scenes"]
    ranges, repaired = _fix_ranges([(s["first_segment"], s["last_segment"]) for s in items], total)
    if repaired:
        notes.append("Ghi chú: khoảng đoạn model trả về chưa liền mạch, tool đã tự chỉnh để phủ liền mạch toàn chương.")
    canon = {nfc(c.name): c.name for c in chars}
    out: list[Scene] = []
    split_idx: set[int] = set()           # scene vừa bị tool cắt đôi: chưa có thuyết minh/visual riêng
    for s, (a, b) in zip(items, ranges):
        names = [canon[nfc(x)] for x in s["characters"] if nfc(x) in canon][:3]
        parts = _split_range(segs, a, b)
        for j, (pa, pb) in enumerate(parts, 1):
            title = s["title"] if len(parts) == 1 else f"{s['title']} ({j}/{len(parts)})"
            out.append(Scene(index=len(out) + 1, title=title, visual=s["visual"], source_text="\n".join(segs[pa - 1:pb]),
                             narration=s["narration"].strip() if len(parts) == 1 else "", characters=names, duration=8))
            if len(parts) > 1:
                split_idx.add(len(out))
    if split_idx:
        notes.append(f"Tool tách thêm {len(split_idx)} scene vì khoảng đoạn model chọn quá dài cho 8 giây (> {SRC_MAX} từ gốc).")

    todo = []
    for sc in out:
        lo, hi = _bounds(_wc(sc.source_text), lang)
        nw = _units(sc.narration, lang)
        if sc.index in split_idx or nw < lo or nw > hi + (4 if lang == "vi" else max(4, hi // 8)):
            todo.append((sc.index, sc, sc.index in split_idx))
    if todo:
        log(f"{len(todo)} scene chưa đạt (thuyết minh bỏ quá {100 - int(KEEP * 100)}% truyện hoặc dài quá 8s) -> nhờ model viết lại...")
        try:
            fixed = _fix_narrations(todo, log, lang)
            for k, sc, need_visual in todo:
                f = fixed.get(k)
                if f and f["narration"].strip():
                    sc.narration = f["narration"].strip()
                    if need_visual and f.get("visual", "").strip():
                        sc.visual = f["visual"].strip()
            notes.append(f"Đã viết lại thuyết minh của {len(todo)} scene để giữ đủ nội dung gốc.")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Không viết lại được {len(todo)} scene chưa đạt ({str(e)[:120]}). Dùng nút 'Căn lại thuyết minh' để thử lại.")
    for sc in out:
        if not sc.narration:
            sc.narration = sc.source_text.strip()
    for sc in out:
        lo, hi = _bounds(_wc(sc.source_text), lang)
        nw = _units(sc.narration, lang)
        if nw < lo:
            notes.append(f"Scene {sc.index}: thuyết minh {nw}/{_wc(sc.source_text)} từ gốc, thấp hơn {int(KEEP * 100)}%.")
        elif nw > hi + (4 if lang == "vi" else max(4, hi // 8)):
            notes.append(f"Scene {sc.index}: thuyết minh {nw} {langs.unit_label(lang)}, dài hơn mức {hi} (đọc nhanh hoặc clip dài hơn 8s).")
    if lang == "vi":
        notes.append(coverage_note(out))
    return out, notes


REALIGN_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "first_segment": {"type": "integer"},
                       "last_segment": {"type": "integer"}, "narration": {"type": "string"}},
        "required": ["index", "first_segment", "last_segment", "narration"],
    }}},
    "required": ["scenes"],
}


def realign_narration(story: str, scenes: list[Scene], log=print, lang: str = "vi") -> tuple[dict[int, tuple[str, str]], list[str]]:
    """Giữ nguyên danh sách scene (và clip đã gen), chỉ gán lại đoạn truyện gốc + viết lại thuyết minh bám sát truyện.
    Trả về ({index: (source_text, narration)}, ghi_chú)."""
    segs = split_segments(story)
    total = len(segs)
    if not total:
        raise ValueError("Chương chưa có nội dung.")
    listing = "\n".join(f"- Scene {s.index} | {s.title} | hình ảnh đã quay: {s.visual}" for s in scenes)
    numbered = "\n".join(f"[{i}] ({_wc(seg)} từ) {seg}" for i, seg in enumerate(segs, 1))
    prompt = f"""Chapter truyện (đã chia thành {total} đoạn đánh số) đã được quay thành {len(scenes)} scene theo đúng thứ tự
dưới đây (mô tả hình ảnh là những gì clip đã quay, KHÔNG được đổi). Hãy gán cho MỖI scene các đoạn truyện tương
ứng và viết lại thuyết minh.

Quy tắc:
- Các scene theo đúng thứ tự trong truyện, các khoảng đoạn nối tiếp nhau, không chồng lấn, phủ từ [1] đến [{total}].
- "first_segment", "last_segment": số đoạn đầu và cuối (gồm cả hai đầu) khớp với hình ảnh của scene đó.
  KHÔNG chép lại nội dung truyện.
- "narration": lời người dẫn truyện viết TỪ các đoạn của scene đó, giữ nguyên tên riêng và thứ tự ý, giọng kể
  ngôi thứ ba, thoại trong ngoặc kép kèm "X nói"; chỉ rút gọn, không thêm chi tiết, không lấy ý scene khác.
  PHẢI GIỮ LẠI ít nhất {int(KEEP * 100)}% số từ/chi tiết của các đoạn gốc (chỉ bỏ từ thừa, lặp), tối đa {NARR_MAX} từ.

Các scene:
{listing}

CHAPTER (đã đánh số đoạn):
{numbered}{_lang_note(lang)}"""
    data = json.loads(llm.generate_json(prompt, REALIGN_SCHEMA, log))
    got = {int(x["index"]): x for x in data["scenes"]}
    notes: list[str] = []
    order = [s for s in scenes if s.index in got]
    for s in scenes:
        if s.index not in got:
            notes.append(f"Scene {s.index}: model không trả kết quả, giữ nguyên thuyết minh cũ.")
    ranges, repaired = _fix_ranges([(got[s.index]["first_segment"], got[s.index]["last_segment"]) for s in order], total) \
        if order else ([], False)
    if repaired:
        notes.append("Ghi chú: khoảng đoạn model trả về chưa liền mạch, tool đã tự chỉnh để phủ liền mạch toàn chương.")
    res: dict[int, tuple[str, str]] = {}
    for s, (a, b) in zip(order, ranges):
        narr = got[s.index]["narration"].strip()
        src = "\n".join(segs[a - 1:b])
        res[s.index] = (src, narr)
        lo, hi = _bounds(_wc(src), lang)
        nu = _units(narr, lang)
        if nu < lo:
            notes.append(f"Scene {s.index}: thuyết minh {_wc(narr)}/{_wc(src)} từ gốc, thấp hơn {int(KEEP * 100)}%.")
        elif nu > hi + (4 if lang == "vi" else max(4, hi // 8)):
            notes.append(f"Scene {s.index}: thuyết minh {nu} {langs.unit_label(lang)}, dài hơn mức {hi}.")
    probe = [Scene(index=i, title="", visual="", source_text=v[0], narration=v[1]) for i, v in res.items()]
    if probe and lang == "vi":
        notes.append(coverage_note(probe))
    return res, notes


MERGE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "narration": {"type": "string"}, "visual": {"type": "string"},
                   "characters": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "narration", "visual", "characters"],
}


def merge_scenes_llm(group: list[Scene], chars: list[Character], log=print, lang: str = "vi") -> dict:
    """Gộp thông minh nhiều scene liền kề thành 1: viết lại thuyết minh ngắn gọn đủ ý theo thứ tự, mô tả hình ảnh cho 1 clip
    duy nhất (tối đa 2-3 nhịp), chọn tối đa 3 nhân vật quan trọng nhất."""
    total_words = sum(_units(s.narration, lang) for s in group)
    budget = max(18, min(34, math.ceil(total_words * 0.85))) if lang == "vi" else max(10, min(langs.budget(lang), math.ceil(total_words * 0.85)))
    parts = "\n\n".join(
        f"[Scene {i}] {s.title}\nĐoạn truyện: {s.source_text or '(không có)'}\nThuyết minh: {s.narration}\n"
        f"Hình ảnh: {s.visual}\nNhân vật: {', '.join(s.characters) or '(không có)'}" for i, s in enumerate(group, 1))
    names = [c.name for c in chars if any(nfc(c.name) == nfc(n) for s in group for n in s.characters)]
    prompt = f"""Gộp {len(group)} scene liền kề của một video kể chuyện thành MỘT scene duy nhất (một clip ngắn).

Yêu cầu:
- "narration": một đoạn thuyết minh liền mạch của người dẫn truyện, GIỮ ĐÚNG THỨ TỰ sự kiện và tên riêng, bao quát ý chính của cả
  {len(group)} scene, không thêm chi tiết mới. Tối đa {budget} {langs.unit_label(lang)}.{' Viết bằng ' + langs.english_name(lang) + ' (cùng ngôn ngữ với thuyết minh các scene gốc).' if lang != 'vi' else ''}
- "visual": mô tả bằng TIẾNG ANH cho một clip duy nhất, thể hiện nhịp hành động chính theo thứ tự (tối đa 3 nhịp), không chứa
  lời thoại, dùng tên không dấu của nhân vật.
- "characters": tối đa 3 tên, chỉ chọn trong danh sách: {', '.join(names) or '(rỗng)'}; ưu tiên người xuất hiện nhiều và quan trọng nhất.
- "title": tiêu đề ngắn tiếng Việt cho scene gộp.

{parts}"""
    data = json.loads(llm.generate_json(prompt, MERGE_SCHEMA, log))
    canon = {nfc(c.name): c.name for c in chars}
    data["characters"] = [canon[nfc(n)] for n in data.get("characters", []) if nfc(n) in canon][:3]
    return data


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {"k": {"type": "integer"}, "narration": {"type": "string"}},
        "required": ["k", "narration"],
    }}},
    "required": ["scenes"],
}
TRANSLATE_BATCH = 24


def translate_narrations(scenes: list[Scene], lang: str, log=print) -> dict[int, str]:
    """Viết lại thuyết minh của các scene bằng ngôn ngữ `lang`, DỊCH từ đoạn truyện gốc tiếng Việt của từng scene (không dịch nối từ bản
    thuyết minh cũ để khỏi sai lệch dần), đúng ngân sách đọc kịp 8 giây. Trả {scene.index: thuyết minh mới}. Scene không có đoạn gốc thì dịch từ
    thuyết minh hiện có. Gọi theo lô nhỏ để tiết kiệm token và không bị cắt đầu ra."""
    hi = NARR_MAX if lang == "vi" else langs.budget(lang)
    unit = "từ" if lang == "vi" else langs.unit_label(lang)
    target = f"{langs.english_name(lang)} ({langs.name(lang)})"
    out: dict[int, str] = {}
    todo = [s for s in scenes if (s.source_text or s.narration).strip()]
    for i in range(0, len(todo), TRANSLATE_BATCH):
        batch = todo[i:i + TRANSLATE_BATCH]
        parts = []
        for s in batch:
            src = s.source_text.strip() or s.narration.strip()
            note = "" if s.source_text.strip() else " (không có đoạn gốc: dịch từ thuyết minh hiện có)"
            parts.append(f"[Scene {s.index}]{note}\nĐoạn truyện gốc (tiếng Việt):\n{src}")
        extra = ("Giữ tối thiểu 70% ý của đoạn gốc." if lang == "vi" else "Được rút gọn ý phụ, giữ đủ sự kiện chính.")
        prompt = f"""Viết thuyết minh cho người dẫn truyện của các scene dưới đây bằng {target}.

Quy tắc cho "narration" của mỗi scene:
- Viết thành lời kể ngôi thứ ba tự nhiên cho người bản ngữ NGHE, đúng thứ tự ý và sự kiện; thoại trong ngoặc kép.
- Tên người/địa danh/thuật ngữ viết theo cách quen thuộc của ngôn ngữ đích (phiên âm tên Hán-Việt sang dạng thông dụng, nhất quán giữa các scene).
- TỐI ĐA {hi} {unit} mỗi scene để đọc kịp trong 8 giây. {extra} Không thêm chi tiết mới, không lấy ý từ scene khác.
- Chỉ trả về bản thuyết minh, không giải thích.

{chr(10).join(parts)}"""
        data = json.loads(llm.generate_json(prompt, TRANSLATE_SCHEMA, log))
        for x in data["scenes"]:
            if str(x.get("narration", "")).strip():
                out[int(x["k"])] = x["narration"].strip()
    return out

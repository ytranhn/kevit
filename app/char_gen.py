"""Tạo nhân vật phù hợp với dự án: (1) AI đọc truyện đề xuất danh sách nhân vật kèm mô tả ngoại hình (prompt tiếng Anh) theo phong cách
của dự án; (2) tuỳ chọn tạo ảnh tham chiếu bằng Gemini (cần Gemini API key)."""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import llm, models, settings
from .models import Character, Project, nfc, safe_dirname

SINGLE_MAX = 24000            # truyện ngắn hơn mức này: một lượt gọi AI; dài hơn: đọc từng đoạn rồi gộp (không bỏ sót nhân vật ở giữa truyện)
CHUNK = 7000
MAX_CHUNKS = 10
MIN_MENTIONS = 2              # nhân vật nhắc dưới số lần này trong toàn truyện bị loại (chống AI bịa ra tên)

CHAR_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "role": {"type": "string"},
        "appearance_en": {"type": "string"},
        "description_vi": {"type": "string"},
        "kind": {"type": "string"},
        "evidence": {"type": "string"},
        "gender": {"type": "string"},
    },
    "required": ["name", "aliases", "role", "appearance_en", "description_vi", "kind", "evidence", "gender"],
}
KINDS = {"person", "creature", "deity"}      # chỉ giữ thực thể có thể xuất hiện trong ảnh như một nhân vật
SCHEMA = {"type": "object", "properties": {"characters": {"type": "array", "items": CHAR_ITEM}}, "required": ["characters"]}


def _fold(x: str) -> str:
    from .scene_planner import _fold as f
    return f(x)


def full_text(p: Project) -> str:
    return "\n\n".join(c.story.strip() for c in p.chapters if c.story.strip())


def story_digest(p: Project, budget: int = SINGLE_MAX) -> str:
    """Gom bối cảnh + truyện các chương (cắt đều theo ngân sách ký tự nếu quá dài). Dùng cho đường một lượt."""
    chs = [c for c in p.chapters if c.story.strip()]
    per = max(1500, budget // max(len(chs), 1))
    parts = [f"BỐI CẢNH CHUNG: {p.synopsis.strip()}"] if p.synopsis.strip() else []
    for c in chs:
        t = c.story.strip()
        if len(t) > per:                                  # giữ đầu và cuối chương: nhân vật thường xuất hiện ở cả hai nơi
            t = t[: per * 2 // 3] + "\n[...lược bớt...]\n" + t[-per // 3:]
        parts.append(f"=== {c.name} ===\n{t}")
    return "\n\n".join(parts)


_WORD = re.compile(r"[\w'’-]+")
_SENT_END = ".!?…\n\"“”"


def _cap_runs(text: str):
    """Các cụm 1-4 từ liên tiếp (cách nhau đúng một dấu cách) đều viết hoa chữ cái đầu: (cụm, có_ở_đầu_câu)."""
    words = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(text)]
    i = 0
    while i < len(words):
        s0, e0, w = words[i]
        if not (w[0].isupper() and any(c.isalpha() for c in w)):
            i += 1
            continue
        j = i
        while (j + 1 < len(words) and j - i < 3 and words[j + 1][0] == words[j][1] + 1 and text[words[j][1]] == " "
               and words[j + 1][2][0].isupper()):
            j += 1
        prev = text[:s0].rstrip(" ")
        yield " ".join(x[2] for x in words[i:j + 1]), (not prev or prev[-1] in _SENT_END)
        i = j + 1


def name_hints(text: str, k: int = 40) -> list[tuple[str, int]]:
    """Các cụm từ viết hoa lặp lại (thường là tên người/địa danh) kèm số lần: mỏ neo để AI không bỏ sót hay bịa tên.
    Chữ viết hoa ĐẦU CÂU không phải bằng chứng (mọi câu đều bắt đầu bằng chữ hoa), nên cụm đầu câu chỉ được tính khi cùng cụm đó
    (hoặc phần sau chữ đầu câu của nó) đã xuất hiện giữa câu."""
    mid: dict[str, int] = {}
    starts: list[str] = []
    for run, at_start in _cap_runs(text):
        if at_start:
            starts.append(run)
        else:
            mid[run] = mid.get(run, 0) + 1
    count = dict(mid)
    for run in starts:
        rest = run.split(" ", 1)[1] if " " in run else ""
        if run in mid:
            count[run] += 1
        elif rest and rest in mid:
            count[rest] += 1
    return sorted(((n, c) for n, c in count.items() if c >= 3), key=lambda x: -x[1])[:k]


_MALE_TITLES = {"ong", "anh", "cau", "chang", "ngai", "chu", "bac", "thay", "cha", "bo", "vua", "lao", "huynh", "hoang tu", "thieu gia", "cong tu", "su huynh", "su phu", "su thuc", "hoang de"}
_FEMALE_TITLES = {"ba", "co", "chi", "nang", "me", "nu", "su ty", "su muoi", "cong chua", "thieu nu", "tieu thu", "di", "thim", "hoang hau", "ni co", "ma ma"}
_MALE_ALONE = {"chua", "ngai", "vua", "cha", "thien chua", "duc chua", "chua giesu", "dang cuu the"}
_MALE_PRON = {"han", "y", "ong", "anh", "chang", "ngai", "lao"}
_FEMALE_PRON = {"nang", "ba", "co", "chi", "nu"}


def detect_gender(text: str, names: list[str]) -> tuple[int, int]:
    """(điểm nam, điểm nữ) từ truyện: danh xưng đứng ngay trước tên (Thầy Hạc, Cô Hến, Bà Lụa) tính mạnh; đại từ/danh xưng ngay sau tên tính nhẹ."""
    f = " ".join(_fold(text).split())
    m_score = f_score = 0
    for nm in names:
        key = _fold(nm).strip()
        if len(key) < 2:
            continue
        for mt in re.finditer(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", f):
            before = f[max(0, mt.start() - 14):mt.start()].split()
            if before:
                for t in (" ".join(before[-2:]), before[-1]):
                    if t in _MALE_TITLES:
                        m_score += 4
                        break
                    if t in _FEMALE_TITLES:
                        f_score += 4
                        break
            if key in _MALE_ALONE:                                  # tên là danh xưng nam đứng một mình: Chúa, Ngài, Vua, Cha
                m_score += 4
            first = key.split()[0]                                  # tên tự mang danh xưng: "Thầy Hạc", "Cô Hến", "Bà Lụa"
            if " " in key and first in _MALE_TITLES:
                m_score += 4
            elif " " in key and first in _FEMALE_TITLES:
                f_score += 4
            after = re.split(r"[.!?;:]", f[mt.end():mt.end() + 40])[0].replace(",", " ").split()[:5]   # chỉ trong cùng câu
            for w in after:
                if w in _MALE_PRON:
                    m_score += 1
                elif w in _FEMALE_PRON:
                    f_score += 1
    return m_score, f_score


_SWAP = {"woman": "man", "women": "men", "female": "male", "girl": "boy", "lady": "gentleman", "she": "he", "her": "his", "hers": "his", "herself": "himself", "feminine": "masculine"}
_SWAP_BACK = {v: k for k, v in _SWAP.items() if v not in ("his",)} | {"his": "her", "him": "her"}
_GENDER_WORDS = re.compile(r"\b(man|men|woman|women|male|female|boy|girl|lady|gentleman|he|she|his|her|him)\b", re.I)


def apply_gender(appearance: str, gender: str) -> str:
    """Bảo đảm mô tả ngoại hình nói đúng giới tính: thiếu thì thêm "A man,"/"A woman,"; sai (vd. viết woman cho nhân vật nam) thì đổi từ."""
    text = appearance.strip()
    if gender not in ("male", "female") or not text:
        return text
    want_male = gender == "male"
    noun = "man" if want_male else "woman"
    found = [m.group(0).lower() for m in _GENDER_WORDS.finditer(text)]
    male_w = {"man", "men", "male", "boy", "gentleman", "he", "his", "him"}
    female_w = {"woman", "women", "female", "girl", "lady", "she", "her"}
    wrong = female_w if want_male else male_w
    if any(w in wrong for w in found):
        mapping = _SWAP if want_male else _SWAP_BACK
        def sub(m):
            w = m.group(0)
            r = mapping.get(w.lower(), w)
            return r.capitalize() if w[:1].isupper() else r
        text = _GENDER_WORDS.sub(sub, text)
    elif not found:
        text = f"A {noun}, " + (text[0].lower() + text[1:] if text[:1].isupper() and not text[:2].isupper() else text)
    return text


def decide_gender(llm_gender: str, text: str, names: list[str]) -> str:
    """Giới tính cuối cùng: bằng chứng từ truyện (danh xưng/đại từ) thắng AI khi đủ mạnh, nếu không thì theo AI (hoặc 'unknown')."""
    m, f = detect_gender(text, names)
    llm_g = (llm_gender or "unknown").strip().lower()
    if m >= f + 3:
        return "male"
    if f >= m + 3:
        return "female"
    return llm_g if llm_g in ("male", "female") else "unknown"


def count_mentions(text_folded: str, names: list[str]) -> int:
    n = 0
    for nm in names:
        f = _fold(nm).strip()
        if len(f) >= 2:
            n += len(re.findall(rf"(?<![a-z0-9]){re.escape(f)}(?![a-z0-9])", text_folded))
    return n


def _instructions(p: Project, have: str, max_n: int) -> str:
    return f"""Bạn giúp đạo diễn dựng video kể chuyện: đọc truyện rồi đề xuất danh sách nhân vật cần có ảnh tham chiếu.

Yêu cầu:
- Chọn tối đa {max_n} nhân vật xuất hiện đáng kể (ưu tiên nhân vật chính, người hay nói, người xuất hiện lặp lại). Bỏ vai quần chúng và địa danh/tổ chức.
- "kind": phân loại thực thể, CHỈ một trong: "person" (người), "creature" (yêu thú/linh thú/quái vật có hành động), "deity" (thần, tiên, Chúa...),
  "place" (địa danh), "organization" (môn phái, triều đình, tổ chức), "object" (vật, thảo dược, pháp bảo), "title" (chức danh chung chung, không chỉ một người cụ thể), "other".
  CHỈ những mục có kind person/creature/deity mới là nhân vật. Địa danh, tổ chức, vật phẩm, chức danh chung KHÔNG phải nhân vật: đừng liệt kê chúng.
- "gender": giới tính nhân vật, CHỈ một trong "male", "female", "unknown", căn cứ đại từ và cách gọi trong truyện (ông/anh/cậu/ngài/cha/thầy... là nam; bà/cô/chị/nàng/mẹ/sư tỷ... là nữ). Chúa, Ngài, Cha (Thiên Chúa, Chúa Giêsu) là nam.
  "appearance_en" PHẢI mở đầu bằng "A man" hoặc "A woman" (hoặc "A boy"/"A girl" nếu là trẻ em) khớp với "gender"; nếu gender là "unknown" thì mô tả trung tính nhưng đừng đoán bừa.
- "evidence": chép NGUYÊN VĂN một câu hoặc cụm từ ngắn (tối đa 20 từ) trong truyện cho thấy nhân vật này hành động hoặc nói.
- Nếu truyện không còn nhân vật mới đáng kể nào ngoài danh sách đã có, trả về mảng rỗng. Đừng cố đủ số lượng.
- NHÂN VẬT KHÔNG CÓ TÊN RIÊNG vẫn được chọn nếu quan trọng và lặp lại, đặt tên theo cách truyện gọi (vd. "Người đàn ông", "Chúa", "Cô gái"); đừng bịa tên mới.
- KHÔNG đề xuất nhân vật đã có: {have}.
- "name": tên chuẩn đúng như trong truyện (một cách gọi duy nhất). "aliases": các cách gọi khác, chức danh, đại từ riêng, biệt danh CÓ trong truyện.
- "role": vai trò ngắn gọn bằng tiếng Việt (vd. "Nhân vật chính", "Sư tỷ", "Phản diện").
- "appearance_en": mô tả NGOẠI HÌNH bằng TIẾNG ANH, 25-45 từ, dùng làm prompt ảnh: giới tính, tuổi, vóc dáng, tóc, gương mặt, trang phục, đặc điểm nổi bật. Phù hợp thể loại/bối cảnh và phong cách hình ảnh "{p.style or 'cinematic'}". Ưu tiên chi tiết truyện đã tả; truyện không tả thì suy ra hợp lý theo bối cảnh. Không lời thoại, không tên riêng.
- "description_vi": 1-2 câu tiếng Việt về nhân vật; nếu ngoại hình do bạn tự suy ra, ghi "(gợi ý)"."""


def _clean(items: list[dict], taken: set[str]) -> list[dict]:
    out, seen = [], set()
    for it in items:
        name = nfc(it.get("name", "")).strip()
        key = _fold(name)
        if not name or key in taken or key in seen:
            continue
        if (it.get("kind") or "person").strip().lower() not in KINDS:
            continue                                   # địa danh / tổ chức / vật phẩm / chức danh chung: không phải nhân vật
        seen.add(key)
        out.append({"name": name, "evidence": it.get("evidence", "").strip(), "gender": it.get("gender", "unknown"),
                    "aliases": [nfc(a).strip() for a in it.get("aliases", []) if a.strip() and _fold(nfc(a)) not in taken],
                    "role": it.get("role", "").strip(), "appearance_en": it.get("appearance_en", "").strip(),
                    "description_vi": it.get("description_vi", "").strip()})
    return out


def _chunks(text: str) -> list[str]:
    """Chia truyện thành các đoạn <= CHUNK ký tự tại ranh giới đoạn văn; quá nhiều thì lấy mẫu đều."""
    paras, cur, out = text.split("\n"), "", []
    for para in paras:
        if cur and len(cur) + len(para) > CHUNK:
            out.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        out.append(cur)
    if len(out) > MAX_CHUNKS:
        step = len(out) / MAX_CHUNKS
        out = [out[int(i * step)] for i in range(MAX_CHUNKS)]
    return out


def suggest_characters(p: Project, existing: list[Character], max_n: int = 10, log=print) -> list[dict]:
    """Đề xuất nhân vật chưa có trong dự án: [{name, aliases, role, appearance_en, description_vi, mentions}], xếp theo số lần nhắc.
    Ổn định hơn một lượt gọi AI đơn lẻ: (1) truyện dài được đọc TỪNG ĐOẠN rồi gộp; (2) có danh sách tên viết hoa lặp lại làm mỏ neo;
    (3) mọi nhân vật được đối chiếu số lần nhắc trong truyện thật, tên bịa/ít xuất hiện bị loại; (4) thứ tự cuối cùng theo số lần nhắc."""
    text = full_text(p)
    if not text.strip() and not p.synopsis.strip():
        raise ValueError("Dự án chưa có nội dung truyện hay bối cảnh. Dán truyện vào tab Truyện (hoặc điền Bối cảnh) trước.")
    have = ", ".join(c.name for c in existing) or "(chưa có)"
    taken = {_fold(nfc(c.name)) for c in existing} | {_fold(nfc(a)) for c in existing for a in c.aliases}
    hints = name_hints(text)
    hint_txt = ("\n\nCác cụm từ viết hoa lặp lại trong truyện (có thể là tên người, địa danh hoặc tổ chức; chỉ chọn những cái là NHÂN VẬT): "
                + ", ".join(f"{n} ({c})" for n, c in hints)) if hints else ""
    synopsis = f"BỐI CẢNH CHUNG: {p.synopsis.strip()}\n\n" if p.synopsis.strip() else ""
    raw: list[dict] = []
    if len(text) <= SINGLE_MAX:
        log(f"Đọc truyện ({len(text)} ký tự) trong một lượt...")
        prompt = f"{_instructions(p, have, max_n + 4)}{hint_txt}\n\n{story_digest(p)}"
        raw = json.loads(llm.generate_json(prompt, SCHEMA, log)).get("characters", [])
    else:
        parts = _chunks(text)
        log(f"Truyện dài ({len(text)} ký tự): đọc {len(parts)} đoạn, mỗi đoạn một lượt rồi gộp...")

        def one(k_part):
            k, part = k_part
            prompt = (f"{_instructions(p, have, 8)}\n\n(Đây là đoạn {k + 1}/{len(parts)} của truyện; chỉ liệt kê nhân vật XUẤT HIỆN trong đoạn này.)"
                      f"{hint_txt}\n\n{synopsis}{part}")
            return json.loads(llm.generate_json(prompt, SCHEMA, log)).get("characters", [])
        with ThreadPoolExecutor(max_workers=3) as ex:
            for res in ex.map(one, list(enumerate(parts))):
                raw += res
    folded = _fold(text)
    cands = _clean(raw, taken)
    # gộp trùng: hai mục chung tên/bí danh là một nhân vật
    merged: list[dict] = []
    for c in cands:
        keys = {_fold(c["name"])} | {_fold(a) for a in c["aliases"]}
        hit = next((m for m in merged if keys & ({_fold(m["name"])} | {_fold(a) for a in m["aliases"]})), None)
        if hit:
            hit["aliases"] = list(dict.fromkeys(hit["aliases"] + [a for a in c["aliases"] if a != hit["name"]] + ([c["name"]] if c["name"] != hit["name"] else [])))
            hit["appearance_en"] = hit["appearance_en"] or c["appearance_en"]
        else:
            merged.append(c)
    for c in merged:
        c["mentions"] = count_mentions(folded, [c["name"]] + c["aliases"])
        c["_rank"] = count_mentions(folded, [c["name"]])
        c["gender"] = decide_gender(c.get("gender", ""), text, [c["name"]] + c["aliases"])
        c["appearance_en"] = apply_gender(c["appearance_en"], c["gender"])          # thứ tự chỉ dựa vào tên chuẩn: bí danh AI chọn mỗi lần một khác
    def verified(c) -> bool:
        words = _fold(c.get("evidence", "")).split()
        return bool(words) and " ".join(words[:6]) in " ".join(folded.split())          # câu trích có thật trong truyện
    kept = [c for c in merged if (c["mentions"] >= MIN_MENTIONS or (len(text) < 600 and c["mentions"] >= 1))
            and (verified(c) or c["mentions"] >= 3)]
    dropped = [c["name"] for c in merged if c not in kept]
    if dropped:
        log(f"Loại {len(dropped)} tên ít xuất hiện/không có trong truyện: {', '.join(dropped[:6])}")
    kept.sort(key=lambda c: (-c["_rank"], -c["mentions"], _fold(c["name"])))
    for c in kept:
        c.pop("_rank", None)
    return kept[:max_n]


def image_prompt(p: Project, appearance: str, gender: str = "unknown") -> str:
    """Prompt tạo ảnh tham chiếu: một nhân vật, nền trơn, đúng phong cách dự án (để ảnh dùng được làm 'ingredient' cho Flow)."""
    style = (p.style or "").strip().rstrip(".")
    who = {"male": "The character is a man (male). ", "female": "The character is a woman (female). "}.get(gender, "")
    return (f"Character reference portrait: {appearance.strip().rstrip('.')}. {who}"
            + (f"Visual style: {style}. " if style else "")
            + "Single character, upper body, facing the camera, centered, plain neutral background, soft even lighting, "
              "highly detailed face, no text, no watermark, no other people. Vertical 3:4 composition.")


def explain_gemini_error(msg: str) -> str | None:
    """Giải thích bằng tiếng Việt các lỗi Gemini thường gặp khi tạo ảnh; None nếu không nhận ra."""
    low = msg.lower()
    if "429" in msg and ("limit: 0" in low or "free_tier" in low or "free tier" in low):
        return ("Key Gemini của bạn đang ở GÓI MIỄN PHÍ, gói này không có hạn mức tạo ảnh (limit 0). Cách xử lý: bật thanh toán "
                "(Billing) cho dự án của key trong Google AI Studio; hoặc dùng “Tạo ảnh bằng Google Flow” (dùng credit Flow của bạn); "
                "hoặc “Copy prompt ảnh” rồi tạo bằng công cụ khác.")
    if "403" in msg or "PERMISSION_DENIED" in msg:
        return "Key Gemini không có quyền dùng model tạo ảnh này. Kiểm tra key và quyền truy cập model trong Google AI Studio, hoặc đổi model ở Cài đặt."
    if "404" in msg or "NOT_FOUND" in msg:
        return "Không tìm thấy model tạo ảnh. Đổi tên model trong Cài đặt → Gemini API (ví dụ gemini-2.5-flash-image)."
    if "API key not valid" in msg or "API_KEY_INVALID" in msg:
        return "Gemini API key không hợp lệ. Kiểm tra lại key ở Cài đặt → Gemini API."
    return None


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
                friendly = explain_gemini_error(msg)
                if friendly:
                    raise ValueError(friendly) from e          # lỗi cấu hình/quota gói miễn phí: thử lại vô ích, nói rõ cách xử lý
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


def generate_images(project_name: str, prompts: dict, backend: str, log=print) -> tuple[dict, list[str]]:
    """Tạo ảnh cho nhiều nhân vật: prompts = {khoá: prompt}. Trả về ({khoá: bytes}, [lỗi từng ảnh]).
    backend 'flow': một phiên Chrome Flow cho cả lô (ảnh Nano Banana, thường 0 tín dụng); 'gemini': Gemini API (cần billing).
    Một ảnh lỗi không làm hỏng cả lô."""
    out, errs = {}, []
    if backend == "flow":
        from . import flow_auto
        p = Project.load(project_name)
        with flow_auto.FlowAuto(log) as f:
            for k, prompt in prompts.items():
                try:
                    out[k] = f.generate_image(p, prompt, "3:4")
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{k}: {e}")
                    log(f"Ảnh '{k}' lỗi: {e}")
                    if "quá tải" in str(e):
                        break                       # Flow đang quá tải: dừng lô, thử tiếp chỉ tốn thời gian
    else:
        for k, prompt in prompts.items():
            try:
                out[k] = generate_image(prompt, None, log)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{k}: {e}")
                if "GÓI MIỄN PHÍ" in str(e) or "API key" in str(e):
                    break
    return out, errs

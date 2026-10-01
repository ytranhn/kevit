"""Gọi LLM trả về JSON: Gemini hoặc Claude (có thể qua gateway/proxy). Tự thử lại khi quá tải/lỗi tạm thời."""
from __future__ import annotations

import json
import time

from . import settings

RETRY_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL", "502", "504", "529",
                 "DEADLINE", "overloaded", "Overloaded", "high demand", "timed out", "timeout", "Timeout",
                 "Connection", "rate limit", "Rate limit")
RETRY_DELAYS = (4, 8, 16, 30, 60, 90)   # tổng chờ tối đa ~3.5 phút


def is_configured() -> tuple[bool, str]:
    if settings.llm_provider() == "claude":
        return (bool(settings.claude_api_key()), "Nhập Claude API key ở tab Cài đặt.")
    return (bool(settings.get_api_key()), "Nhập Gemini API key ở tab Cài đặt.")


def describe() -> str:
    if settings.llm_provider() == "claude":
        via = f" qua {settings.claude_base_url()}" if settings.claude_base_url() else ""
        return f"Claude ({settings.claude_model()}){via}"
    return f"Gemini ({settings.LLM_MODEL})"


def short_name() -> str:
    """Tên model ngắn gọn cho chip trạng thái, vd. 'claude-haiku-4-5'."""
    if settings.llm_provider() == "claude":
        m = settings.claude_model().split("/")[-1]
        return m.replace("-20251001", "")[:26]
    return settings.LLM_MODEL


def extract_json(text: str) -> str:
    """Lấy khối JSON từ câu trả lời (bỏ hàng rào ```json và lời dẫn nếu có), kiểm tra hợp lệ."""
    t = text.strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError(f"Phản hồi không chứa JSON: {t[:200]!r}")
    body = t[a:b + 1]
    try:
        json.loads(body)
        return body
    except json.JSONDecodeError as first:
        fixed = repair_quotes(body)             # lỗi hay gặp: lời thoại dùng dấu " thẳng bên trong chuỗi
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            raise first


def repair_quotes(body: str) -> str:
    """Sửa JSON có dấu ngoặc kép chưa thoát BÊN TRONG chuỗi (vd. "说："要是…。""). Một dấu " trong chuỗi chỉ được coi là kết thúc chuỗi khi
    ngay sau nó (bỏ khoảng trắng) là `,` rồi một khoá/giá trị mới, `:`, `}` hoặc `]`; còn lại là dấu lạc và được thoát bằng \\\"."""
    out, i, n, in_str = [], 0, len(body), False
    while i < n:
        c = body[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
            continue
        if c == "\\" and i + 1 < n:                 # chuỗi thoát có sẵn: giữ nguyên cặp ký tự
            out.append(body[i:i + 2])
            i += 2
            continue
        if c == '"':
            j = i + 1
            while j < n and body[j] in " \t\r\n":
                j += 1
            nxt = body[j] if j < n else ""
            ahead = body[j + 1:j + 12].lstrip() if nxt == "," else ""
            if nxt in (":", "}", "]", "") or (nxt == "," and ahead[:1] in ('"', "{", "[", "-", "0123456789")):
                out.append('"')
                in_str = False
            else:
                out.append('\\"')
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _gemini(prompt: str, schema: dict, log=print) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=settings.get_api_key())
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0.2)
    resp = client.models.generate_content(model=settings.LLM_MODEL, contents=prompt, config=cfg)
    u = getattr(resp, "usage_metadata", None)
    if u:
        log(f"Token: vào {u.prompt_token_count} / ra {u.candidates_token_count}")
    return resp.text


CLAUDE_READ_TIMEOUT = 60.0   # giây im lặng tối đa giữa 2 gói dữ liệu (streaming) trước khi coi là treo


def claude_client():
    import anthropic
    kw: dict = dict(api_key=settings.claude_api_key(), max_retries=0, timeout=CLAUDE_READ_TIMEOUT)
    if settings.claude_base_url():
        kw["base_url"] = settings.claude_base_url()
    if settings.claude_proxy():
        kw["http_client"] = anthropic.DefaultHttpxClient(proxy=settings.claude_proxy(), timeout=CLAUDE_READ_TIMEOUT)
    return anthropic.Anthropic(**kw)


def _claude(prompt: str, schema: dict, log=print) -> str:
    """Dùng streaming: thấy tiến độ nhận dữ liệu, và treo quá CLAUDE_READ_TIMEOUT giây là báo thay vì chờ vô hạn."""
    system = ("Bạn chỉ trả về MỘT đối tượng JSON hợp lệ đúng theo JSON Schema sau, không markdown, không giải thích, "
              "không thêm khoá nào ngoài schema. Trong giá trị chuỗi KHÔNG dùng dấu ngoặc kép thẳng (\"): lời thoại hay trích dẫn dùng “ ” hoặc ‘ ’; "
              "xuống dòng viết là \\n. Mọi dấu \" bên trong chuỗi bắt buộc phải thoát bằng \\\".\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False))
    client, model = claude_client(), settings.claude_model()
    log(f"Đang gửi yêu cầu tới {settings.claude_base_url() or 'api.anthropic.com'} (model {model})...")
    t0, last, got, chunks = time.time(), 0.0, "", 0
    # Model có "thinking" (vd. Opus 5) tự bật suy luận và tính vào max_tokens: chương dài có thể ngốn hết hạn mức mà
    # không ra chữ nào -> tắt suy luận để toàn bộ hạn mức dành cho JSON (rẻ và nhanh hơn).
    kwargs = dict(model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": prompt}])
    stop = None
    for thinking in (True, False):
        try:
            with client.messages.stream(**kwargs, **({"thinking": {"type": "disabled"}} if thinking else {})) as stream:
                for piece in stream.text_stream:
                    got += piece
                    chunks += 1
                    if chunks == 1:
                        log(f"Đã kết nối, bắt đầu nhận phản hồi sau {time.time() - t0:.0f}s...")
                    elif time.time() - last > 8:
                        log(f"...đã nhận {len(got)} ký tự ({time.time() - t0:.0f}s)")
                        last = time.time()
            break
        except Exception as e:  # noqa: BLE001
            if not thinking or "thinking" not in str(e).lower():
                raise
            log("Proxy không nhận tham số tắt suy luận, thử lại không dùng.")
    try:
        final = stream.get_final_message()
        stop = final.stop_reason
        log(f"Token: vào {final.usage.input_tokens} / ra {final.usage.output_tokens}")
    except Exception:  # noqa: BLE001 - một số proxy không trả message cuối/usage
        pass
    if not got.strip():
        if stop == "max_tokens":
            raise ValueError("Model dùng hết hạn mức đầu ra mà chưa trả JSON (thường do model tự suy luận quá dài). "
                             "Thử model không có suy luận (vd. claude-sonnet / haiku) hoặc chia chương ngắn hơn.")
        raise ValueError("Proxy trả về phản hồi rỗng (kiểm tra model và quyền của key).")
    if stop == "max_tokens":
        raise ValueError(f"Phản hồi bị cắt vì chạm giới hạn đầu ra ({len(got)} ký tự). Hãy giảm 'Số scene tối đa' "
                         "hoặc chia chương ngắn hơn.")
    log(f"Nhận xong {len(got)} ký tự trong {time.time() - t0:.0f}s.")
    return extract_json(got)


def generate_json(prompt: str, schema: dict, log=print) -> str:
    """Trả về chuỗi JSON. Lỗi tạm thời (503/429/529/timeout...) tự thử lại; lỗi khác (key sai, 404...) báo ngay."""
    call = _claude if settings.llm_provider() == "claude" else _gemini
    timeouts = 0
    json_retried = False
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return call(prompt, schema, log)
        except json.JSONDecodeError as e:
            if json_retried:
                raise ValueError(f"Mô hình trả JSON hỏng hai lần liên tiếp ({e}). Thử lại, hoặc đổi sang mô hình khác trong Cài đặt.") from e
            json_retried = True            # thường do dấu ngoặc kép thẳng bên trong chuỗi (lời thoại): nhắc rõ rồi thử lại 1 lần
            log("Mô hình trả JSON bị hỏng (thường do dấu ngoặc kép trong lời thoại), đang thử lại...")
            prompt += ("\n\n(LƯU Ý: lần trước JSON bị hỏng vì có dấu \" bên trong nội dung chuỗi. Lần này tuyệt đối không dùng dấu \" trong "
                       "nội dung; lời thoại dùng “ ” hoặc ‘ ’. Chỉ trả đúng một đối tượng JSON hợp lệ.)")
            continue
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "timed out" in msg or type(e).__name__ == "APITimeoutError":
                timeouts += 1
                if timeouts >= 2:  # treo 2 lần liên tiếp: thường là sai địa chỉ/model, không đáng chờ thêm
                    raise RuntimeError(
                        f"Không nhận được phản hồi trong {int(CLAUDE_READ_TIMEOUT)}s (2 lần). Kiểm tra địa chỉ proxy, "
                        f"key và tên model rồi dùng 'Lưu và thử kết nối' trong tab Cài đặt. Chi tiết: {msg[:120]}") from e
            if attempt == len(RETRY_DELAYS) or not any(m in msg for m in RETRY_MARKERS):
                raise
            wait = RETRY_DELAYS[attempt]
            log(f"LLM quá tải/lỗi tạm thời ({msg[:60].strip()}...). Thử lại sau {wait}s "
                f"(lần {attempt + 1}/{len(RETRY_DELAYS)})...")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def ping() -> str:
    """Kiểm tra kết nối: gửi 1 yêu cầu nhỏ, trả về mô tả ngắn."""
    out = generate_json('Trả về JSON {"ok": true}', {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                        log=lambda m: None)
    return f"Kết nối OK: {describe()} → {out[:60]}"

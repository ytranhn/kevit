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
    json.loads(body)  # ném lỗi rõ ràng nếu JSON hỏng
    return body


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
              "không thêm khoá nào ngoài schema.\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False))
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
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return call(prompt, schema, log)
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

import os

from PySide6.QtCore import QSettings

_s = QSettings("veo-story-studio", "veo-story-studio")

LLM_MODEL = "gemini-3.8-flash"
VEO_MODEL = "veo-3.1-generate-preview"  # hỗ trợ reference images + audio
TTS_MODEL = "gemini-2.5-flash-preview-tts"


def get_api_key() -> str:
    return str(_s.value("api_key", "")) or os.environ.get("GEMINI_API_KEY", "")


def set_api_key(key: str) -> None:
    _s.setValue("api_key", key.strip())


# ---- LLM cho bước tách scene / viết lại thuyết minh: gemini | claude ----
CLAUDE_DEFAULT_MODEL = "claude-sonnet-5-5"


def _get(key: str, default: str = "") -> str:
    return str(_s.value(key, default) or default)


def llm_provider() -> str:
    return _get("llm_provider", "gemini")


def claude_api_key() -> str:
    return _get("claude_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")


def normalize_base_url(url: str) -> str:
    """SDK tự thêm /v1/messages, nên bỏ đuôi /v1 hoặc /v1/messages người dùng lỡ dán vào."""
    u = url.strip().rstrip("/")
    for suffix in ("/v1/messages", "/v1"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    return u.rstrip("/")


def claude_base_url() -> str:
    return normalize_base_url(_get("claude_base_url") or os.environ.get("ANTHROPIC_BASE_URL", ""))


def claude_model() -> str:
    return _get("claude_model", CLAUDE_DEFAULT_MODEL)


def claude_proxy() -> str:
    return _get("claude_proxy")


def save_llm(provider: str, claude_key: str, base_url: str, model: str, proxy: str) -> None:
    _s.setValue("llm_provider", provider)
    _s.setValue("claude_api_key", claude_key.strip())
    _s.setValue("claude_base_url", normalize_base_url(base_url))
    _s.setValue("claude_model", model.strip() or CLAUDE_DEFAULT_MODEL)
    _s.setValue("claude_proxy", proxy.strip())

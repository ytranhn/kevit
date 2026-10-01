import json
import os
import uuid
from dataclasses import asdict, dataclass

from PySide6.QtCore import QSettings

# KEVIT_SETTINGS_FILE: dùng file .ini riêng thay cho cấu hình thật của người dùng (dành cho kiểm thử, không ghi vào plist/registry thật)
_s = (QSettings(os.environ["KEVIT_SETTINGS_FILE"], QSettings.IniFormat) if os.environ.get("KEVIT_SETTINGS_FILE")
      else QSettings("veo-story-studio", "veo-story-studio"))

LLM_MODEL = "gemini-3.8-flash"
VEO_MODEL = "veo-3.1-generate-preview"  # hỗ trợ reference images + audio
TTS_MODEL = "gemini-2.5-flash-preview-tts"


def get_api_key() -> str:
    return str(_s.value("api_key", "")) or os.environ.get("GEMINI_API_KEY", "")


def set_api_key(key: str) -> None:
    _s.setValue("api_key", key.strip())


# ---- tạo ảnh nhân vật bằng Gemini ----
IMAGE_DEFAULT_MODEL = "gemini-2.5-flash-image"


def image_backend() -> str:
    """Nơi tạo ảnh nhân vật: 'flow' (Google Flow, dùng tài khoản Flow đã đăng nhập) hoặc 'gemini' (Gemini API)."""
    v = str(_s.value("image_backend", "flow") or "flow")
    return v if v in ("flow", "gemini") else "flow"


def set_image_backend(v: str) -> None:
    _s.setValue("image_backend", v if v in ("flow", "gemini") else "flow")


def image_model() -> str:
    return str(_s.value("image_model", IMAGE_DEFAULT_MODEL) or IMAGE_DEFAULT_MODEL)


def set_image_model(model: str) -> None:
    _s.setValue("image_model", model.strip() or IMAGE_DEFAULT_MODEL)


# ---- LLM cho bước tách scene / viết lại thuyết minh: nhiều mô hình, chọn một cái đang dùng ----
CLAUDE_DEFAULT_MODEL = "claude-sonnet-5-5"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"

# loại mô hình -> nhãn, model mặc định, gợi ý ô nhập
LLM_KINDS = {
    "claude": dict(label="Claude (Anthropic hoặc proxy)", model=CLAUDE_DEFAULT_MODEL, key_hint="sk-ant-…", base_hint="https://proxy.example.com",
                   base_note="Để trống để dùng api.anthropic.com. Dán địa chỉ gốc, không kèm /v1/messages."),
    "gemini": dict(label="Gemini (Google)", model=LLM_MODEL, key_hint="AIza… (để trống = dùng key ở thẻ Gemini)", base_hint="",
                   base_note=""),
    "openai": dict(label="OpenAI-compatible (OpenAI, OpenRouter, DeepSeek, Ollama…)", model="gpt-4o-mini", key_hint="sk-…",
                   base_hint=OPENAI_DEFAULT_BASE,
                   base_note="Địa chỉ gốc của API, kết thúc ở /v1 (ứng dụng tự thêm /chat/completions)."),
}

# mẫu dựng sẵn khi bấm “Thêm mô hình”: (tên hiển thị, loại, địa chỉ API, model)
LLM_PRESETS = [
    ("Claude (Anthropic)", "claude", "", CLAUDE_DEFAULT_MODEL),
    ("Claude qua proxy", "claude", "https://", ""),
    ("Gemini", "gemini", "", LLM_MODEL),
    ("OpenAI", "openai", OPENAI_DEFAULT_BASE, "gpt-4o-mini"),
    ("OpenRouter", "openai", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    ("DeepSeek", "openai", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("Ollama (chạy trên máy)", "openai", "http://localhost:11434/v1", "llama3.1"),
    ("OpenAI-compatible khác", "openai", "https://", ""),
]


@dataclass
class LLMProfile:
    id: str
    name: str
    kind: str = "claude"            # claude | gemini | openai
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    proxy: str = ""                 # HTTP proxy mạng (tuỳ chọn)

    @property
    def kind_label(self) -> str:
        return LLM_KINDS.get(self.kind, LLM_KINDS["claude"])["label"]

    @property
    def effective_model(self) -> str:
        return self.model.strip() or LLM_KINDS.get(self.kind, LLM_KINDS["claude"])["model"]

    @property
    def effective_key(self) -> str:
        """Khoá đã nhập, nếu trống thì lấy từ biến môi trường / khoá Gemini chung."""
        if self.api_key.strip():
            return self.api_key.strip()
        if self.kind == "claude":
            return os.environ.get("ANTHROPIC_API_KEY", "")
        if self.kind == "openai":
            return os.environ.get("OPENAI_API_KEY", "")
        return get_api_key()

    @property
    def effective_base_url(self) -> str:
        if self.kind == "claude":
            return normalize_base_url(self.base_url or os.environ.get("ANTHROPIC_BASE_URL", ""))
        if self.kind == "openai":
            return normalize_openai_base(self.base_url) or OPENAI_DEFAULT_BASE
        return ""

    @property
    def is_local(self) -> bool:
        u = self.effective_base_url
        return "://localhost" in u or "://127.0.0.1" in u or "://[::1]" in u


def _get(key: str, default: str = "") -> str:
    return str(_s.value(key, default) or default)


def normalize_base_url(url: str) -> str:
    """Claude: SDK tự thêm /v1/messages, nên bỏ đuôi /v1 hoặc /v1/messages người dùng lỡ dán vào."""
    u = url.strip().rstrip("/")
    for suffix in ("/v1/messages", "/v1"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    return u.rstrip("/")


def normalize_openai_base(url: str) -> str:
    """OpenAI-compatible: ứng dụng tự thêm /chat/completions, nên bỏ đuôi đó nếu người dùng lỡ dán vào."""
    u = url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    return u.rstrip("/")


def new_profile_id() -> str:
    return uuid.uuid4().hex[:8]


def _legacy_profiles() -> tuple[list[LLMProfile], str]:
    """Bản cũ chỉ có một cấu hình Claude + một Gemini: chuyển thành danh sách để người dùng cũ không mất gì."""
    claude = LLMProfile("claude", "Claude", "claude", _get("claude_api_key"), normalize_base_url(_get("claude_base_url")),
                        _get("claude_model", CLAUDE_DEFAULT_MODEL), _get("claude_proxy"))
    gemini = LLMProfile("gemini", "Gemini", "gemini", "", "", LLM_MODEL)
    return [claude, gemini], ("claude" if _get("llm_provider", "gemini") == "claude" else "gemini")


def llm_profiles() -> list[LLMProfile]:
    raw = _get("llm_profiles")
    if raw:
        try:
            out = [LLMProfile(**{k: v for k, v in d.items() if k in LLMProfile.__dataclass_fields__}) for d in json.loads(raw)]
            if out:
                return out
        except Exception:  # noqa: BLE001 - dữ liệu hỏng: dựng lại từ cấu hình cũ
            pass
    profiles, active = _legacy_profiles()
    save_llm_profiles(profiles, active)
    return profiles


def active_llm_id() -> str:
    profiles = llm_profiles()
    cur = _get("llm_active")
    return cur if any(p.id == cur for p in profiles) else profiles[0].id


def active_llm() -> LLMProfile:
    aid = active_llm_id()
    return next(p for p in llm_profiles() if p.id == aid)


def save_llm_profiles(profiles: list[LLMProfile], active_id: str) -> None:
    if not profiles:
        raise ValueError("Cần ít nhất một mô hình.")
    _s.setValue("llm_profiles", json.dumps([asdict(p) for p in profiles], ensure_ascii=False))
    _s.setValue("llm_active", active_id if any(p.id == active_id for p in profiles) else profiles[0].id)


def set_active_llm(profile_id: str) -> None:
    save_llm_profiles(llm_profiles(), profile_id)


def llm_provider() -> str:
    """Loại của mô hình đang dùng: claude | gemini | openai."""
    return active_llm().kind

"""Mọi chuỗi/selector gắn với giao diện Google Flow nằm ở đây. Flow hiển thị theo ngôn ngữ của TỪNG tài khoản Google (có tài khoản
tiếng Việt, có tài khoản tiếng Anh) nên mỗi nhãn là một biểu thức khớp CẢ HAI (không phân biệt hoa thường). Flow đổi UI hoặc thêm
ngôn ngữ mới thì chỉ cần sửa file này."""
import re


def T(*alts: str) -> "re.Pattern":
    """Nhãn nhiều ngôn ngữ: khớp bất kỳ phương án nào (chứa chuỗi, không phân biệt hoa thường)."""
    return re.compile("|".join(re.escape(a) for a in alts), re.I)


def text_in(alts: tuple, text: str) -> bool:
    return any(a.lower() in text.lower() for a in alts)


HOME_URL = "https://flow.google.com/"
CDP_URL = "http://localhost:9222"

# Trang chủ
BTN_NEW_PROJECT = T("Dự án mới", "New project")
LINK_OPEN_PROJECT = T("Mở dự án", "Open project")
EDIT_TITLE_LABELS = ("Chỉnh sửa tiêu đề dự án", "Edit project title")      # aria-label của nút sửa tên trên thẻ project

# Trong dự án
TITLE_BOX = T("Văn bản có thể chỉnh sửa", "Editable text")
PROMPT_EDITOR = ".prosemirror-editor"
BTN_AGENT_CHIP = ".agent-mode-chip-label"          # nhãn "Agent": giao diện Flow mới mở sẵn chế độ Agent, phải tắt đi mới có ô cài đặt tạo clip
BTN_SETTINGS_PILL = T("Điều kiện kích hoạt cài đặt", "Settings trigger")
RADIO_VIDEO = T("Video")
RADIO_INGREDIENTS = T("Thành phần", "Ingredients")
BTN_MODEL = T("Chọn nhóm mô hình", "Select model family")
BTN_ADD_INGREDIENT = T("Thêm thành phần vào ô nhập câu lệnh", "Add ingredients to the prompt box")
BTN_UPLOAD = T("Tải nội dung nghe nhìn lên", "Upload media")
SEARCH_ASSET = T("Tìm kiếm thành phần", "Search assets")
UPLOADING_LABELS = ("Đang tải lên", "Uploading")
BTN_GENERATE = T("Bắt đầu tạo", "Start generation")
TXT_COST = T("Quá trình tạo sẽ tốn", "Generating will use")
DURATION_UNIT = r"(?:s|giây)"                    # "8s" (Anh) / "8 giây" (Việt)
TILE = "flow-video-tile"
IMAGE_TILE = "flow-image-tile"
PENDING_TILE = "flow-pending-tile"       # ô đang render (cả ảnh lẫn video)
RADIO_IMAGE = T("Hình ảnh", "Image")     # chế độ tạo ảnh (model Nano Banana, thường 0 tín dụng)

# Màn hình clip
BTN_DOWNLOAD = T("Tải nội dung nghe nhìn xuống", "Download")
MENU_IMAGE_ORIGINAL = T("Kích thước gốc", "Original size", "Original")   # ảnh: 1K gốc; 2K/4K là bản nâng độ phân giải
MENU_ORIGINAL = "720p"       # bản gốc, không tốn credit; 1080p/4K là bản upscale

MODELS = ["Omni 1.1 Flash", "Veo 3.1 - Lite", "Veo 3.1 - Fast", "Veo 3.1 - Quality"]

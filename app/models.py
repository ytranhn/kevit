"""Data models + JSON persistence. Mọi dữ liệu nằm trong data/ (gitignored)."""
from __future__ import annotations

import json
import sys
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

def safe_dirname(name: str) -> str:
    """Tên thư mục hợp lệ trên mọi hệ điều hành (Windows cấm < > : " / \\ | ? * và dấu chấm/cách ở cuối)."""
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", nfc(name).strip()).rstrip(" .")
    if out.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        out += "_"
    return out or "du-an"


def rebase_path(path: str) -> str:
    """Đường dẫn tuyệt đối đã lưu mà không còn tồn tại (dự án bị dời thư mục/đổi máy) -> đường dẫn tương ứng dưới PROJ_DIR hiện tại."""
    if not path or Path(path).exists():
        return path
    norm = path.replace("\\", "/")
    for marker in ("/projects/",):
        if marker in norm:
            cand = PROJ_DIR / Path(norm.split(marker, 1)[1])
            if cand.exists():
                return str(cand)
    return path


def nfc(s: str) -> str:
    """Chuẩn hoá Unicode về NFC. macOS ghi tên file ở dạng NFD còn LLM trả NFC: so sánh tên mà không chuẩn hoá thì
    'Cố An' != 'Cố An' (cùng hiển thị, khác byte) và nhân vật bị loại nhầm."""
    return unicodedata.normalize("NFC", s)


_SAVE_LOCK = threading.RLock()
APP_NAME = "Kevit"
LEGACY_APP_NAME = "Veo Story Studio"      # tên cũ của app: thư mục dữ liệu cũ vẫn được dùng tiếp nếu đã tồn tại
FROZEN = bool(getattr(sys, "frozen", False))      # chạy từ bản đóng gói (PyInstaller)


def resource_path(rel: str) -> Path:
    """File đi kèm app (biểu tượng...): trong bản đóng gói nằm ở thư mục giải nén, khi dev nằm cạnh mã nguồn."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def qsettings():
    """Cấu hình người dùng (QSettings). Đặt KEVIT_SETTINGS_FILE để dùng một file .ini riêng thay cho cấu hình thật:
    bắt buộc với mọi bài kiểm thử, vì đổi thư mục dữ liệu trong test từng ghi đè cấu hình thật và làm app 'mất' dữ liệu."""
    from PySide6.QtCore import QSettings
    f = os.environ.get("KEVIT_SETTINGS_FILE")
    return QSettings(f, QSettings.IniFormat) if f else QSettings("veo-story-studio", "veo-story-studio")


def _saved_data_dir() -> str:
    return str(qsettings().value("data_dir", "") or "")


def _data_dir() -> Path:
    """Nơi lưu dự án/nhân vật/profile Chrome. Thứ tự ưu tiên: biến môi trường VEO_DATA_DIR > thư mục người dùng đã chọn
    (Cài đặt → Dữ liệu) > mặc định. Mặc định khi dev: ./data. Bản đóng gói: thư mục dữ liệu người dùng của hệ điều hành,
    NẰM NGOÀI gói app nên build/cập nhật app không bao giờ làm mất dữ liệu."""
    if os.environ.get("VEO_DATA_DIR"):
        return Path(os.environ["VEO_DATA_DIR"]).expanduser()
    saved = _saved_data_dir()
    if saved and Path(saved).is_dir():
        return Path(saved)
    if not FROZEN:
        return Path(__file__).resolve().parent.parent / "data"
    new, old = _os_user_dir(APP_NAME), _os_user_dir(LEGACY_APP_NAME)
    return new if new.exists() or not old.exists() else old


def _os_user_dir(name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / name
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / name


def has_projects(data_dir: Path) -> bool:
    return any((Path(data_dir) / "projects").glob("*/project.json"))


def set_data_dir(path: Path) -> None:
    """Lưu lựa chọn thư mục dữ liệu và cập nhật các đường dẫn đang dùng (cửa sổ đang mở cần khởi động lại để nạp lại)."""
    global DATA_DIR, PROJ_DIR
    qsettings().setValue("data_dir", str(path))
    DATA_DIR = Path(path)
    PROJ_DIR = DATA_DIR / "projects"
    fa = sys.modules.get("app.flow_auto")
    if fa is not None:
        fa.PROFILE_DIR = DATA_DIR / "flow_profile"
        fa.DOWNLOAD_DIR = DATA_DIR / "flow_downloads"
        ac = sys.modules.get("app.accounts")
        if ac is not None:
            ac.activate(ac._active_id)               # hồ sơ Chrome của tài khoản đang dùng nằm theo thư mục dữ liệu mới


def remember_dev_data_dir() -> None:
    """Chạy từ mã nguồn thì ghi nhớ thư mục data đang dùng, để bản đóng gói lần đầu mở có thể đề nghị dùng lại."""
    if FROZEN:
        return
    qsettings().setValue("dev_data_dir", str(DATA_DIR))


def legacy_data_dir() -> Path | None:
    """Thư mục data của bản chạy từ mã nguồn (nếu còn và có dự án) mà bản đóng gói chưa dùng."""
    v = str(qsettings().value("dev_data_dir", "") or "")
    return Path(v) if v and Path(v) != DATA_DIR and has_projects(Path(v)) else None


DATA_DIR = _data_dir()
PROJ_DIR = DATA_DIR / "projects"


_SUMMARY_CACHE: dict[str, tuple[int, tuple[int, int, int]]] = {}


@dataclass
class Character:
    name: str
    description: str = ""          # mô tả ngoại hình/giọng nói, đưa vào prompt
    aliases: list[str] = field(default_factory=list)  # tên gọi khác trong truyện
    image: str = ""                # đường dẫn ảnh tham chiếu (đã tạo hình sẵn)
    role: str = ""                 # vai trò trong truyện (từ gói nhân vật), giúp LLM nhận diện nhân vật
    zh: str = ""                   # tên Hán
    description_vi: str = ""       # mô tả gốc tiếng Việt của gói (tham khảo; prompt dùng `description`)


@dataclass
class Scene:
    index: int
    title: str = ""
    visual: str = ""               # mô tả hình ảnh/bối cảnh/hành động
    source_text: str = ""          # đoạn truyện gốc (nguyên văn) mà scene này diễn tả
    narration: str = ""            # thuyết minh (người dẫn truyện đọc), rút gọn từ source_text
    characters: list[str] = field(default_factory=list)
    duration: int = 8              # giây: 4/6/8
    raw_clip: str = ""             # clip Veo gốc (chỉ hình + ambient)
    audio: str = ""                # file wav thuyết minh (TTS)
    clip: str = ""                 # clip cuối: hình Veo + giọng đọc cố định
    status: str = "pending"        # pending | generating | done | error
    error: str = ""


@dataclass
class Chapter:
    id: str                        # "01", "02"... (cố định, dùng làm tên thư mục)
    title: str = ""
    story: str = ""                # nội dung chapter (nguyên văn)
    scenes: list[Scene] = field(default_factory=list)
    flow_project_url: str = ""     # project Google Flow RIÊNG của chương này (mỗi chương một project để Flow không phải lọc quá nhiều clip)
    post_meta: dict = field(default_factory=dict)    # tiêu đề/mô tả/hashtag để đăng video chương: {title, description, hashtags}

    @property
    def name(self) -> str:
        return self.title or f"Chương {int(self.id)}"

    @property
    def done(self) -> int:
        return sum(1 for s in self.scenes if s.status == "done")


@dataclass
class Project:
    name: str
    synopsis: str = ""             # tóm tắt/bối cảnh truyện, đưa vào ngữ cảnh khi tách scene
    style: str = "cinematic, soft lighting, 35mm film look"
    aspect_ratio: str = "9:16"     # "9:16" (dọc) | "16:9" (ngang) | "flow" (giữ nguyên khổ đang chọn trong Flow)
    tts_provider: str = "edge"     # edge (miễn phí) | gemini
    voice: str = "vi-VN-HoaiMyNeural"  # 1 giọng đọc duy nhất cho cả dự án
    narration_lang: str = "vi"         # ngôn ngữ thuyết minh (vi = giữ nguyên truyện; ngôn ngữ khác = dịch ngắn gọn từ truyện gốc)
    flow_project_url: str = ""     # project Google Flow cấp DỰ ÁN (ảnh nhân vật; dự án cũ: cũng là nơi chứa clip các chương đã gen trước khi tách project theo chương)
    flow_model: str = "Veo 3.1 - Fast"
    flow_resolution: str = "720p"      # chỉ áp dụng cho Omni (Veo cố định)
    flow_parallel: int = 1             # số scene gửi lên Flow cùng lúc (1 = lần lượt từng scene)
    flow_account: str = ""             # tài khoản Google Flow của dự án (id trong accounts.json); rỗng = tài khoản chính
    flow_stash: dict = field(default_factory=dict)   # địa chỉ project Flow của các tài khoản KHÁC (mỗi tài khoản có project riêng của nó)
    flow_auto_duration: bool = True    # Omni: chọn thời lượng clip ngắn nhất đủ đọc thuyết minh
    voice_style: str = "Đọc bằng giọng kể chuyện ấm, rõ ràng, tốc độ vừa phải"
    post_meta: dict = field(default_factory=dict)    # tiêu đề/mô tả/hashtag để đăng video ghép cả dự án
    publish_accounts: list[str] = field(default_factory=list)    # tài khoản (id trong cài đặt Đăng video) mà dự án này đăng lên
    publish_scope: str = "chapters"    # chapters: đăng video từng chương | project: đăng một video cả dự án
    publish_privacy: str = "private"   # private | unlisted | public
    publish_auto: bool = False         # tự ghép, viết mô tả và đăng ngay khi một chương gen xong
    publish_history: list[dict] = field(default_factory=list)    # nhật ký các lần đăng: {chapter, platform, account, ok, url, post_id, time, privacy, message}
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return PROJ_DIR / self.name

    def chapter_dir(self, ch: Chapter) -> Path:
        return self.dir / "chapters" / ch.id

    def merged_path(self, ch: Chapter) -> Path:
        return self.chapter_dir(ch) / "chapter.mp4"

    @property
    def full_path(self) -> Path:
        return self.dir / "full.mp4"

    def new_chapter(self, title: str = "") -> Chapter:
        nid = max((int(c.id) for c in self.chapters), default=0) + 1
        ch = Chapter(f"{nid:02d}", title.strip())
        ch.title = ch.title or f"Chương {nid}"
        self.chapters.append(ch)
        return ch

    @property
    def account_id(self) -> str:
        return self.flow_account or "default"

    def use_flow_account(self, acc_id: str) -> bool:
        """Đổi tài khoản Flow của dự án. Project Flow là của riêng từng tài khoản nên địa chỉ project (cấp dự án và từng chương) được cất
        theo tài khoản cũ và nạp lại địa chỉ của tài khoản mới (chưa có thì để trống, lần gen sau sẽ tạo project mới). True nếu có đổi."""
        acc_id = acc_id or "default"
        cur = self.account_id
        if acc_id == cur:
            return False
        self.flow_stash[cur] = {"project": self.flow_project_url,
                                "chapters": {c.id: c.flow_project_url for c in self.chapters if c.flow_project_url}}
        st = self.flow_stash.pop(acc_id, {})
        self.flow_project_url = st.get("project", "")
        for c in self.chapters:
            c.flow_project_url = st.get("chapters", {}).get(c.id, "")
        self.flow_account = "" if acc_id == "default" else acc_id
        return True

    def all_scenes(self) -> list[tuple[Chapter, Scene]]:
        return [(c, s) for c in self.chapters for s in c.scenes]

    def save(self) -> None:
        """Ghi an toàn khi nhiều luồng cùng lưu (làm giọng chạy song song): có khoá + ghi file tạm rồi đổi tên nguyên tử."""
        with _SAVE_LOCK:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "project.json.tmp"
            tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.dir / "project.json")

    @classmethod
    def load(cls, name: str) -> "Project":
        d = json.loads((PROJ_DIR / name / "project.json").read_text(encoding="utf-8"))
        d.pop("publish_platforms", None)        # bản thử cũ chọn theo nền tảng; nay chọn theo tài khoản
        old_scenes, old_story = d.pop("scenes", None), d.pop("story", None)   # định dạng cũ: 1 dự án = 1 chapter
        chapters = []
        for c in d.pop("chapters", []):
            c = dict(c)
            scenes = [Scene(**s) for s in c.pop("scenes", [])]
            for s in scenes:
                s.characters = [nfc(n) for n in s.characters]
                s.raw_clip, s.audio, s.clip = rebase_path(s.raw_clip), rebase_path(s.audio), rebase_path(s.clip)
            chapters.append(Chapter(**c, scenes=scenes))
        p = cls(**d, chapters=chapters)
        if not chapters and (old_scenes or old_story):
            p._migrate_legacy(old_story or "", [Scene(**s) for s in old_scenes or []])
        return p

    def _migrate_legacy(self, story: str, scenes: list[Scene]) -> None:
        """Dự án cũ (1 chapter phẳng) -> Chương 01: chuyển clips/audio/flow vào chapters/01 và sửa đường dẫn."""
        backup = self.dir / "project.json.bak-truoc-khi-chia-chuong"
        if not backup.exists():  # giữ bản gốc phòng khi cần quay lại
            shutil.copy2(self.dir / "project.json", backup)
        first = next((ln.strip() for ln in story.splitlines() if ln.strip()), "")
        title = first[:120] if re.match(r"(?i)^chương\s*\d+", first) else "Chương 1"
        ch = Chapter("01", title, story, scenes)
        old, new = self.dir, self.chapter_dir(ch)
        new.mkdir(parents=True, exist_ok=True)
        for sub in ("clips", "audio", "flow"):
            if (old / sub).exists() and not (new / sub).exists():
                shutil.move(str(old / sub), str(new / sub))
        legacy_merged = old / f"{self.name}.mp4"
        if legacy_merged.exists() and not self.merged_path(ch).exists():
            shutil.move(str(legacy_merged), str(self.merged_path(ch)))
        for s in scenes:  # đổi theo (thư mục cha, tên file) nên đúng cả khi dự án đã bị di chuyển/đổi máy
            for attr in ("raw_clip", "audio", "clip"):
                v = getattr(s, attr)
                if v and Path(v).parent.name in ("clips", "audio"):
                    setattr(s, attr, str(new / Path(v).parent.name / Path(v).name))
        self.chapters = [ch]
        self.save()

    @staticmethod
    def summary(name: str) -> tuple[int, int, int]:
        """(số chương, số scene xong, tổng scene) đọc thẳng từ file, không migrate dữ liệu cũ, dùng cho danh sách chuyển dự án.
        Có nhớ theo thời điểm sửa file: hàng trăm dự án mà mở danh sách không phải đọc lại từng project.json."""
        f = PROJ_DIR / name / "project.json"
        try:
            m = f.stat().st_mtime_ns
        except OSError:
            return 0, 0, 0
        hit = _SUMMARY_CACHE.get(name)
        if hit and hit[0] == m:
            return hit[1]
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return 0, 0, 0
        groups = [c.get("scenes", []) for c in d.get("chapters", [])] or ([d.get("scenes", [])] if d.get("scenes") else [])
        scenes = [s for g in groups for s in g]
        res = (len(groups), sum(1 for s in scenes if s.get("status") == "done"), len(scenes))
        _SUMMARY_CACHE[name] = (m, res)
        return res

    @staticmethod
    def list_names() -> list[str]:
        return sorted(p.parent.name for p in PROJ_DIR.glob("*/project.json"))


def char_dir(project: str) -> Path:
    return PROJ_DIR / project / "characters"


def load_characters(project: str) -> list[Character]:
    f = char_dir(project) / "characters.json"
    if not f.exists():
        return []
    out = []
    for d in json.loads(f.read_text(encoding="utf-8")):
        c = Character(**d)
        c.name, c.aliases = nfc(c.name), [nfc(a) for a in c.aliases]
        c.image = rebase_path(c.image)
        out.append(c)
    return out


def characters_mtime(project: str) -> float:
    f = char_dir(project) / "characters.json"
    return f.stat().st_mtime if f.exists() else 0.0


def save_characters(project: str, chars: list[Character]) -> None:
    char_dir(project).mkdir(parents=True, exist_ok=True)
    (char_dir(project) / "characters.json").write_text(
        json.dumps([asdict(c) for c in chars], ensure_ascii=False, indent=2), encoding="utf-8")


def import_image(project: str, src: str, char_name: str) -> str:
    """Copy ảnh vào thư mục nhân vật của dự án để không phụ thuộc đường dẫn gốc."""
    d = char_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{char_name}{Path(src).suffix.lower()}"
    if Path(src).resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return str(dst)

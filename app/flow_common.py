"""Phần dùng chung của tự động hoá Flow: hằng số, lỗi, ngân sách credit, khởi chạy/dò Chrome (CDP)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import flow_selectors as S
from .models import DATA_DIR

PROFILE_DIR = DATA_DIR / "flow_profile"
FLOW_URL = "https://labs.google/fx/tools/flow"
DOWNLOAD_DIR = DATA_DIR / "flow_downloads"      # nơi Chrome Flow tự lưu file tải, không bật hộp thoại chọn chỗ lưu


ERROR_RE = r"(lỗi|không thành công|thất bại|failed)"
OVERLOAD_RE = r"(high demand|nhu cầu cao|quá tải|retried at a later time|try again later)"


def _ranges(nums: list[int]) -> str:
    """[2,3,4,7,9,10] -> 'scene 2-4, 7, 9-10'."""
    nums, out, i = sorted(nums), [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return "scene " + ", ".join(out)


class FlowError(RuntimeError):
    pass


class NoCreditError(FlowError):
    """Tài khoản Flow không đủ credit cho scene tiếp theo (đã biết trước khi bấm tạo). Scene chưa gửi KHÔNG bị tính là lỗi: để chuyển tài khoản."""


class Budget:
    """Credit còn lại của tài khoản đang dùng trong một lượt gen: trừ dần theo giá thật của từng scene đã gửi. left=None: chưa biết, không chặn."""

    def __init__(self, left: int | None = None):
        self.left = left

    def can_afford(self, cost: int | None) -> bool:
        return self.left is None or cost is None or cost <= self.left

    def spend(self, cost: int | None) -> None:
        if self.left is not None and cost:
            self.left = max(0, self.left - cost)


def use_account(acc) -> None:
    """Chuyển mọi thao tác Flow sang tài khoản `acc` (accounts.Account): cổng debug và hồ sơ Chrome riêng của nó."""
    global PROFILE_DIR
    S.CDP_URL = acc.cdp_url
    PROFILE_DIR = acc.profile_dir
    _cdp_cache.update(up=False, t=0.0)                # trạng thái Chrome của tài khoản trước không còn đúng


def _cdp_up(url: str | None = None) -> bool:
    """Chrome Flow có đang mở cổng gỡ lỗi không (mặc định: tài khoản đang dùng). CHẶN tới 2s nếu Chrome treo: chỉ gọi ở luồng nền; giao diện dùng cdp_state()."""
    try:
        urllib.request.urlopen((url or S.CDP_URL) + "/json/version", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


_cdp_cache = {"up": False, "t": 0.0, "busy": False}


def cdp_state(max_age: float = 3.0) -> bool:
    """Trạng thái Chrome Flow cho giao diện: trả về NGAY giá trị đã biết, và nếu cũ hơn max_age giây thì kiểm tra lại ở luồng nền
    (Chrome treo không còn làm đứng giao diện 2 giây mỗi lần)."""
    import threading
    now = time.time()
    if now - _cdp_cache["t"] > max_age and not _cdp_cache["busy"]:
        _cdp_cache["busy"] = True

        def run():
            try:
                _cdp_cache["up"] = _cdp_up()
            finally:
                _cdp_cache["t"], _cdp_cache["busy"] = time.time(), False
        threading.Thread(target=run, name="cdp-check", daemon=True).start()
    return _cdp_cache["up"]


def _chrome_exe() -> str | None:
    """Đường dẫn Chrome thật trên Windows/Linux (macOS dùng `open -a`)."""
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for r in filter(None, roots):
            f = Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if f.exists():
                return str(f)
        return shutil.which("chrome") or shutil.which("chrome.exe")
    return next(filter(None, (shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))), None)


def chrome_command(acc=None) -> list[str]:
    """Lệnh mở Chrome BÌNH THƯỜNG (không qua Playwright) với profile riêng + cổng debug (của `acc`, mặc định tài khoản đang dùng)."""
    port = acc.port if acc else S.CDP_URL.rsplit(":", 1)[-1]
    flags = [f"--remote-debugging-port={port}", f"--user-data-dir={acc.profile_dir if acc else PROFILE_DIR}",
             "--no-first-run", FLOW_URL]
    if sys.platform == "darwin":
        return ["open", "-na", "Google Chrome", "--args", *flags]
    exe = _chrome_exe()
    if not exe:
        raise FlowError("Không tìm thấy Google Chrome. Hãy cài Chrome rồi thử lại.")
    return [exe, *flags]


FLOW_ORIGINS = ("https://flow.google.com:443,*", "https://labs.google:443,*")


def seed_download_prefs(profile: Path | None = None) -> bool:
    """Cho phép tải nhiều file tự động cho riêng flow.google.com / labs.google trong profile Chrome của tool, và tắt hỏi nơi lưu,
    để Chrome không bật popup 'trang web muốn tải xuống nhiều tệp' mỗi lần tool tải clip. Chỉ ghi khi Chrome CHƯA chạy bằng profile này
    (Chrome ghi đè file khi thoát). Trả True nếu đã ghi."""
    prof = Path(profile or PROFILE_DIR)
    f = prof / "Default" / "Preferences"
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        ex = d.setdefault("profile", {}).setdefault("content_settings", {}).setdefault("exceptions", {}).setdefault("automatic_downloads", {})
        for o in FLOW_ORIGINS:
            ex[o] = {"setting": 1}
        dl = d.setdefault("download", {})
        dl["prompt_for_download"] = False
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dl["default_directory"] = str(DOWNLOAD_DIR)
        d.setdefault("savefile", {})["default_directory"] = str(DOWNLOAD_DIR)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 - tuỳ chọn tiện lợi, không để làm hỏng việc mở Chrome
        return False


def launch_chrome(acc=None) -> None:
    """Mở Chrome Flow của tài khoản `acc` (mặc định: tài khoản đang dùng) nếu chưa mở; chờ tới khi cổng debug sẵn sàng."""
    url = acc.cdp_url if acc else S.CDP_URL
    prof = Path(acc.profile_dir) if acc else PROFILE_DIR
    if not _cdp_up(url):
        prof.mkdir(parents=True, exist_ok=True)
        seed_download_prefs(prof)
        subprocess.Popen(chrome_command(acc))
        for _ in range(20):
            if _cdp_up(url):
                return
            time.sleep(1)
        raise FlowError(f"Không mở được Chrome (cổng {url.rsplit(':', 1)[-1]}). Nếu Chrome đang mở sẵn bằng hồ sơ khác, "
                        "hãy đóng hết cửa sổ Chrome của tool rồi bấm lại.")

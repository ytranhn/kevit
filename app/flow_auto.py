"""Tự động hoá Google Flow qua Chrome thật (CDP localhost:9222) của người dùng.
Chỉ thao tác trên tab flow.google.com, không chạm tab khác. Gặp CAPTCHA/đăng nhập thì dừng báo lỗi."""
from __future__ import annotations

from .flow_common import (  # noqa: F401 - các nơi khác gọi qua flow_auto.*
    DOWNLOAD_DIR, ERROR_RE, FLOW_ORIGINS, FLOW_URL, OVERLOAD_RE, PROFILE_DIR, Budget, FlowError, NoCreditError, _cdp_up, _chrome_exe,
    _ranges, cdp_state, chrome_command, launch_chrome, seed_download_prefs, use_account)

__all__ = ["FlowAuto", "Budget", "FlowError", "NoCreditError", "cdp_state", "launch_chrome", "use_account", "chrome_command", "seed_download_prefs"]
from .flow_parts.session import FlowSessionMixin
from .flow_parts.clips import FlowClipMixin
from .flow_parts.images import FlowImageMixin
from .flow_parts.sync import FlowSyncMixin


class FlowAuto(FlowSessionMixin, FlowClipMixin, FlowImageMixin, FlowSyncMixin):
    def __init__(self, log=print, dry_run: bool = False, acc=None):
        """acc: tài khoản (accounts.Account) cần điều khiển; None = tài khoản đang dùng. Mỗi tài khoản có Chrome/cổng riêng nên
        có thể điều khiển tài khoản khác với tài khoản đang dùng mà không phải đổi cài đặt chung."""
        self.log, self.dry_run, self.acc = log, dry_run, acc
        self.last_cost: int | None = None            # giá credit thật Flow báo cho cấu hình vừa chọn
        self.pw = self.browser = self.page = None










































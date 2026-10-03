from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from .version import __version__
from . import accounts, theme, flow_auto, llm, models, settings
from .publish_scheduler import Scheduler
from .publish_tab import PublishTab
from .settings_tab import SettingsTab
from .project_tab import ProjectTab
from .welcome import Welcome
from .theme import SP
from .shell import TopBar
from .widgets import Popover, StatusStrip
from .characters_tab import CharactersTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Kevit {__version__}")
        scr = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1360, int(scr.width() * 0.94)), min(900, int(scr.height() * 0.88)))
        self.setMinimumSize(1216, 640)
        self.logbox = QPlainTextEdit(readOnly=True)  # chọn/copy được
        self.logbox.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.logbox.setPlaceholderText("Nhật ký hoạt động")
        chars = CharactersTab()
        self.tabs = tabs = QTabWidget()
        self.proj = proj = ProjectTab(chars, self.logbox.appendPlainText)
        proj.opened.connect(chars.set_project)
        chars.usage_counter = proj.character_usage
        chars.chapter_provider = lambda: proj.chapter.id if proj.chapter else ""
        chars.flow_state_changed.connect(proj.reload_flow_state)
        chars.deleted.connect(proj.on_characters_deleted)
        chars.added.connect(proj.on_characters_added)
        chars.chars_changed.connect(proj.update_empty_state)
        chars.chars_changed.connect(proj.d_chars.refresh)     # nối TRƯỚC khi nạp để chip luôn được vẽ lại với ảnh
        chars.set_project(proj.combo.currentText())
        self.settings_tab = SettingsTab()
        self.settings_tab.accounts_changed.connect(proj.on_accounts_changed)
        self.settings_tab.llm_changed.connect(lambda: self.refresh_chips())
        proj.account_changed.connect(lambda *_: (self.refresh_chips(), self.settings_tab.accounts_panel.refresh()))
        proj.busy_changed.connect(lambda busy, *_: self.settings_tab.accounts_panel.set_busy(busy))
        proj.welcome = Welcome(proj, lambda: tabs.setCurrentWidget(self.settings_tab), proj.new_project, proj.launch_flow_chrome)
        proj.update_welcome()
        tabs.addTab(proj, "Dự án")
        tabs.addTab(chars, "Nhân vật")
        self.publish_tab = PublishTab(lambda: proj.project, self.logbox.appendPlainText)
        proj.generation_done.connect(self.publish_tab.on_generation_done)
        proj.publish_settings_changed.connect(self.publish_tab.reload)
        proj.open_settings_section.connect(lambda key: (tabs.setCurrentWidget(self.settings_tab), self.settings_tab.select_section(key)))
        self.publish_tab.open_settings_requested.connect(lambda: (tabs.setCurrentWidget(self.settings_tab), self.settings_tab.select_section("publish")))
        self.settings_tab.publish_changed.connect(self.publish_tab.refresh_accounts)
        self.scheduler = Scheduler(lambda: proj.project)
        self.publish_tab.set_scheduler(self.scheduler)
        self.scheduler.log.connect(self.logbox.appendPlainText)
        tabs.addTab(self.publish_tab, "Đăng video")
        tabs.addTab(self.settings_tab, "Cài đặt")
        for i in range(tabs.count()):                      # Cmd/Ctrl+1..4: chuyển nhanh giữa các tab
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: tabs.setCurrentIndex(i))

        # nhật ký: ngăn kéo nổi phía trên thanh trạng thái, ẩn mặc định (không chiếm chỗ của nội dung)
        self.strip = StatusStrip()
        self.strip.log_toggled.connect(self.toggle_log)
        self.strip.stop_clicked.connect(proj.request_cancel)
        self.strip.chip_clicked.connect(self.on_chip)
        proj.activity.connect(self.strip.set_activity)
        proj.busy_changed.connect(lambda busy, cancel: self.strip.set_busy(busy, cancel))
        proj.progress_changed.connect(self.strip.set_progress)
        self.publish_tab.activity.connect(self.strip.set_activity)
        self.scheduler.activity.connect(self.strip.set_activity)
        self.scheduler.start()
        self.strip.set_activity("Sẵn sàng.", "info")

        central = QWidget()
        # thanh trên cùng (logo, tab có icon, tài khoản Flow) thay thanh tab mặc định; QTabWidget vẫn giữ để chuyển trang
        tabs.tabBar().hide()
        self.topbar = TopBar(ICON_PATH, [("Dự án", "folder"), ("Nhân vật", "users"), ("Đăng video", "open"), ("Cài đặt", "gear")])
        self.topbar.tab_clicked.connect(tabs.setCurrentIndex)
        self.topbar.account_clicked.connect(lambda: self.on_chip("flow", anchor=self.topbar.pill))
        tabs.currentChanged.connect(self.topbar.set_current)
        self.topbar.set_current(tabs.currentIndex())
        body = QWidget()
        cl = QVBoxLayout(body)
        cl.setContentsMargins(SP.l, SP.l, SP.l, SP.l)
        cl.setSpacing(SP.s)
        cl.addWidget(tabs, 1)
        cl.addWidget(self.strip)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.topbar)
        root.addWidget(body, 1)
        self.setCentralWidget(central)
        self.central = central
        # Ngăn nhật ký là cửa sổ nổi riêng (không viền): khung video dùng bề mặt vẽ riêng nên luôn đè lên mọi widget con,
        # chỉ cửa sổ cấp cao nhất mới nằm trên được.
        self.log_win = QWidget(self, Qt.Tool | Qt.FramelessWindowHint)
        self.log_win.setAttribute(Qt.WA_TranslucentBackground)
        self.log_win.setAttribute(Qt.WA_ShowWithoutActivating)
        lw = QVBoxLayout(self.log_win)
        lw.setContentsMargins(0, 0, 0, 0)
        self.logbox.setParent(self.log_win)
        self.logbox.setObjectName("logOverlay")
        lw.addWidget(self.logbox)
        self.log_win.hide()

        self.chip_timer = QTimer(self)
        self.chip_timer.setInterval(4000)
        self.chip_timer.timeout.connect(self.refresh_chips)
        self.chip_timer.start()
        self.refresh_chips()
        QTimer.singleShot(900, self, self.refresh_chips)     # lần đầu: kết quả kiểm tra Chrome ở luồng nền đã về, cập nhật chip ngay

    LOG_HEIGHT = 230

    def place_log(self):
        """Đặt ngăn nhật ký ngay phía trên thanh trạng thái, rộng bằng vùng nội dung."""
        c = self.central
        h = min(self.LOG_HEIGHT, max(120, c.height() - 200))
        top_left = c.mapToGlobal(QPoint(16, 0))
        strip_top = self.strip.mapToGlobal(QPoint(0, 0)).y()
        self.log_win.setGeometry(top_left.x(), strip_top - 6 - h, c.width() - 32, h)

    def toggle_log(self):
        show = not self.log_win.isVisible()
        if show:
            self.place_log()
            self.log_win.show()
            self.log_win.raise_()
            self.logbox.verticalScrollBar().setValue(self.logbox.verticalScrollBar().maximum())
        else:
            self.log_win.hide()
        self.strip.set_log_open(show)

    def _follow(self):
        if self.log_win.isVisible():
            self.place_log()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._follow()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._follow()

    def closeEvent(self, e):
        if self.scheduler.busy and QMessageBox.question(
                self, "Đang đăng theo lịch", "Kevit đang đăng một video theo lịch. Thoát bây giờ sẽ làm gián đoạn lượt đăng này.\n\nVẫn thoát?") != QMessageBox.Yes:
            e.ignore()
            return
        self.log_win.hide()
        super().closeEvent(e)

    def refresh_chips(self):
        ok, _ = llm.is_configured()
        prof = settings.active_llm()
        self.strip.set_chip("llm", f"LLM · {llm.short_name()}" if ok else f"LLM · {prof.name} chưa nhập key", ok)
        up = flow_auto.cdp_state()
        accs = accounts.all_accounts()
        act = accounts.active()
        who = f" · {act.name}" if len(accs) > 1 else ""         # chỉ nêu tên tài khoản khi có nhiều hơn một
        cr = f" · {accounts.fmt_credits(act.credits)} cr" if act.credits is not None else ""
        self.topbar.set_account(act.name)
        self.strip.set_chip("flow", f"Flow{who}{cr} ● sẵn sàng" if up else f"Flow{who}{cr} ○ chưa mở Chrome", up)
        voice = self.proj.project.voice.split("-")[-1].replace("Neural", "") if self.proj.project else "—"
        self.strip.set_chip("voice", f"Giọng · {voice}", True)
        if self.proj.welcome.isVisible():
            self.proj.welcome.refresh()

    def build_flow_pop(self, pop):
        """Chip Flow: chọn tài khoản Flow cho DỰ ÁN đang mở, mở Chrome của tài khoản đó, hoặc vào quản lý tài khoản."""
        cur = accounts.active().id
        pop.section("Tài khoản Flow của dự án" if self.proj.project else "Tài khoản Flow")
        for a in accounts.all_accounts():
            pop.item(a.name, accounts.describe_credits(a) + ("  ·  mặc định cho dự án mới" if a.id == accounts.default_new_id() else ""),
                     (lambda i=a.id: self.proj.set_account(i)), shortcut="✓" if a.id == cur else "", enabled=bool(self.proj.project))
        pop.separator()
        on = accounts.auto_switch()
        pop.item("Tự chuyển tài khoản: " + ("BẬT" if on else "tắt"),
                 "Hết credit thì tự sang tài khoản khác còn credit" if not on else "Bấm để tắt: hết credit sẽ chặn và báo cảnh báo",
                 lambda: (accounts.set_auto_switch(not on), self.settings_tab.accounts_panel.auto.setChecked(not on)))
        pop.item("Cập nhật credit tài khoản này", "Đọc lại credit từ Flow (mở Chrome nếu chưa mở)",
                 lambda: self.proj.refresh_credits([accounts.active()], deep=True), enabled=not self.proj._busy)
        pop.separator()
        if not flow_auto.cdp_state(0):
            pop.item("Mở Chrome cho tài khoản này", "Đăng nhập Google Flow một lần trong cửa sổ đó", self.proj.launch_flow_chrome)
        pop.item("Quản lý tài khoản…", "Thêm, đổi tên, gỡ, mở Chrome để đăng nhập",
                 lambda: (self.tabs.setCurrentWidget(self.settings_tab), self.settings_tab.select_section("flow")))

    def build_llm_pop(self, pop):
        """Chip LLM: chọn nhanh mô hình AI đang dùng (áp dụng cho mọi dự án), hoặc vào quản lý mô hình."""
        pop.section("Mô hình AI đang dùng")
        cur = settings.active_llm_id()
        for p in settings.llm_profiles():
            ok, _ = llm.is_configured(p)
            sub = f"{p.kind_label.split(' (')[0]}  ·  {p.effective_model}" + ("" if ok else "  ·  chưa nhập key")
            pop.item(p.name, sub, (lambda i=p.id: self.set_llm(i)), shortcut="✓" if p.id == cur else "")
        pop.separator()
        pop.item("Quản lý mô hình…", "Thêm Claude, Gemini, OpenAI và dịch vụ tương thích",
                 lambda: (self.tabs.setCurrentWidget(self.settings_tab), self.settings_tab.select_section("llm")))

    def set_llm(self, profile_id: str):
        settings.set_active_llm(profile_id)
        self.settings_tab.llm_panel.active_id = profile_id
        self.settings_tab.llm_panel.refresh_list(profile_id)
        self.refresh_chips()

    def on_chip(self, key: str, anchor=None):
        if key == "llm":
            if not hasattr(self, "llm_pop"):
                self.llm_pop = Popover(self, self.build_llm_pop, 380)
            self.llm_pop.show_for(self.strip.chips["llm"], "above", "right")
        elif key == "flow":
            if not hasattr(self, "flow_pop"):
                self.flow_pop = Popover(self, self.build_flow_pop, 360)
            if anchor is not None:
                self.flow_pop.show_for(anchor, "below", "right")
            else:
                self.flow_pop.show_for(self.strip.chips["flow"], "above", "right")
        elif key == "voice":
            self.tabs.setCurrentWidget(self.proj)
            self.proj.open_settings()


ICON_PATH = models.resource_path("assets/icon.png")


def main():
    import sys
    if sys.platform == "win32":   # để thanh tác vụ Windows dùng đúng biểu tượng của app thay vì biểu tượng Python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.kevit")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Kevit")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))   # cửa sổ, Dock (macOS), thanh tác vụ (Windows)
    theme.install(app)
    models.remember_dev_data_dir()
    if models.FROZEN and not models.has_projects(models.DATA_DIR):
        old = models.legacy_data_dir()
        if old and QMessageBox.question(
                None, "Tìm thấy dữ liệu cũ",
                f"Tìm thấy {len(list((old / 'projects').glob('*/project.json')))} dự án đã tạo trước đó tại:\n{old}\n\n"
                "Dùng thư mục này làm nơi lưu dữ liệu? Dữ liệu giữ nguyên tại chỗ (không sao chép), đăng nhập Flow cũng dùng tiếp, "
                "và các lần cập nhật app sau này vẫn dùng đúng thư mục này.") == QMessageBox.Yes:
            models.set_data_dir(old)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

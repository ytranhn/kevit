"""Hệ thống thiết kế: nền xám lạnh trung tính, nhấn màu chàm (indigo), bo góc lớn, tiêu đề sans-serif.
Có bản sáng và tối, tự theo chế độ của hệ điều hành. Mọi màu trong ứng dụng lấy từ đây, không gắn cứng."""
from __future__ import annotations

from string import Template

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QMessageBox, QProxyStyle, QStyle, QFileDialog, QInputDialog

def _a(hex_color: str, alpha: float) -> str:
    """Qt QSS chỉ hiểu alpha dạng số nguyên 0-255 (hoặc %), nên đổi ở đây để mỗi token luôn hợp lệ."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{round(alpha * 255)})"


LIGHT = dict(
    bg="#F5F6F8", surface="#FFFFFF", surface2="#ECEEF2", border="#DDE0E6", border_strong="#C3C8D1",
    text="#151922", muted="#586172", faint="#8A92A1",
    accent="#4F46E5", accent_hover="#4338CA", accent_dis=_a("#4F46E5", 0.40), accent_tint=_a("#4F46E5", 0.10),
    on_accent="#FFFFFF", sel=_a("#4F46E5", 0.11),
    ok="#1C8A5A", ok_tint=_a("#1C8A5A", 0.12), warn="#B26A00", warn_tint=_a("#B26A00", 0.13),
    err="#D2383A", err_tint=_a("#D2383A", 0.11), info="#2F6FDE", video="#0B0C10", shadow=_a("#151922", 0.18),
)
DARK = dict(
    bg="#14161B", surface="#1D2027", surface2="#272B34", border="#303541", border_strong="#454C5B",
    text="#ECEEF3", muted="#A4ABB9", faint="#707889",
    accent="#6B6FF2", accent_hover="#8185FF", accent_dis=_a("#6B6FF2", 0.40), accent_tint=_a("#6B6FF2", 0.20),
    on_accent="#FFFFFF", sel=_a("#6B6FF2", 0.24),
    ok="#4CC38A", ok_tint=_a("#4CC38A", 0.16), warn="#E5B454", warn_tint=_a("#E5B454", 0.16),
    err="#F2766D", err_tint=_a("#F2766D", 0.16), info="#6FA8FF", video="#08090C", shadow=_a("#000000", 0.5),
)

# Thang khoảng cách DUY NHẤT của ứng dụng (px). Mọi lề và khoảng cách giữa các thành phần chỉ dùng các giá trị này.
#   xs  quan hệ rất gần (nhãn <-> ô nhập, tiêu đề <-> mô tả)
#   s   giữa các điều khiển trong một hàng
#   m   giữa các nhóm trường trong một biểu mẫu
#   l   lề thẻ/trang, khoảng cách giữa các thẻ và các khối lớn
#   xl  lề hộp thoại
SP = SimpleNamespace(xs=4, s=8, m=12, l=16, xl=24)
SCALE = (0, SP.xs, SP.s, SP.m, SP.l, SP.xl)

T: dict[str, str] = dict(LIGHT)       # token đang dùng (cập nhật bởi apply)
SERIF = "'Helvetica Neue', 'Segoe UI', Arial, sans-serif"

QSS = Template("""
* { font-size: 13px; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; border-radius: 8px; padding: 5px 8px; }

/* ---- ô nhập ---- */
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox, QDateTimeEdit {
    background: $surface; color: $text; border: 1px solid $border; border-radius: 10px; padding: 7px 10px;
    selection-background-color: $accent; selection-color: $on_accent; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus { border: 1px solid $accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QDateTimeEdit:disabled { color: $faint; background: $surface2; }
QLineEdit { min-height: 20px; }
QComboBox { min-height: 20px; padding-right: 26px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 28px; }
QComboBox::down-arrow { image: url($chev_down_png); width: 14px; height: 14px; }
QComboBox::down-arrow:disabled { image: url($chev_down_dis_png); }
QComboBoxPrivateContainer { background: transparent; border: none; }
QComboBox QAbstractItemView { background: $surface; color: $text; border: 1px solid $border; border-radius: 12px;
    padding: 6px; outline: 0; selection-background-color: $sel; selection-color: $text; }
QComboBox QAbstractItemView::item { min-height: 32px; padding: 0 12px; border-radius: 8px; }
QComboBox QAbstractItemView::item:hover { background: $surface2; }
QComboBox QAbstractItemView::item:selected { background: $sel; color: $text; }
QDateTimeEdit { min-height: 20px; padding-right: 30px; }
QDateTimeEdit::drop-down { subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 28px; }
QDateTimeEdit::down-arrow { image: url($chev_down_png); width: 14px; height: 14px; }
QDateTimeEdit::up-button, QDateTimeEdit::down-button { width: 0; border: none; }
QCalendarWidget { background: $surface; border: 1px solid $border; border-radius: 12px; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: transparent; border: none; padding: 6px 6px 2px 6px; min-height: 40px; }
QCalendarWidget QToolButton { background: transparent; color: $text; border: none; border-radius: 8px; padding: 6px 12px;
    font-size: 15px; font-weight: 600; icon-size: 18px; min-height: 28px; }
QCalendarWidget QToolButton:hover { background: $surface2; }
QCalendarWidget QToolButton#qt_calendar_prevmonth, QCalendarWidget QToolButton#qt_calendar_nextmonth { min-width: 32px; padding: 6px; }
QCalendarWidget QToolButton#qt_calendar_prevmonth { qproperty-icon: url($chev_left_png); }
QCalendarWidget QToolButton#qt_calendar_nextmonth { qproperty-icon: url($chev_right_png); }
QCalendarWidget QToolButton::menu-indicator { image: none; width: 0; }
QCalendarWidget QSpinBox { background: $surface2; color: $text; border: 1px solid $border; border-radius: 8px; padding: 2px 8px; min-height: 24px; }
QCalendarWidget QMenu { background: $surface; color: $text; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QCalendarWidget QMenu::item { padding: 6px 18px; border-radius: 6px; }
QCalendarWidget QMenu::item:selected { background: $sel; }
QCalendarWidget QAbstractItemView { background: transparent; color: $text; font-size: 14px; outline: 0; border: none;
    selection-background-color: $accent; selection-color: $on_accent; padding: 4px 8px 8px 8px; }
QCalendarWidget QAbstractItemView:disabled { color: $faint; }
QSpinBox { min-height: 20px; padding-right: 26px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 22px; border: none; background: transparent; }
QSpinBox::up-button { subcontrol-position: top right; }
QSpinBox::down-button { subcontrol-position: bottom right; }
QSpinBox::up-arrow { image: url($chev_up_png); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url($chev_down_png); width: 10px; height: 10px; }
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off { image: url($chev_up_dis_png); }
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off { image: url($chev_down_dis_png); }
QListWidget { background: $surface; border: 1px solid $border; border-radius: 12px; padding: 4px; outline: 0;
    selection-background-color: $sel; selection-color: $text; }
QListWidget::item { padding: 6px 8px; border-radius: 8px; }
QListWidget::item:selected { background: $sel; color: $text; }
QListWidget::item:hover:!selected { background: $surface2; }

/* ---- nút ---- */
QPushButton { background: $surface; color: $text; border: 1px solid $border; border-radius: 10px;
    padding: 7px 16px; min-height: 20px; }
QPushButton:hover { background: $surface2; border-color: $border_strong; }
QPushButton:pressed { background: $surface2; border-color: $muted; }
QPushButton:disabled { color: $faint; background: transparent; border-color: $border; }
QPushButton:checked { background: $accent_tint; border-color: $accent; color: $text; }
QPushButton[primary="true"] { background: $accent; color: $on_accent; border: none; font-weight: 600; padding: 8px 20px; }
QPushButton[primary="true"]:hover { background: $accent_hover; }
QPushButton[primary="true"]:disabled { background: $accent_dis; color: $on_accent; }
QPushButton[flat="true"] { padding: 4px 12px; min-height: 16px; font-size: 12px; border-radius: 9px; }
QPushButton[danger="true"] { color: $err; }
QPushButton[danger="true"]:hover { background: $err_tint; border-color: $err; }
QPushButton[arrow="true"] { padding: 0; }
QPushButton[ghost="true"] { border: none; background: transparent; color: $muted; padding: 4px 8px; }
QPushButton[ghost="true"]:hover { background: $surface2; color: $text; }

/* ---- điều khiển phân đoạn (segmented) ---- */
QFrame[seg="true"] { background: $surface2; border: 1px solid $border; border-radius: 11px; }
QPushButton[segbtn="true"] { border: 1px solid transparent; background: transparent; color: $muted; border-radius: 8px;
    padding: 3px 16px; min-height: 18px; font-weight: 500; }
QPushButton[segbtn="true"]:hover:!checked { color: $text; background: transparent; border-color: transparent; }
QPushButton[segbtn="true"]:checked { background: $surface; color: $text; border: 1px solid $border; font-weight: 600; }
QPushButton[segbtn="true"]:disabled { color: $faint; }

/* ---- khung ứng dụng: thanh trên cùng + điều hướng bên (Cài đặt) ---- */
QFrame[topbar="true"] { background: $surface; border: none; border-bottom: 1px solid $border; }
QPushButton[topnav="true"] { border: none; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: $muted;
    padding: 0 6px; min-height: 40px; font-weight: 500; }
QPushButton[topnav="true"]:hover { color: $text; background: transparent; }
QPushButton[topnav="true"][active="true"] { color: $accent; border-bottom: 2px solid $accent; font-weight: 600; }
QPushButton[acctpill="true"] { border: 1px solid transparent; border-radius: 19px; background: transparent; padding: 0; min-height: 38px; }
QPushButton[acctpill="true"]:hover { background: $surface2; border-color: $border; }
QLabel[avatar2="true"] { background: $accent; color: $on_accent; border-radius: 15px; font-weight: 700; }
QFrame[sidebar="true"] { background: $surface; border: none; border-right: 1px solid $border; }
QPushButton[sidenav="true"] { text-align: left; border: 1px solid transparent; background: transparent; border-radius: 12px; padding: 0; min-height: 58px; }
QPushButton[sidenav="true"]:hover { background: $surface2; }
QPushButton[sidenav="true"][active="true"] { background: $accent_tint; border: 1px solid $accent; }
QLabel[navtile="true"] { background: $surface2; border-radius: 10px; }
QPushButton[sidenav="true"][active="true"] QLabel[navtile="true"] { background: transparent; }
QLabel[pagetitle="true"] { font-family: $serif; font-size: 26px; font-weight: 700; color: $text; }
QPushButton[platab="true"] { border: 1px solid transparent; border-radius: 10px; background: transparent; color: $muted; padding: 8px 14px; font-weight: 600; min-height: 22px; }
QPushButton[platab="true"]:hover { background: $surface2; color: $text; }
QPushButton[platab="true"]:checked { background: $accent_tint; border: 1px solid $accent; color: $text; }
QFrame[guide="true"] { background: $surface; border: 1px solid $border; border-radius: 12px; }
QFrame[secure="true"] { background: $accent_tint; border: 1px solid $border; border-radius: 12px; }
QPushButton[aspcard="true"] { background: $surface; border: 1px solid $border; border-radius: 12px; padding: 0; text-align: center; }
QPushButton[aspcard="true"]:hover { border-color: $border_strong; }
QPushButton[aspcard="true"]:checked { border: 1px solid $accent; background: $accent_tint; }
QFrame[creditcard="true"] { background: $ok_tint; border: 1px solid $ok; border-radius: 12px; }
QFrame[estimate="true"] { background: $warn_tint; border: 1px solid $border; border-radius: 10px; }
QLabel[statusline="true"] { background: $surface2; color: $muted; border-radius: 10px; padding: 10px 14px; }
QLabel[statusline="true"][kind="ok"] { background: $ok_tint; color: $ok; }
QLabel[statusline="true"][kind="warn"] { background: $warn_tint; color: $warn; }
QLabel[statusline="true"][kind="err"] { background: $err_tint; color: $err; }
QTimeEdit { padding-right: 26px; }
QTimeEdit::up-button, QTimeEdit::down-button { subcontrol-origin: border; width: 22px; border: none; background: transparent; }
QTimeEdit::up-button { subcontrol-position: top right; }
QTimeEdit::down-button { subcontrol-position: bottom right; }
QTimeEdit::up-arrow { image: url($chev_up_png); width: 10px; height: 10px; }
QTimeEdit::down-arrow { image: url($chev_down_png); width: 10px; height: 10px; }
QFrame[vidrow="true"] { background: $surface; border: 1px solid $border; border-radius: 12px; }
QFrame[vidrow="true"][selected="true"] { border: 1px solid $accent; background: $accent_tint; }
QFrame[platile="true"] { background: $surface; border: 1px solid $border; border-radius: 12px; }
QFrame[platile="true"][on="true"] { border: 1px solid $accent; background: $accent_tint; }
QFrame[tagbox="true"] { background: $surface; border: 1px solid $border; border-radius: 10px; }
QLineEdit[taginput="true"] { border: none; background: transparent; padding: 4px 2px; min-height: 22px; }
QLineEdit[taginput="true"]:focus { border: none; }
QFrame[hashtag="true"] { background: $surface2; border: 1px solid $border; border-radius: 8px; }
QFrame[hashtag="true"] QLabel { background: transparent; }
QPushButton[tagx="true"] { border: none; background: transparent; color: $muted; padding: 0; min-height: 18px; min-width: 18px; max-width: 18px; border-radius: 9px; }
QPushButton[tagx="true"]:hover { background: $err_tint; color: $err; }
QLabel[stepdot="true"] { border: 1px solid $border_strong; border-radius: 17px; color: $muted; background: $surface; font-weight: 600; }
QLabel[stepdot="true"][state="active"] { background: $accent; border-color: $accent; color: $on_accent; }
QLabel[stepdot="true"][state="done"] { background: $ok_tint; border-color: $ok; color: $ok; }
QFrame[stepline="true"] { background: $border; border: none; min-height: 1px; max-height: 1px; }
QLabel[thumb="true"] { background: $video; border-radius: 10px; color: #FFFFFF; }
QLabel[badge="true"] { background: rgba(0,0,0,170); color: #FFFFFF; border-radius: 6px; padding: 1px 6px; font-size: 11px; }
QLabel[counter="true"] { color: $faint; font-size: 12px; }
QLabel[counter="true"][over="true"] { color: $err; }
QFrame[provrow="true"] { background: $surface; border: 1px solid $border; border-radius: 12px; }
QFrame[provrow="true"][selected="true"] { border: 1px solid $accent; background: $accent_tint; }
QLabel[logotile="true"] { border-radius: 10px; color: #FFFFFF; font-weight: 700; font-size: 16px; }
QFrame[banner="true"] { background: $surface; border: 1px solid $border; border-radius: 12px; }
QListWidget#provList { background: transparent; border: none; padding: 0; }
QListWidget#provList::item { padding: 0; border: none; background: transparent; }
QListWidget#provList::item:selected, QListWidget#provList::item:hover:!selected { background: transparent; }
QPushButton::menu-indicator { image: none; width: 0px; }
QMenu { background: $surface; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 18px; border-radius: 7px; color: $text; }
QMenu::item:selected { background: $sel; }
QMenu::item:disabled { color: $faint; }
QProgressBar[acctbar="true"] { background: $surface2; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar[acctbar="true"]::chunk { background: $accent; border-radius: 4px; }
QProgressBar[greenbar="true"]::chunk { background: $ok; border-radius: 4px; }
QPushButton[iconbtn="true"][active="true"] { background: $accent_tint; border-color: $accent; }
QPushButton[iconbtn="true"] { padding: 0; min-height: 36px; min-width: 36px; max-width: 36px; border-radius: 10px; }

/* ---- popover ---- */
QFrame#popCard { background: $surface; border: 1px solid $border; border-radius: 14px; }
QPushButton[popitem="true"] { text-align: left; border: none; background: transparent; border-radius: 9px; padding: 0; min-height: 40px; }
QPushButton[popitem="true"][popsub="true"] { min-height: 54px; }
QPushButton[popitem="true"][popprog="true"] { min-height: 66px; }
QProgressBar[popbar="true"] { background: $surface2; border-radius: 2px; max-height: 4px; min-height: 4px; }
QProgressBar[popbar="true"]::chunk { background: $accent; border-radius: 2px; }
QPushButton[popitem="true"]:hover { background: $surface2; }
QPushButton[popaction="true"] { border: 1px solid $border_strong; background: transparent; color: $err; padding: 3px 10px;
    min-height: 18px; font-size: 12px; font-weight: 600; border-radius: 8px; }
QPushButton[popaction="true"]:hover { background: $err_tint; border-color: $err; }
QPushButton[popaction="true"]:pressed { background: $err_tint; border-color: $err; }
QPushButton[popitem="true"]:disabled { background: transparent; }
QLabel[popsection="true"] { color: $faint; font-size: 11px; font-weight: 600; padding: 12px 12px 4px 12px; }
QFrame[popsep="true"] { background: $border; max-height: 1px; border: none; margin: 4px 8px; }

/* ---- thẻ, nhãn ---- */
QFrame[card="true"] { background: $surface; border: 1px solid $border; border-radius: 16px; }
QFrame[chip="true"] { border: 1px solid $border; border-radius: 17px; background: $surface2; }
QFrame[chip="true"][missing="true"] { border-color: $err; }
QFrame[cardsep="true"] { background: $border; border: none; min-height: 1px; max-height: 1px; }
QFrame[card="true"] QTableWidget { border: none; background: transparent; border-radius: 0; padding: 0; }
QLabel[caption="true"] { color: $muted; font-size: 12px; }
QLabel[heading="true"] { font-family: $serif; font-size: 20px; font-weight: 600; color: $text; }
QLabel[subheading="true"] { font-family: $serif; font-size: 15px; font-weight: 600; color: $text; }
QLabel[level="error"] { color: $err; font-weight: 600; }
QLabel[level="warn"] { color: $warn; }
QLabel[level="ok"] { color: $ok; }
QPushButton[navbtn="true"] { text-align: left; padding: 0; background: $surface; }
QPushButton[navbtn="true"]:hover { background: $surface2; }
QLabel[dropzone="true"] { border: 2px dashed $border_strong; border-radius: 14px; color: $muted; background: transparent; }
QLabel[mono="true"] { font-family: Menlo, Consolas, 'Courier New', monospace; font-size: 12px; background: $surface2;
    border: 1px solid $border; border-radius: 10px; padding: 12px; color: $text; }
QWidget[welcome="true"] { background: $bg; }
QFrame[stepRow="true"] { background: $surface; border: 1px solid $border; border-radius: 14px; }
QLabel[stepnum="true"] { background: $accent_tint; color: $accent; border-radius: 14px; font-weight: 700; }
QLabel[stepnum="true"][done="true"] { background: $ok_tint; color: $ok; }
QLabel[pill="info"] { color: $muted; background: $surface2; border-radius: 10px; padding: 3px 10px; }
QLabel[pill="ok"] { color: $ok; background: $ok_tint; border-radius: 10px; padding: 3px 10px; }
QLabel[pill="err"] { color: $err; background: $err_tint; border-radius: 10px; padding: 3px 10px; }
QLabel[pill="warn"] { color: $warn; background: $warn_tint; border-radius: 10px; padding: 3px 10px; }
QLabel[avatarph="true"] { background: $surface2; border: 1px dashed $border_strong; border-radius: 16px; color: $faint; }
QLabel[videoph="true"] { background: $video; color: $faint; border-radius: 14px; }

/* ---- chip môi trường (thanh trạng thái) ---- */
QPushButton[chipbtn="true"] { border-radius: 13px; padding: 3px 14px; font-size: 12px; min-height: 18px; background: transparent; color: $muted; }
QPushButton[chipbtn="true"][state="ok"] { color: $ok; border-color: $ok; background: $ok_tint; }
QPushButton[chipbtn="true"][state="warn"] { color: $warn; border-color: $warn; background: $warn_tint; }

/* ---- tab ---- */
QTabWidget::pane { border: none; top: 12px; }
QTabBar::tab { border: 1px solid transparent; padding: 7px 16px; margin-right: 4px; border-radius: 10px; color: $muted; background: transparent; }
QTabBar::tab:selected { background: $surface; color: $text; border: 1px solid $border; font-weight: 600; }
QTabBar::tab:hover:!selected { background: $surface2; color: $text; }

/* ---- bảng ---- */
QTableWidget { background: $surface; color: $text; border: 1px solid $border; border-radius: 14px; padding: 4px;
    gridline-color: transparent; outline: 0; selection-background-color: $sel; selection-color: $text; }
QTableWidget::item { padding: 6px 8px; border: none; }
QTableWidget::item:focus { border: none; outline: 0; }
QTableWidget::item:selected { background: $sel; color: $text; }
QHeaderView::section { background: transparent; color: $muted; border: none; border-bottom: 1px solid $border;
    padding: 6px 8px; font-weight: 600; font-size: 12px; }
QTableCornerButton::section { background: transparent; border: none; }

/* ---- thanh cuộn, tiến độ, thanh trượt ---- */
QScrollBar:vertical { width: 12px; margin: 6px 3px 6px 0; background: transparent; border: none; }
QScrollBar:horizontal { height: 12px; margin: 0 6px 3px 6px; background: transparent; border: none; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 3px; min-height: 28px; margin: 0 1px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 3px; min-width: 28px; margin: 1px 0; }
QScrollBar::handle:hover { background: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 10px; }
QSplitter::handle:vertical { height: 10px; }
QScrollArea { border: none; background: transparent; }
QProgressBar { border: none; border-radius: 4px; background: $surface2; }
QProgressBar::chunk { border-radius: 4px; background: $accent; }
QSlider::groove:horizontal { height: 4px; background: $border_strong; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: $accent; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator, QTableView::indicator, QListView::indicator { width: 16px; height: 16px; border: 1.5px solid $border_strong; border-radius: 5px; background: $surface; }
QCheckBox::indicator:hover, QTableView::indicator:hover, QListView::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked { background: $accent; border-color: $accent; image: url($check_png); }
QCheckBox::indicator:disabled, QTableView::indicator:disabled, QListView::indicator:disabled { background: $surface2; border-color: $border; }
QCheckBox::indicator:checked:disabled, QTableView::indicator:checked:disabled, QListView::indicator:checked:disabled { background: $accent_dis; border-color: transparent; image: url($check_png); }
QMessageBox, QDialog { background: $bg; }
QMessageBox { background: $surface; }
QMessageBox QLabel { color: $text; background: transparent; font-weight: 400; font-size: 13px; }
QMessageBox QLabel#qt_msgbox_label, QMessageBox QLabel#qt_msgbox_informativelabel { min-width: 340px; }
QMessageBox QLabel#qt_msgbox_label { font-size: 14px; }
QMessageBox QPushButton { min-width: 96px; min-height: 34px; }
QPlainTextEdit#logOverlay { background: $surface; border: 1px solid $border_strong; border-radius: 14px; padding: 10px; }
""")


def tokens(dark: bool) -> dict[str, str]:
    return dict(DARK if dark else LIGHT)


def is_dark(app: QGuiApplication | None = None) -> bool:
    app = app or QGuiApplication.instance()
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def _glyph_png(name: str, color: str, size: int = 32) -> str:
    """Ảnh icon nhỏ cho QSS (chỉ nhận ảnh từ file): vẽ một lần vào thư mục tạm, tên file gồm tên icon + màu nên đổi sáng/tối không lẫn nhau."""
    import tempfile
    from pathlib import Path
    from . import icons
    f = Path(tempfile.gettempdir()) / f"kevit-{name}-{color.lstrip('#').replace('(', '').replace(')', '').replace(',', '')}.png"
    if not f.exists() or f.stat().st_size == 0:
        icons.pixmap(name, size, color, dpr=1.0).save(str(f), "PNG")
    return f.as_posix()


def stylesheet(tok: dict[str, str]) -> str:
    return QSS.substitute(**tok, serif=SERIF, check_png=_glyph_png("check", "#FFFFFF"),
                          chev_down_png=_glyph_png("down", tok["muted"]), chev_down_dis_png=_glyph_png("down", tok["faint"]),
                          chev_up_png=_glyph_png("up", tok["muted"]), chev_left_png=_glyph_png("left", tok["text"]), chev_right_png=_glyph_png("right", tok["text"]), chev_up_dis_png=_glyph_png("up", tok["faint"]))


def palette(tok: dict[str, str]) -> QPalette:
    p = QPalette()
    c = QColor
    roles = {
        QPalette.Window: tok["bg"], QPalette.WindowText: tok["text"], QPalette.Base: tok["surface"],
        QPalette.AlternateBase: tok["surface2"], QPalette.Text: tok["text"], QPalette.Button: tok["surface"],
        QPalette.ButtonText: tok["text"], QPalette.ToolTipBase: tok["surface"], QPalette.ToolTipText: tok["text"],
        QPalette.PlaceholderText: tok["faint"], QPalette.Highlight: tok["accent"], QPalette.HighlightedText: tok["on_accent"],
        QPalette.Link: tok["accent"], QPalette.BrightText: tok["err"],
    }
    for role, col in roles.items():
        p.setColor(role, c(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(tok["faint"]))
    return p


class AppStyle(QProxyStyle):
    """Fusion + khoảng cách mặc định theo thang: layout nào chưa đặt giá trị sẽ có spacing 8 và lề 0 thay vì 6 và 9 của Qt.
    Hộp thoại hệ thống (QMessageBox, QFileDialog...) giữ nguyên số đo gốc để không bị chật."""
    _NATIVE = (QMessageBox, QFileDialog, QInputDialog)
    _MARGINS = (QStyle.PM_LayoutLeftMargin, QStyle.PM_LayoutTopMargin, QStyle.PM_LayoutRightMargin, QStyle.PM_LayoutBottomMargin)
    _SPACING = (QStyle.PM_LayoutHorizontalSpacing, QStyle.PM_LayoutVerticalSpacing)

    def __init__(self):
        super().__init__("Fusion")

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ComboBox_Popup:              # False: danh sách xổ ngay BÊN DƯỚI ô, không đè lên nó kiểu macOS
            return 0
        return super().styleHint(hint, option, widget, returnData)

    def pixelMetric(self, metric, option=None, widget=None):
        if not (widget is not None and isinstance(widget.window(), self._NATIVE)):
            if metric in self._MARGINS:
                return 0
            if metric in self._SPACING:
                return SP.s
        return super().pixelMetric(metric, option, widget)


_style_keepalive = []


def apply(app, force_dark: bool | None = None) -> None:
    """Áp dụng giao diện; gọi lại khi hệ điều hành đổi sáng/tối. force_dark dùng cho kiểm thử."""
    dark = is_dark(app) if force_dark is None else force_dark
    T.clear()
    T.update(tokens(dark))
    style = AppStyle()
    _style_keepalive.append(style)
    app.setStyle(style)
    app.setPalette(palette(T))
    app.setStyleSheet(stylesheet(T))
    for cb in _listeners:
        cb()


_listeners: list = []


def on_change(cb) -> None:
    """Đăng ký hàm được gọi sau khi đổi giao diện (để làm mới các màu tính bằng code)."""
    _listeners.append(cb)


def install(app) -> None:
    apply(app)
    app.styleHints().colorSchemeChanged.connect(lambda *_: apply(app))
    from . import dialogs                 # nhập muộn: dialogs cần icons/theme đã sẵn sàng
    dialogs.install(app)

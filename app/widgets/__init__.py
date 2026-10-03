"""Thành phần giao diện dùng chung (xem từng module con)."""
from __future__ import annotations

from .basic import repolish, avatar, rounded_pixmap, ElidedLabel, Combo
from .pickers import CharPickDialog, CharPicker
from .strip import StatusStrip, Segmented, SegStack
from .popover import PopItem, Popover, popover_button
from .cards import FOOTER_H, HEADER_H, card_bar, card_body, card_sep, make_card, NavButton

__all__ = ['FOOTER_H', 'HEADER_H', 'CharPickDialog', 'CharPicker', 'Combo', 'ElidedLabel', 'NavButton', 'PopItem', 'Popover', 'SegStack', 'Segmented', 'StatusStrip', 'avatar', 'card_bar', 'card_body', 'card_sep', 'make_card', 'popover_button', 'repolish', 'rounded_pixmap']

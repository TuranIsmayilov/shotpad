"""Application palette and stylesheet.

Shotpad forces the Fusion style and paints its own chrome. That is deliberate:
a Qt app inheriting GTK colours on XFCE, Breeze on KDE and nothing coherent on
MATE ends up looking broken in at least one of them. One self-contained theme
that follows only the light/dark preference gives the same app everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str          # window background
    bg_alt: str      # sidebars / panels
    bg_sunken: str   # canvas well
    surface: str     # cards, inputs
    surface_hi: str  # hover
    border: str
    text: str
    text_dim: str
    accent: str
    accent_text: str
    success: str      # "that worked" confirmation fill
    success_text: str
    danger: str


LIGHT = Palette(
    dark=False,
    bg="#f6f6f8",
    bg_alt="#ffffff",
    bg_sunken="#e8e8ed",
    surface="#ffffff",
    surface_hi="#eeeef2",
    border="#d8d8e0",
    text="#1b1b20",
    text_dim="#6b6b76",
    accent="#5b5bd6",
    accent_text="#ffffff",
    success="#2f9e63",
    success_text="#ffffff",
    danger="#d33c3c",
)

DARK = Palette(
    dark=True,
    bg="#1b1b1f",
    bg_alt="#212127",
    bg_sunken="#141417",
    surface="#2a2a31",
    surface_hi="#34343d",
    border="#3a3a44",
    text="#ececf0",
    text_dim="#9a9aa6",
    accent="#7c7cf0",
    accent_text="#ffffff",
    success="#35a76b",
    success_text="#ffffff",
    danger="#e5484d",
)

_current: Palette = DARK


def current() -> Palette:
    return _current


def system_prefers_dark(app: QApplication) -> bool:
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        try:
            return scheme() == Qt.ColorScheme.Dark
        except Exception:
            pass
    # Fall back to the palette the platform theme gave us.
    window = app.palette().color(QPalette.ColorRole.Window)
    return window.lightnessF() < 0.5


def _qt_palette(p: Palette) -> QPalette:
    palette = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    palette.setColor(role.Window, QColor(p.bg))
    palette.setColor(role.WindowText, QColor(p.text))
    palette.setColor(role.Base, QColor(p.surface))
    palette.setColor(role.AlternateBase, QColor(p.bg_alt))
    palette.setColor(role.Text, QColor(p.text))
    palette.setColor(role.Button, QColor(p.surface))
    palette.setColor(role.ButtonText, QColor(p.text))
    palette.setColor(role.BrightText, QColor(p.danger))
    palette.setColor(role.Highlight, QColor(p.accent))
    palette.setColor(role.HighlightedText, QColor(p.accent_text))
    palette.setColor(role.ToolTipBase, QColor(p.surface))
    palette.setColor(role.ToolTipText, QColor(p.text))
    palette.setColor(role.PlaceholderText, QColor(p.text_dim))
    palette.setColor(role.Link, QColor(p.accent))

    for g in (group.Disabled,):
        palette.setColor(g, role.Text, QColor(p.text_dim))
        palette.setColor(g, role.ButtonText, QColor(p.text_dim))
        palette.setColor(g, role.WindowText, QColor(p.text_dim))
    return palette


def stylesheet(p: Palette) -> str:
    return f"""
* {{ outline: none; }}

QWidget {{
    color: {p.text};
    font-size: 10pt;
}}
QMainWindow, QDialog {{ background: {p.bg}; }}

#Sidebar, #ToolRail, #HeaderBar {{ background: {p.bg_alt}; }}
#Sidebar {{ border-left: 1px solid {p.border}; }}
#ToolRail {{ border-right: 1px solid {p.border}; }}
#HeaderBar {{ border-bottom: 1px solid {p.border}; }}
#CanvasWell {{ background: {p.bg_sunken}; }}

QLabel#SectionTitle {{
    color: {p.text_dim};
    font-size: 8.5pt;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    padding: 2px 0px;
}}
QLabel#FieldLabel {{ color: {p.text_dim}; font-size: 9pt; }}
QLabel#ValueLabel {{ color: {p.text}; font-size: 9pt; font-weight: 600; }}
QLabel#Hint {{ color: {p.text_dim}; font-size: 9pt; }}
QLabel#BigTitle {{ font-size: 19pt; font-weight: 700; }}
/* Dialog titles sit in our own title bar, so they carry the weight the
   window manager's title would have had. */
QLabel#DialogTitle {{ font-size: 9.5pt; font-weight: 600; }}

QPushButton {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 6px 12px;
    color: {p.text};
}}
QPushButton:hover {{ background: {p.surface_hi}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.text_dim}; background: {p.bg_alt}; }}
QPushButton:checked {{
    background: {p.accent};
    border-color: {p.accent};
    color: {p.accent_text};
}}
QPushButton#Primary {{
    background: {p.accent};
    border-color: {p.accent};
    color: {p.accent_text};
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {QColor(p.accent).lighter(115).name()}; }}
/* Momentary "done" state, set as a dynamic property by ActionButton.flash().
   Listed after #Primary so it wins the tie on the Save button too. */
QPushButton[flash="true"], QPushButton#Primary[flash="true"] {{
    background: {p.success};
    border-color: {p.success};
    color: {p.success_text};
    font-weight: 600;
}}
QPushButton[flash="true"]:hover, QPushButton#Primary[flash="true"]:hover {{
    background: {QColor(p.success).lighter(112).name()};
    border-color: {QColor(p.success).lighter(112).name()};
}}
QPushButton#Flat {{ background: transparent; border: none; padding: 6px; }}
QPushButton#Flat:hover {{ background: {p.surface_hi}; }}
QPushButton#Flat:checked {{ background: {p.accent}; }}
QPushButton#ToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 8px;
}}
QPushButton#ToolButton:hover {{ background: {p.surface_hi}; }}
QPushButton#ToolButton:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}
QPushButton::menu-indicator {{ image: none; width: 0px; }}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px;
}}
QToolButton:hover {{ background: {p.surface_hi}; }}
QToolButton:checked {{ background: {p.accent}; }}
QToolButton::menu-indicator {{ image: none; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 5px 8px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 4px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; }}

QSlider::groove:horizontal {{
    height: 4px;
    background: {p.border};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p.surface};
    border: 2px solid {p.accent};
    width: 13px;
    height: 13px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {p.accent}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {p.border};
    border-radius: 4px;
    background: {p.surface};
}}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p.border}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_dim}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {p.border}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 5px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QToolTip {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 4px 7px;
}}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent;
    color: {p.text_dim};
    padding: 7px 13px;
    border-radius: 7px;
    margin-right: 3px;
}}
QTabBar::tab:selected {{ background: {p.surface}; color: {p.text}; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}

QStatusBar {{ background: {p.bg_alt}; border-top: 1px solid {p.border}; }}
QStatusBar::item {{ border: none; }}

QFrame#Separator {{ background: {p.border}; max-height: 1px; border: none; }}
QFrame#VSeparator {{ background: {p.border}; max-width: 1px; border: none; }}
QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
"""


def apply_theme(app: QApplication, mode: str = "auto") -> Palette:
    """mode: auto | light | dark."""
    global _current
    if mode == "light":
        dark = False
    elif mode == "dark":
        dark = True
    else:
        dark = system_prefers_dark(app)

    palette = DARK if dark else LIGHT
    _current = palette

    app.setStyle("Fusion")
    app.setPalette(_qt_palette(palette))
    app.setStyleSheet(stylesheet(palette))
    return palette

"""The start screen shown when no image is loaded."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..capture import desktop_environment, session_type
from ..icons import icon as make_icon
from ..theme import current as current_theme


class _BigButton(QFrame):
    clicked = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str, shortcut: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(96)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        theme = current_theme()
        glyph = QLabel()
        glyph.setPixmap(
            make_icon(icon_name, theme.accent, 30).pixmap(30, 30)
        )
        glyph.setFixedWidth(34)
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(glyph)

        text = QVBoxLayout()
        text.setSpacing(3)
        heading = QLabel(title)
        heading_font = QFont()
        heading_font.setPointSizeF(11.5)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        caption = QLabel(subtitle)
        caption.setObjectName("Hint")
        caption.setWordWrap(True)
        text.addWidget(heading)
        text.addWidget(caption)
        layout.addLayout(text, 1)

        keys = QLabel(shortcut)
        keys.setObjectName("Hint")
        keys.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout.addWidget(keys)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomeView(QWidget):
    capture_area = Signal()
    capture_screen = Signal()
    capture_window = Signal()
    open_file = Signal()
    paste = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasWell")
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 30, 40, 30)
        outer.addStretch(1)

        title = QLabel("Shotpad")
        title.setObjectName("BigTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        subtitle = QLabel("Capture, annotate and make it presentable.")
        subtitle.setObjectName("Hint")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(subtitle)
        outer.addSpacing(22)

        grid_host = QWidget()
        grid_host.setMaximumWidth(720)
        grid = QGridLayout(grid_host)
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)

        buttons = [
            ("select-area", "Select an area", "Drag a region, or click a window",
             "Ctrl+Shift+A", self.capture_area),
            ("monitor", "Whole screen", "Everything on your displays",
             "Ctrl+Shift+F", self.capture_screen),
            ("window", "A window", "Pick a single application window",
             "Ctrl+Shift+W", self.capture_window),
            ("folder", "Open an image", "Edit a picture you already have",
             "Ctrl+O", self.open_file),
            ("clipboard", "Paste", "Use the image on your clipboard",
             "Ctrl+V", self.paste),
        ]
        for index, (icon_name, heading, caption, keys, signal) in enumerate(buttons):
            button = _BigButton(icon_name, heading, caption, keys)
            button.clicked.connect(signal.emit)
            grid.addWidget(button, index // 2, index % 2)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(grid_host)
        row.addStretch(1)
        outer.addLayout(row)

        outer.addSpacing(18)
        self.env_label = QLabel(
            f"Running on {desktop_environment().upper()} / {session_type()}   -   "
            "you can also drop an image anywhere in this window"
        )
        self.env_label.setObjectName("Hint")
        self.env_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.env_label)
        outer.addStretch(2)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(current_theme().bg_sunken))

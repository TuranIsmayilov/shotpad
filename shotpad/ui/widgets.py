"""Small reusable controls used to build the sidebar and toolbars."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon as make_icon
from ..theme import current as current_theme


def separator(vertical: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("VSeparator" if vertical else "Separator")
    if vertical:
        frame.setFrameShape(QFrame.Shape.VLine)
        frame.setFixedWidth(1)
    else:
        frame.setFrameShape(QFrame.Shape.HLine)
        frame.setFixedHeight(1)
    return frame


def repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed.

    Qt only matches property selectors when the widget is polished, so a
    property set after construction has no effect until we ask for this.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def themed_icon(name: str, size: int = 20, accent: bool = False) -> QIcon:
    theme = current_theme()
    return make_icon(name, theme.accent if accent else theme.text, size)


class IconButton(QPushButton):
    def __init__(
        self,
        icon_name: str,
        tooltip: str = "",
        checkable: bool = False,
        size: int = 20,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.setObjectName("ToolButton")
        self.setCheckable(checkable)
        self.setIconSize(QSize(size, size))
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        theme = current_theme()
        color = "#ffffff" if self.isChecked() else theme.text
        self.setIcon(make_icon(self.icon_name, color, self.iconSize().width()))

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt naming
        super().setChecked(checked)
        self.refresh_icon()

    def nextCheckState(self) -> None:  # noqa: N802 - Qt naming
        super().nextCheckState()
        self.refresh_icon()


class ActionButton(QPushButton):
    """A labelled toolbar button that can confirm what it just did.

    Copy and Save finish without any visible change to the image, and the
    status bar line is easy to miss, so the button briefly turns green and
    reads "Copied" / "Saved" instead.
    """

    FLASH_MS = 1400

    def __init__(
        self,
        label: str,
        icon_name: str,
        done_label: str,
        primary: bool = False,
        size: int = 17,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self._label = f"  {label}"
        self._done_label = f"  {done_label}"
        self._primary = primary
        self._icon_size = size
        if primary:
            self.setObjectName("Primary")
        self.setIconSize(QSize(size, size))
        self.setMinimumHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        self.setText(self._label)
        self.refresh_icon()
        # Reserve the width of the longer, bold confirmation label up front so
        # the toolbar does not shuffle sideways when it appears.
        self.setMinimumWidth(self._hint_width(self._done_label, bold=True))

    def _hint_width(self, text: str, bold: bool = False) -> int:
        font = self.font()
        font.setBold(bold or self._primary)
        metrics = QFontMetrics(font)
        return metrics.horizontalAdvance(text) + self._icon_size + 34

    def refresh_icon(self) -> None:
        theme = current_theme()
        if self.property("flash"):
            name, color = "check", theme.success_text
        else:
            name = self.icon_name
            color = theme.accent_text if self._primary else theme.text
        self.setIcon(make_icon(name, color, self._icon_size))

    def flash(self) -> None:
        """Show the confirmation state, restarting the timer if already shown."""
        self.setText(self._done_label)
        self.setProperty("flash", True)
        repolish(self)
        self._flash_timer.start(self.FLASH_MS)
        self.refresh_icon()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt naming
        # The flash fill outranks the disabled rule, so drop it rather than
        # leave a greyed-out button looking bright green.
        if not enabled and self.property("flash"):
            self._end_flash()
        super().setEnabled(enabled)

    def _end_flash(self) -> None:
        self._flash_timer.stop()
        self.setText(self._label)
        self.setProperty("flash", False)
        repolish(self)
        self.refresh_icon()


class SectionTitle(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("SectionTitle")


class SliderRow(QWidget):
    """Label + slider + live value readout."""

    valueChanged = Signal(float)

    def __init__(
        self,
        label: str,
        minimum: float,
        maximum: float,
        value: float,
        step: float = 1.0,
        suffix: str = "",
        decimals: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._step = step
        self._suffix = suffix
        self._decimals = decimals

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.label.setObjectName("FieldLabel")
        self.value_label = QLabel()
        self.value_label.setObjectName("ValueLabel")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.label)
        header.addStretch(1)
        header.addWidget(self.value_label)
        layout.addLayout(header)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round((maximum - minimum) / step)))
        self.slider.setValue(int(round((value - minimum) / step)))
        self.slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self.slider)

        self._update_label(value)

    def _raw(self, ticks: int) -> float:
        return self._min + ticks * self._step

    def _update_label(self, value: float) -> None:
        text = f"{value:.{self._decimals}f}{self._suffix}"
        self.value_label.setText(text)

    def _on_slider(self, ticks: int) -> None:
        value = self._raw(ticks)
        self._update_label(value)
        self.valueChanged.emit(value)

    def value(self) -> float:
        return self._raw(self.slider.value())

    def setValue(self, value: float) -> None:  # noqa: N802 - Qt naming
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(int(round((value - self._min) / self._step)))
        self.slider.blockSignals(blocked)
        self._update_label(value)


def color_swatch_pixmap(color: QColor, size: int = 18, dpr: float = 1.0) -> QPixmap:
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if color.alpha() < 255:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRoundedRect(0, 0, size, size, 5, 5)
        painter.setBrush(QColor(200, 200, 208))
        painter.drawRect(0, 0, size // 2, size // 2)
        painter.drawRect(size // 2, size // 2, size // 2, size // 2)
    painter.setPen(QColor(0, 0, 0, 70))
    painter.setBrush(color)
    painter.drawRoundedRect(0, 0, size - 1, size - 1, 5, 5)
    painter.end()
    return pixmap


class ColorButton(QPushButton):
    """A swatch that opens a colour picker, with optional alpha."""

    colorChanged = Signal(QColor)

    def __init__(
        self,
        color: QColor,
        with_alpha: bool = False,
        label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._with_alpha = with_alpha
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        if label:
            self.setText("  " + label)
            self.setMinimumWidth(96)
        else:
            self.setFixedWidth(38)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self) -> None:
        self.setIcon(QIcon(color_swatch_pixmap(self._color, 18, 2.0)))
        self.setIconSize(QSize(18, 18))
        self.setToolTip(self._color.name(QColor.NameFormat.HexArgb))

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, color: QColor, emit: bool = True) -> None:  # noqa: N802
        if color is None or not color.isValid():
            return
        self._color = QColor(color)
        self._refresh()
        if emit:
            self.colorChanged.emit(self.color())

    def _pick(self) -> None:
        options = QColorDialog.ColorDialogOption.DontUseNativeDialog
        if self._with_alpha:
            options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
        chosen = QColorDialog.getColor(
            self._color, self, "Choose a colour", options
        )
        if chosen.isValid():
            self.setColor(chosen)


class SwatchGrid(QWidget):
    """A clickable grid of preset colours."""

    picked = Signal(QColor)

    def __init__(
        self, colors: list[str], columns: int = 8, size: int = 22,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self._columns = columns
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(5)
        self.set_colors(colors)

    def set_colors(self, colors: list[str]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, value in enumerate(colors):
            color = QColor(value)
            button = QPushButton()
            button.setObjectName("Flat")
            button.setFixedSize(self._size, self._size)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(QIcon(color_swatch_pixmap(color, self._size - 4, 2.0)))
            button.setIconSize(QSize(self._size - 4, self._size - 4))
            button.setToolTip(color.name())
            button.clicked.connect(
                lambda _=False, c=QColor(color): self.picked.emit(c)
            )
            self._layout.addWidget(
                button, index // self._columns, index % self._columns
            )


class GradientSwatchGrid(QWidget):
    """Preset gradient thumbnails."""

    picked = Signal(object)

    def __init__(self, presets, columns: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        for index, preset in enumerate(presets):
            name, c1, c2, angle = preset
            button = QPushButton()
            button.setObjectName("Flat")
            button.setFixedHeight(34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(name)
            button.setStyleSheet(
                "QPushButton {"
                f" border-radius: 7px; border: 1px solid rgba(0,0,0,0.22);"
                f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                f" stop:0 {c1}, stop:1 {c2}); }}"
                "QPushButton:hover { border: 2px solid palette(highlight); }"
            )
            button.clicked.connect(lambda _=False, p=preset: self.picked.emit(p))
            layout.addWidget(button, index // columns, index % columns)


class SegmentedControl(QWidget):
    """A row of mutually exclusive buttons - lighter than a combo box."""

    changed = Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        value: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._buttons: dict[str, QPushButton] = {}

        for key, label in options:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            button.clicked.connect(lambda _=False, k=key: self.setValue(k))
            layout.addWidget(button)
            self._buttons[key] = button

        self.setValue(value or options[0][0], emit=False)

    def value(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return ""

    def setValue(self, key: str, emit: bool = True) -> None:  # noqa: N802
        if key not in self._buttons:
            return
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        if emit:
            self.changed.emit(key)


class Card(QFrame):
    """A padded container used to group sidebar controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(11, 10, 11, 11)
        self.body.setSpacing(9)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

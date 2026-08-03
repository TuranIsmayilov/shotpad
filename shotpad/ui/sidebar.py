"""Right-hand inspector: tool options, image framing, and background."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..annotations import (
    Annotation,
    ArrowAnn,
    EllipseAnn,
    LineAnn,
    NumberAnn,
    RectAnn,
    RedactAnn,
    TextAnn,
)
from ..model import ASPECT_PRESETS, GRADIENT_PRESETS, SOLID_PRESETS, Document
from ..settings import settings
from .canvas import Canvas, ToolStyle
from .widgets import (
    Card,
    ColorButton,
    GradientSwatchGrid,
    SectionTitle,
    SegmentedControl,
    SliderRow,
    SwatchGrid,
)

TOOL_PALETTE = [
    "#e5484d", "#f76808", "#ffb224", "#46a758", "#12a594",
    "#0091ff", "#3e63dd", "#8e4ec6", "#e93d82", "#ffffff",
    "#8b8d98", "#1c2024",
]


class Sidebar(QScrollArea):
    changed = Signal()          # something that affects rendering changed
    style_changed = Signal()    # the drawing style changed

    def __init__(self, canvas: Canvas, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.setObjectName("Sidebar")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedWidth(292)

        container = QWidget()
        container.setObjectName("Sidebar")
        self.root = QVBoxLayout(container)
        self.root.setContentsMargins(12, 12, 12, 14)
        self.root.setSpacing(12)
        self.setWidget(container)

        self._building = True
        self._build_tool_card()
        self._build_image_card()
        self._build_background_card()
        self.root.addStretch(1)
        self._building = False

        self.sync_from_settings()
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.update_for_tool(canvas.tool)

    # ------------------------------------------------------------------ tool
    def _build_tool_card(self) -> None:
        self.root.addWidget(SectionTitle("Tool"))
        card = Card()
        self.tool_card = card
        style = self.canvas.style

        self.tool_name = QLabel("Select")
        self.tool_name.setObjectName("ValueLabel")
        card.add(self.tool_name)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.color_button = ColorButton(style.color, label="Colour")
        self.color_button.colorChanged.connect(self._on_color)
        row.addWidget(self.color_button)
        row.addStretch(1)
        card.add_layout(row)

        self.palette_grid = SwatchGrid(TOOL_PALETTE, columns=6, size=24)
        self.palette_grid.picked.connect(self._on_color)
        card.add(self.palette_grid)

        self.recent_label = QLabel("Recent")
        self.recent_label.setObjectName("FieldLabel")
        card.add(self.recent_label)
        self.recent_grid = SwatchGrid([], columns=6, size=20)
        self.recent_grid.picked.connect(self._on_color)
        card.add(self.recent_grid)

        self.width_slider = SliderRow("Stroke width", 1, 48, style.width, 1, " px")
        self.width_slider.valueChanged.connect(self._on_width)
        card.add(self.width_slider)

        self.fill_check = QCheckBox("Filled shape")
        self.fill_check.toggled.connect(self._on_fill)
        card.add(self.fill_check)

        self.dash_check = QCheckBox("Dashed line")
        self.dash_check.toggled.connect(self._on_dash)
        card.add(self.dash_check)

        self.double_check = QCheckBox("Arrowhead at both ends")
        self.double_check.toggled.connect(self._on_double)
        card.add(self.double_check)

        self.arrow_taper = SliderRow(
            "Tail thickness", 10, 100, style.arrow_taper * 100, 5, "%"
        )
        self.arrow_taper.setToolTip(
            "How thin the arrow starts. 100% gives an even shaft."
        )
        self.arrow_taper.valueChanged.connect(self._on_arrow_taper)
        card.add(self.arrow_taper)

        self.shape_radius = SliderRow("Corner radius", 0, 60, 0, 1, " px")
        self.shape_radius.valueChanged.connect(self._on_shape_radius)
        card.add(self.shape_radius)

        self.font_box = QFontComboBox()
        self.font_box.setCurrentText(style.font_family)
        self.font_box.currentFontChanged.connect(self._on_font)
        card.add(self.font_box)

        self.font_size = SliderRow("Text size", 8, 160, style.font_size, 1, " px")
        self.font_size.valueChanged.connect(self._on_font_size)
        card.add(self.font_size)

        text_row = QHBoxLayout()
        text_row.setSpacing(6)
        self.bold_check = QCheckBox("Bold")
        self.bold_check.toggled.connect(self._on_bold)
        self.italic_check = QCheckBox("Italic")
        self.italic_check.toggled.connect(self._on_italic)
        text_row.addWidget(self.bold_check)
        text_row.addWidget(self.italic_check)
        text_row.addStretch(1)
        card.add_layout(text_row)

        self.text_bg_check = QCheckBox("Label background")
        self.text_bg_check.toggled.connect(self._on_text_bg)
        card.add(self.text_bg_check)

        self.redact_mode = SegmentedControl(
            [("blur", "Blur"), ("pixelate", "Pixelate"), ("solid", "Block")],
            style.redact_mode,
        )
        self.redact_mode.changed.connect(self._on_redact_mode)
        card.add(self.redact_mode)

        self.redact_strength = SliderRow(
            "Strength", 2, 60, style.redact_strength, 1, ""
        )
        self.redact_strength.valueChanged.connect(self._on_redact_strength)
        card.add(self.redact_strength)

        self.number_size = SliderRow("Badge size", 8, 90, style.number_radius, 1, " px")
        self.number_size.valueChanged.connect(self._on_number_size)
        card.add(self.number_size)

        self.select_hint = QLabel("Click something you have drawn to restyle it.")
        self.select_hint.setObjectName("FieldLabel")
        self.select_hint.setWordWrap(True)
        card.add(self.select_hint)

        self.root.addWidget(card)

    def update_for_tool(self, tool: str) -> None:
        titles = {
            "select": "Select and move",
            "pen": "Pen",
            "highlighter": "Highlighter",
            "line": "Line",
            "arrow": "Arrow",
            "rect": "Rectangle",
            "ellipse": "Ellipse",
            "text": "Text",
            "number": "Numbered badge",
            "redact": "Redact",
            "eraser": "Eraser",
            "crop": "Crop",
        }
        self.tool_name.setText(titles.get(tool, tool.title()))

        # With a drawing tool the controls describe what the *next* mark will
        # look like, so the tool decides which are relevant. With Select they
        # describe the annotation you clicked on, so that annotation decides -
        # a text box has no stroke width, an ellipse has no corner radius.
        ann = self.canvas.selected() if tool == "select" else None
        if tool == "select":
            self._show_controls_for_annotation(ann)
        else:
            self._show_controls_for_tool(tool)

        self.select_hint.setVisible(tool == "select" and ann is None)

    def _set_colour_controls_visible(self, visible: bool) -> None:
        self.color_button.setVisible(visible)
        self.palette_grid.setVisible(visible)
        recent = [c for c in settings.get("recent_colors", []) if QColor(c).isValid()]
        self.recent_grid.set_colors(recent[:6])
        self.recent_label.setVisible(visible and bool(recent))
        self.recent_grid.setVisible(visible and bool(recent))

    def _show_controls_for_tool(self, tool: str) -> None:
        stroke_tools = {"pen", "highlighter", "line", "arrow", "rect", "ellipse", "number"}
        self._set_colour_controls_visible(tool not in ("eraser", "crop"))

        self.width_slider.setVisible(tool in stroke_tools)
        self.fill_check.setVisible(tool in ("rect", "ellipse"))
        self.shape_radius.setVisible(tool == "rect")
        self.dash_check.setVisible(tool == "line")
        self.double_check.setVisible(tool == "arrow")
        self.arrow_taper.setVisible(tool == "arrow")

        text_tool = tool == "text"
        self.font_box.setVisible(text_tool)
        self.font_size.setVisible(text_tool)
        self.bold_check.setVisible(text_tool)
        self.italic_check.setVisible(text_tool)
        self.text_bg_check.setVisible(text_tool)

        self.redact_mode.setVisible(tool == "redact")
        self.redact_strength.setVisible(
            tool == "redact" and self.canvas.style.redact_mode != "solid"
        )
        self.number_size.setVisible(tool == "number")

    def _show_controls_for_annotation(self, ann: Annotation | None) -> None:
        """Show exactly the properties the selected annotation actually has.

        Mirrors what ``Canvas.apply_style_to_selected`` is willing to write, so
        no control is offered that would silently do nothing.
        """
        self._set_colour_controls_visible(ann is not None)

        # EllipseAnn subclasses RectAnn, so it inherits `filled` but its
        # `radius` is meaningless - hence the explicit exclusion below.
        is_rect_like = isinstance(ann, RectAnn)
        self.width_slider.setVisible(
            ann is not None and not isinstance(ann, (RedactAnn, TextAnn))
        )
        self.fill_check.setVisible(is_rect_like)
        self.shape_radius.setVisible(is_rect_like and not isinstance(ann, EllipseAnn))
        self.dash_check.setVisible(isinstance(ann, LineAnn))
        self.double_check.setVisible(isinstance(ann, ArrowAnn))
        self.arrow_taper.setVisible(isinstance(ann, ArrowAnn))

        is_text = isinstance(ann, TextAnn)
        self.font_box.setVisible(is_text)
        self.font_size.setVisible(is_text)
        self.bold_check.setVisible(is_text)
        self.italic_check.setVisible(is_text)
        self.text_bg_check.setVisible(is_text)

        is_redact = isinstance(ann, RedactAnn)
        self.redact_mode.setVisible(is_redact)
        self.redact_strength.setVisible(is_redact and ann.mode != "solid")
        self.number_size.setVisible(isinstance(ann, NumberAnn))

    # -- selection ----------------------------------------------------------
    def _on_selection_changed(self, ann: Annotation | None) -> None:
        if self.canvas.tool != "select":
            return
        if ann is not None:
            self._load_from_annotation(ann)
        self.update_for_tool("select")

    @staticmethod
    def _quietly(widget, setter, *args) -> None:
        """Set a control without its signal firing back as a user edit."""
        blocked = widget.blockSignals(True)
        try:
            setter(*args)
        finally:
            widget.blockSignals(blocked)

    def _load_from_annotation(self, ann: Annotation) -> None:
        """Fill the controls with the selected annotation's current values.

        Without this the panel would show the armed drawing style, so clicking
        a blue arrow while red is armed would show red - and the first nudge of
        any slider would repaint the arrow red.
        """
        self._building = True
        try:
            self.color_button.setColor(QColor(ann.color), emit=False)
            if not isinstance(ann, (RedactAnn, TextAnn)):
                self._quietly(self.width_slider, self.width_slider.setValue, ann.width)
            if isinstance(ann, RectAnn):
                self._quietly(self.fill_check, self.fill_check.setChecked, ann.filled)
                if not isinstance(ann, EllipseAnn):
                    self._quietly(
                        self.shape_radius, self.shape_radius.setValue, ann.radius
                    )
            if isinstance(ann, LineAnn):
                self._quietly(self.dash_check, self.dash_check.setChecked, ann.dashed)
            if isinstance(ann, ArrowAnn):
                self._quietly(
                    self.double_check, self.double_check.setChecked, ann.double_headed
                )
                self._quietly(
                    self.arrow_taper, self.arrow_taper.setValue, ann.taper * 100
                )
            if isinstance(ann, TextAnn):
                self._quietly(
                    self.font_box, self.font_box.setCurrentText, ann.font_family
                )
                self._quietly(self.font_size, self.font_size.setValue, ann.font_size)
                self._quietly(self.bold_check, self.bold_check.setChecked, ann.bold)
                self._quietly(
                    self.italic_check, self.italic_check.setChecked, ann.italic
                )
                self._quietly(
                    self.text_bg_check,
                    self.text_bg_check.setChecked,
                    ann.background is not None,
                )
            if isinstance(ann, RedactAnn):
                self.redact_mode.setValue(ann.mode, emit=False)
                self._quietly(
                    self.redact_strength, self.redact_strength.setValue, ann.strength
                )
            if isinstance(ann, NumberAnn):
                self._quietly(
                    self.number_size, self.number_size.setValue, ann.radius
                )
        finally:
            self._building = False

    # -- tool handlers ------------------------------------------------------
    def _style(self) -> ToolStyle:
        return self.canvas.style

    def _after_style_change(self) -> None:
        if self._building:
            return
        # With Select active, editing a control means editing the thing you
        # clicked on - no confirmation checkbox, the selection is the intent.
        if self.canvas.tool == "select" and self.canvas.selected() is not None:
            self.canvas.apply_style_to_selected()
        self.style_changed.emit()

    def _on_color(self, color: QColor) -> None:
        self._style().color = QColor(color)
        self.color_button.setColor(color, emit=False)
        settings.set("tool_color", color.name())
        settings.push_recent_color(color.name())
        self.recent_grid.set_colors(settings.get("recent_colors", [])[:6])
        self.recent_label.setVisible(True)
        self.recent_grid.setVisible(True)
        self._after_style_change()

    def _on_width(self, value: float) -> None:
        self._style().width = value
        settings.set("tool_width", value)
        self._after_style_change()

    def _on_fill(self, checked: bool) -> None:
        self._style().filled = checked
        self._after_style_change()

    def _on_dash(self, checked: bool) -> None:
        self._style().dashed = checked
        self._after_style_change()

    def _on_double(self, checked: bool) -> None:
        self._style().double_headed = checked
        self._after_style_change()

    def _on_arrow_taper(self, value: float) -> None:
        self._style().arrow_taper = value / 100.0
        settings.set("arrow_taper", value / 100.0)
        self._after_style_change()

    def _on_shape_radius(self, value: float) -> None:
        self._style().corner_radius = value
        self._after_style_change()

    def _on_font(self, font) -> None:
        self._style().font_family = font.family()
        settings.set("font_family", font.family())
        self._after_style_change()

    def _on_font_size(self, value: float) -> None:
        self._style().font_size = value
        settings.set("font_size", value)
        self._after_style_change()

    def _on_bold(self, checked: bool) -> None:
        self._style().bold = checked
        self._after_style_change()

    def _on_italic(self, checked: bool) -> None:
        self._style().italic = checked
        self._after_style_change()

    def _on_text_bg(self, checked: bool) -> None:
        self._style().text_background = checked
        self._after_style_change()

    def _on_redact_mode(self, mode: str) -> None:
        self._style().redact_mode = mode
        self.redact_strength.setVisible(mode != "solid")
        self._after_style_change()

    def _on_redact_strength(self, value: float) -> None:
        self._style().redact_strength = value
        self._after_style_change()

    def _on_number_size(self, value: float) -> None:
        self._style().number_radius = value
        self._after_style_change()

    # ----------------------------------------------------------------- image
    def _build_image_card(self) -> None:
        self.root.addWidget(SectionTitle("Image"))
        card = Card()
        frame = self.canvas.doc.frame

        self.padding_slider = SliderRow("Padding", 0, 200, frame.padding, 1, "")
        self.padding_slider.valueChanged.connect(
            lambda v: self._set_frame("padding", int(v))
        )
        card.add(self.padding_slider)

        self.radius_slider = SliderRow("Corner radius", 0, 100, frame.corner_radius, 1, "")
        self.radius_slider.valueChanged.connect(
            lambda v: self._set_frame("corner_radius", int(v))
        )
        card.add(self.radius_slider)

        self.shadow_slider = SliderRow("Shadow", 0, 100, frame.shadow_strength, 1, "%")
        self.shadow_slider.valueChanged.connect(
            lambda v: self._set_frame("shadow_strength", int(v))
        )
        card.add(self.shadow_slider)

        self.shadow_blur_slider = SliderRow("Shadow softness", 0, 100, frame.shadow_blur, 1, "")
        self.shadow_blur_slider.valueChanged.connect(
            lambda v: self._set_frame("shadow_blur", int(v))
        )
        card.add(self.shadow_blur_slider)

        self.shadow_offset_slider = SliderRow("Shadow offset", 0, 100, frame.shadow_offset, 1, "")
        self.shadow_offset_slider.valueChanged.connect(
            lambda v: self._set_frame("shadow_offset", int(v))
        )
        card.add(self.shadow_offset_slider)

        self.rotation_slider = SliderRow(
            "Tilt", -20, 20, frame.rotation, 0.5, "°", decimals=1
        )
        self.rotation_slider.valueChanged.connect(
            lambda v: self._set_frame("rotation", float(v))
        )
        card.add(self.rotation_slider)

        self.border_slider = SliderRow("Inner border", 0, 12, frame.border_width, 0.5, " px", decimals=1)
        self.border_slider.valueChanged.connect(
            lambda v: self._set_frame("border_width", float(v))
        )
        card.add(self.border_slider)

        aspect_row = QHBoxLayout()
        aspect_row.setSpacing(8)
        label = QLabel("Aspect")
        label.setObjectName("FieldLabel")
        self.aspect_box = QComboBox()
        for name, value in ASPECT_PRESETS:
            self.aspect_box.addItem(name, value)
        self.aspect_box.currentIndexChanged.connect(self._on_aspect)
        aspect_row.addWidget(label)
        aspect_row.addWidget(self.aspect_box, 1)
        card.add_layout(aspect_row)

        self.root.addWidget(card)

    def _set_frame(self, attribute: str, value) -> None:
        if self._building:
            return
        setattr(self.canvas.doc.frame, attribute, value)
        self.canvas.doc.touch_base()
        self.canvas.invalidate(full=True)
        self.changed.emit()

    def _on_aspect(self, index: int) -> None:
        if self._building:
            return
        self.canvas.doc.frame.aspect = self.aspect_box.itemData(index)
        self.canvas.doc.touch_base()
        self.canvas.invalidate(full=True)
        self.changed.emit()

    # ------------------------------------------------------------ background
    def _build_background_card(self) -> None:
        self.root.addWidget(SectionTitle("Background"))
        card = Card()
        background = self.canvas.doc.background

        self.bg_mode = SegmentedControl(
            [("linear", "Gradient"), ("solid", "Solid"), ("image", "Image")],
            background.kind if background.kind in ("linear", "solid", "image") else "linear",
        )
        self.bg_mode.changed.connect(self._on_bg_mode)
        card.add(self.bg_mode)

        self.gradient_shape = SegmentedControl(
            [("linear", "Linear"), ("radial", "Radial"), ("conical", "Conic")], "linear"
        )
        self.gradient_shape.changed.connect(self._on_gradient_shape)
        card.add(self.gradient_shape)

        self.gradient_grid = GradientSwatchGrid(GRADIENT_PRESETS, columns=4)
        self.gradient_grid.picked.connect(self._on_gradient_preset)
        card.add(self.gradient_grid)

        colors_row = QHBoxLayout()
        colors_row.setSpacing(8)
        self.bg_color1 = ColorButton(background.color1)
        self.bg_color1.colorChanged.connect(lambda c: self._set_bg("color1", c))
        self.bg_color2 = ColorButton(background.color2)
        self.bg_color2.colorChanged.connect(lambda c: self._set_bg("color2", c))
        swap = QPushButton("Swap")
        swap.clicked.connect(self._swap_bg_colors)
        colors_row.addWidget(self.bg_color1)
        colors_row.addWidget(self.bg_color2)
        colors_row.addWidget(swap, 1)
        card.add_layout(colors_row)

        self.bg_angle = SliderRow("Angle", 0, 360, background.angle, 5, "°")
        self.bg_angle.valueChanged.connect(lambda v: self._set_bg("angle", int(v)))
        card.add(self.bg_angle)

        self.solid_grid = SwatchGrid(SOLID_PRESETS, columns=8, size=22)
        self.solid_grid.picked.connect(lambda c: self._set_bg("color1", c))
        card.add(self.solid_grid)

        self.bg_image_button = QPushButton("Choose image…")
        self.bg_image_button.clicked.connect(self._pick_bg_image)
        card.add(self.bg_image_button)

        self.bg_image_blur = SliderRow("Image blur", 0, 60, background.image_blur, 1, "")
        self.bg_image_blur.valueChanged.connect(
            lambda v: self._set_bg("image_blur", float(v))
        )
        card.add(self.bg_image_blur)

        self.bg_image_dim = SliderRow(
            "Darken", 0, 90, background.image_dim * 100, 1, "%"
        )
        self.bg_image_dim.valueChanged.connect(
            lambda v: self._set_bg("image_dim", float(v) / 100.0)
        )
        card.add(self.bg_image_dim)

        self.bg_noise = SliderRow("Grain", 0, 100, background.noise * 100, 1, "%")
        self.bg_noise.valueChanged.connect(
            lambda v: self._set_bg("noise", float(v) / 100.0)
        )
        card.add(self.bg_noise)

        self.transparent_check = QCheckBox("Transparent background")
        self.transparent_check.toggled.connect(self._on_transparent)
        card.add(self.transparent_check)

        self.root.addWidget(card)
        self._update_bg_visibility()

    def _current_bg_kind(self) -> str:
        if self.transparent_check.isChecked():
            return "transparent"
        mode = self.bg_mode.value()
        if mode == "linear":
            return self.gradient_shape.value()
        return mode

    def _update_bg_visibility(self) -> None:
        transparent = self.transparent_check.isChecked()
        mode = self.bg_mode.value()
        gradient = mode == "linear" and not transparent
        solid = mode == "solid" and not transparent
        image = mode == "image" and not transparent

        self.bg_mode.setEnabled(not transparent)
        self.gradient_shape.setVisible(gradient)
        self.gradient_grid.setVisible(gradient)
        self.bg_color2.setVisible(gradient)
        self.bg_angle.setVisible(gradient and self.gradient_shape.value() != "radial")
        self.bg_color1.setVisible(gradient or solid)
        self.solid_grid.setVisible(solid)
        self.bg_image_button.setVisible(image)
        self.bg_image_blur.setVisible(image)
        self.bg_image_dim.setVisible(image)
        self.bg_noise.setVisible(not transparent)

    def _set_bg(self, attribute: str, value) -> None:
        if self._building:
            return
        setattr(self.canvas.doc.background, attribute, value)
        self.canvas.doc.touch_base()
        self.canvas.invalidate(full=True)
        self.changed.emit()

    def _on_bg_mode(self, _mode: str) -> None:
        if self._building:
            return
        self._update_bg_visibility()
        self._set_bg("kind", self._current_bg_kind())

    def _on_gradient_shape(self, _shape: str) -> None:
        if self._building:
            return
        self._update_bg_visibility()
        self._set_bg("kind", self._current_bg_kind())

    def _on_transparent(self, _checked: bool) -> None:
        if self._building:
            return
        self._update_bg_visibility()
        self._set_bg("kind", self._current_bg_kind())

    def _on_gradient_preset(self, preset) -> None:
        _name, c1, c2, angle = preset
        background = self.canvas.doc.background
        background.color1 = QColor(c1)
        background.color2 = QColor(c2)
        background.angle = angle
        self.bg_color1.setColor(QColor(c1), emit=False)
        self.bg_color2.setColor(QColor(c2), emit=False)
        self.bg_angle.setValue(angle)
        self._set_bg("kind", self._current_bg_kind())

    def _swap_bg_colors(self) -> None:
        background = self.canvas.doc.background
        background.color1, background.color2 = (
            QColor(background.color2), QColor(background.color1)
        )
        self.bg_color1.setColor(background.color1, emit=False)
        self.bg_color2.setColor(background.color2, emit=False)
        self.canvas.doc.touch_base()
        self.canvas.invalidate(full=True)
        self.changed.emit()

    def _pick_bg_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a background image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.avif *.tif *.tiff)",
        )
        if path:
            self.bg_image_button.setText(path.rsplit("/", 1)[-1])
            self._set_bg("image_path", path)
            self._set_bg("kind", "image")

    # --------------------------------------------------------------- sync
    def sync_from_settings(self) -> None:
        self._building = True
        style = self.canvas.style
        color = QColor(str(settings.get("tool_color")))
        if color.isValid():
            style.color = color
            self.color_button.setColor(color, emit=False)
        style.width = float(settings.get("tool_width"))
        self.width_slider.setValue(style.width)
        style.arrow_taper = float(settings.get("arrow_taper"))
        self.arrow_taper.setValue(style.arrow_taper * 100)
        style.font_family = str(settings.get("font_family"))
        style.font_size = float(settings.get("font_size"))
        self.font_size.setValue(style.font_size)
        self.recent_grid.set_colors(settings.get("recent_colors", [])[:6])
        self._building = False

    def sync_from_document(self, doc: Document) -> None:
        """Reflect a document's state back into the controls."""
        self._building = True
        frame = doc.frame
        self.padding_slider.setValue(frame.padding)
        self.radius_slider.setValue(frame.corner_radius)
        self.shadow_slider.setValue(frame.shadow_strength)
        self.shadow_blur_slider.setValue(frame.shadow_blur)
        self.shadow_offset_slider.setValue(frame.shadow_offset)
        self.rotation_slider.setValue(frame.rotation)
        self.border_slider.setValue(frame.border_width)
        index = self.aspect_box.findData(frame.aspect)
        self.aspect_box.setCurrentIndex(max(0, index))

        background = doc.background
        self.transparent_check.setChecked(background.kind == "transparent")
        if background.kind in ("linear", "radial", "conical"):
            self.bg_mode.setValue("linear", emit=False)
            self.gradient_shape.setValue(background.kind, emit=False)
        elif background.kind in ("solid", "image"):
            self.bg_mode.setValue(background.kind, emit=False)
        self.bg_color1.setColor(background.color1, emit=False)
        self.bg_color2.setColor(background.color2, emit=False)
        self.bg_angle.setValue(background.angle)
        self.bg_noise.setValue(background.noise * 100)
        self.bg_image_blur.setValue(background.image_blur)
        self.bg_image_dim.setValue(background.image_dim * 100)
        self._update_bg_visibility()
        self._building = False

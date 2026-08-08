"""The editing canvas.

Draws the document through the same renderer used for export, and turns mouse
input into annotations. The expensive part of the render (background, shadow,
scaled screenshot, baked pixel effects) is cached and only recomputed when
something below the annotation layer changes, so dragging an arrow stays smooth
even on a 4K screenshot.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field

from PySide6.QtCore import (
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ..annotations import (
    Annotation,
    ArrowAnn,
    EllipseAnn,
    HighlighterStroke,
    LineAnn,
    NumberAnn,
    PenStroke,
    RectAnn,
    RedactAnn,
    TextAnn,
)
from ..model import Document
from ..render import (
    annotation_transform,
    canvas_layout,
    draw_annotations,
    render_base,
)
from ..theme import current as current_theme
from ..util import blur_image, checkerboard_brush, pixelate_image

HANDLE_R = 5.0
HIT_TOLERANCE = 7.0

#: Blur radius past which the drag preview shrinks the patch first. Qt's blur
#: cost grows with the radius, and zoomed in a radius of 70+ takes long enough
#: to stutter - while a blur is exactly the thing a downscale round trip does
#: not visibly change.
PREVIEW_BLUR_CAP = 20.0


def _preview_blur(patch: QImage, radius: float) -> QImage:
    """Blur for the on-canvas preview: accurate enough, always fast.

    May return a smaller image than it was given - the caller scales it back
    up while drawing, which costs nothing next to blurring at full size.
    """
    if radius <= PREVIEW_BLUR_CAP:
        return blur_image(patch, radius)
    shrink = PREVIEW_BLUR_CAP / radius
    small = patch.scaled(
        max(1, int(patch.width() * shrink)),
        max(1, int(patch.height() * shrink)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return blur_image(small, PREVIEW_BLUR_CAP)


@dataclass
class ToolStyle:
    """The live drawing style, shared with the sidebar."""

    color: QColor = field(default_factory=lambda: QColor("#e5484d"))
    width: float = 6.0
    filled: bool = False
    dashed: bool = False
    double_headed: bool = False
    arrow_taper: float = 0.35
    corner_radius: float = 0.0
    font_family: str = "Sans Serif"
    font_size: float = 32.0
    bold: bool = False
    italic: bool = False
    text_background: bool = False
    redact_mode: str = "blur"
    redact_strength: float = 18.0
    number_radius: float = 22.0


class Canvas(QWidget):
    document_changed = Signal()
    selection_changed = Signal(object)
    tool_finished = Signal()
    zoom_changed = Signal(float)
    crop_mode_changed = Signal(bool)
    #: A region was swept with the Grab text tool, in source coordinates. The
    #: window does the recognising - the canvas only says where.
    text_region_selected = Signal(QRectF)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasWell")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(320, 240)

        self.doc = Document()
        self.tool = "select"
        self.style = ToolStyle()

        self._base_cache: QImage | None = None
        self._cache_key: tuple | None = None

        self._zoom = 0.0            # 0 => fit to window
        self._pan = QPointF(0, 0)
        self._display_rect = QRectF()
        self._display_scale = 1.0

        self._drawing: Annotation | None = None
        self._selected: Annotation | None = None
        self._erase_rect: QRectF | None = None
        self._grab_rect: QRectF | None = None
        self._drag_mode = ""        # "" | move | handle | pan | crop | erase | grabtext
        self._drag_handle = -1
        self._drag_origin = QPointF()
        self._drag_start_geometry = None
        self._space_pan = False
        self._pushed_undo = False

        self.crop_mode = False
        self._crop_rect: QRect | None = None
        self._crop_handle = ""

        self._checker = checkerboard_brush(11, current_theme().dark)
        self._text_editor: QPlainTextEdit | None = None
        self._editing_text: TextAnn | None = None

    # -- document -----------------------------------------------------------
    def set_document(self, doc: Document) -> None:
        self.commit_text()
        self.doc = doc
        self._selected = None
        self._drawing = None
        self._erase_rect = None
        self._grab_rect = None
        self.crop_mode = False
        self._crop_rect = None
        self.invalidate(full=True)
        self.reset_view()
        self.selection_changed.emit(None)
        self.document_changed.emit()

    def invalidate(self, full: bool = False) -> None:
        if full:
            self._base_cache = None
            self._cache_key = None
        self.update()

    def refresh_theme(self) -> None:
        self._checker = checkerboard_brush(11, current_theme().dark)
        self.update()

    # -- view ---------------------------------------------------------------
    def reset_view(self) -> None:
        self._zoom = 0.0
        self._pan = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit(self.effective_scale())

    def fit_scale(self) -> float:
        layout = canvas_layout(self.doc)
        if layout.canvas.isEmpty():
            return 1.0
        margin = 34
        available_w = max(40, self.width() - margin * 2)
        available_h = max(40, self.height() - margin * 2)
        scale = min(
            available_w / layout.canvas.width(),
            available_h / layout.canvas.height(),
        )
        return min(scale, 1.0) if scale > 0 else 1.0

    def effective_scale(self) -> float:
        return self._zoom if self._zoom > 0 else self.fit_scale()

    def set_zoom(self, value: float | None) -> None:
        if value is None:
            self._zoom = 0.0
            self._pan = QPointF(0, 0)
        else:
            self._zoom = max(0.05, min(8.0, value))
        self.update()
        self.zoom_changed.emit(self.effective_scale())

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.effective_scale() * factor)

    # -- coordinate mapping -------------------------------------------------
    def _compute_display(self) -> None:
        layout = canvas_layout(self.doc)
        scale = self.effective_scale()
        width = layout.canvas.width() * scale
        height = layout.canvas.height() * scale
        x = (self.width() - width) / 2 + self._pan.x()
        y = (self.height() - height) / 2 + self._pan.y()
        self._display_rect = QRectF(x, y, width, height)
        self._display_scale = scale

    def widget_to_source(self, point: QPointF) -> QPointF:
        transform = annotation_transform(self.doc, self._display_scale)
        inverted, ok = transform.inverted()
        local = QPointF(
            point.x() - self._display_rect.x(), point.y() - self._display_rect.y()
        )
        return inverted.map(local) if ok else local

    def source_to_widget(self, point: QPointF) -> QPointF:
        transform = annotation_transform(self.doc, self._display_scale)
        mapped = transform.map(point)
        return QPointF(
            mapped.x() + self._display_rect.x(), mapped.y() + self._display_rect.y()
        )

    def _source_length(self, widget_length: float) -> float:
        """Convert a distance in widget pixels to source-image pixels."""
        transform = annotation_transform(self.doc, self._display_scale)
        inverted, ok = transform.inverted()
        if not ok:
            return widget_length
        a = inverted.map(QPointF(0, 0))
        b = inverted.map(QPointF(widget_length, 0))
        return math.hypot(b.x() - a.x(), b.y() - a.y())

    # -- painting -----------------------------------------------------------
    def _ensure_base(self) -> QImage | None:
        if self.doc.is_empty():
            return None
        key = (
            self.doc.base_revision,
            round(self._display_scale, 4),
            self.doc.source.cacheKey(),
        )
        if self._cache_key != key or self._base_cache is None:
            self._base_cache = render_base(self.doc, self._display_scale)
            self._cache_key = key
        return self._base_cache

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        theme = current_theme()
        painter.fillRect(self.rect(), QColor(theme.bg_sunken))

        if self.doc.is_empty():
            self._paint_placeholder(painter)
            return

        self._compute_display()
        rect = self._display_rect

        if self.doc.background.kind == "transparent":
            painter.fillRect(rect, self._checker)

        base = self._ensure_base()
        if base is not None:
            painter.drawImage(rect.topLeft(), base)
            if self._drawing is not None and self._drawing.pixel_effect:
                self._paint_pixel_preview(painter, self._drawing, base)

        painter.save()
        painter.translate(rect.topLeft())
        extra = [self._drawing] if self._drawing is not None else None
        draw_annotations(painter, self.doc, self._display_scale, extra=extra)
        painter.restore()

        # A hairline keeps the canvas readable against a similar-coloured well.
        painter.setPen(QPen(QColor(0, 0, 0, 45), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5))

        if self.crop_mode:
            self._paint_crop(painter)
        elif self._erase_rect is not None:
            self._paint_erase_rect(painter, self._erase_rect)
        elif self._grab_rect is not None:
            self._paint_grab_rect(painter, self._grab_rect)
        elif self._selected is not None:
            self._paint_selection(painter, self._selected)

    def _paint_pixel_preview(
        self, painter: QPainter, ann: RedactAnn, base: QImage
    ) -> None:
        """Show a blur / pixelate / block region while it is still being dragged.

        Pixel effects are baked into the base image, and that only happens once
        the annotation is committed - so without this the region you are
        dragging out stays invisible until you let go of the button. The
        preview reworks the cached base at display scale rather than the
        screenshot at full size, which is what keeps it cheap enough to do on
        every mouse move.
        """
        transform = annotation_transform(self.doc, self._display_scale)
        quad = transform.map(QPolygonF(ann.bounds()))
        path = QPainterPath()
        path.addPolygon(quad)

        # The strength is expressed in screenshot pixels; the base is already
        # scaled to fit the window, so the preview has to be too.
        strength = max(1.0, ann.strength * self._display_scale)

        # Only rework what is on screen. Zoomed in, the base is far larger than
        # the viewport, and blurring all of it would cost seconds per frame.
        # The margin keeps the blur at the viewport edge sampling real pixels.
        margin = int(math.ceil(strength * 2)) + 2
        visible = self.rect().translated(-self._display_rect.topLeft().toPoint())
        region = quad.boundingRect().toAlignedRect().intersected(
            visible.adjusted(-margin, -margin, margin, margin)
        ).intersected(QRect(0, 0, base.width(), base.height()))

        painter.save()
        painter.translate(self._display_rect.topLeft())
        # Clip to the viewport before the region itself: zoomed in the mapped
        # quad can be tens of thousands of pixels across, and rasterising a
        # clip that big is slower than everything else here put together.
        painter.setClipRect(visible)
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        if ann.mode == "solid":
            painter.fillRect(region, QBrush(ann.color))
        elif region.width() >= 2 and region.height() >= 2:
            patch = base.copy(region)
            if ann.mode == "pixelate":
                patch = pixelate_image(patch, max(2, int(round(strength))))
            else:
                patch = _preview_blur(patch, strength)
            painter.drawImage(QRectF(region), patch)
        painter.setClipping(False)

        # A hairline outline, since a light blur over a plain background can
        # otherwise be hard to make out while dragging.
        pen = QPen(QColor(current_theme().accent), 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(quad)
        painter.restore()

    def _paint_placeholder(self, painter: QPainter) -> None:
        theme = current_theme()
        painter.setPen(QColor(theme.text_dim))
        font = QFont("Sans Serif")
        font.setPointSizeF(12.0)
        painter.setFont(font)
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignCenter,
            "No image yet\n\nTake a screenshot, open a file,\nor drop an image here",
        )

    def _paint_selection(self, painter: QPainter, ann: Annotation) -> None:
        theme = current_theme()
        accent = QColor(theme.accent)
        bounds = ann.bounds()
        corners = [
            self.source_to_widget(bounds.topLeft()),
            self.source_to_widget(bounds.topRight()),
            self.source_to_widget(bounds.bottomRight()),
            self.source_to_widget(bounds.bottomLeft()),
        ]
        pen = QPen(accent, 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(corners)

        painter.setPen(QPen(QColor(255, 255, 255), 1.4))
        painter.setBrush(QBrush(accent))
        for point in ann.handles():
            painter.drawEllipse(self.source_to_widget(point), HANDLE_R, HANDLE_R)

    # -- crop ---------------------------------------------------------------
    def begin_crop(self) -> None:
        if self.doc.is_empty():
            return
        self.commit_text()
        self.crop_mode = True
        self._crop_rect = QRect(self.doc.crop_rect())
        self._selected = None
        self.selection_changed.emit(None)
        self.crop_mode_changed.emit(True)
        self.update()

    def cancel_crop(self) -> None:
        self.crop_mode = False
        self._crop_rect = None
        self.crop_mode_changed.emit(False)
        self.update()

    def apply_crop(self) -> None:
        if not self.crop_mode or self._crop_rect is None:
            return
        rect = self._crop_rect.intersected(
            QRect(0, 0, self.doc.source.width(), self.doc.source.height())
        )
        if rect.width() >= 8 and rect.height() >= 8:
            self.doc.push_undo()
            full = QRect(0, 0, self.doc.source.width(), self.doc.source.height())
            self.doc.crop = None if rect == full else rect
            self.doc.touch_all()
            self.invalidate(full=True)
            self.document_changed.emit()
        self.cancel_crop()

    def reset_crop(self) -> None:
        if self.doc.crop is None:
            return
        self.doc.push_undo()
        self.doc.crop = None
        self.doc.touch_all()
        self.invalidate(full=True)
        self.document_changed.emit()

    def _crop_handle_points(self) -> dict[str, QPointF]:
        if self._crop_rect is None:
            return {}
        r = QRectF(self._crop_rect)
        return {
            "tl": r.topLeft(),
            "t": QPointF(r.center().x(), r.top()),
            "tr": r.topRight(),
            "r": QPointF(r.right(), r.center().y()),
            "br": r.bottomRight(),
            "b": QPointF(r.center().x(), r.bottom()),
            "bl": r.bottomLeft(),
            "l": QPointF(r.left(), r.center().y()),
        }

    def _paint_crop(self, painter: QPainter) -> None:
        if self._crop_rect is None:
            return
        source_full = QRectF(0, 0, self.doc.source.width(), self.doc.source.height())
        outer = QRectF(
            self.source_to_widget(source_full.topLeft()),
            self.source_to_widget(source_full.bottomRight()),
        ).normalized()
        inner = QRectF(
            self.source_to_widget(QRectF(self._crop_rect).topLeft()),
            self.source_to_widget(QRectF(self._crop_rect).bottomRight()),
        ).normalized()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 10, 16, 140))
        painter.drawRect(QRectF(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()))
        painter.drawRect(QRectF(outer.left(), inner.bottom(), outer.width(), outer.bottom() - inner.bottom()))
        painter.drawRect(QRectF(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()))
        painter.drawRect(QRectF(inner.right(), inner.top(), outer.right() - inner.right(), inner.height()))

        accent = QColor(current_theme().accent)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.2))
        painter.drawRect(inner)

        # Rule-of-thirds guides.
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
        for i in (1, 2):
            x = inner.left() + inner.width() * i / 3
            y = inner.top() + inner.height() * i / 3
            painter.drawLine(QPointF(x, inner.top()), QPointF(x, inner.bottom()))
            painter.drawLine(QPointF(inner.left(), y), QPointF(inner.right(), y))

        painter.setPen(QPen(QColor(255, 255, 255), 1.4))
        painter.setBrush(QBrush(accent))
        for point in self._crop_handle_points().values():
            painter.drawEllipse(self.source_to_widget(point), HANDLE_R + 1, HANDLE_R + 1)

        label = f"{self._crop_rect.width()} x {self._crop_rect.height()}"
        painter.setPen(QColor(255, 255, 255))
        font = QFont("Sans Serif")
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            inner.adjusted(6, 6, -6, -6), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            label,
        )

    def _crop_hit(self, pos: QPointF) -> str:
        for name, point in self._crop_handle_points().items():
            if (self.source_to_widget(point) - pos).manhattanLength() <= HANDLE_R * 3:
                return name
        if self._crop_rect is not None:
            inner = QRectF(
                self.source_to_widget(QRectF(self._crop_rect).topLeft()),
                self.source_to_widget(QRectF(self._crop_rect).bottomRight()),
            ).normalized()
            if inner.contains(pos):
                return "move"
        return ""

    def _resize_crop(self, handle: str, point: QPointF) -> None:
        if self._crop_rect is None:
            return
        rect = QRectF(self._crop_rect)
        x, y = point.x(), point.y()
        if "l" in handle:
            rect.setLeft(min(x, rect.right() - 8))
        if "r" in handle:
            rect.setRight(max(x, rect.left() + 8))
        if "t" in handle:
            rect.setTop(min(y, rect.bottom() - 8))
        if "b" in handle:
            rect.setBottom(max(y, rect.top() + 8))
        bounds = QRectF(0, 0, self.doc.source.width(), self.doc.source.height())
        self._crop_rect = rect.intersected(bounds).toRect()

    # -- tools --------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self.commit_text()
        self.tool = tool
        if tool != "select":
            self._selected = None
            self.selection_changed.emit(None)
        cursors = {
            "select": Qt.CursorShape.ArrowCursor,
            "pen": Qt.CursorShape.CrossCursor,
            "highlighter": Qt.CursorShape.CrossCursor,
            "text": Qt.CursorShape.IBeamCursor,
            "eraser": Qt.CursorShape.PointingHandCursor,
            "grabtext": Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cursors.get(tool, Qt.CursorShape.CrossCursor))
        self.update()

    def _new_annotation(self, point: QPointF) -> Annotation | None:
        s = self.style
        color = QColor(s.color)
        tool = self.tool

        if tool == "pen":
            return PenStroke(color=color, width=s.width, points=[point])
        if tool == "highlighter":
            return HighlighterStroke(color=color, width=s.width, points=[point])
        if tool == "line":
            return LineAnn(color=color, width=s.width, start=point, end=point,
                           dashed=s.dashed)
        if tool == "arrow":
            return ArrowAnn(color=color, width=s.width, start=point, end=point,
                            double_headed=s.double_headed, taper=s.arrow_taper)
        if tool == "rect":
            return RectAnn(color=color, width=s.width, start=point, end=point,
                           filled=s.filled, radius=s.corner_radius)
        if tool == "ellipse":
            return EllipseAnn(color=color, width=s.width, start=point, end=point,
                              filled=s.filled)
        if tool == "redact":
            return RedactAnn(color=QColor(20, 20, 26), width=1.0, start=point,
                             end=point, mode=s.redact_mode,
                             strength=s.redact_strength)
        if tool == "number":
            return NumberAnn(color=color, width=s.width, center=point,
                             number=self.doc.next_number(), radius=s.number_radius)
        if tool == "text":
            return TextAnn(
                color=color, width=s.width, position=point, text="",
                font_size=s.font_size, font_family=s.font_family,
                bold=s.bold, italic=s.italic,
                background=QColor(0, 0, 0, 150) if s.text_background else None,
            )
        return None

    def _annotation_at(self, point: QPointF) -> Annotation | None:
        tolerance = self._source_length(HIT_TOLERANCE)
        for ann in reversed(self.doc.annotations):
            if ann.hit(point, tolerance):
                return ann
        return None

    def _handle_at(self, ann: Annotation, pos: QPointF) -> int:
        for index, point in enumerate(ann.handles()):
            if (self.source_to_widget(point) - pos).manhattanLength() <= HANDLE_R * 3:
                return index
        return -1

    # -- mouse --------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.doc.is_empty():
            return
        self._compute_display()
        pos = event.position()
        source = self.widget_to_source(pos)

        if event.button() == Qt.MouseButton.MiddleButton or self._space_pan:
            self._drag_mode = "pan"
            self._drag_origin = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self.start_drag_out()
            return

        if self.crop_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self._crop_handle = self._crop_hit(pos) or "new"
                self._drag_mode = "crop"
                self._drag_origin = source
                if self._crop_handle == "new":
                    self._crop_rect = QRect(source.toPoint(), QSize(1, 1))
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        self.commit_text()

        if self.tool == "eraser":
            # Whether this is a click on one mark or a sweep over several is
            # only known on release, so both start the same way.
            self._drag_mode = "erase"
            self._drag_origin = source
            self._erase_rect = None
            return

        if self.tool == "grabtext":
            self._drag_mode = "grabtext"
            self._drag_origin = source
            self._grab_rect = None
            return

        if self.tool == "select":
            if self._selected is not None:
                handle = self._handle_at(self._selected, pos)
                if handle >= 0:
                    self.doc.push_undo()
                    self._pushed_undo = True
                    self._drag_mode = "handle"
                    self._drag_handle = handle
                    return
            target = self._annotation_at(source)
            self._selected = target
            self.selection_changed.emit(target)
            if target is not None:
                self.doc.push_undo()
                self._pushed_undo = True
                self._drag_mode = "move"
                self._drag_origin = source
            self.update()
            return

        if self.tool == "text":
            ann = self._new_annotation(source)
            if ann is not None:
                self.doc.push_undo()
                self.doc.add_annotation(ann)
                self._selected = ann
                self.start_text_edit(ann)
                self.document_changed.emit()
            return

        if self.tool == "number":
            ann = self._new_annotation(source)
            if ann is not None:
                self.doc.push_undo()
                self.doc.add_annotation(ann)
                self.update()
                self.document_changed.emit()
                self.tool_finished.emit()
            return

        ann = self._new_annotation(source)
        if ann is not None:
            self._drawing = ann
            self._drag_origin = source
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.doc.is_empty():
            return
        pos = event.position()

        if self._drag_mode == "pan":
            delta = pos - self._drag_origin
            self._pan += delta
            self._drag_origin = pos
            self.update()
            return

        self._compute_display()
        source = self.widget_to_source(pos)

        if self._drag_mode == "crop":
            if self._crop_handle == "new":
                rect = QRectF(self._drag_origin, source).normalized()
                bounds = QRectF(0, 0, self.doc.source.width(), self.doc.source.height())
                self._crop_rect = rect.intersected(bounds).toRect()
            elif self._crop_handle == "move" and self._crop_rect is not None:
                delta = source - self._drag_origin
                moved = self._crop_rect.translated(
                    int(round(delta.x())), int(round(delta.y()))
                )
                bounds = QRect(0, 0, self.doc.source.width(), self.doc.source.height())
                if moved.width() <= bounds.width():
                    moved.moveLeft(max(0, min(moved.left(), bounds.width() - moved.width())))
                if moved.height() <= bounds.height():
                    moved.moveTop(max(0, min(moved.top(), bounds.height() - moved.height())))
                self._crop_rect = moved
                self._drag_origin = source
            elif self._crop_handle:
                self._resize_crop(self._crop_handle, source)
            self.update()
            return

        if self._drag_mode == "erase":
            self._erase_rect = QRectF(self._drag_origin, source).normalized()
            self.update()
            return

        if self._drag_mode == "grabtext":
            bounds = QRectF(0, 0, self.doc.source.width(), self.doc.source.height())
            self._grab_rect = QRectF(
                self._drag_origin, source
            ).normalized().intersected(bounds)
            self.update()
            return

        if self._drawing is not None:
            self._update_drawing(source, event.modifiers())
            self.update()
            return

        if self._drag_mode == "move" and self._selected is not None:
            delta = source - self._drag_origin
            self._selected.translate(delta.x(), delta.y())
            self._drag_origin = source
            if self._selected.pixel_effect:
                self.doc.touch_base()
                self.invalidate(full=True)
            self.doc.touch_annotations()
            self.update()
            return

        if self._drag_mode == "handle" and self._selected is not None:
            self._selected.move_handle(self._drag_handle, source)
            if self._selected.pixel_effect:
                self.doc.touch_base()
                self.invalidate(full=True)
            self.doc.touch_annotations()
            self.update()
            return

        # Hover feedback for the select tool.
        if self.tool == "select":
            if self._selected is not None and self._handle_at(self._selected, pos) >= 0:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif self._annotation_at(source) is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def _erase(self, rect: QRectF | None) -> None:
        """Remove what the eraser covered: everything in a swept area, or the
        one mark under a plain click."""
        slack = self._source_length(4.0)
        if rect is not None and (rect.width() > slack or rect.height() > slack):
            targets = [a for a in self.doc.annotations if a.intersects(rect)]
        else:
            one = self._annotation_at(self._drag_origin)
            targets = [one] if one is not None else []

        if not targets:
            self.update()
            return

        self.doc.push_undo()
        for ann in targets:
            self.doc.remove_annotation(ann)
        if self._selected in targets:
            self._selected = None
            self.selection_changed.emit(None)
        self.invalidate(full=any(a.pixel_effect for a in targets))
        self.document_changed.emit()

    def _paint_grab_rect(self, painter: QPainter, rect: QRectF) -> None:
        """The region about to be read. Accent-coloured, so it does not read as
        the eraser's destructive red or the crop's commitment."""
        accent = QColor(current_theme().accent)
        quad = [
            self.source_to_widget(rect.topLeft()),
            self.source_to_widget(rect.topRight()),
            self.source_to_widget(rect.bottomRight()),
            self.source_to_widget(rect.bottomLeft()),
        ]
        fill = QColor(accent)
        fill.setAlpha(34)
        pen = QPen(accent, 1.4)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(quad)

    def _paint_erase_rect(self, painter: QPainter, rect: QRectF) -> None:
        """The area the eraser is about to clear, in the colour of removal."""
        danger = QColor(current_theme().danger)
        quad = [
            self.source_to_widget(rect.topLeft()),
            self.source_to_widget(rect.topRight()),
            self.source_to_widget(rect.bottomRight()),
            self.source_to_widget(rect.bottomLeft()),
        ]
        fill = QColor(danger)
        fill.setAlpha(38)
        pen = QPen(danger, 1.3)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(quad)

    def _update_drawing(self, source: QPointF, modifiers) -> None:
        ann = self._drawing
        if isinstance(ann, PenStroke):
            last = ann.points[-1]
            # Drop samples closer than a pixel so the smoothing stays stable.
            if (source - last).manhattanLength() > self._source_length(1.2):
                ann.points.append(source)
            return

        point = QPointF(source)
        if modifiers & Qt.KeyboardModifier.ShiftModifier and hasattr(ann, "start"):
            dx = point.x() - ann.start.x()
            dy = point.y() - ann.start.y()
            if isinstance(ann, (LineAnn, ArrowAnn)):
                angle = math.degrees(math.atan2(dy, dx))
                snapped = round(angle / 15.0) * 15.0
                length = math.hypot(dx, dy)
                rad = math.radians(snapped)
                point = QPointF(
                    ann.start.x() + length * math.cos(rad),
                    ann.start.y() + length * math.sin(rad),
                )
            else:
                side = max(abs(dx), abs(dy))
                point = QPointF(
                    ann.start.x() + math.copysign(side, dx or 1),
                    ann.start.y() + math.copysign(side, dy or 1),
                )
        if hasattr(ann, "end"):
            ann.end = point

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode == "pan":
            self._drag_mode = ""
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.set_tool(self.tool)
            return

        if self._drag_mode == "crop":
            self._drag_mode = ""
            self._crop_handle = ""
            return

        if self._drag_mode == "erase":
            self._drag_mode = ""
            rect, self._erase_rect = self._erase_rect, None
            self._erase(rect)
            return

        if self._drag_mode == "grabtext":
            self._drag_mode = ""
            rect, self._grab_rect = self._grab_rect, None
            self.update()
            # A stray click is not a region; recognising a 2px box would only
            # produce noise and clear the marquee for nothing.
            slack = self._source_length(6.0)
            if rect is not None and rect.width() > slack and rect.height() > slack:
                self.text_region_selected.emit(rect)
            return

        if self._drawing is not None:
            ann = self._drawing
            self._drawing = None
            if not ann.is_degenerate():
                self.doc.push_undo()
                self.doc.add_annotation(ann)
                self.invalidate(full=ann.pixel_effect)
                self.document_changed.emit()
                self.tool_finished.emit()
            else:
                self.update()
            return

        if self._drag_mode in ("move", "handle"):
            self._drag_mode = ""
            self._drag_handle = -1
            self._pushed_undo = False
            self.document_changed.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            self.zoom_by(1.12 ** steps)
            event.accept()
            return
        if self._zoom > 0:
            delta = event.angleDelta()
            self._pan += QPointF(delta.x() * 0.5, delta.y() * 0.5)
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    # -- keyboard -----------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self.crop_mode:
            if key == Qt.Key.Key_Escape:
                self.cancel_crop()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.apply_crop()
                return
        if key == Qt.Key.Key_Escape and self._drag_mode == "erase":
            # Called off mid-sweep: drop the area without erasing anything.
            self._drag_mode = ""
            self._erase_rect = None
            self.update()
            return
        if key == Qt.Key.Key_Escape:
            if self._selected is not None:
                self._selected = None
                self.selection_changed.emit(None)
                self.update()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected:
            self.delete_selected()
            return
        if self._selected is not None and key in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down
        ):
            step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx = -step if key == Qt.Key.Key_Left else step if key == Qt.Key.Key_Right else 0
            dy = -step if key == Qt.Key.Key_Up else step if key == Qt.Key.Key_Down else 0
            self.doc.push_undo()
            self._selected.translate(dx, dy)
            self.doc.touch_annotations()
            if self._selected.pixel_effect:
                self.invalidate(full=True)
            self.update()
            self.document_changed.emit()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = False
            self.set_tool(self.tool)
            return
        super().keyReleaseEvent(event)

    # -- selection ops ------------------------------------------------------
    def selected(self) -> Annotation | None:
        return self._selected

    def select_annotation(self, ann: Annotation | None) -> None:
        self._selected = ann
        self.selection_changed.emit(ann)
        self.update()

    def delete_selected(self) -> None:
        if self._selected is None:
            return
        self.doc.push_undo()
        pixel = self._selected.pixel_effect
        self.doc.remove_annotation(self._selected)
        self._selected = None
        self.selection_changed.emit(None)
        self.invalidate(full=pixel)
        self.document_changed.emit()

    def clear_annotations(self) -> None:
        if not self.doc.annotations:
            return
        self.doc.push_undo()
        self.doc.annotations.clear()
        self._selected = None
        self.selection_changed.emit(None)
        self.doc.touch_all()
        self.invalidate(full=True)
        self.document_changed.emit()

    def raise_selected(self) -> None:
        if self._selected is None:
            return
        self.doc.push_undo()
        self.doc.annotations.remove(self._selected)
        self.doc.annotations.append(self._selected)
        self.doc.touch_all()
        self.invalidate(full=True)
        self.document_changed.emit()

    def apply_style_to_selected(self) -> None:
        """Push the current sidebar style onto the selected annotation."""
        ann = self._selected
        if ann is None:
            return
        s = self.style
        ann.color = QColor(s.color)
        if not isinstance(ann, (RedactAnn, TextAnn)):
            ann.width = s.width
        if isinstance(ann, RectAnn):
            ann.filled = s.filled
            ann.radius = s.corner_radius
        if isinstance(ann, LineAnn):
            ann.dashed = s.dashed
        if isinstance(ann, ArrowAnn):
            ann.double_headed = s.double_headed
            ann.taper = s.arrow_taper
        if isinstance(ann, TextAnn):
            ann.font_size = s.font_size
            ann.font_family = s.font_family
            ann.bold = s.bold
            ann.italic = s.italic
            ann.background = QColor(0, 0, 0, 150) if s.text_background else None
        if isinstance(ann, RedactAnn):
            ann.mode = s.redact_mode
            ann.strength = s.redact_strength
            self.doc.touch_base()
            self.invalidate(full=True)
        if isinstance(ann, NumberAnn):
            ann.radius = s.number_radius
        self.doc.touch_annotations()
        self.update()
        self.document_changed.emit()

    # -- inline text editing ------------------------------------------------
    def start_text_edit(self, ann: TextAnn) -> None:
        self.commit_text()
        self._editing_text = ann

        editor = QPlainTextEdit(self)
        editor.setPlainText(ann.text)
        editor.setFrameStyle(0)
        theme = current_theme()
        editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.surface}; color: {theme.text};"
            f" border: 2px solid {theme.accent}; border-radius: 6px; padding: 4px; }}"
        )
        font = QFont(ann.font_family)
        font.setPointSizeF(max(9.0, ann.font_size * self._display_scale * 0.75))
        font.setBold(ann.bold)
        font.setItalic(ann.italic)
        editor.setFont(font)

        top_left = self.source_to_widget(ann.position)
        editor.move(QPoint(int(top_left.x()) - 6, int(top_left.y()) - 6))
        editor.resize(
            max(180, int(240 * self._display_scale)),
            max(46, int(font.pointSizeF() * 2.6)),
        )
        editor.show()
        editor.setFocus()
        editor.textChanged.connect(self._on_text_changed)
        self._text_editor = editor
        self.update()

    def _on_text_changed(self) -> None:
        if self._text_editor is None or self._editing_text is None:
            return
        self._editing_text.text = self._text_editor.toPlainText()
        self.doc.touch_annotations()
        # Grow the editor with the text so it never hides what you typed.
        doc_height = self._text_editor.document().size().height()
        self._text_editor.resize(
            self._text_editor.width(), int(doc_height) + 18
        )
        self.update()

    def commit_text(self) -> None:
        editor, ann = self._text_editor, self._editing_text
        self._text_editor = None
        self._editing_text = None
        if editor is None:
            return
        text = editor.toPlainText()
        editor.deleteLater()
        if ann is None:
            return
        ann.text = text
        if ann.is_degenerate():
            self.doc.remove_annotation(ann)
            if self._selected is ann:
                self._selected = None
                self.selection_changed.emit(None)
        self.doc.touch_annotations()
        self.update()
        self.document_changed.emit()

    def is_editing_text(self) -> bool:
        return self._text_editor is not None

    # -- drag out -----------------------------------------------------------
    def start_drag_out(self) -> None:
        """Alt+drag the finished image straight into another application."""
        if self.doc.is_empty():
            return
        self.commit_text()

        from ..render import render_document

        image = render_document(self.doc, 1.0)
        directory = os.path.join(
            tempfile.gettempdir(), f"shotpad-drag-{os.getpid()}"
        )
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "shotpad.png")
        if not image.save(path, "PNG"):
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        mime.setImageData(image)

        drag = QDrag(self)
        drag.setMimeData(mime)
        preview = QPixmap.fromImage(
            image.scaled(
                180, 180,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(preview.width() // 2, preview.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)

    # -- misc ---------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.invalidate(full=True)
        self.zoom_changed.emit(self.effective_scale())

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._space_pan = False

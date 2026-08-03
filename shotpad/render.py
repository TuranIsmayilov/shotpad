"""Rendering pipeline.

One function renders the document, and both the on-screen preview and the
exported file go through it. That is what makes the editor WYSIWYG: the preview
is literally the export at a smaller scale.

Layer order:
    1. background (solid / gradient / image / transparent)
    2. drop shadow of the screenshot plate
    3. the screenshot, rounded and optionally rotated
    4. pixel effects (blur / pixelate / block redactions), baked into 1-3
    5. vector annotations
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
    QTransform,
)

from .model import BackgroundSpec, Document, FrameSpec
from .util import blur_image, pixelate_image, rounded_path, scaled_cover

#: The padding slider is a percentage of the screenshot's short edge; this caps
#: how much canvas a padding of 200 can add.
PADDING_UNIT = 0.005


@dataclass(frozen=True)
class Layout:
    """Where things live, in document units (canvas pixels at scale 1.0)."""

    canvas: QSize
    image_rect: QRectF

    @property
    def canvas_rect(self) -> QRectF:
        return QRectF(0, 0, self.canvas.width(), self.canvas.height())


def canvas_layout(doc: Document) -> Layout:
    """Compute canvas size and the screenshot's placement inside it."""
    size = doc.image_size()
    iw, ih = max(1, size.width()), max(1, size.height())
    frame = doc.frame

    pad = frame.padding * PADDING_UNIT * min(iw, ih)

    # A rotated plate needs a slightly bigger box or the corners clip.
    if abs(frame.rotation) > 0.01:
        rad = math.radians(abs(frame.rotation))
        rot_w = iw * math.cos(rad) + ih * math.sin(rad)
        rot_h = iw * math.sin(rad) + ih * math.cos(rad)
        pad_x = pad + (rot_w - iw) / 2
        pad_y = pad + (rot_h - ih) / 2
    else:
        pad_x = pad_y = pad

    cw = iw + pad_x * 2
    ch = ih + pad_y * 2

    if frame.aspect:
        aw, ah = frame.aspect
        target = aw / ah
        if cw / ch < target:
            cw = ch * target
        else:
            ch = cw / target

    cw = max(1.0, cw)
    ch = max(1.0, ch)
    image_rect = QRectF((cw - iw) / 2.0, (ch - ih) / 2.0, iw, ih)
    return Layout(QSize(int(round(cw)), int(round(ch))), image_rect)


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


def _gradient_brush(spec: BackgroundSpec, rect: QRectF) -> QBrush:
    if spec.kind == "radial":
        gradient = QRadialGradient(
            rect.center(), max(rect.width(), rect.height()) * 0.72
        )
        gradient.setColorAt(0.0, spec.color1)
        gradient.setColorAt(1.0, spec.color2)
        return QBrush(gradient)

    if spec.kind == "conical":
        gradient = QConicalGradient(rect.center(), spec.angle)
        gradient.setColorAt(0.0, spec.color1)
        gradient.setColorAt(0.5, spec.color2)
        gradient.setColorAt(1.0, spec.color1)
        return QBrush(gradient)

    angle = math.radians(spec.angle)
    dx, dy = math.cos(angle), math.sin(angle)
    half = math.hypot(rect.width(), rect.height()) / 2.0
    center = rect.center()
    gradient = QLinearGradient(
        center.x() - dx * half, center.y() - dy * half,
        center.x() + dx * half, center.y() + dy * half,
    )
    gradient.setColorAt(0.0, spec.color1)
    gradient.setColorAt(1.0, spec.color2)
    return QBrush(gradient)


_noise_cache: dict[tuple[int, int], QImage] = {}


def _noise_tile(seed: int = 7) -> QImage:
    """A deterministic 128x128 grain tile, generated once and reused."""
    key = (128, seed)
    cached = _noise_cache.get(key)
    if cached is not None:
        return cached

    import random

    rng = random.Random(seed)
    tile = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(128):
        for x in range(128):
            value = rng.randint(0, 255)
            tile.setPixelColor(x, y, QColor(value, value, value, 255))
    _noise_cache[key] = tile
    return tile


def draw_background(painter: QPainter, spec: BackgroundSpec, rect: QRectF) -> None:
    if spec.kind == "transparent":
        return

    if spec.kind == "image" and spec.image_path:
        image = QImage(spec.image_path)
        if not image.isNull():
            target = QSize(
                max(1, int(math.ceil(rect.width()))),
                max(1, int(math.ceil(rect.height()))),
            )
            covered = scaled_cover(image, target)
            if spec.image_blur > 0.5:
                covered = blur_image(covered, spec.image_blur)
            painter.drawImage(rect, covered)
            if spec.image_dim > 0.01:
                overlay = QColor(0, 0, 0)
                overlay.setAlphaF(min(0.9, spec.image_dim))
                painter.fillRect(rect, overlay)
            _draw_noise(painter, spec, rect)
            return
        # fall through to a solid fill when the file went missing

    if spec.kind == "solid" or spec.kind == "image":
        painter.fillRect(rect, spec.color1)
    else:
        painter.fillRect(rect, _gradient_brush(spec, rect))

    _draw_noise(painter, spec, rect)


def _draw_noise(painter: QPainter, spec: BackgroundSpec, rect: QRectF) -> None:
    if spec.noise <= 0.005:
        return
    painter.save()
    painter.setOpacity(min(0.35, spec.noise * 0.35))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
    painter.fillRect(rect, QBrush(_noise_tile()))
    painter.restore()


# ---------------------------------------------------------------------------
# The screenshot plate
# ---------------------------------------------------------------------------


def _radius_for(frame: FrameSpec, image_rect: QRectF) -> float:
    return frame.corner_radius * 0.01 * min(image_rect.width(), image_rect.height()) * 0.5


def _draw_shadow(
    painter: QPainter, layout: Layout, frame: FrameSpec, scale: float
) -> None:
    """Draw the plate shadow directly in device pixels.

    The shadow is rasterised at output resolution and blitted with the painter
    transform reset, so the blur radius stays visually identical whether we are
    drawing a 900px preview or a 3x export.
    """
    if frame.shadow_strength <= 0:
        return

    rect = layout.image_rect
    short_edge = min(rect.width(), rect.height())
    radius = _radius_for(frame, rect) * scale
    blur = max(1.5, frame.shadow_blur * 0.006 * short_edge * scale)
    offset = frame.shadow_offset * 0.006 * short_edge * scale

    dw = rect.width() * scale
    dh = rect.height() * scale
    margin = int(math.ceil(blur * 2.2)) + 4
    w = int(math.ceil(dw)) + margin * 2
    h = int(math.ceil(dh)) + margin * 2
    if w <= 0 or h <= 0 or w * h > 80_000_000:
        return

    mask = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    mp = QPainter(mask)
    mp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    mp.setPen(Qt.PenStyle.NoPen)
    mp.setBrush(QBrush(QColor(frame.shadow_color)))
    mp.drawPath(rounded_path(QRectF(margin, margin, dw, dh), radius))
    mp.end()

    shadow = blur_image(mask, blur)

    painter.save()
    painter.setOpacity(min(1.0, frame.shadow_strength / 100.0))
    if abs(frame.rotation) > 0.01:
        center = rect.center()
        rot = QTransform()
        rot.translate(center.x() * scale, center.y() * scale)
        rot.rotate(frame.rotation)
        rot.translate(-center.x() * scale, -center.y() * scale)
        painter.setTransform(rot)
    painter.drawImage(
        QPointF(rect.x() * scale - margin, rect.y() * scale - margin + offset), shadow
    )
    painter.restore()


def _draw_plate(
    painter: QPainter, doc: Document, layout: Layout, scale: float
) -> None:
    image = bake_redactions(doc)
    if image.isNull():
        return

    frame = doc.frame
    rect = layout.image_rect
    dev_rect = QRectF(
        rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale
    )
    radius = _radius_for(frame, rect) * scale

    painter.save()
    if abs(frame.rotation) > 0.01:
        center = rect.center()
        transform = QTransform()
        transform.translate(center.x() * scale, center.y() * scale)
        transform.rotate(frame.rotation)
        transform.translate(-center.x() * scale, -center.y() * scale)
        painter.setTransform(transform)

    path = rounded_path(dev_rect, radius)
    painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
    painter.drawImage(dev_rect, image)
    painter.setClipping(False)

    if frame.border_width > 0.05:
        pen = QPen(QColor(frame.border_color), frame.border_width * scale)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = frame.border_width * scale / 2.0
        painter.drawPath(
            rounded_path(dev_rect.adjusted(inset, inset, -inset, -inset), radius - inset)
        )
    painter.restore()


# ---------------------------------------------------------------------------
# Pixel effects
# ---------------------------------------------------------------------------


def bake_redactions(doc: Document) -> QImage:
    """Return the cropped screenshot with blur/pixelate/block regions applied.

    Redactions are baked into the screenshot itself rather than painted over
    the composited canvas. That keeps them correct when the plate is tilted,
    and means the blur radius is expressed in screenshot pixels, so it scales
    with the image exactly like the rest of the picture.
    """
    image = doc.cropped_image()
    effects = [a for a in doc.annotations if a.pixel_effect]
    if not effects or image.isNull():
        return image

    result = image.copy()
    crop = doc.crop_rect()
    bounds = QRect(0, 0, result.width(), result.height())

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for ann in effects:
        rect = ann.bounds().translated(-crop.x(), -crop.y()).toRect()
        region = rect.intersected(bounds)
        if region.width() < 2 or region.height() < 2:
            continue

        if ann.mode == "solid":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ann.color))
            painter.drawRect(region)
            continue

        patch = result.copy(region)
        if ann.mode == "pixelate":
            patch = pixelate_image(patch, max(2, int(ann.strength)))
        else:
            patch = blur_image(patch, max(1.0, ann.strength))
        painter.drawImage(region.topLeft(), patch)
    painter.end()
    return result


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def render_base(doc: Document, scale: float = 1.0) -> QImage:
    """Background + shadow + screenshot + pixel effects (no vector overlays)."""
    layout = canvas_layout(doc)
    width = max(1, int(round(layout.canvas.width() * scale)))
    height = max(1, int(round(layout.canvas.height() * scale)))

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    draw_background(painter, doc.background, QRectF(0, 0, width, height))
    _draw_shadow(painter, layout, doc.frame, scale)
    _draw_plate(painter, doc, layout, scale)
    painter.end()
    return image


def annotation_transform(doc: Document, scale: float) -> QTransform:
    """Maps source-image coordinates to device pixels on the rendered canvas."""
    layout = canvas_layout(doc)
    crop = doc.crop_rect()
    frame = doc.frame

    transform = QTransform()
    transform.scale(scale, scale)
    if abs(frame.rotation) > 0.01:
        center = layout.image_rect.center()
        transform.translate(center.x(), center.y())
        transform.rotate(frame.rotation)
        transform.translate(-center.x(), -center.y())
    transform.translate(
        layout.image_rect.x() - crop.x(), layout.image_rect.y() - crop.y()
    )
    return transform


def draw_annotations(
    painter: QPainter,
    doc: Document,
    scale: float,
    skip: set[int] | None = None,
    extra: list | None = None,
) -> None:
    """Stroke the vector annotations onto an active painter."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setTransform(annotation_transform(doc, scale), True)

    items = list(doc.annotations)
    if extra:
        items.extend(extra)
    for ann in items:
        if ann.pixel_effect:
            continue
        if skip and id(ann) in skip:
            continue
        painter.save()
        ann.draw(painter)
        painter.restore()
    painter.restore()


def render_document(doc: Document, scale: float = 1.0) -> QImage:
    """Full render - what gets saved to disk or put on the clipboard."""
    image = render_base(doc, scale)
    if not doc.annotations:
        return image
    painter = QPainter(image)
    draw_annotations(painter, doc, scale)
    painter.end()
    return image


def export_scale_for(doc: Document, factor: float, max_pixels: int = 40_000_000) -> float:
    """Clamp a user-requested export multiplier to something renderable."""
    layout = canvas_layout(doc)
    pixels = layout.canvas.width() * layout.canvas.height() * factor * factor
    if pixels <= max_pixels:
        return factor
    return max(0.1, factor * math.sqrt(max_pixels / pixels))

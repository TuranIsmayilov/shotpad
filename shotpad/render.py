"""Rendering pipeline.

One function renders the document, and both the on-screen preview and the
exported file go through it. That is what makes the editor WYSIWYG: the preview
is literally the export at a smaller scale.

Layer order:
    1. background (solid / gradient / image / transparent)
    2. drop shadow of the screenshot plate
    3. the outer border, a band around the screenshot
    4. the screenshot, rounded and optionally rotated
    5. pixel effects (blur / pixelate / block redactions), baked into 1-4
    6. vector annotations
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

#: The outer border slider is read directly as a percentage of the short edge.
OUTER_BORDER_UNIT = 0.01

#: What a glass mat's opacity decays to at the far corner. Measured off a real
#: frosted frame: ~25% white along the top and left edges, ~14% along the right
#: and bottom, so the sheen keeps a little over half its strength.
GLASS_FALLOFF = 0.55


@dataclass(frozen=True)
class Layout:
    """Where things live, in document units (canvas pixels at scale 1.0)."""

    canvas: QSize
    image_rect: QRectF
    border: float = 0.0  # width of the outer border band

    @property
    def canvas_rect(self) -> QRectF:
        return QRectF(0, 0, self.canvas.width(), self.canvas.height())

    @property
    def plate_rect(self) -> QRectF:
        """The screenshot plus its outer border - the shape that casts the shadow."""
        b = self.border
        return self.image_rect.adjusted(-b, -b, b, b)


def canvas_layout(doc: Document) -> Layout:
    """Compute canvas size and the screenshot's placement inside it."""
    size = doc.image_size()
    iw, ih = max(1, size.width()), max(1, size.height())
    frame = doc.frame

    pad = frame.padding * PADDING_UNIT * min(iw, ih)
    border = max(0.0, frame.outer_border) * OUTER_BORDER_UNIT * min(iw, ih)

    # Padding is the gap the user sees, so it is measured from the outside of
    # the border band - widening the border pushes the canvas out rather than
    # eating into the padding.
    pw, ph = iw + border * 2, ih + border * 2

    # A rotated plate needs a slightly bigger box or the corners clip.
    if abs(frame.rotation) > 0.01:
        rad = math.radians(abs(frame.rotation))
        rot_w = pw * math.cos(rad) + ph * math.sin(rad)
        rot_h = pw * math.sin(rad) + ph * math.cos(rad)
        pad_x = pad + (rot_w - iw) / 2
        pad_y = pad + (rot_h - ih) / 2
    else:
        pad_x = pad_y = pad + border

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
    return Layout(QSize(int(round(cw)), int(round(ch))), image_rect, border)


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


def _plate_radius(frame: FrameSpec, layout: Layout) -> float:
    """Outer radius of the border band, concentric with the screenshot's.

    Adding the band width keeps the two curves parallel, so the band stays an
    even thickness all the way round instead of pinching at the corners. A
    square screenshot keeps a square band.
    """
    inner = _radius_for(frame, layout.image_rect)
    return inner + layout.border if inner > 0.01 else 0.0


def _dev(rect: QRectF, scale: float) -> QRectF:
    """A document-unit rect in device pixels."""
    return QRectF(
        rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale
    )


def _rotation(frame: FrameSpec, layout: Layout, scale: float) -> QTransform | None:
    """The tilt shared by the shadow, the border band and the screenshot."""
    if abs(frame.rotation) <= 0.01:
        return None
    center = layout.image_rect.center()
    transform = QTransform()
    transform.translate(center.x() * scale, center.y() * scale)
    transform.rotate(frame.rotation)
    transform.translate(-center.x() * scale, -center.y() * scale)
    return transform


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

    # The band is part of the plate, so it is what throws the shadow - but the
    # blur and offset stay keyed to the screenshot, so framing a shot does not
    # silently restyle its shadow.
    rect = layout.plate_rect
    short_edge = min(layout.image_rect.width(), layout.image_rect.height())
    radius = _plate_radius(frame, layout) * scale
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
    rotation = _rotation(frame, layout, scale)
    if rotation is not None:
        painter.setTransform(rotation)
    painter.drawImage(
        QPointF(rect.x() * scale - margin, rect.y() * scale - margin + offset), shadow
    )
    painter.restore()


def _draw_outer_border(
    painter: QPainter, layout: Layout, frame: FrameSpec, scale: float
) -> None:
    """The band around the screenshot, laid down before the screenshot itself.

    Filling the whole plate and letting the screenshot cover the middle is what
    keeps a translucent band honest. Stroking the plate edge instead would blend
    the inner half of the stroke with the screenshot's own pixels, so the mat
    would read as a different colour on a dark shot than on a light one.
    """
    if layout.border <= 0.05 or frame.outer_border_color.alpha() == 0:
        return

    painter.save()
    rotation = _rotation(frame, layout, scale)
    if rotation is not None:
        painter.setTransform(rotation)
    dev_rect = _dev(layout.plate_rect, scale)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(border_brush(frame, dev_rect))
    painter.drawPath(rounded_path(dev_rect, _plate_radius(frame, layout) * scale))
    painter.restore()


def border_brush(frame: FrameSpec, rect: QRectF) -> QBrush:
    """How the outer border is filled: a flat colour, or a glass sheen.

    The sheen runs top-left to bottom-right, which is where the light comes
    from in every other shadow this app draws, and fades the *alpha* rather
    than the colour - so it stays a see-through pane picking up the background
    instead of turning into a white-to-grey stripe.
    """
    near = QColor(frame.outer_border_color)
    if not frame.outer_border_glass:
        return QBrush(near)

    far = QColor(near)
    far.setAlpha(int(round(near.alpha() * GLASS_FALLOFF)))
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, near)
    gradient.setColorAt(1.0, far)
    return QBrush(gradient)


def _draw_plate(
    painter: QPainter, doc: Document, layout: Layout, scale: float
) -> None:
    image = bake_redactions(doc)
    if image.isNull():
        return

    frame = doc.frame
    dev_rect = _dev(layout.image_rect, scale)
    radius = _radius_for(frame, layout.image_rect) * scale

    painter.save()
    rotation = _rotation(frame, layout, scale)
    if rotation is not None:
        painter.setTransform(rotation)

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
    _draw_outer_border(painter, layout, doc.frame, scale)
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

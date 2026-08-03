"""Small imaging and geometry helpers shared across Shotpad."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
)


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rounded rectangle path, radius clamped to what the rect can hold."""
    path = QPainterPath()
    r = max(0.0, min(radius, min(rect.width(), rect.height()) / 2.0))
    if r <= 0.01:
        path.addRect(rect)
    else:
        path.addRoundedRect(rect, r, r)
    return path


def blur_image(src: QImage, radius: float) -> QImage:
    """Blur an image using Qt's graphics effect pipeline.

    Rendering through a QGraphicsScene is the only blur Qt exposes without
    pulling in a numeric stack, and it keeps the AppImage lean.
    """
    if radius <= 0.05 or src.isNull():
        return src

    margin = int(math.ceil(radius * 2)) + 2
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(src))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    out = QImage(
        src.width() + margin * 2,
        src.height() + margin * 2,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(
        painter,
        QRectF(0, 0, out.width(), out.height()),
        QRectF(-margin, -margin, out.width(), out.height()),
    )
    painter.end()
    scene.clear()
    return out.copy(margin, margin, src.width(), src.height())


def pixelate_image(src: QImage, block: int) -> QImage:
    """Downscale/upscale round-trip - the cheapest honest pixelation."""
    if src.isNull():
        return src
    block = max(2, int(block))
    w = max(1, src.width() // block)
    h = max(1, src.height() // block)
    small = src.scaled(
        w, h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return small.scaled(
        src.width(), src.height(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )


def checkerboard_brush(size: int = 12, dark: bool = False) -> QBrush:
    """Transparency checkerboard used behind images with an alpha channel."""
    a, b = (QColor(58, 58, 62), QColor(48, 48, 52)) if dark else (
        QColor(255, 255, 255), QColor(222, 222, 226)
    )
    tile = QPixmap(size * 2, size * 2)
    tile.fill(a)
    painter = QPainter(tile)
    painter.fillRect(0, 0, size, size, b)
    painter.fillRect(size, size, size, size, b)
    painter.end()
    return QBrush(tile)


def scaled_cover(src: QImage, target: QSize) -> QImage:
    """Scale preserving aspect ratio so the image fully covers `target`."""
    if src.isNull() or target.isEmpty():
        return src
    scaled = src.scaled(
        target,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - target.width()) // 2)
    y = max(0, (scaled.height() - target.height()) // 2)
    return scaled.copy(QRect(x, y, target.width(), target.height()))


def dist_point_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
    ax, ay = a.x(), a.y()
    vx, vy = b.x() - ax, b.y() - ay
    len_sq = vx * vx + vy * vy
    if len_sq < 1e-9:
        return math.hypot(p.x() - ax, p.y() - ay)
    t = max(0.0, min(1.0, ((p.x() - ax) * vx + (p.y() - ay) * vy) / len_sq))
    return math.hypot(p.x() - (ax + t * vx), p.y() - (ay + t * vy))


def normalized_rect(a: QPointF, b: QPointF) -> QRectF:
    return QRectF(a, b).normalized()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def contrasting_text_color(bg: QColor) -> QColor:
    luma = 0.2126 * bg.redF() + 0.7152 * bg.greenF() + 0.0722 * bg.blueF()
    return QColor(20, 20, 24) if luma > 0.55 else QColor(250, 250, 252)


def color_to_hex(color: QColor) -> str:
    return color.name(QColor.NameFormat.HexArgb)


def color_from_hex(text: str, fallback: QColor | None = None) -> QColor:
    color = QColor(text)
    if not color.isValid():
        return QColor(fallback) if fallback else QColor(Qt.GlobalColor.black)
    return color

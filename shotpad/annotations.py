"""Annotation objects.

Every annotation stores its geometry in *source image coordinates* - the pixel
space of the original screenshot before cropping, padding or scaling. That way
padding, corner radius, aspect ratio and crop can all change underneath an
annotation without it drifting off the thing it points at. Coordinates outside
the image bounds are legal: an arrow may start out in the padding area.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

from .util import clamp, dist_point_to_segment, normalized_rect


def _pen(color: QColor, width: float, cap=Qt.PenCapStyle.RoundCap) -> QPen:
    pen = QPen(color, max(0.5, width))
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


@dataclass
class Annotation:
    """Base class. Subclasses are plain dataclasses with a draw/hit contract."""

    color: QColor = field(default_factory=lambda: QColor("#e5484d"))
    width: float = 6.0

    #: pixel-effect annotations are composited into the base layer instead of
    #: being stroked on top of it
    pixel_effect: bool = field(default=False, init=False, repr=False)

    kind: str = field(default="annotation", init=False, repr=False)

    # -- geometry -----------------------------------------------------------
    def bounds(self) -> QRectF:
        raise NotImplementedError

    def translate(self, dx: float, dy: float) -> None:
        self.map_points(lambda p: QPointF(p.x() + dx, p.y() + dy))

    def map_points(self, fn) -> None:
        """Remap every stored point - used by rotate, flip and crop-flatten."""
        raise NotImplementedError

    def scale_sizes(self, factor: float) -> None:
        """Scale size-like attributes that are not points (radii, type size)."""
        self.width *= factor

    def handles(self) -> list[QPointF]:
        return []

    def move_handle(self, index: int, point: QPointF) -> None:
        pass

    def hit(self, point: QPointF, tolerance: float) -> bool:
        return self.bounds().adjusted(
            -tolerance, -tolerance, tolerance, tolerance
        ).contains(point)

    # -- painting -----------------------------------------------------------
    def draw(self, painter: QPainter) -> None:
        raise NotImplementedError

    def is_degenerate(self) -> bool:
        """True when the shape is too small to be worth keeping."""
        b = self.bounds()
        return b.width() < 1.5 and b.height() < 1.5


# ---------------------------------------------------------------------------
# Freehand
# ---------------------------------------------------------------------------


@dataclass
class PenStroke(Annotation):
    points: list[QPointF] = field(default_factory=list)
    smooth: bool = True

    def __post_init__(self) -> None:
        self.kind = "pen"

    def _path(self) -> QPainterPath:
        path = QPainterPath()
        pts = self.points
        if not pts:
            return path
        path.moveTo(pts[0])
        if len(pts) == 1:
            path.lineTo(pts[0] + QPointF(0.01, 0.01))
        elif not self.smooth or len(pts) < 3:
            for p in pts[1:]:
                path.lineTo(p)
        else:
            # Quadratic through midpoints: cheap Catmull-Rom-ish smoothing that
            # removes the polyline faceting you get from raw mouse samples.
            for i in range(1, len(pts) - 1):
                mid = (pts[i] + pts[i + 1]) / 2.0
                path.quadTo(pts[i], mid)
            path.lineTo(pts[-1])
        return path

    def bounds(self) -> QRectF:
        if not self.points:
            return QRectF()
        xs = [p.x() for p in self.points]
        ys = [p.y() for p in self.points]
        pad = self.width / 2 + 1
        return QRectF(
            min(xs) - pad, min(ys) - pad,
            max(xs) - min(xs) + pad * 2, max(ys) - min(ys) + pad * 2,
        )

    def map_points(self, fn) -> None:
        self.points = [fn(p) for p in self.points]

    def hit(self, point: QPointF, tolerance: float) -> bool:
        tol = tolerance + self.width / 2
        if len(self.points) == 1:
            return (self.points[0] - point).manhattanLength() <= tol * 2
        return any(
            dist_point_to_segment(point, a, b) <= tol
            for a, b in zip(self.points, self.points[1:])
        )

    def draw(self, painter: QPainter) -> None:
        painter.setPen(_pen(self.color, self.width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path())

    def is_degenerate(self) -> bool:
        return len(self.points) < 2 and self.width < 1


@dataclass
class HighlighterStroke(PenStroke):
    def __post_init__(self) -> None:
        self.kind = "highlighter"

    def draw(self, painter: QPainter) -> None:
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        color = QColor(self.color)
        color.setAlpha(115)
        pen = _pen(color, self.width * 3.0, Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path())
        painter.restore()

    def bounds(self) -> QRectF:
        b = super().bounds()
        extra = self.width * 1.5
        return b.adjusted(-extra, -extra, extra, extra)


# ---------------------------------------------------------------------------
# Two-point shapes
# ---------------------------------------------------------------------------


@dataclass
class TwoPointAnnotation(Annotation):
    start: QPointF = field(default_factory=QPointF)
    end: QPointF = field(default_factory=QPointF)

    def bounds(self) -> QRectF:
        pad = self.width / 2 + 1
        return normalized_rect(self.start, self.end).adjusted(-pad, -pad, pad, pad)

    def map_points(self, fn) -> None:
        self.start = fn(self.start)
        self.end = fn(self.end)

    def handles(self) -> list[QPointF]:
        return [QPointF(self.start), QPointF(self.end)]

    def move_handle(self, index: int, point: QPointF) -> None:
        if index == 0:
            self.start = QPointF(point)
        elif index == 1:
            self.end = QPointF(point)

    def is_degenerate(self) -> bool:
        # Measure the geometry itself, not bounds() - that is inflated by the
        # stroke width, which would let a stray click leave an invisible
        # zero-length shape behind in the document.
        return (
            abs(self.end.x() - self.start.x()) < 2.0
            and abs(self.end.y() - self.start.y()) < 2.0
        )


@dataclass
class LineAnn(TwoPointAnnotation):
    dashed: bool = False

    def __post_init__(self) -> None:
        self.kind = "line"

    def hit(self, point: QPointF, tolerance: float) -> bool:
        return dist_point_to_segment(point, self.start, self.end) <= (
            tolerance + self.width / 2
        )

    def draw(self, painter: QPainter) -> None:
        pen = _pen(self.color, self.width)
        if self.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([3.0, 2.5])
        painter.setPen(pen)
        painter.drawLine(self.start, self.end)


@dataclass
class ArrowAnn(TwoPointAnnotation):
    head_scale: float = 3.2
    double_headed: bool = False

    #: Shaft thickness at the tail, as a fraction of the thickness where it
    #: meets the head. Below 1.0 the arrow tapers, which reads as direction on
    #: its own - the eye follows the widening towards the point. 1.0 gives the
    #: old uniform shaft.
    taper: float = 0.35

    def __post_init__(self) -> None:
        self.kind = "arrow"

    def hit(self, point: QPointF, tolerance: float) -> bool:
        return dist_point_to_segment(point, self.start, self.end) <= (
            tolerance + self.width
        )

    def _head(self, tip: QPointF, tail: QPointF) -> QPolygonF:
        angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
        size = max(self.width * self.head_scale, 6.0)
        spread = math.radians(26)
        p1 = QPointF(
            tip.x() - size * math.cos(angle - spread),
            tip.y() - size * math.sin(angle - spread),
        )
        p2 = QPointF(
            tip.x() - size * math.cos(angle + spread),
            tip.y() - size * math.sin(angle + spread),
        )
        # Pull the base in slightly so the shaft does not poke through the head.
        base = QPointF(
            tip.x() - size * 0.72 * math.cos(angle),
            tip.y() - size * 0.72 * math.sin(angle),
        )
        return QPolygonF([tip, p1, base, p2])

    def _shaft(
        self, start: QPointF, end: QPointF, half_tail: float, half_tip: float
    ) -> QPainterPath:
        """The shaft as a filled quad, narrow at `start` and wide at `end`.

        A stroked line cannot vary in width, so the tapered shaft has to be a
        filled shape. The tail is capped with a small arc rather than cut flat,
        which stops it looking snapped off at low taper values.
        """
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        path = QPainterPath()
        if length < 1e-6:
            return path

        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux  # unit normal

        path.moveTo(start.x() + nx * half_tail, start.y() + ny * half_tail)
        path.lineTo(end.x() + nx * half_tip, end.y() + ny * half_tip)
        path.lineTo(end.x() - nx * half_tip, end.y() - ny * half_tip)
        path.lineTo(start.x() - nx * half_tail, start.y() - ny * half_tail)
        path.quadTo(
            start.x() - ux * half_tail * 1.4,
            start.y() - uy * half_tail * 1.4,
            start.x() + nx * half_tail,
            start.y() + ny * half_tail,
        )
        path.closeSubpath()
        return path

    def draw(self, painter: QPainter) -> None:
        length = math.hypot(self.end.x() - self.start.x(), self.end.y() - self.start.y())
        if length < 0.5:
            return
        head = max(self.width * self.head_scale, 6.0)
        ux = (self.end.x() - self.start.x()) / length
        uy = (self.end.y() - self.start.y()) / length

        shaft_start = QPointF(self.start)
        shaft_end = QPointF(
            self.end.x() - ux * head * 0.65, self.end.y() - uy * head * 0.65
        )
        if self.double_headed:
            shaft_start = QPointF(
                self.start.x() + ux * head * 0.65, self.start.y() + uy * head * 0.65
            )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.color))

        # A taper needs somewhere to happen: on a stubby arrow the head already
        # takes up most of the length, so fall back to an even shaft. Likewise
        # with two heads, where a taper would make the arrow look lopsided.
        shaft_length = math.hypot(
            shaft_end.x() - shaft_start.x(), shaft_end.y() - shaft_start.y()
        )
        taper = 1.0 if self.double_headed else clamp(self.taper, 0.05, 1.0)
        if shaft_length < self.width * 1.5:
            taper = 1.0

        if shaft_length > 0.5:
            painter.drawPath(
                self._shaft(
                    shaft_start, shaft_end,
                    self.width * 0.5 * taper,
                    self.width * 0.5,
                )
            )

        painter.drawPolygon(self._head(self.end, self.start))
        if self.double_headed:
            painter.drawPolygon(self._head(self.start, self.end))


@dataclass
class RectAnn(TwoPointAnnotation):
    filled: bool = False
    radius: float = 0.0

    def __post_init__(self) -> None:
        self.kind = "rect"

    def handles(self) -> list[QPointF]:
        r = normalized_rect(self.start, self.end)
        return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]

    def move_handle(self, index: int, point: QPointF) -> None:
        r = normalized_rect(self.start, self.end)
        corners = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        opposite = corners[(index + 2) % 4]
        self.start, self.end = QPointF(opposite), QPointF(point)

    def hit(self, point: QPointF, tolerance: float) -> bool:
        r = normalized_rect(self.start, self.end)
        tol = tolerance + self.width / 2
        if self.filled:
            return r.adjusted(-tol, -tol, tol, tol).contains(point)
        outer = r.adjusted(-tol, -tol, tol, tol)
        inner = r.adjusted(tol, tol, -tol, -tol)
        return outer.contains(point) and not inner.contains(point)

    def draw(self, painter: QPainter) -> None:
        r = normalized_rect(self.start, self.end)
        painter.setPen(Qt.PenStyle.NoPen if self.filled else _pen(self.color, self.width))
        painter.setBrush(QBrush(self.color) if self.filled else Qt.BrushStyle.NoBrush)
        if self.radius > 0.1:
            painter.drawRoundedRect(r, self.radius, self.radius)
        else:
            painter.drawRect(r)


@dataclass
class EllipseAnn(RectAnn):
    def __post_init__(self) -> None:
        self.kind = "ellipse"

    def hit(self, point: QPointF, tolerance: float) -> bool:
        r = normalized_rect(self.start, self.end)
        if r.width() < 1e-6 or r.height() < 1e-6:
            return False
        cx, cy = r.center().x(), r.center().y()
        nx = (point.x() - cx) / (r.width() / 2)
        ny = (point.y() - cy) / (r.height() / 2)
        value = nx * nx + ny * ny
        tol = (tolerance + self.width) / max(r.width(), r.height()) * 2
        if self.filled:
            return value <= (1 + tol) ** 2
        return (1 - tol) ** 2 <= value <= (1 + tol) ** 2

    def draw(self, painter: QPainter) -> None:
        r = normalized_rect(self.start, self.end)
        painter.setPen(Qt.PenStyle.NoPen if self.filled else _pen(self.color, self.width))
        painter.setBrush(QBrush(self.color) if self.filled else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(r)


# ---------------------------------------------------------------------------
# Text and numbering
# ---------------------------------------------------------------------------


@dataclass
class TextAnn(Annotation):
    position: QPointF = field(default_factory=QPointF)
    text: str = ""
    font_size: float = 32.0
    font_family: str = "Sans Serif"
    bold: bool = False
    italic: bool = False
    background: QColor | None = None
    outline: bool = True

    def __post_init__(self) -> None:
        self.kind = "text"

    def font(self) -> QFont:
        font = QFont(self.font_family)
        font.setPointSizeF(max(4.0, self.font_size))
        font.setBold(self.bold)
        font.setItalic(self.italic)
        return font

    def _lines(self) -> list[str]:
        return self.text.split("\n") if self.text else [""]

    def bounds(self) -> QRectF:
        metrics = QFontMetricsF(self.font())
        lines = self._lines()
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
        height = metrics.lineSpacing() * len(lines)
        pad = self.font_size * 0.18
        return QRectF(
            self.position.x() - pad,
            self.position.y() - pad,
            max(width, self.font_size * 0.6) + pad * 2,
            height + pad * 2,
        )

    def map_points(self, fn) -> None:
        self.position = fn(self.position)

    def scale_sizes(self, factor: float) -> None:
        self.font_size *= factor

    def handles(self) -> list[QPointF]:
        return [self.bounds().bottomRight()]

    def move_handle(self, index: int, point: QPointF) -> None:
        # Dragging the corner scales the type size.
        height = max(4.0, point.y() - self.position.y())
        lines = max(1, len(self._lines()))
        self.font_size = max(6.0, height / lines * 0.78)

    def draw(self, painter: QPainter) -> None:
        font = self.font()
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        if self.background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self.background))
            radius = self.font_size * 0.22
            painter.drawRoundedRect(self.bounds(), radius, radius)

        baseline_x = self.position.x()
        y = self.position.y() + metrics.ascent()
        for line in self._lines():
            if self.outline and self.background is None:
                path = QPainterPath()
                path.addText(baseline_x, y, font, line)
                halo = QColor(0, 0, 0, 150)
                if self.color.lightnessF() < 0.4:
                    halo = QColor(255, 255, 255, 170)
                pen = QPen(halo, max(1.0, self.font_size * 0.11))
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(QBrush(self.color))
                painter.drawPath(path)
            else:
                painter.setPen(QPen(self.color))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawText(QPointF(baseline_x, y), line)
            y += metrics.lineSpacing()

    def is_degenerate(self) -> bool:
        return not self.text.strip()


@dataclass
class NumberAnn(Annotation):
    center: QPointF = field(default_factory=QPointF)
    number: int = 1
    radius: float = 22.0

    def __post_init__(self) -> None:
        self.kind = "number"

    def bounds(self) -> QRectF:
        r = self.radius + self.width / 2
        return QRectF(
            self.center.x() - r, self.center.y() - r, r * 2, r * 2
        )

    def map_points(self, fn) -> None:
        self.center = fn(self.center)

    def scale_sizes(self, factor: float) -> None:
        self.radius *= factor

    def handles(self) -> list[QPointF]:
        return [QPointF(self.center.x() + self.radius, self.center.y())]

    def move_handle(self, index: int, point: QPointF) -> None:
        self.radius = max(
            8.0, math.hypot(point.x() - self.center.x(), point.y() - self.center.y())
        )

    def hit(self, point: QPointF, tolerance: float) -> bool:
        return math.hypot(
            point.x() - self.center.x(), point.y() - self.center.y()
        ) <= self.radius + tolerance

    def draw(self, painter: QPainter) -> None:
        rect = QRectF(
            self.center.x() - self.radius, self.center.y() - self.radius,
            self.radius * 2, self.radius * 2,
        )
        painter.setPen(_pen(QColor(255, 255, 255, 235), max(1.5, self.radius * 0.10)))
        painter.setBrush(QBrush(self.color))
        painter.drawEllipse(rect)

        font = QFont("Sans Serif")
        font.setBold(True)
        font.setPointSizeF(max(6.0, self.radius * 1.05))
        painter.setFont(font)
        text_color = QColor(255, 255, 255)
        if self.color.lightnessF() > 0.65:
            text_color = QColor(25, 25, 30)
        painter.setPen(QPen(text_color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self.number))

    def is_degenerate(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Pixel effects (composited into the base layer)
# ---------------------------------------------------------------------------


@dataclass
class RedactAnn(TwoPointAnnotation):
    """Blur / pixelate / solid-block redaction over a rectangular region."""

    mode: str = "blur"  # blur | pixelate | solid
    strength: float = 18.0

    def __post_init__(self) -> None:
        self.kind = "redact"
        self.pixel_effect = True

    def rect(self) -> QRectF:
        return normalized_rect(self.start, self.end)

    def scale_sizes(self, factor: float) -> None:
        self.strength *= factor

    def bounds(self) -> QRectF:
        return self.rect()

    def handles(self) -> list[QPointF]:
        r = self.rect()
        return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]

    def move_handle(self, index: int, point: QPointF) -> None:
        r = self.rect()
        corners = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        opposite = corners[(index + 2) % 4]
        self.start, self.end = QPointF(opposite), QPointF(point)

    def hit(self, point: QPointF, tolerance: float) -> bool:
        return self.rect().adjusted(
            -tolerance, -tolerance, tolerance, tolerance
        ).contains(point)

    def draw(self, painter: QPainter) -> None:
        # Only reached when the region falls outside the screenshot, where
        # there are no pixels to redact; show the intent instead of nothing.
        if self.mode == "solid":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self.color))
            painter.drawRect(self.rect())


ANNOTATION_TYPES = {
    "pen": PenStroke,
    "highlighter": HighlighterStroke,
    "line": LineAnn,
    "arrow": ArrowAnn,
    "rect": RectAnn,
    "ellipse": EllipseAnn,
    "text": TextAnn,
    "number": NumberAnn,
    "redact": RedactAnn,
}

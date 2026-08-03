"""Document model: the screenshot plus every setting that shapes the export."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QTransform

from .annotations import Annotation

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

GRADIENT_PRESETS: list[tuple[str, str, str, int]] = [
    ("Sunset", "#ff7e5f", "#feb47b", 135),
    ("Grape", "#7f5af0", "#2cb67d", 135),
    ("Ocean", "#2193b0", "#6dd5ed", 135),
    ("Ember", "#f12711", "#f5af19", 120),
    ("Mint", "#00b09b", "#96c93d", 135),
    ("Berry", "#c94b4b", "#4b134f", 150),
    ("Slate", "#232526", "#414345", 135),
    ("Peach", "#ffecd2", "#fcb69f", 120),
    ("Aurora", "#00c6ff", "#0072ff", 160),
    ("Candy", "#ee9ca7", "#ffdde1", 135),
    ("Deep", "#0f2027", "#2c5364", 140),
    ("Blush", "#eecda3", "#ef629f", 135),
    ("Neon", "#12c2e9", "#f64f59", 135),
    ("Forest", "#134e5e", "#71b280", 135),
    ("Royal", "#141e30", "#243b55", 135),
    ("Coral", "#ff512f", "#dd2476", 135),
]

SOLID_PRESETS: list[str] = [
    "#ffffff", "#f4f4f5", "#e4e4e7", "#a1a1aa", "#52525b", "#27272a",
    "#18181b", "#0b0b0f", "#1e3a8a", "#0369a1", "#047857", "#4d7c0f",
    "#b45309", "#b91c1c", "#a21caf", "#4c1d95",
]

ASPECT_PRESETS: list[tuple[str, tuple[int, int] | None]] = [
    ("Auto", None),
    ("1:1", (1, 1)),
    ("4:3", (4, 3)),
    ("3:2", (3, 2)),
    ("16:9", (16, 9)),
    ("21:9", (21, 9)),
    ("9:16", (9, 16)),
    ("3:4", (3, 4)),
]


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass
class BackgroundSpec:
    kind: str = "linear"  # solid | linear | radial | image | transparent
    # Ocean, matching DEFAULTS["default_background"] in settings.py - a document
    # built without going through _apply_document_defaults should still look
    # like the one the user gets from a capture.
    color1: QColor = field(default_factory=lambda: QColor("#2193b0"))
    color2: QColor = field(default_factory=lambda: QColor("#6dd5ed"))
    angle: int = 135
    image_path: str = ""
    image_blur: float = 0.0
    image_dim: float = 0.0
    noise: float = 0.0

    def clone(self) -> "BackgroundSpec":
        return replace(self, color1=QColor(self.color1), color2=QColor(self.color2))


@dataclass
class FrameSpec:
    """Everything about how the screenshot itself sits on the canvas."""

    padding: int = 12          # 0-200, percentage-ish knob (see canvas_layout)
    corner_radius: int = 9     # 0-100 knob
    shadow_strength: int = 55  # 0-100
    shadow_blur: int = 45      # 0-100
    shadow_offset: int = 18    # 0-100
    shadow_color: QColor = field(default_factory=lambda: QColor(0, 0, 0))
    rotation: float = 0.0      # degrees, -25..25
    aspect: tuple[int, int] | None = None
    border_width: float = 0.0
    border_color: QColor = field(default_factory=lambda: QColor(255, 255, 255, 60))

    def clone(self) -> "FrameSpec":
        return replace(
            self,
            shadow_color=QColor(self.shadow_color),
            border_color=QColor(self.border_color),
        )


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


class Document:
    """Source image + crop + frame + background + annotations, with undo."""

    MAX_HISTORY = 80

    def __init__(self, image: QImage | None = None) -> None:
        self.source: QImage = image if image is not None else QImage()
        self.crop: QRect | None = None
        self.frame = FrameSpec()
        self.background = BackgroundSpec()
        self.annotations: list[Annotation] = []
        self.source_path: str = ""

        # Two counters so the canvas can cache the expensive base layer and
        # still repaint vector annotations at interactive speed.
        self.base_revision = 0
        self.ann_revision = 0

        self._undo: list[dict] = []
        self._redo: list[dict] = []

    # -- geometry -----------------------------------------------------------
    def image_size(self) -> QSize:
        if self.crop is not None and self.crop.isValid():
            return self.crop.size()
        return self.source.size()

    def crop_rect(self) -> QRect:
        if self.crop is not None and self.crop.isValid():
            return QRect(self.crop)
        return QRect(0, 0, self.source.width(), self.source.height())

    def cropped_image(self) -> QImage:
        if self.crop is not None and self.crop.isValid():
            return self.source.copy(self.crop)
        return self.source

    def is_empty(self) -> bool:
        return self.source.isNull()

    # -- change tracking ----------------------------------------------------
    def touch_base(self) -> None:
        self.base_revision += 1

    def touch_annotations(self) -> None:
        self.ann_revision += 1

    def touch_all(self) -> None:
        self.base_revision += 1
        self.ann_revision += 1

    # -- undo / redo --------------------------------------------------------
    def _snapshot(self) -> dict:
        return {
            "crop": QRect(self.crop) if self.crop is not None else None,
            "frame": self.frame.clone(),
            "background": self.background.clone(),
            "annotations": copy.deepcopy(self.annotations),
        }

    def _restore(self, state: dict) -> None:
        self.crop = QRect(state["crop"]) if state["crop"] is not None else None
        self.frame = state["frame"].clone()
        self.background = state["background"].clone()
        self.annotations = copy.deepcopy(state["annotations"])
        self.touch_all()

    def push_undo(self) -> None:
        """Record the current state before a change is applied."""
        self._undo.append(self._snapshot())
        if len(self._undo) > self.MAX_HISTORY:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True

    def clear_history(self) -> None:
        self._undo.clear()
        self._redo.clear()

    # -- annotations --------------------------------------------------------
    def next_number(self) -> int:
        from .annotations import NumberAnn

        used = [a.number for a in self.annotations if isinstance(a, NumberAnn)]
        return max(used) + 1 if used else 1

    def add_annotation(self, annotation: Annotation) -> None:
        self.annotations.append(annotation)
        if annotation.pixel_effect:
            self.touch_base()
        self.touch_annotations()

    def remove_annotation(self, annotation: Annotation) -> None:
        if annotation in self.annotations:
            self.annotations.remove(annotation)
            if annotation.pixel_effect:
                self.touch_base()
            self.touch_annotations()

    # -- image ops ----------------------------------------------------------
    def set_image(self, image: QImage, path: str = "") -> None:
        self.source = image
        self.source_path = path
        self.crop = None
        self.annotations.clear()
        self.clear_history()
        self.touch_all()

    def flatten_crop(self) -> None:
        """Make the current crop permanent, rebasing annotations onto it.

        Rotating or flipping a cropped document is ambiguous, so the crop is
        baked in first and annotation coordinates are shifted to match.
        """
        if self.crop is None or not self.crop.isValid():
            return
        offset = self.crop.topLeft()
        self.source = self.source.copy(self.crop)
        self.crop = None
        for ann in self.annotations:
            ann.map_points(
                lambda p: QPointF(p.x() - offset.x(), p.y() - offset.y())
            )

    def rotate_source(self, degrees: int) -> None:
        """Rotate the pixels in 90 degree steps, carrying annotations along."""
        if self.source.isNull():
            return
        self.flatten_crop()
        width, height = self.source.width(), self.source.height()

        self.source = self.source.transformed(
            QTransform().rotate(degrees),
            mode=Qt.TransformationMode.SmoothTransformation,
        )

        if degrees % 360 == 90:
            remap = lambda p: QPointF(height - p.y(), p.x())          # noqa: E731
        elif degrees % 360 == 270:
            remap = lambda p: QPointF(p.y(), width - p.x())           # noqa: E731
        elif degrees % 360 == 180:
            remap = lambda p: QPointF(width - p.x(), height - p.y())  # noqa: E731
        else:
            remap = None

        if remap is None:
            self.annotations.clear()
        else:
            for ann in self.annotations:
                ann.map_points(remap)
        self.touch_all()

    def flip_source(self, horizontal: bool) -> None:
        if self.source.isNull():
            return
        self.flatten_crop()
        width, height = self.source.width(), self.source.height()
        # QImage.mirrored is deprecated and QImage.flipped is Qt 6.9+; a
        # negative scale works on every version we support.
        self.source = self.source.transformed(
            QTransform().scale(-1, 1) if horizontal else QTransform().scale(1, -1)
        )

        if horizontal:
            remap = lambda p: QPointF(width - p.x(), p.y())   # noqa: E731
        else:
            remap = lambda p: QPointF(p.x(), height - p.y())  # noqa: E731
        for ann in self.annotations:
            ann.map_points(remap)
        self.touch_all()

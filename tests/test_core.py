"""Headless tests for the document model and rendering pipeline.

Run with:  QT_QPA_PLATFORM=offscreen python -m pytest tests -q
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from shotpad.annotations import (  # noqa: E402
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
from shotpad.model import Document  # noqa: E402
from shotpad.render import (  # noqa: E402
    annotation_transform,
    bake_redactions,
    canvas_layout,
    export_scale_for,
    render_document,
)
from shotpad.util import blur_image, pixelate_image, scaled_cover  # noqa: E402


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication(sys.argv[:1])
    yield instance


def make_image(width=400, height=300, color="#3355aa") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    # A distinctive marker in the top-left quadrant so orientation is testable.
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 4, height // 4, QColor("#ff0000"))
    painter.end()
    return image


# ---------------------------------------------------------------- layout


def test_padding_grows_the_canvas(app):
    doc = Document(make_image())
    doc.frame.padding = 0
    doc.frame.rotation = 0
    bare = canvas_layout(doc).canvas

    doc.frame.padding = 100
    padded = canvas_layout(doc).canvas

    assert bare.width() == 400 and bare.height() == 300
    assert padded.width() > bare.width()
    assert padded.height() > bare.height()
    # Padding is symmetric, so the image stays centred.
    rect = canvas_layout(doc).image_rect
    assert rect.x() == pytest.approx(
        canvas_layout(doc).canvas.width() - rect.right(), abs=1.0
    )


def test_outer_border_grows_the_canvas_without_eating_the_padding(app):
    doc = Document(make_image(400, 300))
    doc.frame.padding = 40
    doc.frame.rotation = 0
    plain = canvas_layout(doc)

    doc.frame.outer_border = 5.0  # 5% of the 300px short edge = 15px
    framed = canvas_layout(doc)

    assert framed.border == pytest.approx(15.0)
    assert framed.canvas.width() == plain.canvas.width() + 30
    assert framed.canvas.height() == plain.canvas.height() + 30
    # The screenshot is unchanged and still centred; the band is what is new.
    assert framed.image_rect.size() == plain.image_rect.size()
    assert framed.image_rect.x() == pytest.approx(
        framed.canvas.width() - framed.image_rect.right(), abs=1.0
    )
    assert framed.plate_rect.width() == pytest.approx(
        framed.image_rect.width() + 30
    )


def test_outer_border_paints_a_band_around_the_screenshot(app):
    doc = Document(make_image(400, 300, "#000000"))
    doc.frame.padding = 20
    doc.frame.rotation = 0
    doc.frame.corner_radius = 0
    doc.frame.shadow_strength = 0
    doc.frame.outer_border = 5.0
    doc.frame.outer_border_color = QColor("#ff00ff")
    doc.background.kind = "solid"
    doc.background.color1 = QColor("#ffffff")

    layout = canvas_layout(doc)
    out = render_document(doc, 1.0)

    # Just outside the screenshot is band; just inside it is screenshot.
    middle = int(layout.image_rect.center().y())
    band = out.pixelColor(int(layout.image_rect.x()) - 7, middle)
    shot = out.pixelColor(int(layout.image_rect.x()) + 7, middle)
    outside = out.pixelColor(2, middle)
    assert band == QColor("#ff00ff")
    assert shot == QColor("#000000")
    assert outside == QColor("#ffffff")


def test_glass_border_fades_across_the_diagonal(app):
    """What separates glass from a flat mat: the sheen is brightest top-left."""
    doc = Document(make_image(400, 400, "#000000"))
    doc.frame.padding = 20
    doc.frame.rotation = 0
    doc.frame.corner_radius = 0
    doc.frame.shadow_strength = 0
    doc.frame.outer_border = 6.0
    doc.frame.outer_border_color = QColor(255, 255, 255, 128)
    doc.background.kind = "solid"
    doc.background.color1 = QColor("#000000")

    layout = canvas_layout(doc)
    band = layout.border / 2.0
    near_x = int(layout.plate_rect.x() + band)
    near_y = int(layout.plate_rect.y() + band)
    far_x = int(layout.plate_rect.right() - band)
    far_y = int(layout.plate_rect.bottom() - band)

    doc.frame.outer_border_glass = False
    flat = render_document(doc, 1.0)
    assert flat.pixelColor(near_x, near_y).red() == pytest.approx(
        flat.pixelColor(far_x, far_y).red(), abs=2
    )

    doc.frame.outer_border_glass = True
    glass = render_document(doc, 1.0)
    near = glass.pixelColor(near_x, near_y).red()
    far = glass.pixelColor(far_x, far_y).red()
    assert near > far + 20, f"expected a falloff, got {near} -> {far}"
    # It fades the alpha, not the hue: over black, white at any alpha is grey.
    corner = glass.pixelColor(near_x, near_y)
    assert corner.red() == corner.green() == corner.blue()


def test_glass_is_the_first_border_preset(app):
    from shotpad.model import BORDER_PRESETS

    assert BORDER_PRESETS[0].glass is True
    assert BORDER_PRESETS[0].name == "Glass"
    assert sum(1 for p in BORDER_PRESETS if p.glass) == 1


def test_frame_clone_carries_the_glass_flag(app):
    doc = Document(make_image())
    doc.frame.outer_border_glass = True
    assert doc.frame.clone().outer_border_glass is True


def test_outer_border_is_off_by_default(app):
    doc = Document(make_image())
    assert doc.frame.outer_border == 0.0
    assert canvas_layout(doc).border == 0.0
    assert canvas_layout(doc).plate_rect == canvas_layout(doc).image_rect


def test_frame_clone_copies_the_border_colour(app):
    doc = Document(make_image())
    doc.frame.outer_border_color = QColor("#80123456")
    clone = doc.frame.clone()
    clone.outer_border_color.setAlpha(255)
    assert doc.frame.outer_border_color.alpha() == 0x80


def test_aspect_ratio_is_honoured(app):
    doc = Document(make_image(400, 300))
    doc.frame.aspect = (16, 9)
    canvas = canvas_layout(doc).canvas
    assert canvas.width() / canvas.height() == pytest.approx(16 / 9, abs=0.01)

    doc.frame.aspect = (9, 16)
    canvas = canvas_layout(doc).canvas
    assert canvas.width() / canvas.height() == pytest.approx(9 / 16, abs=0.01)


def test_annotation_transform_round_trips(app):
    doc = Document(make_image())
    doc.frame.padding = 60
    doc.frame.rotation = 7.5
    doc.crop = QRect(20, 30, 200, 150)

    transform = annotation_transform(doc, 0.63)
    inverse, ok = transform.inverted()
    assert ok
    for point in (QPointF(0, 0), QPointF(123.5, 88.25), QPointF(-40, 400)):
        back = inverse.map(transform.map(point))
        assert back.x() == pytest.approx(point.x(), abs=1e-6)
        assert back.y() == pytest.approx(point.y(), abs=1e-6)


def test_padding_does_not_move_annotations_relative_to_the_image(app):
    """The whole point of source-space coordinates."""
    doc = Document(make_image())
    target = QPointF(100, 75)

    doc.frame.padding = 10
    a = annotation_transform(doc, 1.0).map(target)
    origin_a = annotation_transform(doc, 1.0).map(QPointF(0, 0))

    doc.frame.padding = 180
    b = annotation_transform(doc, 1.0).map(target)
    origin_b = annotation_transform(doc, 1.0).map(QPointF(0, 0))

    # The offset from the image origin is unchanged.
    assert (a - origin_a).x() == pytest.approx((b - origin_b).x(), abs=1e-6)
    assert (a - origin_a).y() == pytest.approx((b - origin_b).y(), abs=1e-6)


# ---------------------------------------------------------------- rendering


def test_render_matches_layout_size(app):
    doc = Document(make_image())
    layout = canvas_layout(doc)
    out = render_document(doc, 1.0)
    assert out.width() == layout.canvas.width()
    assert out.height() == layout.canvas.height()


def test_export_scale_multiplies_dimensions(app):
    doc = Document(make_image(200, 150))
    one = render_document(doc, 1.0)
    two = render_document(doc, 2.0)
    assert two.width() == pytest.approx(one.width() * 2, abs=2)
    assert two.height() == pytest.approx(one.height() * 2, abs=2)


def test_export_scale_is_clamped(app):
    doc = Document(make_image(6000, 4000))
    assert export_scale_for(doc, 1.0) <= 1.0
    assert export_scale_for(doc, 8.0) < 8.0
    assert export_scale_for(doc, 1.0, max_pixels=10**12) == 1.0


def test_transparent_background_keeps_alpha(app):
    doc = Document(make_image())
    doc.background.kind = "transparent"
    doc.frame.padding = 100
    doc.frame.shadow_strength = 0
    out = render_document(doc, 1.0)
    assert out.hasAlphaChannel()
    assert out.pixelColor(2, 2).alpha() == 0


def test_solid_background_fills_the_corners(app):
    doc = Document(make_image())
    doc.background.kind = "solid"
    doc.background.color1 = QColor("#00ff00")
    doc.frame.padding = 120
    doc.frame.shadow_strength = 0
    out = render_document(doc, 1.0)
    corner = out.pixelColor(1, 1)
    assert corner.green() > 200 and corner.red() < 40


def test_every_annotation_type_renders(app):
    doc = Document(make_image())
    doc.annotations = [
        PenStroke(points=[QPointF(10, 10), QPointF(50, 60), QPointF(90, 20)]),
        HighlighterStroke(points=[QPointF(10, 100), QPointF(120, 110)]),
        LineAnn(start=QPointF(10, 150), end=QPointF(200, 160)),
        ArrowAnn(start=QPointF(20, 200), end=QPointF(180, 250)),
        RectAnn(start=QPointF(200, 20), end=QPointF(320, 90)),
        EllipseAnn(start=QPointF(220, 120), end=QPointF(340, 200), filled=True),
        TextAnn(position=QPointF(40, 260), text="hello\nworld", font_size=20),
        NumberAnn(center=QPointF(360, 260), number=3),
        RedactAnn(start=QPointF(0, 0), end=QPointF(90, 70), mode="pixelate"),
    ]
    out = render_document(doc, 1.0)
    assert not out.isNull()
    assert out.width() > 0


# ---------------------------------------------------------------- redaction


def test_redaction_is_baked_into_the_screenshot(app):
    doc = Document(make_image())
    before = doc.cropped_image().copy()
    doc.annotations.append(
        RedactAnn(start=QPointF(0, 0), end=QPointF(100, 75), mode="solid",
                  color=QColor("#000000"))
    )
    after = bake_redactions(doc)
    assert before.pixelColor(10, 10) != after.pixelColor(10, 10)
    assert after.pixelColor(10, 10).red() < 20
    # Outside the region nothing changed.
    assert before.pixelColor(300, 250) == after.pixelColor(300, 250)


def test_redaction_respects_the_crop_offset(app):
    doc = Document(make_image())
    doc.crop = QRect(100, 100, 200, 150)
    # Source coordinates (110,110) are (10,10) inside the crop.
    doc.annotations.append(
        RedactAnn(start=QPointF(110, 110), end=QPointF(150, 140), mode="solid",
                  color=QColor("#000000"))
    )
    baked = bake_redactions(doc)
    assert baked.pixelColor(20, 20).red() < 20      # inside
    assert baked.pixelColor(70, 60).red() > 20      # outside


def test_redaction_survives_tilt(app):
    """A tilted plate must not shift the redaction off its target."""
    doc = Document(make_image())
    doc.annotations.append(
        RedactAnn(start=QPointF(0, 0), end=QPointF(100, 75), mode="solid",
                  color=QColor("#000000"))
    )
    flat = bake_redactions(doc)
    doc.frame.rotation = 12.0
    tilted = bake_redactions(doc)
    assert flat == tilted


# ---------------------------------------------------------------- transforms


def test_rotation_carries_annotations(app):
    doc = Document(make_image(400, 300))
    marker = QPointF(10, 10)  # inside the red marker square
    doc.annotations.append(NumberAnn(center=QPointF(marker), number=1))

    doc.rotate_source(90)
    assert doc.source.width() == 300 and doc.source.height() == 400
    assert len(doc.annotations) == 1

    moved = doc.annotations[0].center
    # The red marker is now in the top-right corner; the badge went with it.
    assert moved.x() == pytest.approx(300 - 10, abs=1.0)
    assert moved.y() == pytest.approx(10, abs=1.0)
    assert doc.source.pixelColor(int(moved.x()) - 5, int(moved.y())).red() > 200


def test_four_rotations_return_to_the_start(app):
    doc = Document(make_image(400, 300))
    doc.annotations.append(NumberAnn(center=QPointF(37, 61), number=1))
    for _ in range(4):
        doc.rotate_source(90)
    assert doc.source.width() == 400 and doc.source.height() == 300
    point = doc.annotations[0].center
    assert point.x() == pytest.approx(37, abs=1.0)
    assert point.y() == pytest.approx(61, abs=1.0)


def test_flip_carries_annotations(app):
    doc = Document(make_image(400, 300))
    doc.annotations.append(NumberAnn(center=QPointF(10, 10), number=1))
    doc.flip_source(horizontal=True)
    assert doc.annotations[0].center.x() == pytest.approx(390, abs=1.0)
    assert doc.annotations[0].center.y() == pytest.approx(10, abs=1.0)


def test_flatten_crop_rebases_annotations(app):
    doc = Document(make_image(400, 300))
    doc.crop = QRect(50, 40, 200, 150)
    doc.annotations.append(NumberAnn(center=QPointF(90, 80), number=1))
    doc.flatten_crop()
    assert doc.crop is None
    assert doc.source.width() == 200 and doc.source.height() == 150
    assert doc.annotations[0].center == QPointF(40, 40)


def test_rotate_after_crop_keeps_the_crop(app):
    doc = Document(make_image(400, 300))
    doc.crop = QRect(0, 0, 200, 150)
    doc.rotate_source(90)
    assert doc.crop is None
    assert doc.source.width() == 150 and doc.source.height() == 200


# ---------------------------------------------------------------- undo


def test_undo_restores_annotations_and_frame(app):
    doc = Document(make_image())
    doc.frame.padding = 20

    doc.push_undo()
    doc.add_annotation(ArrowAnn(start=QPointF(0, 0), end=QPointF(50, 50)))
    doc.frame.padding = 150

    assert doc.can_undo()
    doc.undo()
    assert doc.annotations == []
    assert doc.frame.padding == 20

    assert doc.can_redo()
    doc.redo()
    assert len(doc.annotations) == 1
    assert doc.frame.padding == 150


def test_undo_history_is_bounded(app):
    doc = Document(make_image())
    for index in range(Document.MAX_HISTORY + 30):
        doc.push_undo()
        doc.add_annotation(NumberAnn(center=QPointF(index, index), number=index))
    assert len(doc._undo) <= Document.MAX_HISTORY


def test_undo_snapshots_are_deep_copies(app):
    doc = Document(make_image())
    arrow = ArrowAnn(start=QPointF(0, 0), end=QPointF(10, 10))
    doc.add_annotation(arrow)
    doc.push_undo()
    arrow.translate(100, 100)
    doc.undo()
    assert doc.annotations[0].start == QPointF(0, 0)


def test_next_number_increments(app):
    doc = Document(make_image())
    assert doc.next_number() == 1
    doc.add_annotation(NumberAnn(center=QPointF(0, 0), number=1))
    doc.add_annotation(NumberAnn(center=QPointF(0, 0), number=2))
    assert doc.next_number() == 3


# ---------------------------------------------------------------- hit testing


def test_hit_testing_finds_shapes(app):
    line = LineAnn(start=QPointF(0, 0), end=QPointF(100, 100), width=4)
    assert line.hit(QPointF(50, 50), 2)
    assert not line.hit(QPointF(50, 80), 2)

    rect = RectAnn(start=QPointF(0, 0), end=QPointF(100, 100), width=4)
    assert rect.hit(QPointF(0, 50), 3)       # on the edge
    assert not rect.hit(QPointF(50, 50), 3)  # hollow centre
    rect.filled = True
    assert rect.hit(QPointF(50, 50), 3)

    ellipse = EllipseAnn(start=QPointF(0, 0), end=QPointF(100, 50), filled=True)
    assert ellipse.hit(QPointF(50, 25), 1)
    assert not ellipse.hit(QPointF(2, 2), 1)

    badge = NumberAnn(center=QPointF(50, 50), radius=20)
    assert badge.hit(QPointF(60, 55), 0)
    assert not badge.hit(QPointF(90, 90), 0)


def test_degenerate_shapes_are_rejected(app):
    assert ArrowAnn(start=QPointF(5, 5), end=QPointF(5, 5)).is_degenerate()
    assert not ArrowAnn(start=QPointF(0, 0), end=QPointF(80, 0)).is_degenerate()
    assert TextAnn(position=QPointF(0, 0), text="   ").is_degenerate()
    assert not TextAnn(position=QPointF(0, 0), text="hi").is_degenerate()


def test_handles_resize_shapes(app):
    rect = RectAnn(start=QPointF(0, 0), end=QPointF(100, 100))
    rect.move_handle(2, QPointF(200, 150))   # bottom-right
    bounds = rect.bounds()
    assert bounds.width() >= 199
    assert bounds.height() >= 149


# ---------------------------------------------------------------- utilities


def test_blur_preserves_size(app):
    source = make_image(120, 90)
    blurred = blur_image(source, 6)
    assert blurred.size() == source.size()
    assert blurred != source


def test_pixelate_preserves_size_and_flattens(app):
    source = make_image(120, 90)
    out = pixelate_image(source, 20)
    assert out.size() == source.size()
    # Neighbouring pixels inside one block are identical.
    assert out.pixelColor(41, 41) == out.pixelColor(45, 45)


def test_scaled_cover_fills_exactly(app):
    from PySide6.QtCore import QSize

    out = scaled_cover(make_image(400, 100), QSize(200, 200))
    assert out.size() == QSize(200, 200)


def test_zero_size_document_does_not_crash(app):
    doc = Document(QImage())
    assert doc.is_empty()
    layout = canvas_layout(doc)
    assert layout.canvas.width() >= 1
    out = render_document(doc, 1.0)
    assert not out.isNull()


# ---------------------------------------------------------------- portal


def test_response_slot_signature_is_registered(app):
    """Guard against the silent-hang bug class.

    QtDBus resolves the slot by signature string. If RESPONSE_SLOT and the
    @Slot decorator ever drift apart, bus.connect() returns False, the portal's
    Response signal is never delivered, and every capture hangs until it times
    out - with no error anywhere pointing at the cause. Assert here that the
    signature Shotpad asks for is one the class actually registered.
    """
    from PySide6.QtCore import QMetaObject

    from shotpad.capture.portal import RESPONSE_SLOT, _ResponseWaiter

    # SLOT() prefixes the signature with a metacall type code.
    wanted = RESPONSE_SLOT.lstrip("0123456789")
    assert wanted == "_on_response(uint,QVariantMap)"

    meta: QMetaObject = _ResponseWaiter.staticMetaObject
    registered = {
        meta.method(i).methodSignature().data().decode()
        for i in range(meta.methodOffset(), meta.methodCount())
    }
    assert wanted in registered, (
        f"{wanted} is not a registered slot; found {sorted(registered)}"
    )


def test_response_slot_demarshals_to_a_plain_dict(app):
    """The slot must take (uint, QVariantMap), not QDBusMessage.

    With a QDBusMessage parameter the results arrive as a QDBusArgument whose
    streaming reader is broken in PySide6, so the screenshot URI silently comes
    back empty and capture fails with "the portal returned no image".
    """
    from PySide6.QtCore import QMetaObject

    from shotpad.capture.portal import _ResponseWaiter

    meta: QMetaObject = _ResponseWaiter.staticMetaObject
    index = meta.indexOfMethod("_on_response(uint,QVariantMap)")
    assert index >= 0
    types = [
        bytes(t).decode() for t in meta.method(index).parameterTypes()
    ]
    assert types == ["uint", "QVariantMap"]


def test_escape_in_the_desktops_picker_is_a_cancellation(app):
    """Response 2 after a visible UI must not read as a failed capture.

    GNOME reports 2 - "ended in some other way" - when Escape closes its
    screenshot UI. Treating that as an error sent the capture on to the next
    backend, which opened a second picker and then told the user their desktop
    could not be captured.
    """
    from shotpad.capture.portal import (
        PortalCancelled,
        PortalError,
        raise_for_response,
    )

    raise_for_response(0, 1.0)  # success raises nothing

    with pytest.raises(PortalCancelled):
        raise_for_response(1, 0.0)
    with pytest.raises(PortalCancelled):
        raise_for_response(2, 1.5)

    # But a portal declining before any UI could exist is a real failure, so
    # the other backends still get a turn.
    with pytest.raises(PortalError) as caught:
        raise_for_response(2, 0.0)
    assert not isinstance(caught.value, PortalCancelled)


def test_a_cancelled_portal_grab_stops_the_backend_walk(app):
    """CaptureCancelled has to escape grab_screen rather than be collected."""
    from shotpad.capture import backends
    from shotpad.capture.portal import PortalCancelled

    def cancelled(*args, **kwargs):
        raise PortalCancelled()

    original = backends.portal_screenshot
    backends.portal_screenshot = cancelled
    try:
        with pytest.raises(backends.CaptureCancelled):
            backends._portal_grab()
        with pytest.raises(backends.CaptureCancelled):
            backends._portal_interactive_grab()
    finally:
        backends.portal_screenshot = original


def test_portal_only_deletes_files_it_caused(app, tmp_path):
    """GNOME's portal writes into ~/Pictures, so deletion must be guarded."""
    import time

    from shotpad.capture.portal import _uri_to_image

    image = make_image(20, 20)

    # A file that predates the request must survive.
    old = tmp_path / "pre-existing.png"
    image.save(str(old))
    os.utime(old, (time.time() - 600, time.time() - 600))
    _uri_to_image(old.as_uri(), created_after=time.time())
    assert old.exists(), "a pre-existing file must never be deleted"

    # A file the portal just wrote for us is cleaned up.
    fresh = tmp_path / "portal-output.png"
    image.save(str(fresh))
    _uri_to_image(fresh.as_uri(), created_after=time.time())
    assert not fresh.exists(), "the portal's transport file should be removed"

    # Without a timestamp nothing is deleted at all.
    keep = tmp_path / "keep.png"
    image.save(str(keep))
    _uri_to_image(keep.as_uri())
    assert keep.exists()


# ---------------------------------------------------------------- clipboard


def test_clipboard_copy_then_exit_does_not_crash():
    """A copy must not take the process down on the way out.

    QClipboard.setMimeData() transfers ownership to Qt, but PySide frees the
    wrapper again during interpreter shutdown and the process dies with SIGSEGV
    *after* main() has returned. Nothing in-process can catch that, so this has
    to be an exit code from a real subprocess.

    It is worth a test because the symptom is so misleading: everything works,
    the image lands on the clipboard, and then the app crashes as it quits.
    """
    code = textwrap.dedent(
        """
        import sys
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage, QColor
        app = QApplication(sys.argv[:1])
        from shotpad.clipboard import set_clipboard_image
        image = QImage(60, 40, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#336699"))
        set_clipboard_image(image)
        """
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=root)
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, timeout=120,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"copying then exiting crashed with {result.returncode}: "
        f"{result.stderr.decode('utf-8', 'replace')[-400:]}"
    )


def test_window_close_with_copy_enabled_exits_cleanly():
    """The same crash, reached the way a user reaches it: by closing the window."""
    code = textwrap.dedent(
        """
        import sys
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtCore import QTimer
        app = QApplication(sys.argv[:1])
        from shotpad.settings import settings
        settings.set("copy_on_close", True)
        from shotpad.theme import apply_theme
        apply_theme(app, "dark")
        from shotpad.ui.window import MainWindow
        image = QImage(200, 150, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#336699"))
        window = MainWindow()
        window.show()
        window._load_image(image, "")
        QTimer.singleShot(300, lambda: (window.close(), app.quit()))
        app.exec()
        """
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=root)
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, timeout=180, capture_output=True
    )
    assert result.returncode == 0, (
        f"closing the window crashed with {result.returncode}: "
        f"{result.stderr.decode('utf-8', 'replace')[-400:]}"
    )


def test_holder_command_reruns_the_appimage(app, monkeypatch, tmp_path):
    """Inside an AppImage the child must re-run the bundle, not our interpreter.

    The parent's /tmp/.mount_* squashfs disappears the instant the parent
    exits, taking the interpreter and Qt with it, so a child launched as
    sys.executable would die immediately.
    """
    from shotpad.clipboard import DAEMON_FLAG, _holder_command

    bundle = tmp_path / "Shotpad.AppImage"
    bundle.write_text("#!/bin/sh\n")
    monkeypatch.setenv("APPIMAGE", str(bundle))

    command = _holder_command("/tmp/x.png")
    assert command[0] == str(bundle)
    assert command[1] == DAEMON_FLAG
    assert command[2] == "/tmp/x.png"


def test_holder_command_falls_back_to_the_module(app, monkeypatch):
    from shotpad.clipboard import DAEMON_FLAG, _holder_command

    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr("sys.argv", ["pytest"])
    command = _holder_command("/tmp/x.png")
    assert command[1:] == ["-m", "shotpad", DAEMON_FLAG, "/tmp/x.png"]


def test_holder_pidfile_round_trip(app, monkeypatch, tmp_path):
    """Only one holder may exist; the newest one wins."""
    from shotpad.clipboard import (
        _clear_pidfile,
        _pidfile_path,
        _replace_previous_holder,
    )

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    # A stale pid belonging to no live Shotpad must not stop us claiming.
    with open(_pidfile_path(), "w", encoding="utf-8") as handle:
        handle.write("999999999")

    _replace_previous_holder()
    with open(_pidfile_path(), encoding="utf-8") as handle:
        assert int(handle.read()) == os.getpid()

    _clear_pidfile()
    assert not os.path.exists(_pidfile_path())


def test_copy_on_close_default_is_off(app):
    """Closing the window must not silently replace the user's clipboard.

    Taking the clipboard is a side effect on something the user owns and did
    not ask about, so it is opt-in from Preferences rather than on by default.
    """
    from shotpad.settings import DEFAULTS

    assert DEFAULTS["copy_on_close"] is False


def test_new_document_defaults(app):
    """The framing a fresh screenshot starts with."""
    from shotpad.model import FrameSpec
    from shotpad.settings import DEFAULTS

    assert FrameSpec().padding == 12
    assert FrameSpec().corner_radius == 9
    assert DEFAULTS["default_padding"] == 12
    assert DEFAULTS["default_radius"] == 9


def test_shortcut_reference_covers_every_tool(app):
    """The Preferences list is generated, so it cannot drift from the bindings."""
    from shotpad.ui.window import TOOLS, shortcut_groups

    groups = dict(shortcut_groups())
    listed = {keys for keys, _ in groups["Tools"]}
    assert listed == {shortcut for _k, _i, _l, _t, shortcut in TOOLS}
    for name in ("Capture", "File", "Edit", "Tools", "View"):
        assert groups[name], f"{name} section is empty"


# ---------------------------------------------------------------- arrow taper


def test_arrow_tapers_from_tail_to_head(app):
    """The shaft must actually get wider towards the point.

    Measured off a render rather than asserted on geometry, so this fails if the
    drawing changes shape rather than just if the numbers move.
    """
    from PySide6.QtGui import QPainter

    def shaft_thickness(taper: float, at: float) -> int:
        canvas = QImage(400, 120, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor("#ffffff"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        ArrowAnn(
            color=QColor("#000000"), width=20,
            start=QPointF(30, 60), end=QPointF(370, 60), taper=taper,
        ).draw(painter)
        painter.end()
        # Count dark pixels in a vertical slice through the shaft.
        x = int(30 + (370 - 30) * at)
        return sum(
            1 for y in range(120)
            if canvas.pixelColor(x, y).lightnessF() < 0.5
        )

    near_tail = shaft_thickness(0.35, 0.06)
    mid = shaft_thickness(0.35, 0.40)
    near_head = shaft_thickness(0.35, 0.72)

    assert 0 < near_tail < mid < near_head, (
        f"expected a widening shaft, measured {near_tail} / {mid} / {near_head}"
    )

    # taper=1.0 is the old uniform shaft and must stay uniform.
    flat_tail = shaft_thickness(1.0, 0.06)
    flat_head = shaft_thickness(1.0, 0.72)
    assert abs(flat_tail - flat_head) <= 1


def test_double_headed_arrow_is_not_tapered(app):
    """A taper on a two-headed arrow would just look lopsided."""
    from PySide6.QtGui import QPainter

    canvas = QImage(400, 120, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#ffffff"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    ArrowAnn(
        color=QColor("#000000"), width=20, start=QPointF(30, 60),
        end=QPointF(370, 60), taper=0.2, double_headed=True,
    ).draw(painter)
    painter.end()

    def thickness(x: int) -> int:
        return sum(
            1 for y in range(120) if canvas.pixelColor(x, y).lightnessF() < 0.5
        )

    assert abs(thickness(140) - thickness(260)) <= 1


def test_short_arrow_degrades_to_an_even_shaft(app):
    """No room to taper on a stubby arrow; it must still draw without error."""
    from PySide6.QtGui import QPainter

    canvas = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#ffffff"))
    painter = QPainter(canvas)
    ArrowAnn(
        color=QColor("#000000"), width=12,
        start=QPointF(20, 40), end=QPointF(45, 40),
    ).draw(painter)
    painter.end()
    dark = sum(
        1 for y in range(80) for x in range(80)
        if canvas.pixelColor(x, y).lightnessF() < 0.5
    )
    assert dark > 0, "a short arrow should still render its head"


def test_arrow_taper_is_clamped(app):
    """Nonsense values must not invert the shaft."""
    from PySide6.QtGui import QPainter

    canvas = QImage(200, 80, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#ffffff"))
    painter = QPainter(canvas)
    for taper in (-5.0, 0.0, 40.0):
        ArrowAnn(
            color=QColor("#000000"), width=10, start=QPointF(20, 40),
            end=QPointF(180, 40), taper=taper,
        ).draw(painter)
    painter.end()
    assert not canvas.isNull()


# ------------------------------------------------------------ window controls


def test_window_controls_order_and_signals(app):
    """Cinnamon order is minimise, maximise, close - close in the corner.

    Worth pinning: the buttons are hand-drawn, so the mapping from x position to
    signal is just an index into a list and a reorder would silently wire
    "close" to the minimise button.
    """
    from shotpad.ui.titlebar import WindowControls

    controls = WindowControls()
    fired: list[str] = []
    controls.minimiseClicked.connect(lambda: fired.append("minimise"))
    controls.maximiseClicked.connect(lambda: fired.append("maximise"))
    controls.closeClicked.connect(lambda: fired.append("close"))

    for index in range(3):
        centre = controls._button_rect(index).center()
        assert controls._index_at(centre) == index
        controls._pressed = index
        # The 5-argument QMouseEvent overload is deprecated; pass the scene
        # position too so Qt uses the current constructor.
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(centre),
            QPointF(centre),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        controls.mouseReleaseEvent(event)

    assert fired == ["minimise", "maximise", "close"]

    # Close must be the right-most control.
    assert controls._button_rect(WindowControls.CLOSE).right() >= (
        controls.width() - 2
    )


def test_window_controls_glyphs_are_always_drawn(app):
    """No hover required: the glyphs must be visible at rest."""
    from PySide6.QtGui import QPixmap

    from shotpad.ui.titlebar import WindowControls

    controls = WindowControls()
    controls._hovered = -1
    canvas = QPixmap(controls.size())
    canvas.fill(QColor("#212127"))
    controls.render(canvas)

    image = canvas.toImage()
    # Each of the three buttons must contain pixels lighter than the backdrop.
    for index in range(3):
        rect = controls._button_rect(index)
        lit = sum(
            1
            for y in range(rect.top(), rect.bottom())
            for x in range(rect.left(), rect.right())
            if image.pixelColor(x, y).lightnessF() > 0.4
        )
        assert lit > 5, f"button {index} appears to have no glyph"


def test_area_erase_takes_what_it_covers_and_leaves_the_rest(app):
    """The eraser's box deletes by geometry, not by bounding box.

    Worth pinning: a long diagonal arrow has a huge, mostly empty bounding
    box, so sweeping a corner of it would take the whole arrow with it if
    intersects() ever falls back to bounds.
    """
    from shotpad.ui.canvas import Canvas

    canvas = Canvas()
    doc = Document()
    doc.source = make_image()
    canvas.set_document(doc)

    box = QRectF(60, 60, 280, 180)
    inside = RectAnn(start=QPointF(80, 80), end=QPointF(200, 160), width=5)
    crossing = LineAnn(start=QPointF(20, 300), end=QPointF(330, 90), width=5)
    # Its box overlaps `box`; its shaft passes well clear of the nearest corner.
    grazing = ArrowAnn(start=QPointF(100, 430), end=QPointF(600, 100), width=5)
    away = EllipseAnn(start=QPointF(420, 300), end=QPointF(560, 420), width=5)
    for ann in (inside, crossing, grazing, away):
        doc.add_annotation(ann)

    canvas._drag_origin = box.topLeft()
    canvas._erase(box)

    assert doc.annotations == [grazing, away]

    doc.undo()
    assert len(doc.annotations) == 4


def test_erasing_nothing_does_not_push_an_undo_step(app):
    """A click on empty canvas must not cost the user their undo history."""
    from shotpad.ui.canvas import Canvas

    canvas = Canvas()
    doc = Document()
    doc.source = make_image()
    canvas.set_document(doc)
    doc.add_annotation(RectAnn(start=QPointF(10, 10), end=QPointF(40, 40), width=4))

    canvas._drag_origin = QPointF(380, 280)
    canvas._erase(QRectF(380, 280, 1, 1))

    assert len(doc.annotations) == 1
    assert not doc.undo()


def test_pen_stroke_intersects_only_where_the_ink_is(app):
    """A stroke drawn along the top edge is not caught by a box underneath it."""
    stroke = PenStroke(
        width=4, points=[QPointF(x, 20 + (x % 12)) for x in range(20, 300, 10)]
    )

    assert stroke.intersects(QRectF(100, 10, 40, 40))
    assert not stroke.intersects(QRectF(100, 120, 40, 40))
    # The box need not contain the whole stroke, only meet it.
    assert stroke.intersects(QRectF(0, 0, 60, 60))


def test_tool_rail_names_widen_it_and_the_choice_sticks(app):
    """The rail toggle is the only way to reach the labels, so pin the round trip.

    The expanded width is deliberately not a constant: it comes from the
    layout, so a longer tool name or a larger font cannot clip the labels.
    """
    from shotpad.settings import DEFAULTS, settings
    from shotpad.theme import apply_theme
    from shotpad.ui.window import MainWindow

    # The rail ships open: the names are the discoverable state, and the
    # collapse is for people who already know the icons.
    assert DEFAULTS["tool_rail_labels"] is True

    apply_theme(app, "dark")
    previous = settings.get("tool_rail_labels")
    try:
        settings.set("tool_rail_labels", False)
        window = MainWindow()
        window.show()
        assert window.tool_rail.width() == 52
        assert window.tool_buttons["select"].text() == ""

        window.toggle_tool_names()
        assert window.tool_rail.sizeHint().width() > 52
        assert window.tool_buttons["select"].text().strip() == "Select"
        assert settings.get("tool_rail_labels") is True

        # Every tool must be named, or the rail reads as a list with holes.
        for key, button in window.tool_buttons.items():
            assert button.text().strip(), f"{key} has no label"

        window.toggle_tool_names()
        assert window.tool_rail.width() == 52
        assert settings.get("tool_rail_labels") is False

        # A fresh window follows the saved choice.
        settings.set("tool_rail_labels", True)
        again = MainWindow()
        assert again._rail_expanded
        assert again.tool_buttons["crop"].text().strip() == "Crop"
        again.close()
        window.close()
    finally:
        settings.set("tool_rail_labels", previous)

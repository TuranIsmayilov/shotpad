"""Fullscreen region / window selection overlay.

Rather than delegating area selection to each desktop's own tool (four
different UIs, four sets of quirks, and none of them available everywhere),
Shotpad captures the whole desktop first and then lets you pick a region from a
frozen copy of it. The selection experience is then byte-identical on GNOME,
KDE, XFCE and MATE, on both X11 and Wayland.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import (
    QEventLoop,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from ..capture import list_windows, session_type, window_listing_supported

HANDLE_SIZE = 9
MIN_SELECTION = 4


class ScreenMap:
    """Maps logical desktop coordinates onto pixels of the captured image."""

    def __init__(self, image: QImage, screens: list) -> None:
        self.image = image
        self.screens = screens

        left = min(s.geometry().left() for s in screens)
        top = min(s.geometry().top() for s in screens)
        right = max(s.geometry().right() for s in screens)
        bottom = max(s.geometry().bottom() for s in screens)
        self.virtual = QRect(left, top, right - left + 1, bottom - top + 1)

        # Preferred mapping: every screen contributes geometry * its own DPR.
        dev_right = max(
            round(s.geometry().right() * s.devicePixelRatio()) for s in screens
        )
        dev_bottom = max(
            round(s.geometry().bottom() * s.devicePixelRatio()) for s in screens
        )
        dev_left = min(round(s.geometry().left() * s.devicePixelRatio()) for s in screens)
        dev_top = min(round(s.geometry().top() * s.devicePixelRatio()) for s in screens)
        expected = QSize(dev_right - dev_left + 1, dev_bottom - dev_top + 1)

        close_enough = (
            abs(expected.width() - image.width()) <= 2
            and abs(expected.height() - image.height()) <= 2
        )
        self.per_screen = close_enough
        self.dev_origin = QPoint(dev_left, dev_top)

        # Fallback for compositors that hand back a differently scaled buffer
        # (fractional scaling on GNOME, for instance): stretch uniformly.
        self.sx = image.width() / max(1, self.virtual.width())
        self.sy = image.height() / max(1, self.virtual.height())

    def to_image(self, point: QPointF, screen=None) -> QPointF:
        if self.per_screen:
            scr = screen or QGuiApplication.screenAt(point.toPoint()) or self.screens[0]
            dpr = scr.devicePixelRatio()
            geo = scr.geometry()
            x = (point.x() - geo.x()) * dpr + geo.x() * dpr - self.dev_origin.x()
            y = (point.y() - geo.y()) * dpr + geo.y() * dpr - self.dev_origin.y()
            return QPointF(x, y)
        return QPointF(
            (point.x() - self.virtual.x()) * self.sx,
            (point.y() - self.virtual.y()) * self.sy,
        )

    def rect_to_image(self, rect: QRectF) -> QRect:
        top_left = self.to_image(rect.topLeft())
        bottom_right = self.to_image(rect.bottomRight())
        result = QRectF(top_left, bottom_right).normalized().toRect()
        return result.intersected(QRect(0, 0, self.image.width(), self.image.height()))

    def device_to_logical(self, rect: QRect) -> QRect:
        """Native-pixel desktop coordinates -> the overlay's logical ones.

        X11 reports window geometry in native pixels (xwininfo does not know
        about Qt's scaling), while the overlay, its mouse events and the
        selection all live in logical coordinates. Left unconverted, a window
        highlight under a devicePixelRatio above 1 is both offset and too
        large, so clicking it captures part of the window and a strip of
        desktop.
        """
        screen = self._screen_at_device(rect.center()) or self.screens[0]
        dpr = screen.devicePixelRatio()
        if dpr == 1.0:
            return QRect(rect)
        return QRectF(
            rect.x() / dpr, rect.y() / dpr, rect.width() / dpr, rect.height() / dpr
        ).toRect()

    def _screen_at_device(self, point: QPoint):
        """The screen whose geometry, scaled to native pixels, holds `point`."""
        for screen in self.screens:
            geo = screen.geometry()
            dpr = screen.devicePixelRatio()
            native = QRectF(
                geo.x() * dpr, geo.y() * dpr, geo.width() * dpr, geo.height() * dpr
            )
            if native.contains(QPointF(point)):
                return screen
        return None

    def screen_slice(self, screen) -> QRect:
        """Portion of the captured image belonging to `screen`."""
        geo = QRectF(screen.geometry())
        top_left = self.to_image(geo.topLeft(), screen)
        size = QPointF(
            geo.width() * (screen.devicePixelRatio() if self.per_screen else self.sx),
            geo.height() * (screen.devicePixelRatio() if self.per_screen else self.sy),
        )
        return QRectF(top_left, QSize(int(size.x()), int(size.y()))).toRect()


class _SelectionState(QObject):
    changed = Signal()
    finished = Signal()

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode                     # region | window
        self.origin: QPointF | None = None
        self.rect = QRectF()
        self.dragging = False
        self.moving = False
        self.move_anchor = QPointF()
        self.hover_window: QRect | None = None
        self.cancelled = False
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True
        self.finished.emit()

    def cancel(self) -> None:
        self.cancelled = True
        self.finished.emit()


class _Overlay(QWidget):
    """One fullscreen window per monitor, all sharing a single selection."""

    def __init__(self, screen, mapper: ScreenMap, state: _SelectionState, windows) -> None:
        super().__init__()
        self._screen = screen
        self._map = mapper
        self._state = state
        self._windows = windows

        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if session_type() == "x11":
            flags |= (
                Qt.WindowType.BypassWindowManagerHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setWindowTitle("Shotpad - select an area")

        slice_rect = mapper.screen_slice(screen)
        # Drawn stretched to the widget rect, so the full captured resolution
        # is preserved on HiDPI backing stores.
        self._pixmap = QPixmap.fromImage(mapper.image.copy(slice_rect))

        state.changed.connect(self.update)

    # -- placement ----------------------------------------------------------
    def place(self) -> None:
        geo = self._screen.geometry()
        self.setGeometry(geo)
        # windowHandle() only exists once the widget has a native window, and
        # under Wayland the screen must be chosen before it is shown.
        self.winId()
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(self._screen)
        if session_type() == "x11":
            self.show()
            self.setGeometry(geo)
        else:
            self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def _to_global(self, pos) -> QPointF:
        geo = self._screen.geometry()
        return QPointF(pos.x() + geo.x(), pos.y() + geo.y())

    def _to_local(self, point: QPointF) -> QPointF:
        geo = self._screen.geometry()
        return QPointF(point.x() - geo.x(), point.y() - geo.y())

    # -- input --------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._state.cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        point = self._to_global(event.position())
        state = self._state
        if state.mode == "window" and state.hover_window is not None:
            state.rect = QRectF(state.hover_window)
            state.accept()
            return

        state.origin = point
        state.rect = QRectF(point, point)
        state.dragging = True
        state.moving = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        state.move_anchor = point
        state.changed.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = self._to_global(event.position())
        state = self._state

        if state.dragging and state.origin is not None:
            if state.moving:
                delta = point - state.move_anchor
                state.rect = state.rect.translated(delta)
                state.move_anchor = point
            else:
                rect = QRectF(state.origin, point).normalized()
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    side = max(rect.width(), rect.height())
                    rect.setSize(QSize(int(side), int(side)))
                state.rect = rect
            state.changed.emit()
            return

        if state.mode == "window" or (
            not state.dragging and self._windows and state.rect.isEmpty()
        ):
            hovered = None
            for window in self._windows:
                if window.rect.contains(point.toPoint()):
                    hovered = window.rect
                    break
            if hovered != state.hover_window:
                state.hover_window = hovered
                state.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        state = self._state
        if event.button() != Qt.MouseButton.LeftButton or not state.dragging:
            return
        state.dragging = False
        state.moving = False

        if state.rect.width() < MIN_SELECTION or state.rect.height() < MIN_SELECTION:
            # A click, not a drag: take the window under the cursor if we know
            # of one, otherwise the whole monitor.
            if state.hover_window is not None:
                state.rect = QRectF(state.hover_window)
            else:
                state.rect = QRectF(self._screen.geometry())
        state.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._state.rect.isEmpty():
            self._state.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        state = self._state
        if key == Qt.Key.Key_Escape:
            state.cancel()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if state.rect.isEmpty():
                state.rect = QRectF(self._screen.geometry())
            state.accept()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_Space):
            state.rect = QRectF(self._screen.geometry())
            state.accept()
        elif key == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            state.rect = QRectF(self._map.virtual)
            state.accept()
        else:
            super().keyPressEvent(event)

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawPixmap(self.rect(), self._pixmap)

        state = self._state
        selection = state.rect
        highlight = None
        if not selection.isEmpty():
            highlight = selection
        elif state.hover_window is not None:
            highlight = QRectF(state.hover_window)

        dim = QColor(8, 10, 16, 150)
        if highlight is None:
            painter.fillRect(self.rect(), dim)
            self._draw_hint(painter)
            return

        local = QRectF(
            self._to_local(highlight.topLeft()), self._to_local(highlight.bottomRight())
        ).normalized()

        # Dim everything except the highlighted rectangle.
        full = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dim)
        painter.drawRect(QRectF(full.left(), full.top(), full.width(), local.top() - full.top()))
        painter.drawRect(QRectF(full.left(), local.bottom(), full.width(), full.bottom() - local.bottom()))
        painter.drawRect(QRectF(full.left(), local.top(), local.left() - full.left(), local.height()))
        painter.drawRect(QRectF(local.right(), local.top(), full.right() - local.right(), local.height()))

        accent = QColor("#7c7cf0")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 235), 1.0))
        painter.drawRect(local.adjusted(-1, -1, 1, 1))
        painter.setPen(QPen(accent, 1.4))
        painter.drawRect(local)

        if not state.dragging and not selection.isEmpty():
            self._draw_handles(painter, local, accent)

        self._draw_readout(painter, local, highlight)
        if state.mode == "window" and state.hover_window is not None:
            self._draw_hint(painter)

    def _draw_handles(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        painter.setPen(QPen(QColor(255, 255, 255), 1.4))
        painter.setBrush(accent)
        half = HANDLE_SIZE / 2
        points = [
            rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight(),
            QPointF(rect.center().x(), rect.top()),
            QPointF(rect.center().x(), rect.bottom()),
            QPointF(rect.left(), rect.center().y()),
            QPointF(rect.right(), rect.center().y()),
        ]
        for point in points:
            painter.drawEllipse(point, half, half)

    def _draw_readout(self, painter: QPainter, local: QRectF, global_rect: QRectF) -> None:
        image_rect = self._map.rect_to_image(global_rect)
        label = f"{image_rect.width()} x {image_rect.height()}"

        font = QFont("Sans Serif")
        font.setPointSizeF(10.0)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 16
        height = metrics.height() + 8

        x = local.left()
        y = local.top() - height - 7
        if y < 4:
            y = min(local.bottom() + 7, self.height() - height - 4)
        x = max(4.0, min(x, self.width() - width - 4.0))

        box = QRectF(x, y, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 18, 24, 225))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor(240, 240, 250))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_hint(self, painter: QPainter) -> None:
        lines = [
            "Drag to select an area   -   Click for the window or whole screen",
            "Shift: move selection    Ctrl: square    Enter: this screen    Esc: cancel",
        ]
        font = QFont("Sans Serif")
        font.setPointSizeF(10.5)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(line) for line in lines) + 34
        height = metrics.height() * len(lines) + 24

        box = QRectF(
            (self.width() - width) / 2,
            self.height() * 0.5 - height / 2,
            width, height,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(16, 16, 22, 205))
        painter.drawRoundedRect(box, 12, 12)

        painter.setPen(QColor(235, 235, 245))
        y = box.top() + 12
        for index, line in enumerate(lines):
            if index == 1:
                painter.setPen(QColor(165, 165, 185))
            painter.drawText(
                QRectF(box.left(), y, box.width(), metrics.height()),
                Qt.AlignmentFlag.AlignCenter, line,
            )
            y += metrics.height()


def select_region(image: QImage, mode: str = "region") -> QRect | None:
    """Show the overlay and return the chosen rect in image pixel coordinates.

    Returns None when the user cancelled.
    """
    screens = QGuiApplication.screens()
    if not screens:
        return None

    mapper = ScreenMap(image, screens)
    state = _SelectionState(mode)
    windows = list_windows() if window_listing_supported() else []
    # list_windows() measures in native pixels; everything below is logical.
    windows = [
        replace(window, rect=mapper.device_to_logical(window.rect))
        for window in windows
    ]

    overlays = [_Overlay(screen, mapper, state, windows) for screen in screens]
    loop = QEventLoop()
    state.finished.connect(loop.quit)

    for overlay in overlays:
        overlay.place()
    if overlays:
        overlays[0].setFocus()

    loop.exec()

    for overlay in overlays:
        overlay.close()

    if state.cancelled or not state.accepted or state.rect.isEmpty():
        return None

    rect = mapper.rect_to_image(state.rect)
    if rect.width() < 1 or rect.height() < 1:
        return None
    return rect

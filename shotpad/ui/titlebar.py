"""Window controls and the frameless-window plumbing they need.

Dropping the system title bar means taking over three jobs the window manager
was doing: moving the window, resizing it from its edges, and the buttons
themselves. Qt exposes ``startSystemMove`` and ``startSystemResize``, which
hand the interaction back to the compositor - so this works the same on X11
and Wayland instead of trying to reimplement dragging with raw coordinates
(which Wayland does not allow anyway).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..settings import settings

#: Button hit area and the gap between buttons. Cinnamon's controls are
#: symbolic glyphs inside a circular hover area, sized for comfortable clicking
#: rather than the small dots macOS uses.
BUTTON = 26
GAP = 2
GLYPH = 9.0

#: Mint-Y tints only the close button, and only on hover.
CLOSE_HOVER = QColor("#cc575d")
CLOSE_HOVER_PRESSED = QColor("#b8474d")


class WindowControls(QWidget):
    """Minimise / maximise / close, in the Linux Mint Cinnamon (Mint-Y) style.

    That means: monochrome symbolic glyphs that follow the theme's text colour,
    always visible, ordered left to right as minimise, maximise, close - so
    close lands in the corner where a Linux user reaches for it. A circular
    background appears behind whichever button the pointer is over, and the
    close button alone tints red on hover.

    The maximise glyph becomes a restore glyph while the window is maximised,
    which is what Cinnamon does.
    """

    closeClicked = Signal()
    minimiseClicked = Signal()
    maximiseClicked = Signal()

    MINIMISE, MAXIMISE, CLOSE = 0, 1, 2

    def __init__(
        self,
        parent: QWidget | None = None,
        buttons: tuple[int, ...] = (MINIMISE, MAXIMISE, CLOSE),
    ) -> None:
        """``buttons`` lists which controls to draw, left to right.

        Dialogs pass ``(CLOSE,)``: they are not minimisable or maximisable, and
        drawing dead buttons there would be worse than drawing none.
        """
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self._buttons = tuple(buttons)
        count = len(self._buttons)
        self.setFixedSize(BUTTON * count + GAP * max(0, count - 1), BUTTON)

        self._hovered = -1
        self._pressed = -1
        self._active = True
        self._maximised = False
        self._update_tooltip()

    # -- geometry -----------------------------------------------------------
    def _button_rect(self, slot: int) -> QRect:
        """Rect of the ``slot``-th button from the left (not its kind)."""
        return QRect(slot * (BUTTON + GAP), 0, BUTTON, BUTTON)

    def _index_at(self, point: QPoint) -> int:
        for slot in range(len(self._buttons)):
            if self._button_rect(slot).contains(point):
                return slot
        return -1

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def set_maximized(self, maximised: bool) -> None:
        if self._maximised != maximised:
            self._maximised = maximised
            self._update_tooltip()
            self.update()

    def _update_tooltip(self) -> None:
        names = {
            self.MINIMISE: "Minimise",
            self.MAXIMISE: "Restore" if self._maximised else "Maximise",
            self.CLOSE: "Close",
        }
        self.setToolTip(", ".join(names[kind] for kind in self._buttons))

    # -- input --------------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position().toPoint())
        if index != self._hovered:
            self._hovered = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = -1
        self._pressed = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._index_at(event.position().toPoint())
            self.update()
            # Swallow the press so the header does not start dragging the
            # window out from under the click.
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        slot = self._index_at(event.position().toPoint())
        pressed, self._pressed = self._pressed, -1
        self.update()
        if slot >= 0 and slot == pressed:
            {
                self.MINIMISE: self.minimiseClicked,
                self.MAXIMISE: self.maximiseClicked,
                self.CLOSE: self.closeClicked,
            }[self._buttons[slot]].emit()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        # Never let a double click here reach the drag bar, which would toggle
        # maximise a second time.
        event.accept()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        from ..theme import current as current_theme

        theme = current_theme()

        for slot, kind in enumerate(self._buttons):
            rect = self._button_rect(slot)
            hovered = self._hovered == slot
            pressed = self._pressed == slot

            glyph = QColor(theme.text if self._active else theme.text_dim)

            if hovered or pressed:
                if kind == self.CLOSE:
                    background = CLOSE_HOVER_PRESSED if pressed else CLOSE_HOVER
                    glyph = QColor("#ffffff")
                else:
                    # A translucent overlay rather than a palette colour: on a
                    # light title bar surface_hi is almost the same white as the
                    # background, and the hover simply would not show.
                    if theme.dark:
                        background = QColor(255, 255, 255, 58 if pressed else 32)
                    else:
                        background = QColor(0, 0, 0, 52 if pressed else 28)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(background)
                inset = (BUTTON - (BUTTON - 4)) / 2.0
                painter.drawEllipse(
                    QRectF(rect).adjusted(inset, inset, -inset, -inset)
                )

            self._draw_glyph(painter, kind, rect, glyph)

        painter.end()

    def _draw_glyph(
        self, painter: QPainter, kind: int, rect: QRect, color: QColor
    ) -> None:
        cx = rect.x() + BUTTON / 2.0
        cy = rect.y() + BUTTON / 2.0
        half = GLYPH / 2.0

        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if kind == self.MINIMISE:
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            return

        if kind == self.MAXIMISE:
            if self._maximised:
                # Restore: a front square with the back one peeking out.
                back = QRectF(cx - half + 2.5, cy - half, GLYPH - 2.5, GLYPH - 2.5)
                painter.drawLine(back.topLeft(), back.topRight())
                painter.drawLine(back.topRight(), back.bottomRight())
                painter.drawRect(
                    QRectF(cx - half, cy - half + 2.5, GLYPH - 2.5, GLYPH - 2.5)
                )
            else:
                painter.drawRect(QRectF(cx - half, cy - half, GLYPH, GLYPH))
            return

        # Close: a cross. Round caps read better on a diagonal than square.
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        reach = half * 0.92
        painter.drawLine(
            QPointF(cx - reach, cy - reach), QPointF(cx + reach, cy + reach)
        )
        painter.drawLine(
            QPointF(cx + reach, cy - reach), QPointF(cx - reach, cy + reach)
        )


class TitleDragBar(QWidget):
    """A header that doubles as a title bar.

    Press-and-drag moves the window, double click toggles maximise. Child
    widgets that accept mouse events (every button in the header) consume their
    own clicks first, so only the empty space drags.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enabled = True

    def set_drag_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def mousePressEvent(self, event) -> None:
        if not self._enabled or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self.window().windowHandle()
        if handle is not None:
            handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if not self._enabled or event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        event.accept()


def enable_mouse_tracking(root: QWidget) -> None:
    """Make ``root`` and everything under it report button-less mouse moves.

    Qt only sends a moving pointer to widgets that asked to track it, so
    without this an application-wide filter never learns the pointer is
    hovering the header, the sidebar or the status bar - the very widgets
    sitting against the window's edges. The resize cursor then only ever
    appeared over the bottom-right QSizeGrip, which is a real widget doing its
    own hit testing, making that the only corner that looked resizable.

    Tracking costs nothing beyond extra mouseMoveEvent calls, which widgets
    that do not override it ignore.
    """
    root.setMouseTracking(True)
    for child in root.findChildren(QWidget):
        child.setMouseTracking(True)


class EdgeResizer:
    """Gives a frameless window back its resizable edges.

    Qt delivers mouse events to whichever child widget is under the pointer, so
    a filter on the window alone would never see the edges - the header, canvas
    and sidebar all sit flush against them. This watches the whole application
    and claims the outer few pixels of one specific window.
    """

    MARGIN = 8
    #: Corners reach further along each arm than the edges do. A MARGIN-sized
    #: square is close to unhittable, and the corner is exactly what people aim
    #: for when they want to resize both axes at once.
    CORNER = 18

    def __init__(self, window: QWidget) -> None:
        self._window = window
        self._override_active = False
        self._filter = _EdgeFilter(window, self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._filter)
        enable_mouse_tracking(window)

    def track_new_children(self, widget: QWidget) -> None:
        enable_mouse_tracking(widget)

    def detach(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self._filter)
        self._clear_cursor()

    # -- hit testing --------------------------------------------------------
    def edges_at(self, global_pos: QPoint):
        window = self._window
        if window.isMaximized() or window.isFullScreen() or not window.isVisible():
            return None

        # Work in window-local coordinates. mapFromGlobal goes through the
        # platform's own mapping, so this stays correct on Wayland, where a
        # client is not told where its own window sits on screen.
        pos = window.mapFromGlobal(global_pos)
        rect = window.rect()
        if not rect.contains(pos):
            return None

        margin = self.MARGIN
        edges = Qt.Edge(0)
        if pos.x() <= rect.left() + margin:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= rect.right() - margin:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= rect.top() + margin:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= rect.bottom() - margin:
            edges |= Qt.Edge.BottomEdge

        # Along a horizontal edge, reach further for the corner, and vice
        # versa - so the grabbable corner is an L of CORNER length rather than
        # a MARGIN square.
        corner = self.CORNER
        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            if pos.x() <= rect.left() + corner:
                edges |= Qt.Edge.LeftEdge
            elif pos.x() >= rect.right() - corner:
                edges |= Qt.Edge.RightEdge
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            if pos.y() <= rect.top() + corner:
                edges |= Qt.Edge.TopEdge
            elif pos.y() >= rect.bottom() - corner:
                edges |= Qt.Edge.BottomEdge

        # bool(), not int(): Qt.Edge is an enum.Flag in current PySide6, and
        # int() on one raises TypeError. That threw inside the event filter on
        # every pointer move near an edge, which is what left the bottom-right
        # QSizeGrip as the only working resize handle.
        return edges if edges else None

    @staticmethod
    def cursor_for(edges) -> Qt.CursorShape:
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeVerCursor

    # -- cursor -------------------------------------------------------------
    def apply_cursor(self, edges) -> None:
        if edges is None:
            self._clear_cursor()
            return
        shape = self.cursor_for(edges)
        if self._override_active:
            QApplication.changeOverrideCursor(shape)
        else:
            QApplication.setOverrideCursor(shape)
            self._override_active = True

    def _clear_cursor(self) -> None:
        if self._override_active:
            QApplication.restoreOverrideCursor()
            self._override_active = False

    def begin_resize(self, edges) -> bool:
        handle = self._window.windowHandle()
        if handle is None:
            return False
        self._clear_cursor()
        return bool(handle.startSystemResize(edges))


class ChromeDialog(QDialog):
    """A dialog wearing the same title bar the main window draws.

    Without this, every dialog carried the desktop's own decorations while the
    main window carried Shotpad's - so opening Preferences swapped the window
    buttons for a different set, in a different order, at a different size.
    The dialogs now follow the ``system_titlebar`` preference exactly as the
    main window does, which keeps the two consistent whichever way it is set.

    Subclasses (and callers) put their widgets in ``self.body``.
    """

    #: Shorter than the main window's 52px toolbar: a dialog header holds only
    #: a title, so matching that height would look top-heavy.
    HEADER = 40

    def __init__(
        self,
        parent: QWidget | None = None,
        title: str = "",
        *,
        resizable: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)

        self._resizer: EdgeResizer | None = None
        self._resizable = resizable
        self._custom_chrome = not bool(settings.get("system_titlebar"))
        self._title_label: QLabel | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if self._custom_chrome:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            outer.addWidget(self._build_header(title))

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        # A framed dialog gets its padding from the window manager; ours has to
        # supply its own so the content does not touch the edges.
        self.body_layout.setContentsMargins(16, 14, 16, 14)
        self.body_layout.setSpacing(12)
        outer.addWidget(self.body, 1)

    # -- chrome -------------------------------------------------------------
    def _build_header(self, title: str) -> QWidget:
        from .widgets import separator

        header = TitleDragBar()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(self.HEADER)

        row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(0)

        controls = WindowControls(buttons=(WindowControls.CLOSE,))
        controls.closeClicked.connect(self.reject)
        self.window_controls = controls

        label = QLabel(title)
        label.setObjectName("DialogTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label = label

        # Balance the close button on the left so the title sits in the true
        # centre of the header, the way Cinnamon centres a dialog title.
        spacer = QWidget()
        spacer.setFixedWidth(controls.width())
        row.addWidget(spacer)
        row.addWidget(label, 1)
        row.addWidget(controls)

        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(header)
        wrapper_layout.addWidget(separator())
        return wrapper

    def fit_to_width(self, width: int) -> None:
        """Pin the dialog to ``width`` and grow it to fit the wrapped text.

        A dialog whose width is fixed after the fact keeps the height Qt
        worked out for its natural, unwrapped width - which is far too short,
        so the labels overlap. Asking the layout what it needs at the real
        width is the only way to get this right.
        """
        self.setFixedWidth(width)
        layout = self.layout()
        if layout is not None:
            self.setMinimumHeight(layout.totalHeightForWidth(width))
        self.adjustSize()

    def setWindowTitle(self, title: str) -> None:  # noqa: N802 - Qt override
        super().setWindowTitle(title)
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setText(title)

    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.ActivationChange and self._custom_chrome:
            self.window_controls.set_active(self.isActiveWindow())
        super().changeEvent(event)

    # -- resize edges -------------------------------------------------------
    # EdgeResizer filters events for the whole application, so it must be
    # attached only while the dialog is on screen and dropped again when it
    # closes - otherwise every Preferences visit would leave a filter behind.
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._custom_chrome and self._resizable and self._resizer is None:
            self._resizer = EdgeResizer(self)

    def done(self, result: int) -> None:
        if self._resizer is not None:
            self._resizer.detach()
            self._resizer = None
        super().done(result)


class _EdgeFilter(QWidget):
    """Event filter object for EdgeResizer (kept separate to avoid a cycle)."""

    def __init__(self, window: QWidget, owner: EdgeResizer) -> None:
        super().__init__(None)
        self.hide()
        self._window = window
        self._owner = owner

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent

        kind = event.type()
        if kind not in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Leave,
            QEvent.Type.ChildAdded,
        ):
            return False

        if not isinstance(obj, QWidget) or obj.window() is not self._window:
            return False

        if kind == QEvent.Type.ChildAdded:
            # Panels are rebuilt as tools change; a child added afterwards
            # would otherwise be a blind spot along whichever edge it covers.
            child = event.child()
            if isinstance(child, QWidget):
                self._owner.track_new_children(child)
            return False

        if kind == QEvent.Type.Leave:
            self._owner.apply_cursor(None)
            return False

        global_pos = event.globalPosition().toPoint()
        edges = self._owner.edges_at(global_pos)

        if kind == QEvent.Type.MouseMove:
            # Do not fight the cursor while a button is held: that is a drag
            # inside the app (drawing, dragging an annotation), not a resize.
            if event.buttons() == Qt.MouseButton.NoButton:
                self._owner.apply_cursor(edges)
            return False

        if edges is not None and event.button() == Qt.MouseButton.LeftButton:
            return self._owner.begin_resize(edges)
        return False

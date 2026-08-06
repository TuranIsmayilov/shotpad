"""The main window: header bar, tool rail, canvas, inspector, status bar."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, AUTHOR, COPYRIGHT_YEAR, __version__
from ..capture import (
    CaptureCancelled,
    CaptureError,
    PortalCancelled,
    capture_diagnostics,
    grab_screen,
    grab_was_preselected,
    portal_screenshot,
    window_listing_supported,
)
from ..clipboard import copy_image_persistent, set_clipboard_image
from ..icons import icon as make_icon
from ..model import Document
from ..render import export_scale_for, render_document
from ..settings import settings
from ..theme import apply_theme, current as current_theme
from .canvas import Canvas
from .home import HomeView
from .selector import select_region
from .sidebar import Sidebar
from .titlebar import ChromeDialog, EdgeResizer, TitleDragBar, WindowControls
from .widgets import ActionButton, IconButton, SectionTitle, separator

TOOLS = [
    ("select", "cursor", "Select and move", "V"),
    ("pen", "pen", "Freehand pen", "P"),
    ("highlighter", "highlighter", "Highlighter", "H"),
    ("arrow", "arrow", "Arrow", "A"),
    ("line", "line", "Line", "L"),
    ("rect", "square", "Rectangle", "R"),
    ("ellipse", "circle", "Ellipse", "E"),
    ("text", "text", "Text", "T"),
    ("number", "number", "Numbered badge", "N"),
    ("redact", "blur", "Blur / pixelate / block out", "B"),
    ("eraser", "eraser", "Erase a mark, or drag over several", "X"),
    ("crop", "crop", "Crop", "C"),
]


def _display_only_shortcut(action: QAction, keys: str) -> None:
    """Show a shortcut in a menu without registering it a second time.

    The real bindings are QShortcuts on the window; letting the menu action
    claim the same sequence makes Qt report an ambiguous overload and fire
    neither of them.
    """
    action.setShortcut(QKeySequence(keys))
    action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(940, 620)
        self.setAcceptDrops(True)

        self.current_path: str = ""
        self.dirty = False
        self._last_backend = ""
        self._pending_capture: str | None = None
        #: Set when the process was started purely to take one capture, e.g. by
        #: a Print Screen binding on `shotpad --area`. Cancelling that capture
        #: means the user wants nothing at all, so leaving an empty editor on
        #: screen for them to close would be an odd reward for pressing Escape.
        self.quit_if_capture_cancelled = False

        self.canvas = Canvas()
        self.sidebar = Sidebar(self.canvas)
        self.home = HomeView()

        self._resizer: EdgeResizer | None = None
        self._build_ui()
        self._apply_titlebar_mode()
        self._build_shortcuts()
        self._connect()
        self._apply_document_defaults(self.canvas.doc)
        self._update_actions()

        geometry = settings.get("window_geometry")
        if geometry:
            try:
                self.restoreGeometry(geometry)
            except Exception:
                self.resize(1240, 800)
        else:
            self.resize(1240, 800)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(separator())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.tool_rail = self._build_tool_rail()
        body.addWidget(self.tool_rail)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.canvas)
        body.addWidget(self.stack, 1)

        body.addWidget(self.sidebar)
        root.addLayout(body, 1)

        self.setCentralWidget(central)
        self._build_status_bar()
        self._show_home(True)

    def _build_header(self) -> QWidget:
        header = TitleDragBar()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(52)
        self.header = header
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(7)

        self.capture_button = QPushButton("  Capture")
        self.capture_button.setObjectName("Primary")
        self.capture_button.setIcon(make_icon("camera", "#ffffff", 18))
        self.capture_button.setIconSize(QSize(18, 18))
        self.capture_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.capture_button.setMinimumHeight(34)
        self.capture_button.clicked.connect(lambda: self.capture("area"))
        layout.addWidget(self.capture_button)

        self.capture_menu_button = IconButton("chevron-down", "Capture options", size=16)
        self.capture_menu_button.setMinimumHeight(34)
        menu = QMenu(self)
        theme = current_theme()
        actions = [
            ("Select an area…", "select-area", "Ctrl+Shift+A", lambda: self.capture("area")),
            ("Whole screen", "monitor", "Ctrl+Shift+F", lambda: self.capture("screen")),
            ("A window…", "window", "Ctrl+Shift+W", lambda: self.capture("window")),
        ]
        for label, icon_name, keys, handler in actions:
            action = QAction(make_icon(icon_name, theme.text, 18), label, self)
            _display_only_shortcut(action, keys)
            action.triggered.connect(handler)
            menu.addAction(action)
        menu.addSeparator()
        for seconds in (3, 5, 10):
            action = QAction(
                make_icon("timer", theme.text, 18), f"Area after {seconds} seconds", self
            )
            action.triggered.connect(
                lambda _=False, s=seconds: self.capture("area", delay=s)
            )
            menu.addAction(action)
        menu.addSeparator()
        desktop_action = QAction(
            make_icon("camera", theme.text, 18), "Use the desktop's own picker", self
        )
        desktop_action.setToolTip(
            "Hand the selection over to GNOME or KDE's built-in screenshot UI"
        )
        desktop_action.triggered.connect(self.capture_with_desktop_ui)
        menu.addAction(desktop_action)
        self.capture_menu_button.setMenu(menu)
        layout.addWidget(self.capture_menu_button)

        layout.addWidget(separator(vertical=True))

        self.open_button = IconButton("folder", "Open an image (Ctrl+O)")
        self.open_button.clicked.connect(self.open_file)
        layout.addWidget(self.open_button)

        self.paste_button = IconButton("clipboard", "Paste from clipboard (Ctrl+V)")
        self.paste_button.clicked.connect(self.paste_from_clipboard)
        layout.addWidget(self.paste_button)

        layout.addWidget(separator(vertical=True))

        self.undo_button = IconButton("undo", "Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo)
        layout.addWidget(self.undo_button)

        self.redo_button = IconButton("redo", "Redo (Ctrl+Shift+Z)")
        self.redo_button.clicked.connect(self.redo)
        layout.addWidget(self.redo_button)

        layout.addStretch(1)

        self.crop_bar = QWidget()
        crop_layout = QHBoxLayout(self.crop_bar)
        crop_layout.setContentsMargins(0, 0, 0, 0)
        crop_layout.setSpacing(6)
        crop_hint = QLabel("Crop:")
        crop_hint.setObjectName("FieldLabel")
        crop_layout.addWidget(crop_hint)
        apply_crop = QPushButton("Apply")
        apply_crop.setObjectName("Primary")
        apply_crop.clicked.connect(self.canvas.apply_crop)
        crop_layout.addWidget(apply_crop)
        cancel_crop = QPushButton("Cancel")
        cancel_crop.clicked.connect(self.canvas.cancel_crop)
        crop_layout.addWidget(cancel_crop)
        reset_crop = QPushButton("Reset")
        reset_crop.clicked.connect(self.canvas.reset_crop)
        crop_layout.addWidget(reset_crop)
        self.crop_bar.hide()
        layout.addWidget(self.crop_bar)

        layout.addWidget(separator(vertical=True))

        self.copy_button = ActionButton("Copy", "copy", "Copied")
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        layout.addWidget(self.copy_button)

        self.save_button = ActionButton("Save", "download", "Saved", primary=True)
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)

        self.save_menu_button = IconButton("chevron-down", "Export options", size=16)
        self.save_menu_button.setMinimumHeight(34)
        save_menu = QMenu(self)
        save_as = QAction("Save as…", self)
        _display_only_shortcut(save_as, "Ctrl+Shift+S")
        save_as.triggered.connect(self.save_as)
        save_menu.addAction(save_as)
        save_menu.addSeparator()
        for factor in (1.0, 1.5, 2.0, 3.0):
            action = QAction(f"Export at {factor:g}x", self)
            action.triggered.connect(
                lambda _=False, f=factor: self.save_as(scale=f)
            )
            save_menu.addAction(action)
        save_menu.addSeparator()
        open_folder = QAction("Open the save folder", self)
        open_folder.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(settings.save_directory())
            )
        )
        save_menu.addAction(open_folder)
        self.save_menu_button.setMenu(save_menu)
        layout.addWidget(self.save_menu_button)

        self.menu_button = IconButton("menu", "Menu")
        self.menu_button.setMinimumHeight(34)
        self.menu_button.setMenu(self._build_main_menu())
        layout.addWidget(self.menu_button)

        # Window controls, Cinnamon style, in the corner this desktop family
        # puts them. Only shown when we own the title bar.
        self.window_controls = WindowControls()
        self.window_controls.closeClicked.connect(self.close)
        self.window_controls.minimiseClicked.connect(self.showMinimized)
        self.window_controls.maximiseClicked.connect(self._toggle_maximised)
        layout.addSpacing(6)
        layout.addWidget(self.window_controls)

        return header

    def _build_main_menu(self) -> QMenu:
        menu = QMenu(self)
        theme = current_theme()

        rotate_left = QAction(make_icon("rotate-ccw", theme.text, 18), "Rotate left", self)
        rotate_left.triggered.connect(lambda: self._rotate(-90))
        menu.addAction(rotate_left)

        rotate_right = QAction(make_icon("rotate-cw", theme.text, 18), "Rotate right", self)
        rotate_right.triggered.connect(lambda: self._rotate(90))
        menu.addAction(rotate_right)

        flip_h = QAction(make_icon("flip-h", theme.text, 18), "Flip horizontally", self)
        flip_h.triggered.connect(lambda: self._flip(True))
        menu.addAction(flip_h)

        flip_v = QAction(make_icon("flip-v", theme.text, 18), "Flip vertically", self)
        flip_v.triggered.connect(lambda: self._flip(False))
        menu.addAction(flip_v)

        menu.addSeparator()

        clear = QAction(make_icon("trash", theme.text, 18), "Clear all annotations", self)
        _display_only_shortcut(clear, "Ctrl+Shift+Delete")
        clear.triggered.connect(self.canvas.clear_annotations)
        menu.addAction(clear)

        reset_style = QAction("Reset framing and background", self)
        reset_style.triggered.connect(self.reset_style)
        menu.addAction(reset_style)

        menu.addSeparator()

        theme_menu = menu.addMenu("Appearance")
        group = QActionGroup(self)
        for key, label in (("auto", "Follow the system"), ("light", "Light"), ("dark", "Dark")):
            action = QAction(label, self, checkable=True)
            action.setChecked(settings.get("theme") == key)
            action.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            group.addAction(action)
            theme_menu.addAction(action)

        prefs = QAction(make_icon("sliders", theme.text, 18), "Preferences…", self)
        _display_only_shortcut(prefs, "Ctrl+,")
        prefs.triggered.connect(self.show_preferences)
        menu.addAction(prefs)

        menu.addSeparator()

        diagnostics = QAction(make_icon("info", theme.text, 18), "Capture diagnostics…", self)
        diagnostics.triggered.connect(self.show_diagnostics)
        menu.addAction(diagnostics)

        about = QAction(f"About {APP_NAME}", self)
        about.triggered.connect(self.show_about)
        menu.addAction(about)

        menu.addSeparator()
        quit_action = QAction("Quit", self)
        _display_only_shortcut(quit_action, "Ctrl+Q")
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        return menu

    def _build_tool_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("ToolRail")
        rail.setFixedWidth(52)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(7, 10, 7, 10)
        layout.setSpacing(4)

        self.tool_buttons: dict[str, IconButton] = {}
        for key, icon_name, tooltip, shortcut in TOOLS:
            button = IconButton(
                icon_name, f"{tooltip}  ({shortcut})", checkable=True, size=21
            )
            button.setFixedSize(38, 38)
            button.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            layout.addWidget(button)
            self.tool_buttons[key] = button
            if key in ("select", "number"):
                layout.addSpacing(4)

        layout.addStretch(1)
        self.tool_buttons["select"].setChecked(True)
        return rail

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(True)

        self.size_label = QLabel("")
        self.size_label.setObjectName("Hint")
        bar.addWidget(self.size_label)

        self.backend_label = QLabel("")
        self.backend_label.setObjectName("Hint")
        bar.addWidget(self.backend_label)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer, 1)

        zoom_out = IconButton("zoom-out", "Zoom out (Ctrl+-)", size=16)
        zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.25))
        bar.addPermanentWidget(zoom_out)

        self.zoom_label = QPushButton("Fit")
        self.zoom_label.setObjectName("Flat")
        self.zoom_label.setToolTip("Reset the zoom (Ctrl+0)")
        self.zoom_label.setFixedWidth(58)
        self.zoom_label.clicked.connect(lambda: self.canvas.set_zoom(None))
        bar.addPermanentWidget(self.zoom_label)

        zoom_in = IconButton("zoom-in", "Zoom in (Ctrl++)", size=16)
        zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        bar.addPermanentWidget(zoom_in)

        self.setStatusBar(bar)

    # ----------------------------------------------------------- title bar
    def _apply_titlebar_mode(self) -> None:
        """Switch between our own title bar and the desktop's."""
        system = bool(settings.get("system_titlebar"))
        was_visible = self.isVisible()
        geometry = self.saveGeometry()

        if self._resizer is not None:
            self._resizer.detach()
            self._resizer = None

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, not system)
        self.window_controls.setVisible(not system)
        self.header.set_drag_enabled(not system)

        if not system:
            # A frameless window loses the window manager's resize borders.
            self._resizer = EdgeResizer(self)

        if was_visible:
            # Changing window flags un-maps the window on most platforms.
            self.show()
            self.restoreGeometry(geometry)

    def _toggle_maximised(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.ActivationChange:
            self.window_controls.set_active(self.isActiveWindow())
        elif event.type() == QEvent.Type.WindowStateChange:
            # Cinnamon swaps the maximise glyph for a restore glyph.
            self.window_controls.set_maximized(self.isMaximized())
        super().changeEvent(event)

    # ------------------------------------------------------------- plumbing
    def _connect(self) -> None:
        self.canvas.document_changed.connect(self._on_document_changed)
        self.canvas.zoom_changed.connect(self._on_zoom_changed)
        self.canvas.tool_finished.connect(self._on_tool_finished)
        self.canvas.crop_mode_changed.connect(self._on_crop_mode)
        self.canvas.selection_changed.connect(lambda _: self._update_actions())

        self.sidebar.changed.connect(self._on_document_changed)
        self.sidebar.style_changed.connect(lambda: self.canvas.update())

        self.home.capture_area.connect(lambda: self.capture("area"))
        self.home.capture_screen.connect(lambda: self.capture("screen"))
        self.home.capture_window.connect(lambda: self.capture("window"))
        self.home.open_file.connect(self.open_file)
        self.home.paste.connect(self.paste_from_clipboard)

    def _typing(self) -> QWidget | None:
        """The focused text-entry widget, if the user is typing into one.

        Shotpad binds bare letters to tools and Ctrl+C/V/Z to the document, so
        every shortcut has to step aside while a text field has focus - both
        the inline text-annotation editor and the sidebar's font box.
        """
        from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit

        widget = QApplication.focusWidget()
        if isinstance(widget, (QPlainTextEdit, QLineEdit, QAbstractSpinBox)):
            return widget
        if isinstance(widget, QComboBox) and widget.isEditable():
            return widget.lineEdit()
        return None

    def _build_shortcuts(self) -> None:
        def add(sequence: str, handler, guard: bool = True) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)

            def run() -> None:
                if guard and self._typing() is not None:
                    return
                handler()

            shortcut.activated.connect(run)

        def forwarding(method: str, handler):
            """Ctrl+C/V/X/Z belong to the focused text field when there is one."""
            def run() -> None:
                widget = self._typing()
                if widget is not None:
                    if hasattr(widget, method):
                        getattr(widget, method)()
                    return
                handler()
            return run

        add("Ctrl+Shift+A", lambda: self.capture("area"))
        add("Ctrl+Shift+F", lambda: self.capture("screen"))
        add("Ctrl+Shift+W", lambda: self.capture("window"))
        add("Ctrl+O", self.open_file)
        add("Ctrl+S", self.save)
        add("Ctrl+Shift+S", self.save_as)
        add("Ctrl+C", forwarding("copy", self.copy_to_clipboard), guard=False)
        add("Ctrl+V", forwarding("paste", self.paste_from_clipboard), guard=False)
        add("Ctrl+Z", forwarding("undo", self.undo), guard=False)
        add("Ctrl+Shift+Z", forwarding("redo", self.redo), guard=False)
        add("Ctrl+Y", forwarding("redo", self.redo), guard=False)
        add("Ctrl+0", lambda: self.canvas.set_zoom(None))
        add("Ctrl++", lambda: self.canvas.zoom_by(1.25))
        add("Ctrl+=", lambda: self.canvas.zoom_by(1.25))
        add("Ctrl+-", lambda: self.canvas.zoom_by(1 / 1.25))
        add("Ctrl+Shift+Delete", self.canvas.clear_annotations)
        add("Ctrl+Q", self.close)
        add("Ctrl+,", self.show_preferences)

        for key, _icon, _tooltip, shortcut in TOOLS:
            add(shortcut, lambda k=key: self.set_tool(k))

    # ----------------------------------------------------------------- state
    def _show_home(self, show: bool) -> None:
        self.stack.setCurrentWidget(self.home if show else self.canvas)
        self.tool_rail.setVisible(not show)
        self.sidebar.setVisible(not show)
        for widget in (
            self.copy_button, self.save_button, self.save_menu_button,
            self.undo_button, self.redo_button,
        ):
            widget.setEnabled(not show)

    def _apply_document_defaults(self, doc: Document) -> None:
        doc.frame.padding = int(settings.get("default_padding"))
        doc.frame.corner_radius = int(settings.get("default_radius"))
        doc.frame.shadow_strength = int(settings.get("default_shadow"))
        spec = str(settings.get("default_background") or "")
        parts = spec.split(":")
        if len(parts) == 4:
            kind, c1, c2, angle = parts
            doc.background.kind = kind
            doc.background.color1 = QColor(c1)
            doc.background.color2 = QColor(c2)
            try:
                doc.background.angle = int(angle)
            except ValueError:
                pass
        self.sidebar.sync_from_document(doc)

    def _on_document_changed(self) -> None:
        self.dirty = True
        self._update_actions()
        self._update_status()

    def _on_zoom_changed(self, scale: float) -> None:
        self.zoom_label.setText(
            "Fit" if self.canvas._zoom == 0 else f"{scale * 100:.0f}%"
        )

    def _on_tool_finished(self) -> None:
        # Freehand tools stay active; one-shot shapes fall back to select so a
        # stray click does not add a second copy.
        if self.canvas.tool in ("number",):
            return
        self._update_actions()

    def _on_crop_mode(self, active: bool) -> None:
        self.crop_bar.setVisible(active)
        if not active and self.canvas.tool == "crop":
            self.set_tool("select")

    def _update_actions(self) -> None:
        doc = self.canvas.doc
        has_image = not doc.is_empty()
        self.undo_button.setEnabled(has_image and doc.can_undo())
        self.redo_button.setEnabled(has_image and doc.can_redo())
        self._update_status()

    def _update_status(self) -> None:
        doc = self.canvas.doc
        if doc.is_empty():
            self.size_label.setText("")
            self.backend_label.setText("")
            return
        from ..render import canvas_layout

        image = doc.image_size()
        canvas = canvas_layout(doc).canvas
        self.size_label.setText(
            f"  Image {image.width()} x {image.height()}     "
            f"Canvas {canvas.width()} x {canvas.height()}     "
            f"{len(doc.annotations)} annotation"
            f"{'' if len(doc.annotations) == 1 else 's'}  "
        )
        if self._last_backend:
            self.backend_label.setText(f"   via {self._last_backend}  ")

    def set_tool(self, tool: str) -> None:
        if self.canvas.doc.is_empty():
            return
        if tool == "crop":
            for key, button in self.tool_buttons.items():
                button.setChecked(key == "crop")
            self.canvas.set_tool("crop")
            self.canvas.begin_crop()
            self.sidebar.update_for_tool("crop")
            return
        if self.canvas.crop_mode:
            self.canvas.cancel_crop()
        for key, button in self.tool_buttons.items():
            button.setChecked(key == tool)
        self.canvas.set_tool(tool)
        self.sidebar.update_for_tool(tool)
        settings.set("last_tool", tool)

    def set_theme(self, mode: str) -> None:
        settings.set("theme", mode)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        self.canvas.refresh_theme()
        for button in self.tool_buttons.values():
            button.refresh_icon()
        for button in (
            self.open_button, self.paste_button, self.undo_button,
            self.redo_button, self.menu_button, self.capture_menu_button,
            self.save_menu_button, self.copy_button, self.save_button,
        ):
            button.refresh_icon()
        self.menu_button.setMenu(self._build_main_menu())

    # --------------------------------------------------------------- capture
    def capture(self, mode: str = "area", delay: int | None = None) -> None:
        if delay is None:
            delay = int(settings.get("capture_delay"))
        self._pending_capture = mode

        hide = settings.get("hide_window_on_capture") and self.isVisible()
        if hide:
            self.hide()
            QApplication.processEvents()

        total_delay = max(0, int(delay)) * 1000
        # Even without a user delay, give the compositor a moment to finish
        # animating this window away before we grab the framebuffer.
        QTimer.singleShot(
            max(total_delay, 220 if hide else 60),
            lambda: self._do_capture(mode, hide),
        )

    def _do_capture(self, mode: str, was_hidden: bool) -> None:
        backend = str(settings.get("capture_backend") or "auto")
        try:
            image, used = grab_screen(backend)
        except CaptureCancelled:
            self._capture_cancelled(was_hidden)
            return
        except CaptureError as exc:
            self._restore_after_capture(was_hidden)
            self._error("Screen capture failed", str(exc))
            return

        rect = None
        if mode in ("area", "window") and not grab_was_preselected(image, used):
            rect = select_region(image, "window" if mode == "window" else "region")
            if rect is None:
                self._capture_cancelled(was_hidden)
                return
            image = image.copy(rect)

        self._last_backend = used
        self._load_image(image, path="")
        self._restore_after_capture(was_hidden)

        if settings.get("copy_after_capture"):
            self.copy_to_clipboard(silent=True)

    def capture_with_desktop_ui(self) -> None:
        """Delegate to the compositor's own interactive screenshot UI."""
        hide = settings.get("hide_window_on_capture") and self.isVisible()
        if hide:
            self.hide()
            QApplication.processEvents()
        QTimer.singleShot(200, lambda: self._do_desktop_capture(hide))

    def _do_desktop_capture(self, was_hidden: bool) -> None:
        try:
            image = portal_screenshot(interactive=True)
        except PortalCancelled:
            self._capture_cancelled(was_hidden)
            return
        except Exception as exc:
            self._restore_after_capture(was_hidden)
            self._error("The desktop's screenshot tool failed", str(exc))
            return
        self._last_backend = "portal (desktop UI)"
        self._load_image(image, path="")
        self._restore_after_capture(was_hidden)

    def _capture_cancelled(self, was_hidden: bool) -> None:
        """The user backed out of a capture - silently, with nothing to show."""
        if self.quit_if_capture_cancelled:
            self.close()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return
        self._restore_after_capture(was_hidden)

    def _restore_after_capture(self, was_hidden: bool) -> None:
        if was_hidden or not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ file
    def _load_image(self, image: QImage, path: str = "") -> None:
        if image.isNull():
            self._error("Unsupported image", "That file could not be read as an image.")
            return
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        # There is something to edit now, so the editor has earned its place:
        # any capture cancelled from here on leaves it open.
        self.quit_if_capture_cancelled = False

        doc = Document(image)
        doc.source_path = path
        self._apply_document_defaults(doc)
        self.canvas.set_document(doc)
        self.current_path = path
        self.dirty = False

        self._show_home(False)
        self.set_tool(str(settings.get("last_tool") or "select"))
        self.canvas.setFocus()
        self.setWindowTitle(
            f"{os.path.basename(path)} - {APP_NAME}" if path else APP_NAME
        )
        self._update_actions()

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open an image", settings.save_directory(),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif);;All files (*)",
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        image = QImage(path)
        if image.isNull():
            self._error("Unsupported image", f"Could not read {path}")
            return
        self._last_backend = ""
        self._load_image(image, path)

    def paste_from_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        image = clipboard.image()
        if image.isNull() and mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    candidate = QImage(url.toLocalFile())
                    if not candidate.isNull():
                        self.open_path(url.toLocalFile())
                        return
        if image.isNull():
            self._error(
                "Nothing to paste", "The clipboard does not contain an image."
            )
            return
        self._last_backend = "clipboard"
        self._load_image(image, "")

    def _default_filename(self) -> str:
        pattern = str(settings.get("filename_pattern"))
        try:
            stem = datetime.now().strftime(pattern)
        except ValueError:
            stem = datetime.now().strftime("Shotpad %Y-%m-%d %H-%M-%S")
        extension = str(settings.get("save_format") or "png")
        return f"{stem}.{extension}"

    def _render_for_export(self, scale: float | None = None) -> QImage:
        self.canvas.commit_text()
        factor = float(scale if scale is not None else settings.get("export_scale"))
        factor = export_scale_for(self.canvas.doc, factor)
        return render_document(self.canvas.doc, factor)

    def save(self) -> None:
        if self.canvas.doc.is_empty():
            return
        directory = settings.save_directory()
        path = os.path.join(directory, self._default_filename())
        self._write(path)

    def save_as(self, scale: float | None = None) -> None:
        if self.canvas.doc.is_empty():
            return
        suggested = os.path.join(settings.save_directory(), self._default_filename())
        path, selected = QFileDialog.getSaveFileName(
            self, "Save image", suggested,
            "PNG image (*.png);;JPEG image (*.jpg);;WebP image (*.webp)",
        )
        if not path:
            return
        if "." not in os.path.basename(path):
            extension = {"PNG image (*.png)": "png", "JPEG image (*.jpg)": "jpg"}.get(
                selected, "webp" if "WebP" in selected else "png"
            )
            path = f"{path}.{extension}"
        self._write(path, scale)

    def _write(self, path: str, scale: float | None = None) -> None:
        image = self._render_for_export(scale)
        extension = os.path.splitext(path)[1].lower().lstrip(".") or "png"

        if extension in ("jpg", "jpeg") and image.hasAlphaChannel():
            # JPEG has no alpha; flatten onto white so it does not go black.
            from PySide6.QtGui import QPainter

            flat = QImage(image.size(), QImage.Format.Format_RGB32)
            flat.fill(Qt.GlobalColor.white)
            painter = QPainter(flat)
            painter.drawImage(0, 0, image)
            painter.end()
            image = flat

        quality = int(settings.get("jpeg_quality")) if extension in (
            "jpg", "jpeg", "webp"
        ) else -1

        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except OSError:
            pass

        if not image.save(path, None, quality):
            self._error("Could not save", f"Writing {path} failed.")
            return

        self.dirty = False
        self.current_path = path
        self.save_button.flash()
        self.statusBar().showMessage(f"Saved to {path}", 6000)
        if settings.get("close_after_save"):
            self.close()

    def copy_to_clipboard(self, silent: bool = False) -> None:
        if self.canvas.doc.is_empty():
            return
        # set_clipboard_image uses setImage rather than setMimeData; see the
        # note there for why that matters (it is the difference between exiting
        # cleanly and segfaulting).
        set_clipboard_image(self._render_for_export())

        if not silent:
            self.copy_button.flash()
            self.statusBar().showMessage("Copied to the clipboard", 4000)

    # ------------------------------------------------------------------ edit
    def undo(self) -> None:
        self.canvas.commit_text()
        if self.canvas.doc.undo():
            self.canvas.select_annotation(None)
            self.canvas.invalidate(full=True)
            self.sidebar.sync_from_document(self.canvas.doc)
            self._update_actions()

    def redo(self) -> None:
        self.canvas.commit_text()
        if self.canvas.doc.redo():
            self.canvas.select_annotation(None)
            self.canvas.invalidate(full=True)
            self.sidebar.sync_from_document(self.canvas.doc)
            self._update_actions()

    def _rotate(self, degrees: int) -> None:
        if self.canvas.doc.is_empty():
            return
        self.canvas.doc.rotate_source(degrees)
        self.canvas.invalidate(full=True)
        self.canvas.reset_view()
        self._on_document_changed()

    def _flip(self, horizontal: bool) -> None:
        if self.canvas.doc.is_empty():
            return
        self.canvas.doc.flip_source(horizontal)
        self.canvas.invalidate(full=True)
        self._on_document_changed()

    def reset_style(self) -> None:
        doc = self.canvas.doc
        if doc.is_empty():
            return
        doc.push_undo()
        from ..model import BackgroundSpec, FrameSpec

        doc.frame = FrameSpec()
        doc.background = BackgroundSpec()
        self._apply_document_defaults(doc)
        doc.touch_all()
        self.canvas.invalidate(full=True)
        self._on_document_changed()

    # --------------------------------------------------------------- dialogs
    def show_preferences(self) -> None:
        dialog = PreferencesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply()
            self.set_theme(str(settings.get("theme")))
            # The title bar preference now decides how dialogs are decorated
            # too, so it has to take effect without a restart.
            self._apply_titlebar_mode()

    def show_diagnostics(self) -> None:
        dialog = ChromeDialog(self, "Capture diagnostics")
        dialog.resize(520, 420)
        layout = dialog.body_layout

        info = QLabel(
            "Shotpad picks the first working capture backend for your desktop. "
            "An asterisk marks the ones available right now."
        )
        info.setWordWrap(True)
        info.setObjectName("Hint")
        layout.addWidget(info)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            capture_diagnostics()
            + "\n\nWindow list support: "
            + ("yes" if window_listing_supported() else "no (Wayland or xprop missing)")
            + f"\nQt platform     : {QGuiApplication.platformName()}"
            + f"\nShotpad version : {__version__}"
        )
        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_about(self) -> None:
        dialog = ChromeDialog(self, f"About {APP_NAME}", resizable=False)
        layout = dialog.body_layout
        layout.setContentsMargins(22, 18, 22, 18)

        pixmap = QApplication.windowIcon().pixmap(72, 72)
        if not pixmap.isNull():
            logo = QLabel()
            logo.setPixmap(pixmap)
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo)

        heading = QLabel(f"{APP_NAME} {__version__}")
        heading.setObjectName("BigTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        blurb = QLabel(
            "A screenshot editor for almost every Linux desktop.\n\n"
            "Padding, gradients, shadows, arrows, pen, text, numbered badges "
            "and redaction."
        )
        blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(blurb)

        # GPL-3 asks that a GUI program show its copyright, the absence of a
        # warranty and where to read the licence. The about box is the place
        # the licence text itself suggests for that.
        notice = QLabel(
            f"© {COPYRIGHT_YEAR} {AUTHOR}<br>"
            "Licensed under the "
            '<a href="https://www.gnu.org/licenses/gpl-3.0.html">'
            "GNU GPL, version 3 or later</a>, with ABSOLUTELY NO WARRANTY.<br>"
            "Uses Qt via PySide6, under the "
            '<a href="https://www.gnu.org/licenses/lgpl-3.0.html">LGPL-3.0</a>.'
        )
        notice.setObjectName("Hint")
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setOpenExternalLinks(True)
        layout.addWidget(notice)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.fit_to_width(420)
        dialog.exec()

    def _error(self, title: str, message: str) -> None:
        dialog = ChromeDialog(self, title, resizable=False)
        layout = dialog.body_layout

        row = QHBoxLayout()
        row.setSpacing(12)
        symbol = QLabel()
        symbol.setPixmap(
            make_icon("alert", current_theme().danger, 32).pixmap(32, 32)
        )
        symbol.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        row.addWidget(symbol)

        # The title bar already carries the title, so repeating it here would
        # just say the same thing twice.
        detail = QLabel(message)
        detail.setWordWrap(True)
        detail.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        row.addWidget(detail, 1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.fit_to_width(440)
        dialog.exec()

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasImage() or mime.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    self.open_path(url.toLocalFile())
                    event.acceptProposedAction()
                    return
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                self._last_backend = "drag and drop"
                self._load_image(image, "")
                event.acceptProposedAction()

    # ------------------------------------------------------------------ misc
    def _should_copy_on_close(self) -> bool:
        """Whether this close is a real user close we should copy for.

        Qt also delivers a close event to top-level windows while it is
        destroying the application, and doing real work there - rendering the
        export and touching the clipboard - crashes the process on the way out.
        closingDown() is what distinguishes "the user clicked the X" from "the
        interpreter is shutting down".
        """
        if not settings.get("copy_on_close"):
            return False
        if self.canvas.doc.is_empty():
            return False
        app = QApplication.instance()
        if app is None or app.closingDown():
            return False
        return True

    def closeEvent(self, event) -> None:
        self.canvas.commit_text()
        settings.set("window_geometry", self.saveGeometry())

        if self._should_copy_on_close():
            try:
                # Not copy_to_clipboard(): an in-process copy dies with the
                # process on any desktop without a clipboard manager, which
                # includes stock GNOME. See shotpad/clipboard.py.
                copy_image_persistent(self._render_for_export())
            except Exception:
                # A clipboard problem must never stop the window closing.
                pass

        settings.sync()
        super().closeEvent(event)




# ---------------------------------------------------------------------------
# Shortcut reference
# ---------------------------------------------------------------------------
#
# Derived from TOOLS where possible so the Preferences list cannot drift out of
# sync with the keys the window actually binds.

def shortcut_groups() -> list[tuple[str, list[tuple[str, str]]]]:
    tools = [(shortcut, tooltip) for _key, _icon, tooltip, shortcut in TOOLS]
    return [
        ("Capture", [
            ("Ctrl+Shift+A", "Capture an area"),
            ("Ctrl+Shift+F", "Capture the whole screen"),
            ("Ctrl+Shift+W", "Capture a window"),
        ]),
        ("File", [
            ("Ctrl+O", "Open an image"),
            ("Ctrl+V", "Paste the clipboard image"),
            ("Ctrl+S", "Save to the screenshots folder"),
            ("Ctrl+Shift+S", "Save as…"),
            ("Ctrl+C", "Copy the finished image"),
            ("Ctrl+Q", "Quit"),
        ]),
        ("Edit", [
            ("Ctrl+Z", "Undo"),
            ("Ctrl+Shift+Z", "Redo"),
            ("Delete", "Delete the selected annotation"),
            ("Ctrl+Shift+Delete", "Clear all annotations"),
            ("Arrow keys", "Nudge the selection (Shift for larger steps)"),
            ("Esc", "Deselect, or cancel a crop"),
            ("Enter", "Apply the crop"),
        ]),
        ("Tools", tools),
        ("View", [
            ("Ctrl+0", "Fit to the window"),
            ("Ctrl++ / Ctrl+-", "Zoom in and out"),
            ("Ctrl+scroll", "Zoom at the pointer"),
            ("Space-drag", "Pan"),
            ("Middle-drag", "Pan"),
            ("Alt-drag", "Drag the finished image into another app"),
        ]),
        ("While drawing", [
            ("Shift", "Snap arrows and lines to 15°, constrain to a square"),
        ]),
        ("Region overlay", [
            ("Drag", "Select an area"),
            ("Click", "Take the window, or the whole screen"),
            ("Shift-drag", "Move the selection"),
            ("Ctrl-drag", "Constrain to a square"),
            ("Enter", "Take the current monitor"),
            ("Esc", "Cancel"),
        ]),
    ]


class ShortcutSheet(QWidget):
    """Read-only, grouped shortcut reference."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        theme = current_theme()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 8, 2)
        layout.setSpacing(14)

        key_style = (
            f"background: {theme.surface_hi}; border: 1px solid {theme.border};"
            " border-radius: 5px; padding: 2px 7px; font-family: monospace;"
            " font-size: 9pt;"
        )

        for title, entries in shortcut_groups():
            layout.addWidget(SectionTitle(title))
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(6)
            grid.setColumnStretch(1, 1)
            for row, (keys, description) in enumerate(entries):
                key_label = QLabel(keys)
                key_label.setStyleSheet(key_style)
                key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                key_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                text = QLabel(description)
                text.setWordWrap(True)
                grid.addWidget(
                    key_label, row, 0,
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                )
                grid.addWidget(text, row, 1)
            layout.addLayout(grid)

        layout.addWidget(separator())
        note = QLabel(
            "<b>One-press capture.</b> No Linux desktop lets an application "
            "claim the Print Screen key for itself, so bind one in your "
            "desktop's own keyboard settings to:"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        command = QLabel(_launch_command() + " --area")
        command.setStyleSheet(key_style)
        command.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(command)

        where = QLabel(
            "<table cellpadding='2'>"
            "<tr><td><b>GNOME</b></td><td>Settings → Keyboard → View and "
            "Customise Shortcuts → Custom Shortcuts</td></tr>"
            "<tr><td><b>KDE</b></td><td>System Settings → Shortcuts → Add → "
            "Command or Script</td></tr>"
            "<tr><td><b>XFCE</b></td><td>Settings → Keyboard → Application "
            "Shortcuts</td></tr>"
            "<tr><td><b>MATE</b></td><td>Control Center → Keyboard Shortcuts "
            "→ Add</td></tr>"
            "</table>"
        )
        where.setWordWrap(True)
        where.setObjectName("Hint")
        layout.addWidget(where)

        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)


def _launch_command() -> str:
    try:
        from ..desktop_install import launcher_command

        return launcher_command()
    except Exception:
        return "shotpad"


class PreferencesDialog(ChromeDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, "Preferences")
        self.setMinimumSize(560, 560)

        layout = self.body_layout
        tabs = QTabWidget()
        tabs.addTab(self._build_general(), "General")
        tabs.addTab(self._build_capture(), "Capture")
        tabs.addTab(ShortcutSheet(), "Shortcuts")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- tabs ---------------------------------------------------------------
    def _build_general(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        form.setSpacing(10)

        self.theme_box = QComboBox()
        for key, label in (
            ("auto", "Follow the system"), ("light", "Light"), ("dark", "Dark")
        ):
            self.theme_box.addItem(label, key)
        self.theme_box.setCurrentIndex(
            max(0, self.theme_box.findData(settings.get("theme")))
        )
        form.addRow("Appearance", self.theme_box)

        directory_row = QHBoxLayout()
        self.dir_label = QLabel(settings.save_directory())
        self.dir_label.setWordWrap(True)
        browse = QPushButton("Change…")
        browse.clicked.connect(self._pick_directory)
        directory_row.addWidget(self.dir_label, 1)
        directory_row.addWidget(browse)
        form.addRow("Save folder", directory_row)

        self.pattern_edit = QComboBox()
        self.pattern_edit.setEditable(True)
        patterns = [
            "Shotpad %Y-%m-%d %H-%M-%S",
            "Screenshot_%Y%m%d_%H%M%S",
            "shot-%s",
        ]
        current = str(settings.get("filename_pattern"))
        if current not in patterns:
            patterns.insert(0, current)
        self.pattern_edit.addItems(patterns)
        self.pattern_edit.setCurrentText(current)
        form.addRow("Filename pattern", self.pattern_edit)

        self.format_box = QComboBox()
        for key, label in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WebP")):
            self.format_box.addItem(label, key)
        self.format_box.setCurrentIndex(
            max(0, self.format_box.findData(settings.get("save_format")))
        )
        form.addRow("Quick-save format", self.format_box)

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(40, 100)
        self.quality_spin.setValue(int(settings.get("jpeg_quality")))
        form.addRow("JPEG / WebP quality", self.quality_spin)

        self.scale_box = QComboBox()
        for factor in (1.0, 1.5, 2.0, 3.0):
            self.scale_box.addItem(f"{factor:g}x", factor)
        self.scale_box.setCurrentIndex(
            max(0, self.scale_box.findData(float(settings.get("export_scale"))))
        )
        form.addRow("Export scale", self.scale_box)

        outer.addLayout(form)
        outer.addWidget(separator())

        self.titlebar_check = QCheckBox("Use the desktop's title bar instead of Shotpad's")
        self.titlebar_check.setChecked(bool(settings.get("system_titlebar")))
        self.titlebar_check.setToolTip(
            "Shotpad draws its own title bar with Cinnamon-style window controls. "
            "Turn this on if your window manager handles frameless windows badly."
        )
        outer.addWidget(self.titlebar_check)

        self.copy_close_check = QCheckBox(
            "Copy the finished image to the clipboard when the window closes"
        )
        self.copy_close_check.setChecked(bool(settings.get("copy_on_close")))
        outer.addWidget(self.copy_close_check)

        self.close_check = QCheckBox("Quit after saving")
        self.close_check.setChecked(bool(settings.get("close_after_save")))
        outer.addWidget(self.close_check)

        outer.addStretch(1)
        return page

    def _build_capture(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        form = QFormLayout()
        form.setSpacing(10)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setSuffix(" s")
        self.delay_spin.setValue(int(settings.get("capture_delay")))
        form.addRow("Capture delay", self.delay_spin)

        self.backend_box = QComboBox()
        self.backend_box.addItem("Automatic", "auto")
        from ..capture import BACKENDS, backend_order

        for name in backend_order():
            try:
                mark = "" if BACKENDS[name].available() else "  (not installed)"
            except Exception:
                mark = "  (unavailable)"
            self.backend_box.addItem(name + mark, name)
        self.backend_box.setCurrentIndex(
            max(0, self.backend_box.findData(settings.get("capture_backend")))
        )
        form.addRow("Capture backend", self.backend_box)

        outer.addLayout(form)
        outer.addWidget(separator())

        self.hide_check = QCheckBox("Hide the Shotpad window while capturing")
        self.hide_check.setChecked(bool(settings.get("hide_window_on_capture")))
        outer.addWidget(self.hide_check)

        self.copy_check = QCheckBox("Copy to the clipboard right after capturing")
        self.copy_check.setChecked(bool(settings.get("copy_after_capture")))
        outer.addWidget(self.copy_check)

        outer.addWidget(separator())
        defaults = QLabel("Defaults for a new screenshot")
        defaults.setObjectName("SectionTitle")
        outer.addWidget(defaults)

        defaults_form = QFormLayout()
        defaults_form.setSpacing(10)

        self.default_padding = QSpinBox()
        self.default_padding.setRange(0, 200)
        self.default_padding.setValue(int(settings.get("default_padding")))
        defaults_form.addRow("Padding", self.default_padding)

        self.default_radius = QSpinBox()
        self.default_radius.setRange(0, 100)
        self.default_radius.setValue(int(settings.get("default_radius")))
        defaults_form.addRow("Corner radius", self.default_radius)

        self.default_shadow = QSpinBox()
        self.default_shadow.setRange(0, 100)
        self.default_shadow.setSuffix(" %")
        self.default_shadow.setValue(int(settings.get("default_shadow")))
        defaults_form.addRow("Shadow", self.default_shadow)

        outer.addLayout(defaults_form)
        outer.addStretch(1)
        return page

    # -- actions ------------------------------------------------------------
    def _pick_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a save folder", self.dir_label.text()
        )
        if directory:
            self.dir_label.setText(directory)

    def apply(self) -> None:
        settings.set("theme", self.theme_box.currentData())
        settings.set("save_dir", self.dir_label.text())
        settings.set("filename_pattern", self.pattern_edit.currentText())
        settings.set("save_format", self.format_box.currentData())
        settings.set("jpeg_quality", self.quality_spin.value())
        settings.set("export_scale", self.scale_box.currentData())
        settings.set("capture_delay", self.delay_spin.value())
        settings.set("capture_backend", self.backend_box.currentData())
        settings.set("hide_window_on_capture", self.hide_check.isChecked())
        settings.set("copy_after_capture", self.copy_check.isChecked())
        settings.set("copy_on_close", self.copy_close_check.isChecked())
        settings.set("close_after_save", self.close_check.isChecked())
        settings.set("system_titlebar", self.titlebar_check.isChecked())
        settings.set("default_padding", self.default_padding.value())
        settings.set("default_radius", self.default_radius.value())
        settings.set("default_shadow", self.default_shadow.value())
        settings.sync()

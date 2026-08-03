"""Application entry point and command line interface.

The CLI matters as much as the GUI here: no Linux desktop lets an ordinary
application register a global Print Screen binding, so the supported way to get
one-press capture is to bind a key in the desktop's own keyboard settings to
``shotpad --area``. Every capture mode is therefore reachable from the command
line, and ``--no-edit`` turns Shotpad into a plain screenshot tool.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from . import (
    APP_ID,
    APP_NAME,
    AUTHOR,
    COPYRIGHT_YEAR,
    LICENSE_SPDX,
    __version__,
)
from .clipboard import DAEMON_FLAG
from .icons import app_icon_svg
from .settings import settings
from .theme import apply_theme


class _VersionAction(argparse.Action):
    """``--version`` that keeps its line breaks.

    argparse runs the version string through the help formatter, which rewraps
    it into a paragraph - and the GPL notice is only readable as separate
    lines.
    """

    def __init__(self, option_strings, dest, text: str = "", help: str = "") -> None:
        super().__init__(option_strings, dest, nargs=0, help=help)
        self.text = text

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        print(self.text)
        parser.exit()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shotpad",
        description=(
            "Capture, annotate and beautify screenshots on any Linux desktop."
        ),
        epilog=(
            "Bind a key in your desktop's keyboard settings to "
            "'shotpad --area' for one-press capture."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--area", "-a", action="store_true",
        help="capture immediately and open the region selector",
    )
    mode.add_argument(
        "--screen", "-s", action="store_true",
        help="capture the whole desktop immediately",
    )
    mode.add_argument(
        "--window", "-w", action="store_true",
        help="capture immediately and pick a window",
    )
    mode.add_argument(
        "--desktop-ui", action="store_true",
        help="use the desktop's own screenshot picker (GNOME / KDE portal)",
    )
    mode.add_argument(
        "--clipboard", "-p", action="store_true",
        help="start with the image currently on the clipboard",
    )

    parser.add_argument("file", nargs="?", help="an image file to open")
    parser.add_argument(
        "--delay", "-d", type=int, default=None, metavar="SECONDS",
        help="wait this many seconds before capturing",
    )
    parser.add_argument(
        "--out", "-o", metavar="PATH",
        help="with --no-edit, write the capture here instead of the save folder",
    )
    parser.add_argument(
        "--no-edit", action="store_true",
        help="save the capture straight away without opening the editor",
    )
    parser.add_argument(
        "--copy", action="store_true",
        help="also place the result on the clipboard",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="with --no-edit, skip padding and background styling",
    )
    parser.add_argument(
        "--backend", metavar="NAME",
        help="force a capture backend (see --list-backends)",
    )
    parser.add_argument(
        "--list-backends", action="store_true",
        help="print the capture backends detected on this system and exit",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="add a menu entry and icon for the current user (for the AppImage)",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="remove the menu entry and icon added by --install",
    )
    parser.add_argument(
        "--version", "-V", action=_VersionAction,
        help="show the version and licence, then exit",
        text=(
            f"{APP_NAME} {__version__}\n"
            f"Copyright (C) {COPYRIGHT_YEAR} {AUTHOR}\n"
            f"License {LICENSE_SPDX}: GNU GPL version 3 or later "
            "<https://gnu.org/licenses/gpl.html>\n"
            "This is free software: you are free to change and redistribute it.\n"
            "There is NO WARRANTY, to the extent permitted by law."
        ),
    )
    return parser


def _app_icon() -> QIcon:
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(app_icon_svg().encode("utf-8")))
    icon = QIcon()
    for size in (32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _headless_capture(args, parser) -> int:
    """--no-edit: grab, optionally style, write to disk, exit."""
    from .capture import (
        CaptureCancelled,
        CaptureError,
        grab_screen,
        grab_was_preselected,
    )
    from .model import Document
    from .render import render_document
    from .ui.selector import select_region

    try:
        image, backend = grab_screen(args.backend or str(settings.get("capture_backend")))
    except CaptureCancelled:
        return 1
    except CaptureError as exc:
        print(f"shotpad: {exc}", file=sys.stderr)
        return 2

    if (args.area or args.window) and not grab_was_preselected(image, backend):
        rect = select_region(image, "window" if args.window else "region")
        if rect is None:
            return 1
        image = image.copy(rect)

    if not args.raw:
        doc = Document(image)
        doc.frame.padding = int(settings.get("default_padding"))
        doc.frame.corner_radius = int(settings.get("default_radius"))
        doc.frame.shadow_strength = int(settings.get("default_shadow"))
        image = render_document(doc, float(settings.get("export_scale")))

    if args.out:
        path = os.path.expanduser(args.out)
    else:
        pattern = str(settings.get("filename_pattern"))
        try:
            stem = datetime.now().strftime(pattern)
        except ValueError:
            stem = datetime.now().strftime("Shotpad %Y-%m-%d %H-%M-%S")
        path = os.path.join(
            settings.save_directory(), f"{stem}.{settings.get('save_format')}"
        )

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    if not image.save(path):
        print(f"shotpad: could not write {path}", file=sys.stderr)
        return 3

    if args.copy:
        QGuiApplication.clipboard().setImage(image)
        # Give the clipboard manager a moment to take ownership before we exit.
        QTimer.singleShot(400, QApplication.instance().quit)
        QApplication.instance().exec()

    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])

    # Handled before argparse: this is an internal re-entry, not a user flag.
    if DAEMON_FLAG in raw:
        index = raw.index(DAEMON_FLAG)
        if index + 1 < len(raw):
            from .clipboard import run_clipboard_daemon

            QApplication.setApplicationName(APP_NAME)
            return run_clipboard_daemon(raw[index + 1])
        return 2

    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.install:
        from .desktop_install import install

        return install()

    if args.uninstall:
        from .desktop_install import uninstall

        return uninstall()

    if args.list_backends:
        # Backend probing needs a Qt application for the Qt/X11 backend.
        app = QApplication(sys.argv[:1])
        from .capture import capture_diagnostics

        print(capture_diagnostics())
        return 0

    # How the desktop finds our icon: the window has to carry an identifier the
    # shell can trace back to the .desktop file. On Wayland that identifier is
    # the xdg-shell app_id, which Qt takes from setDesktopFileName(). On X11 it
    # is WM_CLASS, whose instance half Qt derives from argv[0] - which is
    # "__main__.py" when the AppImage runs us as `python3 -m shotpad`. RESOURCE_NAME
    # overrides that, and must be set before QApplication reads it.
    os.environ.setdefault("RESOURCE_NAME", APP_ID)

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setDesktopFileName(APP_ID)
    QApplication.setApplicationVersion(__version__)

    app = QApplication(sys.argv[:1])
    app.setWindowIcon(_app_icon())
    apply_theme(app, str(settings.get("theme")))

    if args.delay is not None:
        settings.set("capture_delay", args.delay)
    if args.backend:
        settings.set("capture_backend", args.backend)

    if args.no_edit:
        if not (args.area or args.screen or args.window):
            args.screen = True
        return _headless_capture(args, parser)

    from .ui.window import MainWindow

    window = MainWindow()
    # Started for one capture and nothing else - typically a Print Screen
    # binding on `shotpad --area`. If that capture is cancelled there is no
    # reason for the editor to linger.
    window.quit_if_capture_cancelled = bool(
        args.area or args.screen or args.window or args.desktop_ui
    )
    window.show()

    if args.file:
        QTimer.singleShot(0, lambda: window.open_path(os.path.expanduser(args.file)))
    elif args.clipboard:
        QTimer.singleShot(0, window.paste_from_clipboard)
    elif args.desktop_ui:
        QTimer.singleShot(120, window.capture_with_desktop_ui)
    elif args.area:
        QTimer.singleShot(120, lambda: window.capture("area", args.delay))
    elif args.screen:
        QTimer.singleShot(120, lambda: window.capture("screen", args.delay))
    elif args.window:
        QTimer.singleShot(120, lambda: window.capture("window", args.delay))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

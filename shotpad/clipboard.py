"""Putting an image on the clipboard so that it outlives Shotpad.

Both X11 and Wayland keep clipboard *contents* inside the application that
copied them and transfer the bytes lazily, when something asks to paste. A
program that copies and then exits therefore leaves an empty clipboard, unless
a clipboard manager happened to take the data off its hands first - and stock
GNOME ships no clipboard manager at all.

That is why "copy on close" cannot just set the clipboard and quit. The fix is
the one wl-copy uses: hand the data to a small process that stays alive and
keeps serving it until something else takes the clipboard. Shotpad spawns itself
in that role rather than depending on wl-copy or xclip being installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage

#: Hidden CLI flag used to re-enter this module as the holder process.
DAEMON_FLAG = "--clipboard-daemon"


def set_clipboard_image(image: QImage) -> None:
    """Copy within this process. Only lasts as long as the process does.

    Deliberately setImage() and not setMimeData(): QClipboard takes ownership of
    a QMimeData, but PySide's wrapper frees it again when the interpreter shuts
    down, and the process dies with a segfault on exit. setImage() sidesteps the
    ownership question entirely and costs nothing - Qt's own conversion
    machinery still advertises image/png, image/bmp, image/tiff and the rest, so
    GTK applications that only accept image/png are served just the same.
    """
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setImage(image)


def _holder_command(path: str) -> list[str] | None:
    """How to launch a second copy of Shotpad as the clipboard holder."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.exists(appimage):
        # Re-running the AppImage gives the child its own mount, which matters:
        # the parent's /tmp/.mount_* disappears the moment the parent exits.
        return [appimage, DAEMON_FLAG, path]

    if os.path.basename(sys.argv[0] or "") == "shotpad" and os.path.exists(sys.argv[0]):
        return [sys.argv[0], DAEMON_FLAG, path]

    return [sys.executable, "-m", "shotpad", DAEMON_FLAG, path]


def copy_image_persistent(image: QImage) -> str:
    """Copy `image`, and try to make it survive this process exiting.

    Returns a short description of what was actually done, for the status bar
    and for tests. Never raises: a clipboard failure must not stop the window
    from closing.
    """
    if image.isNull():
        return "nothing to copy"

    # Fast path first: if a clipboard manager *is* running, this alone is
    # enough and the paste works instantly.
    try:
        set_clipboard_image(image)
    except Exception:
        pass

    try:
        handle, path = tempfile.mkstemp(prefix="shotpad-clip-", suffix=".png")
        os.close(handle)
        if not image.save(path, "PNG"):
            os.unlink(path)
            return "clipboard (this session only)"

        command = _holder_command(path)
        if command is None:
            os.unlink(path)
            return "clipboard (this session only)"

        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "clipboard"
    except Exception:
        return "clipboard (this session only)"


#: A holder is only useful while the user might still paste, and on Wayland
#: nothing tells it when the clipboard has moved on - so it is capped. Fifteen
#: minutes is well past any realistic copy-then-paste, and combined with the
#: single-instance pidfile it means at most one short-lived helper exists.
HOLDER_MAX_LIFETIME_MS = 15 * 60 * 1000


def _pidfile_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(runtime, f"shotpad-clipboard-{os.getuid()}.pid")


def _replace_previous_holder() -> None:
    """Make sure only one holder exists: the newest copy is the real one."""
    path = _pidfile_path()
    try:
        with open(path, encoding="utf-8") as handle:
            previous = int(handle.read().strip())
    except (OSError, ValueError):
        previous = 0

    if previous and previous != os.getpid():
        try:
            # Confirm it really is one of ours before signalling anything.
            with open(f"/proc/{previous}/cmdline", "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace")
            if DAEMON_FLAG in cmdline:
                os.kill(previous, 15)
        except (OSError, ValueError):
            pass

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        pass


def _clear_pidfile() -> None:
    path = _pidfile_path()
    try:
        with open(path, encoding="utf-8") as handle:
            if int(handle.read().strip()) == os.getpid():
                os.unlink(path)
    except (OSError, ValueError):
        pass


def _make_holder_surface():
    """A 1x1 window, needed only so Wayland will let us serve the clipboard.

    A client with no surface can *announce* a selection but cannot serve it:
    the paste comes back as zero bytes. wl-clipboard needs a surface for the
    same reason.

    The flags here are the ones that were measured to work. In particular do
    not add WindowDoesNotAcceptFocus or setWindowOpacity(0.0): with either of
    them the compositor stops offering the selection altogether, and the
    failure is silent - the clipboard simply reads back empty.

    Qt.Tool keeps it out of the taskbar and WA_ShowWithoutActivating stops it
    stealing focus; the window is hidden again as soon as the data is set.
    """
    from PySide6.QtWidgets import QWidget

    window = QWidget()
    window.setWindowFlags(
        Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
    )
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.resize(1, 1)
    window.setWindowTitle("Shotpad clipboard")
    window.show()
    return window


def run_clipboard_daemon(path: str) -> int:
    """Own the clipboard on behalf of a Shotpad that has already exited.

    Lives until another client takes the selection (detectable on X11 only),
    until a newer holder replaces it, or until the lifetime cap - whichever
    comes first. Only ever one of these exists at a time.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    image = QImage(path)
    try:
        os.unlink(path)
    except OSError:
        pass
    if image.isNull():
        return 1

    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return 1

    _replace_previous_holder()
    holder = _make_holder_surface()

    # show() is asynchronous: the Wayland surface is not mapped until the event
    # loop has run. Claiming the selection before that silently half-works -
    # the formats get advertised but every paste reads back zero bytes - so the
    # clipboard must not be touched until the surface is actually up.
    def claim() -> None:
        clipboard.setImage(image)
        QTimer.singleShot(900, settle)

    def settle() -> None:
        # The surface has done its job; serving the data still works once it
        # is hidden, so take it off the screen.
        holder.hide()
        # Best effort: on X11 both of these do report that another client took
        # the selection, and the holder exits the moment it becomes useless.
        # On Wayland neither ever fires for an unfocused client - Qt keeps
        # reporting ownsClipboard() == True - which is why the lifetime cap
        # below, and the single-instance pidfile, are what actually bound this.
        clipboard.dataChanged.connect(app.quit)
        watchdog.start(2000)

    def check_ownership() -> None:
        if not clipboard.ownsClipboard():
            app.quit()

    watchdog = QTimer()
    watchdog.timeout.connect(check_ownership)

    QTimer.singleShot(HOLDER_MAX_LIFETIME_MS, app.quit)

    QTimer.singleShot(400, claim)
    status = app.exec()
    holder.close()
    _clear_pidfile()
    return status

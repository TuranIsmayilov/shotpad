"""Screen capture backends.

Shotpad never assumes a desktop. It probes what is actually on the machine and
walks an ordered list of backends until one produces pixels:

  Wayland : portal -> grim -> desktop-specific CLI -> spectacle/gnome-screenshot
  X11     : Qt's own grab -> desktop-specific CLI -> maim/scrot/import -> portal

Every backend returns a single QImage covering the whole virtual desktop; area
and window selection happen afterwards in Shotpad's own overlay, so the
selection experience is identical on GNOME, KDE, XFCE and MATE.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from PySide6.QtGui import QGuiApplication, QImage

from .portal import (
    PortalCancelled,
    PortalError,
    portal_available,
    portal_screenshot,
)


class CaptureError(RuntimeError):
    pass


class CaptureCancelled(Exception):
    """Raised when the user dismissed a compositor-provided capture UI."""


def session_type() -> str:
    value = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if value in ("wayland", "x11"):
        return value
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def desktop_environment() -> str:
    """Normalised desktop name: gnome, kde, xfce, mate, cinnamon, ... """
    raw = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or ""
    ).lower()
    for name in ("gnome", "kde", "plasma", "xfce", "mate", "cinnamon",
                 "lxqt", "budgie", "deepin", "pantheon", "sway", "hyprland",
                 "wlroots", "i3"):
        if name in raw:
            return "kde" if name == "plasma" else name
    if os.environ.get("KDE_FULL_SESSION"):
        return "kde"
    if os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return "gnome"
    return raw.split(":")[0] if raw else "unknown"


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout, check=False
    )


def _load_and_unlink(path: str) -> QImage:
    image = QImage(path)
    try:
        os.unlink(path)
    except OSError:
        pass
    if image.isNull():
        raise CaptureError("The capture tool produced an unreadable image.")
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _temp_png() -> str:
    handle, path = tempfile.mkstemp(prefix="shotpad-", suffix=".png")
    os.close(handle)
    # Most CLI tools refuse to overwrite, or write nothing to an existing file.
    try:
        os.unlink(path)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------------------
# Individual backends
# ---------------------------------------------------------------------------


@dataclass
class Backend:
    name: str
    available: callable
    grab: callable
    #: True when the compositor runs its own region picker as part of the grab,
    #: so the returned image is already the region the user chose.
    preselects: bool = False


def _qt_available() -> bool:
    return session_type() == "x11" and QGuiApplication.instance() is not None


def _qt_grab() -> QImage:
    """Grab the whole virtual desktop through Qt (X11 only)."""
    app = QGuiApplication.instance()
    if app is None:
        raise CaptureError("No Qt application instance.")
    screens = QGuiApplication.screens()
    if not screens:
        raise CaptureError("No screens reported by Qt.")

    # Union of all screen geometries, in device pixels.
    left = min(s.geometry().left() * s.devicePixelRatio() for s in screens)
    top = min(s.geometry().top() * s.devicePixelRatio() for s in screens)
    right = max(s.geometry().right() * s.devicePixelRatio() for s in screens)
    bottom = max(s.geometry().bottom() * s.devicePixelRatio() for s in screens)

    width = int(right - left) + 1
    height = int(bottom - top) + 1

    canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)

    from PySide6.QtGui import QPainter

    painter = QPainter(canvas)
    got_pixels = False
    for screen in screens:
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            continue
        got_pixels = True
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()
        painter.drawPixmap(
            int(geo.left() * dpr - left), int(geo.top() * dpr - top), pixmap
        )
    painter.end()
    if not got_pixels:
        raise CaptureError("Qt returned an empty screen grab.")
    canvas.setDevicePixelRatio(1.0)
    return canvas


def _portal_grab() -> QImage:
    try:
        return portal_screenshot(interactive=False)
    except PortalCancelled as exc:
        raise CaptureCancelled() from exc
    except PortalError as exc:
        raise CaptureError(str(exc)) from exc


def _portal_interactive_grab() -> QImage:
    try:
        return portal_screenshot(interactive=True)
    except PortalCancelled as exc:
        raise CaptureCancelled() from exc
    except PortalError as exc:
        raise CaptureError(str(exc)) from exc


def _cli_backend(binary: str, args_for: callable) -> Backend:
    def available() -> bool:
        return shutil.which(binary) is not None

    def grab() -> QImage:
        path = _temp_png()
        try:
            result = _run([binary, *args_for(path)])
            if result.returncode != 0 or not os.path.exists(path):
                stderr = result.stderr.decode("utf-8", "replace").strip()
                raise CaptureError(f"{binary} failed: {stderr or result.returncode}")
            return _load_and_unlink(path)
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    return Backend(binary, available, grab)


def _grim_grab() -> QImage:
    path = _temp_png()
    result = _run(["grim", path])
    if result.returncode != 0 or not os.path.exists(path):
        raise CaptureError("grim failed to capture the screen.")
    return _load_and_unlink(path)


BACKENDS: dict[str, Backend] = {
    "portal": Backend("portal", portal_available, _portal_grab),
    "portal-interactive": Backend(
        "portal-interactive", portal_available, _portal_interactive_grab,
        preselects=True,
    ),
    "qt": Backend("qt", _qt_available, _qt_grab),
    "grim": Backend("grim", lambda: shutil.which("grim") is not None, _grim_grab),
    "gnome-screenshot": _cli_backend(
        "gnome-screenshot", lambda p: ["-f", p]
    ),
    "spectacle": _cli_backend(
        "spectacle", lambda p: ["-b", "-n", "-f", "-o", p]
    ),
    "xfce4-screenshooter": _cli_backend(
        "xfce4-screenshooter", lambda p: ["-f", "-s", p]
    ),
    "mate-screenshot": _cli_backend(
        "mate-screenshot", lambda p: ["-f", "--file", p]
    ),
    "gm": _cli_backend("gnome-screenshot", lambda p: ["-f", p]),
    "maim": _cli_backend("maim", lambda p: ["-u", p]),
    "scrot": _cli_backend("scrot", lambda p: ["-o", "-z", p]),
    "import": _cli_backend("import", lambda p: ["-window", "root", p]),
    "xwd": _cli_backend("xwd", lambda p: ["-root", "-silent", "-out", p]),
}


def backend_order() -> list[str]:
    """Preference order for the current session and desktop."""
    desktop = desktop_environment()
    if session_type() == "wayland":
        order = ["portal"]
        if desktop in ("sway", "hyprland", "wlroots", "i3"):
            order = ["grim", "portal"]
        order += ["grim"]
        if desktop == "kde":
            order += ["spectacle"]
        elif desktop == "gnome":
            order += ["gnome-screenshot"]
        order += ["spectacle", "gnome-screenshot", "portal-interactive"]
    else:
        order = ["qt"]
        if desktop == "xfce":
            order += ["xfce4-screenshooter"]
        elif desktop == "mate":
            order += ["mate-screenshot"]
        elif desktop == "kde":
            order += ["spectacle"]
        elif desktop == "gnome":
            order += ["gnome-screenshot"]
        order += [
            "maim", "scrot", "import",
            "xfce4-screenshooter", "mate-screenshot",
            "gnome-screenshot", "spectacle", "portal",
        ]

    seen: set[str] = set()
    return [b for b in order if not (b in seen or seen.add(b))]


def available_backends() -> list[str]:
    return [name for name in backend_order() if BACKENDS[name].available()]


def grab_screen(preferred: str = "auto") -> tuple[QImage, str]:
    """Capture the full desktop. Returns the image and the backend used."""
    if preferred != "auto" and preferred in BACKENDS:
        names = [preferred] + [n for n in backend_order() if n != preferred]
    else:
        names = backend_order()

    errors: list[str] = []
    for name in names:
        backend = BACKENDS[name]
        try:
            if not backend.available():
                continue
        except Exception:  # a probe should never take the app down
            continue
        try:
            image = backend.grab()
        except CaptureCancelled:
            raise
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if not image.isNull():
            return image, name
        errors.append(f"{name}: produced an empty image")

    detail = "\n".join(f"  - {e}" for e in errors) or "  - no backend was available"
    raise CaptureError(
        "Could not capture the screen on this desktop.\n"
        f"Session: {session_type()}, desktop: {desktop_environment()}\n"
        f"Backends tried:\n{detail}\n\n"
        "Install one of: grim (Wayland), maim, scrot, imagemagick, "
        "gnome-screenshot, spectacle, xfce4-screenshooter or mate-screenshot."
    )


def _full_grab_sizes() -> list[tuple[int, int]]:
    """Sizes an uncropped grab may legitimately have, in device pixels.

    Either the whole virtual desktop, or - some portals hand back a single
    monitor rather than the union - any one screen on its own.
    """
    if QGuiApplication.instance() is None:
        return []
    screens = QGuiApplication.screens()
    if not screens:
        return []

    left = min(s.geometry().left() * s.devicePixelRatio() for s in screens)
    top = min(s.geometry().top() * s.devicePixelRatio() for s in screens)
    right = max(s.geometry().right() * s.devicePixelRatio() for s in screens)
    bottom = max(s.geometry().bottom() * s.devicePixelRatio() for s in screens)

    sizes = [(int(right - left) + 1, int(bottom - top) + 1)]
    for screen in screens:
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()
        sizes.append((int(geo.width() * dpr), int(geo.height() * dpr)))
    return sizes


def grab_was_preselected(image: QImage, backend: str) -> bool:
    """True when the compositor already cropped this grab to a chosen region.

    GNOME's portal runs its own screenshot UI - with Screen/Window/Selection
    tabs - even for a request that asked for the plain full screen, so what
    comes back can already be a region. Putting Shotpad's own overlay on top of
    that would ask the user to select twice, inside their first selection.

    The decision is made from the pixels rather than from an assumption about
    the desktop: anything matching a full screen (or the whole desktop) is
    treated as uncropped, and Shotpad picks the region as usual.
    """
    entry = BACKENDS.get(backend)
    if entry is not None and entry.preselects:
        return True

    sizes = _full_grab_sizes()
    if not sizes:
        return False
    for width, height in sizes:
        if width <= 0 or height <= 0:
            continue
        # A couple of pixels of slack: rounding between Qt's geometry and the
        # compositor's own idea of the framebuffer is normal.
        if (abs(image.width() - width) <= max(2, int(width * 0.02))
                and abs(image.height() - height) <= max(2, int(height * 0.02))):
            return False
    return True


def capture_diagnostics() -> str:
    """Human-readable summary shown in the About / troubleshooting dialog."""
    lines = [
        f"Session type   : {session_type()}",
        f"Desktop        : {desktop_environment()}",
        f"XDG portal     : {'yes' if portal_available() else 'no'}",
        "Backends:",
    ]
    for name in backend_order():
        try:
            ok = BACKENDS[name].available()
        except Exception:
            ok = False
        lines.append(f"  {'*' if ok else ' '} {name}")
    return "\n".join(lines)

"""XDG Desktop Portal screenshot backend.

This is the only capture path that works on GNOME and KDE under Wayland, where
clients are forbidden from reading the framebuffer directly. It talks to
org.freedesktop.portal.Desktop over the session bus using QtDBus, so there is
no python-dbus dependency to bundle.
"""

from __future__ import annotations

import os
import secrets
import time
from urllib.parse import unquote, urlparse

from PySide6.QtCore import SLOT, QEventLoop, QObject, QTimer, QUrl, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PySide6.QtGui import QImage

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"


class PortalError(RuntimeError):
    pass


class PortalCancelled(PortalError):
    """The user dismissed the portal's own UI without choosing anything.

    Not a failure, and not something to report: the caller should abandon the
    capture quietly rather than fall through to another backend, which on GNOME
    would put a second picker on screen for a user who just said no.
    """

    def __init__(self, message: str = "cancelled") -> None:
        super().__init__(message)


#: How quickly a non-zero response has to arrive before we stop believing a
#: human produced it. GNOME's screenshot UI needs longer than this just to fade
#: in, so anything faster is the portal declining on its own - a real failure
#: the remaining backends should get a chance at.
_INTERACTION_FLOOR = 0.25


def portal_available() -> bool:
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    iface = bus.interface()
    if iface is None:
        return False
    reply = iface.isServiceRegistered(PORTAL_SERVICE)
    return bool(reply.value()) if hasattr(reply, "value") else bool(reply)


#: Two things have to line up here, and getting either wrong fails silently.
#:
#: 1. QtDBus wants a C++ slot *signature*, not a method name. A bare
#:    "_on_response" makes Qt read the first character as a metacall type code,
#:    connect() returns False, and the Response signal is never delivered - the
#:    request then just hangs until it times out.
#:
#: 2. The signature must match what @Slot actually registered, and it must ask
#:    for the demarshalled types. With a QDBusMessage parameter the results
#:    arrive as a raw QDBusArgument that PySide cannot read (its streaming API
#:    returns None); asking for (uint, QVariantMap) makes Qt demarshal the
#:    a{sv} into a plain dict for us.
#:
#: Note @Slot("uint", ...) - @Slot(int, ...) registers "int" and will not match.
RESPONSE_SLOT = SLOT("_on_response(uint,QVariantMap)")


class _ResponseWaiter(QObject):
    """Blocks on a portal Request until its Response signal arrives."""

    def __init__(self, bus: QDBusConnection, path: str) -> None:
        super().__init__()
        self.bus = bus
        self.path = path
        self.loop = QEventLoop()
        self.response: int | None = None
        self.results: dict = {}
        self.connected = bus.connect(
            PORTAL_SERVICE, path, REQUEST_IFACE, "Response",
            self, RESPONSE_SLOT,
        )
        if not self.connected:
            raise PortalError(
                "Could not subscribe to the desktop portal's reply signal. "
                "Without it the screenshot request would never complete."
            )

    @Slot("uint", "QVariantMap")
    def _on_response(self, response: int, results: dict) -> None:
        self.response = int(response)
        self.results = dict(results) if results else {}
        if self.loop.isRunning():
            self.loop.quit()

    def wait(self, timeout_ms: int = 90_000) -> tuple[int, dict]:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self.loop.quit)
        timer.start(timeout_ms)
        self.loop.exec()
        timer.stop()
        if self.connected:
            self.bus.disconnect(
                PORTAL_SERVICE, self.path, REQUEST_IFACE, "Response",
                self, RESPONSE_SLOT,
            )
        if self.response is None:
            raise PortalError(
                "The desktop portal did not answer.\n\n"
                "GNOME and KDE ask for permission the first time an "
                "application takes a screenshot. If a dialog is waiting on "
                "screen, allow it and try again - after that Shotpad captures "
                "without asking."
            )
        return self.response, self.results


def _request_path(bus: QDBusConnection, token: str) -> str:
    unique = bus.baseService().lstrip(":").replace(".", "_")
    return f"{PORTAL_PATH}/request/{unique}/{token}"


def _uri_to_image(uri: str, created_after: float | None = None) -> QImage:
    """Load the image the portal handed us, then clean up after it.

    The file is a transport detail, not something the user asked to keep, and
    xdg-desktop-portal-gnome drops it straight into ~/Pictures rather than a
    cache directory - so leaving it behind litters a real folder with a
    Screenshot-N.png for every capture.

    Deleting anything in ~/Pictures deserves care, so removal is gated on the
    file having been written *after* we made the request. A pre-existing file
    the portal happened to name the same thing is never touched.
    """
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        raise PortalError(f"Portal returned an unsupported URI scheme: {parsed.scheme}")
    path = unquote(parsed.path) if parsed.scheme else uri

    image = QImage(path)
    if image.isNull():
        raise PortalError(f"Could not read the portal screenshot at {path}")

    if created_after is not None:
        try:
            if os.path.getmtime(path) >= created_after - 2.0:
                os.unlink(path)
        except OSError:
            pass

    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def raise_for_response(response: int, elapsed: float) -> None:
    """Turn a portal Response code into the right exception, or none at all.

    The spec defines exactly three codes: 0 success, 1 "the user cancelled the
    interaction", 2 "the user interaction was ended in some other way". Both
    non-zero codes describe a user who ended up asking for nothing - real
    failures never reach the Response signal at all, they come back as D-Bus
    errors on the call itself.

    Reading 2 as a failure is what made Escape in GNOME's screenshot UI - which
    reports 2, not 1 - fall through to the next backend, open a second picker,
    and finally show "could not capture the screen" to a user who had simply
    changed their mind.

    ``elapsed`` is the one thing that distinguishes a dismissed UI from a portal
    that declined on its own: a code 2 that arrives before any UI could have
    been drawn was nobody's decision, so it stays an error and the remaining
    backends get their turn.
    """
    if response == 0:
        return
    if response == 1:
        raise PortalCancelled()
    if elapsed < _INTERACTION_FLOOR:
        raise PortalError("The portal refused the screenshot request.")
    raise PortalCancelled()


def portal_screenshot(interactive: bool = False, parent_handle: str = "") -> QImage:
    """Ask the portal for a screenshot.

    With ``interactive=False`` the compositor hands back the full screen (after
    a one-time permission prompt on GNOME). With ``interactive=True`` the
    desktop's own screenshot UI appears and the user picks the region.
    """
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise PortalError("No D-Bus session bus is available.")

    token = "shotpad_" + secrets.token_hex(8)
    waiter = _ResponseWaiter(bus, _request_path(bus, token))

    iface = QDBusInterface(PORTAL_SERVICE, PORTAL_PATH, SCREENSHOT_IFACE, bus)
    if not iface.isValid():
        raise PortalError("The screenshot portal is not available on this session.")

    options = {
        "handle_token": token,
        "interactive": bool(interactive),
        "modal": True,
    }
    requested_at = time.time()
    started = time.monotonic()
    reply = iface.call("Screenshot", parent_handle, options)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise PortalError(reply.errorMessage() or "Portal call failed.")

    response, results = waiter.wait()
    raise_for_response(response, time.monotonic() - started)

    uri = results.get("uri")
    if isinstance(uri, QUrl):
        uri = uri.toString()
    if not uri:
        raise PortalError("The portal returned no image.")
    return _uri_to_image(str(uri), created_after=requested_at)


def portal_pick_color() -> tuple[float, float, float] | None:
    """Screen colour picker, used by the eyedropper button."""
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    token = "shotpad_" + secrets.token_hex(8)
    try:
        waiter = _ResponseWaiter(bus, _request_path(bus, token))
    except PortalError:
        return None
    iface = QDBusInterface(PORTAL_SERVICE, PORTAL_PATH, SCREENSHOT_IFACE, bus)
    if not iface.isValid():
        return None
    reply = iface.call("PickColor", "", {"handle_token": token})
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        return None
    try:
        response, results = waiter.wait(timeout_ms=120_000)
    except PortalError:
        return None
    if response != 0:
        return None
    color = results.get("color")
    if not color:
        return None
    try:
        r, g, b = (float(c) for c in list(color)[:3])
    except (TypeError, ValueError):
        return None
    return r, g, b

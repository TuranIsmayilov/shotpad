"""Screen capture: desktop detection, backends and window discovery."""

from .backends import (
    BACKENDS,
    CaptureCancelled,
    CaptureError,
    available_backends,
    backend_order,
    capture_diagnostics,
    desktop_environment,
    grab_screen,
    grab_was_preselected,
    session_type,
)
from .portal import (
    PortalCancelled,
    portal_available,
    portal_pick_color,
    portal_screenshot,
)
from .windows import WindowInfo, list_windows, window_listing_supported

__all__ = [
    "BACKENDS",
    "CaptureCancelled",
    "CaptureError",
    "PortalCancelled",
    "WindowInfo",
    "available_backends",
    "backend_order",
    "capture_diagnostics",
    "desktop_environment",
    "grab_screen",
    "grab_was_preselected",
    "list_windows",
    "portal_available",
    "portal_pick_color",
    "portal_screenshot",
    "session_type",
    "window_listing_supported",
]

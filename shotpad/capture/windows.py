"""Window geometry discovery for the "capture a window" mode.

On X11 we can enumerate toplevels with standard EWMH properties, which lets the
region overlay snap to whole windows as the pointer moves over them - the same
behaviour on XFCE, MATE, KDE/X11 and GNOME/X11.

Wayland gives clients no way to enumerate other windows by design, so there we
fall back to the compositor's own picker via the portal.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from PySide6.QtCore import QRect

from .backends import session_type


@dataclass(frozen=True)
class WindowInfo:
    rect: QRect       # device pixels, virtual-desktop coordinates
    title: str
    wid: str


def window_listing_supported() -> bool:
    return (
        session_type() == "x11"
        and shutil.which("xprop") is not None
        and shutil.which("xwininfo") is not None
    )


def _run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8", "replace")


_ID_RE = re.compile(r"0x[0-9a-fA-F]+")


def list_windows() -> list[WindowInfo]:
    """Toplevel windows, topmost first. Empty when unsupported."""
    if not window_listing_supported():
        return []

    out = _run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
    if "not found" in out or "=" not in out:
        out = _run(["xprop", "-root", "_NET_CLIENT_LIST"])
    ids = _ID_RE.findall(out)
    if not ids:
        return []

    windows: list[WindowInfo] = []
    for wid in reversed(ids):  # stacking list is bottom-to-top
        info = _window_info(wid)
        if info is not None:
            windows.append(info)
    return windows


def _window_info(wid: str) -> WindowInfo | None:
    text = _run(["xwininfo", "-id", wid])
    if not text:
        return None

    def field(name: str) -> int | None:
        match = re.search(rf"{name}:\s+(-?\d+)", text)
        return int(match.group(1)) if match else None

    if "IsViewable" not in text:
        return None

    x, y = field("Absolute upper-left X"), field("Absolute upper-left Y")
    w, h = field("Width"), field("Height")
    if None in (x, y, w, h) or w < 8 or h < 8:
        return None

    # Include the frame/shadow area the WM added, when it reports one.
    extents = _run(["xprop", "-id", wid, "_NET_FRAME_EXTENTS"])
    numbers = re.findall(r"\d+", extents.split("=")[-1]) if "=" in extents else []
    if len(numbers) == 4:
        left, right, top, bottom = (int(n) for n in numbers)
        x -= left
        y -= top
        w += left + right
        h += top + bottom

    title_out = _run(["xprop", "-id", wid, "_NET_WM_NAME"])
    match = re.search(r'"(.*)"', title_out)
    title = match.group(1) if match else ""

    return WindowInfo(QRect(x, y, w, h), title, wid)

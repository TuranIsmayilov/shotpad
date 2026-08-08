"""Built-in vector icon set.

Shotpad ships its own icons rather than pulling from the desktop icon theme.
Adwaita, Breeze, Papirus and whatever XFCE happens to have installed all name
and draw things differently; drawing them ourselves is the only way the toolbar
looks the same on every desktop, and it keeps the AppImage free of a theme
dependency.

Each entry is the body of a 24x24 SVG that inherits stroke/fill from the
wrapper, so one definition serves both light and dark palettes.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# fmt: off
_PATHS: dict[str, str] = {
    "camera": '<path d="M3 8.5A2 2 0 0 1 5 6.5h2.2l1.2-2h7.2l1.2 2H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.5"/>',
    "monitor": '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8.5 20.5h7M12 17v3.5"/>',
    "select-area": '<path d="M3 8V5.5A2.5 2.5 0 0 1 5.5 3H8M16 3h2.5A2.5 2.5 0 0 1 21 5.5V8M21 16v2.5a2.5 2.5 0 0 1-2.5 2.5H16M8 21H5.5A2.5 2.5 0 0 1 3 18.5V16"/>',
    "window": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><circle cx="6.2" cy="6.5" r=".8" fill="currentColor"/><circle cx="8.8" cy="6.5" r=".8" fill="currentColor"/>',
    "timer": '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 9.5v4l2.5 2M9.5 2.5h5"/>',
    "cursor": '<path d="M5 3l6.5 17 2.6-7 7-2.6z"/>',
    "pen": '<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4.5 1.5L5 15z"/><path d="M14.5 5.5l3 3"/>',
    "highlighter": '<path d="M13.5 3.5l7 7-7.5 7.5H6l-2.5-2.5z"/><path d="M4 21h16"/>',
    "line": '<path d="M4.5 19.5l15-15"/>',
    "arrow": '<path d="M5 19L19 5"/><path d="M11 5h8v8"/>',
    "square": '<rect x="4" y="4" width="16" height="16" rx="2.5"/>',
    "circle": '<circle cx="12" cy="12" r="8.5"/>',
    "text": '<path d="M5 6.5V4.5h14v2M12 4.5v15M8.5 19.5h7"/>',
    "number": '<circle cx="12" cy="12" r="8.5"/><path d="M10.7 9.8L12.6 8.6v6.8M10.4 15.4h4.2"/>',
    "blur": '<path d="M12 3.5s6 6.2 6 10a6 6 0 0 1-12 0c0-3.8 6-10 6-10z"/><path d="M9.3 14.6a2.9 2.9 0 0 0 2.9 2.6"/>',
    "eraser": '<path d="M8.5 20.5H20"/><path d="M14.2 3.8l6 6a1.8 1.8 0 0 1 0 2.5l-7.4 7.4a1.8 1.8 0 0 1-2.5 0l-6-6a1.8 1.8 0 0 1 0-2.5l7.4-7.4a1.8 1.8 0 0 1 2.5 0z"/><path d="M7 9l8 8"/>',
    "crop": '<path d="M6.5 2.5v15h15"/><path d="M2.5 6.5h15v15"/>',
    # Grab text: a selection frame with lines of type inside it, so it reads as
    # "pick a region, get words" rather than as another text tool.
    "grabtext": '<path d="M3 7.5V5.5A2 2 0 0 1 5 3.5h2M17 3.5h2a2 2 0 0 1 2 2v2M21 16.5v2a2 2 0 0 1-2 2h-2M7 20.5H5a2 2 0 0 1-2-2v-2"/><path d="M7.5 8.5h9M7.5 12h9M7.5 15.5h5.5"/>',
    "undo": '<path d="M3.5 8.5h11a6 6 0 0 1 0 12H8"/><path d="M7.5 4.5l-4 4 4 4"/>',
    "redo": '<path d="M20.5 8.5h-11a6 6 0 0 0 0 12H16"/><path d="M16.5 4.5l4 4-4 4"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 5.5v-1a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h1"/>',
    "save": '<path d="M19.5 20.5h-15a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1h11l5 5v11a1 1 0 0 1-1 1z"/><path d="M7.5 3.5v6h8v-6M7.5 20.5v-6h9v6"/>',
    "folder": '<path d="M3 6.5A2 2 0 0 1 5 4.5h4l2 2.5h8a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "clipboard": '<rect x="7.5" y="3.5" width="9" height="4" rx="1.2"/><path d="M8.5 5.5H6a1.5 1.5 0 0 0-1.5 1.5v12A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5V7A1.5 1.5 0 0 0 18 5.5h-2.5"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/>',
    "image": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><circle cx="8.5" cy="10" r="1.8"/><path d="M3.5 17l5-5 4.5 4.5L16 14l4.5 4.5"/>',
    "palette": '<path d="M12 3.2a8.8 8.8 0 0 0 0 17.6c1.2 0 1.9-.8 1.9-1.7 0-.5-.2-.8-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.8-1.7 1.8-1.7h1.7a4.4 4.4 0 0 0 4.4-4.4c0-4.2-4-7.4-8.8-7.4z"/><circle cx="7.6" cy="11" r="1.1" fill="currentColor"/><circle cx="10.4" cy="7.2" r="1.1" fill="currentColor"/><circle cx="15" cy="7.8" r="1.1" fill="currentColor"/>',
    "chevron-down": '<path d="M6 9.5l6 6 6-6"/>',
    "chevron-right": '<path d="M9.5 6l6 6-6 6"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "check": '<path d="M4.5 12.5l5 5 10-11"/>',
    "trash": '<path d="M3.5 6.5h17M8.5 6.5V4.2A1.7 1.7 0 0 1 10.2 2.5h3.6a1.7 1.7 0 0 1 1.7 1.7v2.3"/><path d="M5.8 6.5l1 13a2 2 0 0 0 2 1.9h6.4a2 2 0 0 0 2-1.9l1-13"/>',
    "rotate-cw": '<path d="M20.5 5v6h-6"/><path d="M20 11a8 8 0 1 0-1.6 6.2"/>',
    "rotate-ccw": '<path d="M3.5 5v6h6"/><path d="M4 11a8 8 0 1 1 1.6 6.2"/>',
    "flip-h": '<path d="M12 3v18"/><path d="M8.5 7.5L3.5 12l5 4.5z"/><path d="M15.5 7.5l5 4.5-5 4.5z"/>',
    "flip-v": '<path d="M3 12h18"/><path d="M7.5 8.5L12 3.5l4.5 5z"/><path d="M7.5 15.5L12 20.5l4.5-5z"/>',
    "zoom-in": '<circle cx="10.5" cy="10.5" r="7"/><path d="M15.6 15.6l5 5M8 10.5h5M10.5 8v5"/>',
    "zoom-out": '<circle cx="10.5" cy="10.5" r="7"/><path d="M15.6 15.6l5 5M8 10.5h5"/>',
    "fit": '<path d="M3.5 9V4.5a1 1 0 0 1 1-1H9M15 3.5h4.5a1 1 0 0 1 1 1V9M20.5 15v4.5a1 1 0 0 1-1 1H15M9 20.5H4.5a1 1 0 0 1-1-1V15"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8v.4"/>',
    "alert": '<path d="M10.3 3.9a2 2 0 0 1 3.4 0l7.4 13.1a2 2 0 0 1-1.7 3H4.6a2 2 0 0 1-1.7-3z"/><path d="M12 9v4.6M12 16.9v.3"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "eyedropper": '<path d="M14 4.5l5.5 5.5"/><path d="M17.2 2.8a2.6 2.6 0 0 1 3.7 3.7l-2 2-3.7-3.7z"/><path d="M15.2 6.8L5.5 16.5 4 20l3.5-1.5 9.7-9.7z"/>',
    "layers": '<path d="M12 2.8l9 4.8-9 4.8-9-4.8z"/><path d="M3 12.4l9 4.8 9-4.8M3 17.1l9 4.8 9-4.8"/>',
    "share": '<circle cx="18" cy="5.5" r="2.8"/><circle cx="6" cy="12" r="2.8"/><circle cx="18" cy="18.5" r="2.8"/><path d="M8.4 10.7l7.2-3.9M8.4 13.3l7.2 3.9"/>',
    "grid": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    "aspect": '<rect x="2.5" y="5.5" width="19" height="13" rx="2"/><path d="M8 5.5v13M2.5 12h19"/>',
    "sun": '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6"/>',
    "moon": '<path d="M20.5 14.2A8.7 8.7 0 0 1 9.8 3.5a8.7 8.7 0 1 0 10.7 10.7z"/>',
    "shadow": '<rect x="3.5" y="3.5" width="12" height="12" rx="2.5"/><path d="M8.5 20.5h9a3 3 0 0 0 3-3v-9" opacity=".45"/>',
    "padding": '<rect x="2.5" y="2.5" width="19" height="19" rx="2.5" stroke-dasharray="3 2.5"/><rect x="7.5" y="7.5" width="9" height="9" rx="1.5"/>',
    "corner": '<path d="M3.5 20.5v-9a8 8 0 0 1 8-8h9"/><path d="M3.5 3.5h3M20.5 20.5v-3" opacity=".5"/>',
    "download": '<path d="M12 3.5v12M7.5 11l4.5 4.5 4.5-4.5"/><path d="M3.5 16v3a1.5 1.5 0 0 0 1.5 1.5h14a1.5 1.5 0 0 0 1.5-1.5v-3"/>',
    "pin": '<path d="M12 21v-6.5"/><path d="M8.2 3h7.6l-1 5.2 2.7 3.1v1.2H6.5v-1.2l2.7-3.1z"/>',
    "history": '<path d="M3.5 5v6h6"/><path d="M4 11a8 8 0 1 1 1.6 6.2"/><path d="M12 8v4.5l3 1.8"/>',
}
# fmt: on


def icon_names() -> list[str]:
    return sorted(_PATHS)


@lru_cache(maxsize=1024)
def _pixmap(name: str, size: int, color: str, dpr_x100: int) -> QPixmap:
    body = _PATHS.get(name, _PATHS["square"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round" '
        f'color="{color}">{body}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    dpr = dpr_x100 / 100.0
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon(name: str, color: QColor | str = "#000000", size: int = 20) -> QIcon:
    """Build a QIcon for `name` tinted with `color`."""
    text = color.name() if isinstance(color, QColor) else str(color)
    result = QIcon()
    for dpr in (100, 200):
        result.addPixmap(_pixmap(name, size, text, dpr))
    return result


def icon_pixmap(name: str, color: QColor | str, size: int = 20, dpr: float = 1.0) -> QPixmap:
    text = color.name() if isinstance(color, QColor) else str(color)
    return _pixmap(name, size, text, int(round(dpr * 100)))


def app_icon_svg(size: int = 256) -> str:
    """The application icon, also written to the AppImage / .desktop entry.

    A screenshot plate held inside crop marks, with the gradient showing through
    as padding - the three things Shotpad does, in one mark. Kept to primitives
    QtSvg renders: the ridges trace the plate's rounded bottom by hand rather
    than relying on a clipPath, because QSvgRenderer draws this too and its
    clipping and filter support is thinner than a browser's.
    """
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7f5af0"/>
      <stop offset="1" stop-color="#2cb67d"/>
    </linearGradient>
    <linearGradient id="plate" x1="0.1" y1="0" x2="0.5" y2="1">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#eaeef6"/>
    </linearGradient>
    <linearGradient id="hill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#34c88a"/>
      <stop offset="1" stop-color="#1f9e6b"/>
    </linearGradient>
    <filter id="sh" x="-30%" y="-30%" width="160%" height="170%">
      <feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#101733" flood-opacity="0.3"/>
    </filter>
  </defs>
  <rect width="256" height="256" rx="60" fill="url(#bg)"/>
  <g filter="url(#sh)">
    <rect x="52" y="80" width="152" height="96" rx="18" fill="url(#plate)"/>
  </g>
  <circle cx="86" cy="100" r="10" fill="#f7b32b"/>
  <path d="M52 140l40-32 32 28 28-20 52 30v12a18 18 0 0 1-18 18H70a18 18 0 0 1-18-18z"
        fill="#8fd9bb"/>
  <path d="M52 154l48-30 28 24 26-16 50 26a18 18 0 0 1-18 18H70a18 18 0 0 1-18-18z"
        fill="url(#hill)"/>
  <g fill="none" stroke="#ffffff" stroke-width="11" stroke-linecap="round">
    <path d="M40 96V80a14 14 0 0 1 14-14h16"/>
    <path d="M186 66h16a14 14 0 0 1 14 14v16"/>
    <path d="M216 160v16a14 14 0 0 1-14 14h-16"/>
    <path d="M70 190h-16a14 14 0 0 1-14-14v-16"/>
  </g>
</svg>"""

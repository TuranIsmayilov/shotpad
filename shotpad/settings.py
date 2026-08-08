"""Persisted preferences (QSettings -> ~/.config/Shotpad/Shotpad.conf)."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, QStandardPaths

from . import APP_NAME, ORG_NAME

DEFAULTS: dict[str, object] = {
    "theme": "auto",                    # auto | light | dark
    "save_dir": "",                     # empty -> XDG Pictures/Screenshots
    "save_format": "png",               # png | jpg | webp
    "jpeg_quality": 92,
    "export_scale": 1.0,
    "copy_after_capture": False,
    "close_after_save": False,
    "copy_on_close": False,
    "system_titlebar": False,
    "hide_window_on_capture": True,
    "capture_delay": 0,
    "capture_backend": "auto",
    "include_pointer": False,
    "filename_pattern": "Shotpad %Y-%m-%d %H-%M-%S",
    "last_tool": "select",
    "tool_rail_labels": True,           # name each tool in the rail; the ☰
                                        # button collapses it to icons only
    "tool_color": "#e5484d",
    "tool_width": 14.0,
    "arrow_taper": 0.35,
    "font_family": "Sans Serif",
    "font_size": 32.0,
    "recent_colors": [],
    "remember_style": True,
    "default_padding": 12,
    "default_radius": 6,
    "default_shadow": 55,
    "default_background": "linear:#2193b0:#6dd5ed:135",  # Ocean
    "ocr_language": "",                 # empty -> ocr.default_language()
    "show_home_on_start": True,
    "window_geometry": b"",
}


class Settings:
    def __init__(self) -> None:
        self._qs = QSettings(ORG_NAME, APP_NAME)

    def get(self, key: str, default=None):
        fallback = DEFAULTS.get(key, default)
        value = self._qs.value(key, fallback)
        if isinstance(fallback, bool):
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(fallback, int) and not isinstance(fallback, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                return fallback
        if isinstance(fallback, float):
            try:
                return float(value)
            except (TypeError, ValueError):
                return fallback
        if isinstance(fallback, list) and not isinstance(value, list):
            return list(value) if value else []
        return value

    def set(self, key: str, value) -> None:
        self._qs.setValue(key, value)

    def sync(self) -> None:
        self._qs.sync()

    # -- derived ------------------------------------------------------------
    def save_directory(self) -> str:
        configured = str(self.get("save_dir") or "")
        if configured and os.path.isdir(configured):
            return configured
        pictures = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        ) or os.path.expanduser("~")
        target = os.path.join(pictures, "Screenshots")
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            return pictures
        return target

    def push_recent_color(self, hex_color: str) -> None:
        recent = [c for c in self.get("recent_colors", []) if c != hex_color]
        recent.insert(0, hex_color)
        self.set("recent_colors", recent[:12])


settings = Settings()

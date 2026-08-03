"""Desktop integration for the AppImage.

An AppImage is a single file the desktop knows nothing about: no menu entry, no
icon, no MIME association, and nothing for a keyboard shortcut to point at.
``shotpad --install`` writes the small handful of XDG files that fix that, into
the user's own ~/.local/share, and ``--uninstall`` removes exactly those files
again. This is the same on GNOME, KDE, XFCE and MATE because it is the
freedesktop spec they all implement.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import APP_ID, APP_NAME

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.5
Name={name}
GenericName=Screenshot Editor
Comment=Capture, annotate and beautify screenshots
Keywords=screenshot;capture;screen;snip;annotate;arrow;blur;redact;crop;padding;
Exec={exec_path} %f
TryExec={exec_bare}
Icon={app_id}
Terminal=false
Categories=Graphics;2DGraphics;RasterGraphics;Photography;
MimeType=image/png;image/jpeg;image/webp;image/bmp;image/tiff;image/gif;
StartupNotify=true
StartupWMClass={app_id}
Actions=Area;Screen;Window;

[Desktop Action Area]
Name=Capture an area
Exec={exec_path} --area

[Desktop Action Screen]
Name=Capture the whole screen
Exec={exec_path} --screen

[Desktop Action Window]
Name=Capture a window
Exec={exec_path} --window
"""


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def _paths() -> dict[str, str]:
    data = _data_home()
    return {
        "desktop": os.path.join(data, "applications", f"{APP_ID}.desktop"),
        "icon": os.path.join(
            data, "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg"
        ),
        "metainfo": os.path.join(data, "metainfo", f"{APP_ID}.metainfo.xml"),
    }


def _metainfo_source() -> str:
    """Where this copy of Shotpad keeps its AppStream metainfo, if anywhere.

    It lives inside the package rather than in ``packaging/`` so that a pip or
    pipx install carries it too - setuptools ships what is under ``shotpad/``
    and nothing else. Returns "" if it is missing, which is not fatal: the
    metainfo only matters to software centres.
    """
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", f"{APP_ID}.metainfo.xml"
    )
    return path if os.path.exists(path) else ""


def launcher_command() -> str:
    """The command line that should start this copy of Shotpad.

    Inside an AppImage the runtime exports APPIMAGE with the absolute path of
    the bundle; outside one, fall back to the console script or the module.
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.exists(appimage):
        return _quote(appimage)

    script = shutil.which("shotpad")
    if script:
        return _quote(script)

    return f"{_quote(sys.executable)} -m shotpad"


def _quote(path: str) -> str:
    return f'"{path}"' if " " in path else path


def _refresh_caches() -> None:
    """Nudge the desktop to notice the new files. Failure is harmless."""
    data = _data_home()
    for command in (
        ["update-desktop-database", os.path.join(data, "applications")],
        ["gtk-update-icon-cache", "-f", "-t", os.path.join(data, "icons", "hicolor")],
        ["xdg-desktop-menu", "forceupdate"],
        ["kbuildsycoca6", "--noincremental"],
    ):
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(
                command, capture_output=True, timeout=25, check=False
            )
        except (OSError, subprocess.SubprocessError):
            pass


def install() -> int:
    from .icons import app_icon_svg

    paths = _paths()
    command = launcher_command()
    bare = command.split()[0].strip('"')

    for path in paths.values():
        os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(paths["desktop"], "w", encoding="utf-8") as handle:
        handle.write(
            DESKTOP_TEMPLATE.format(
                name=APP_NAME, exec_path=command, exec_bare=bare, app_id=APP_ID
            )
        )
    os.chmod(paths["desktop"], 0o755)

    with open(paths["icon"], "w", encoding="utf-8") as handle:
        handle.write(app_icon_svg())

    metainfo = _metainfo_source()
    if metainfo:
        shutil.copyfile(metainfo, paths["metainfo"])

    _refresh_caches()

    print(f"{APP_NAME} installed for the current user.")
    print(f"  launcher : {paths['desktop']}")
    print(f"  command  : {command}")
    print()
    print("For one-press capture, bind a key in your desktop's keyboard")
    print("settings to this command:")
    print()
    print(f"    {command} --area")
    print()
    print("  GNOME  Settings > Keyboard > View and Customise Shortcuts >")
    print("         Custom Shortcuts")
    print("  KDE    System Settings > Shortcuts > Add > Command or Script")
    print("  XFCE   Settings > Keyboard > Application Shortcuts")
    print("  MATE   Control Center > Keyboard Shortcuts > Add")
    return 0


def uninstall() -> int:
    removed = 0
    for path in _paths().values():
        if os.path.exists(path):
            try:
                os.unlink(path)
                removed += 1
            except OSError as exc:
                print(f"shotpad: could not remove {path}: {exc}", file=sys.stderr)
    _refresh_caches()
    print(f"Removed {removed} file{'' if removed == 1 else 's'}.")
    return 0

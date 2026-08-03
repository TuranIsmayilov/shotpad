Shotpad - licences of everything inside this AppImage
=====================================================

An AppImage is self-contained: besides Shotpad itself it carries a Python
interpreter and the Qt libraries. Those are separate works with their own
licences, and this directory holds the full text of each one.


Shotpad
-------
Copyright (C) 2026 Turan Ismayilov
Licence : GNU General Public License, version 3 or later  -> GPL-3.0.txt
Source  : https://github.com/TuranIsmayilov/shotpad

  The complete source of this build is the tagged release matching the
  version reported by `shotpad --version`.

  The name "Shotpad" and the Shotpad icon are not covered by the GPL and
  remain the property of Turan Ismayilov. A modified or repackaged build
  must be renamed.


Qt, via PySide6 and shiboken6
-----------------------------
Copyright (C) The Qt Company Ltd. and contributors
Licence : GNU Lesser General Public License, version 3  -> LGPL-3.0.txt
          (the LGPL builds on the GPL, so GPL-3.0.txt applies as well)
Source  : https://download.qt.io/official_releases/qt/
          https://download.qt.io/official_releases/QtForPython/

  Qt is bundled unmodified, as shared libraries under
  usr/python/lib/python3.12/site-packages/PySide6/Qt/lib/. Because they are
  dynamically linked, they can be replaced with your own build of the same
  Qt version: extract the AppImage with `--appimage-extract`, swap the
  libraries, and run the extracted AppRun.


CPython
-------
Copyright (C) Python Software Foundation
Licence : PSF License Agreement  -> Python-LICENSE.txt
Source  : https://github.com/astral-sh/python-build-standalone

  The relocatable interpreter comes from python-build-standalone.

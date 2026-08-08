"""Text recognition: hand Tesseract a crop, get lines of text back.

Shotpad talks to the ``tesseract`` binary over a pipe rather than linking
libtesseract. The binary the AppImage carries is built static and stripped of
its optional backends - no libcurl, no libarchive, and a Leptonica with every
image-format reader switched off - which is what keeps the bundle portable and
free of an OpenSSL/Kerberos tail it would otherwise inherit. A subprocess is
also how the clipboard holder already works, so this adds no new machinery.

Because Leptonica is built without the image libraries, the one format it can
still read is the one Qt is happy to write: PPM. That is the whole handshake.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

#: Tesseract wants roughly 300dpi. Screenshot text is often half that: UI text
#: at 12px on a 1x display reads "Corner radius" as "Comer radius", while the
#: same words from a HiDPI capture come back clean. So the scale is measured,
#: not assumed - a first pass reports how tall the words actually are.
TARGET_TEXT_HEIGHT = 30.0
MIN_TEXT_HEIGHT = 20.0
MAX_UPSCALE = 4.0

#: Words below this confidence are dropped rather than guessed at.
WORD_CONFIDENCE_FLOOR = 40.0
#: Below this mean confidence the result is offered, but not called reliable.
RESULT_CONFIDENCE_FLOOR = 65.0

_TIMEOUT = 60


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float


@dataclass(frozen=True)
class OcrResult:
    """What a grab produced, and how much to trust it."""

    lines: list[OcrLine] = field(default_factory=list)
    scale: float = 1.0

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def confidence(self) -> float:
        """Mean word confidence, weighted by how much text each line carries."""
        weighted = sum(line.confidence * len(line.text) for line in self.lines)
        length = sum(len(line.text) for line in self.lines)
        return weighted / length if length else 0.0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def is_uncertain(self) -> bool:
        return not self.is_empty and self.confidence < RESULT_CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# Locating the engine
# ---------------------------------------------------------------------------


def tesseract_path() -> str | None:
    """The binary to run, or None if this build has no OCR available.

    SHOTPAD_TESSERACT comes first so a packager - or someone testing a new
    build - can point at their own without rebuilding.
    """
    override = os.environ.get("SHOTPAD_TESSERACT")
    if override:
        return override if os.path.isfile(override) else None

    appdir = os.environ.get("APPDIR")
    if appdir:
        bundled = os.path.join(appdir, "usr", "bin", "tesseract")
        if os.path.isfile(bundled):
            return bundled

    # A pip install has no AppDir; fall back to whatever the system provides.
    return shutil.which("tesseract")


def tessdata_dir() -> str | None:
    """Where the language models live.

    A user directory is checked first, so extra languages can be dropped in
    without touching the bundle - the AppImage is read-only, and shipping every
    language would multiply the download for no one's benefit.
    """
    user = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "shotpad", "tessdata",
    )
    candidates = [user]

    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, "usr", "share", "tessdata"))
    if os.environ.get("TESSDATA_PREFIX"):
        candidates.append(os.environ["TESSDATA_PREFIX"])
    candidates.append("/usr/share/tesseract-ocr/5/tessdata")
    candidates.append("/usr/share/tessdata")

    for path in candidates:
        if os.path.isdir(path) and any(
            name.endswith(".traineddata") for name in os.listdir(path)
        ):
            return path
    return None


def available() -> bool:
    """Whether a grab could actually run right now."""
    return tesseract_path() is not None and tessdata_dir() is not None


def languages() -> list[str]:
    """Language codes with a model present, e.g. ['eng', 'tur']."""
    directory = tessdata_dir()
    if directory is None:
        return []
    found = sorted(
        name[: -len(".traineddata")]
        for name in os.listdir(directory)
        if name.endswith(".traineddata")
    )
    # osd is orientation detection, not a language anyone would pick.
    return [code for code in found if code != "osd"]


#: Tesseract uses ISO 639-2/T; Qt and the environment speak 639-1. Only the
#: codes Shotpad ships are mapped - anything else falls through to the name
#: Tesseract itself uses.
LANGUAGE_NAMES: dict[str, str] = {
    "eng": "English",
    "tur": "Türkçe",
    "aze": "Azərbaycan",
    "rus": "Русский",
}

_ISO_639_1 = {"en": "eng", "tr": "tur", "az": "aze", "ru": "rus"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def default_language() -> str:
    """The language to start on: the user's own if we ship a model for it.

    Defaulting to English would be the wrong guess for most of the people this
    list was chosen for, and the locale is the one hint available without
    asking.
    """
    installed = languages()
    if not installed:
        return "eng"

    from PySide6.QtCore import QLocale

    candidates = []
    for name in QLocale().uiLanguages() + [QLocale().name()]:
        two = name.replace("_", "-").split("-")[0].lower()
        if two in _ISO_639_1:
            candidates.append(_ISO_639_1[two])
    for code in candidates:
        if code in installed:
            return code
    return "eng" if "eng" in installed else installed[0]


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _run(image: QImage, language: str) -> list[list[str]]:
    """Feed one image to Tesseract and return its TSV rows, header dropped."""
    binary = tesseract_path()
    directory = tessdata_dir()
    if binary is None or directory is None or image.isNull():
        return []

    with tempfile.TemporaryDirectory(prefix="shotpad-ocr-") as work:
        source = os.path.join(work, "in.ppm")
        # PPM is the format Leptonica can still read with no image libraries
        # compiled in, and Qt writes it without a plugin.
        if not image.save(source, "PPM"):
            return []

        env = dict(os.environ, TESSDATA_PREFIX=directory)
        try:
            result = subprocess.run(
                # `-c tessedit_create_tsv=1` rather than the `tsv` config
                # file: configs live in tessdata/configs/, so asking for the
                # renderer by name would mean shipping that directory too and
                # failing confusingly if it were ever missing.
                [
                    binary, source, "-", "--psm", "6", "-l", language,
                    "-c", "tessedit_create_tsv=1",
                ],
                env=env, capture_output=True, timeout=_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return []

    if result.returncode != 0:
        return []
    # stderr is deliberately ignored: a Leptonica built without TIFF prints
    # "pixReadMemTiff: function not present" from its debug-font path on every
    # run. It is noise from a code path we never reach, not a failure.
    text = result.stdout.decode("utf-8", "replace")
    rows = [line.split("\t") for line in text.splitlines()]
    return [row for row in rows[1:] if len(row) >= 12]


def _median_text_height(rows: list[list[str]]) -> float:
    """How tall the recognised words are, in pixels of the image we sent."""
    heights = []
    for row in rows:
        try:
            confidence = float(row[10])
            height = float(row[9])
        except ValueError:
            continue
        if confidence >= 0 and row[11].strip():
            heights.append(height)
    if not heights:
        return 0.0
    heights.sort()
    return heights[len(heights) // 2]


def _lines_from(rows: list[list[str]]) -> list[OcrLine]:
    """Group TSV words back into lines, dropping the ones Tesseract doubts."""
    grouped: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    order: list[tuple[str, str, str]] = []

    for row in rows:
        try:
            confidence = float(row[10])
        except ValueError:
            continue
        word = row[11].strip()
        if not word or confidence < WORD_CONFIDENCE_FLOOR:
            continue
        key = (row[2], row[3], row[4])  # block, paragraph, line
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((word, confidence))

    lines = []
    for key in order:
        words = grouped[key]
        text = " ".join(word for word, _c in words)
        mean = sum(c for _w, c in words) / len(words)
        lines.append(OcrLine(text=text, confidence=mean))
    return lines


def recognise(image: QImage, language: str = "eng") -> OcrResult:
    """Read the text in `image`, upscaling first if the words are small.

    The first pass doubles as a measurement: Tesseract reports a bounding box
    per word, so the median word height says whether this is a HiDPI capture
    that is already legible or 1x UI text that needs enlarging. Re-running is
    cheap - a small crop takes about a tenth of a second.
    """
    if image.isNull() or not available():
        return OcrResult()

    rows = _run(image, language)
    if not rows:
        return OcrResult()

    height = _median_text_height(rows)
    if 0.0 < height < MIN_TEXT_HEIGHT:
        scale = min(MAX_UPSCALE, TARGET_TEXT_HEIGHT / height)
        bigger = image.scaled(
            max(1, int(round(image.width() * scale))),
            max(1, int(round(image.height() * scale))),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        rescanned = _run(bigger, language)
        if rescanned:
            return OcrResult(lines=_lines_from(rescanned), scale=scale)

    return OcrResult(lines=_lines_from(rows), scale=1.0)

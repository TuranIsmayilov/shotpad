#!/usr/bin/env bash
#
# Build a self-contained Shotpad AppImage.
#
# The bundle carries a relocatable CPython (from python-build-standalone, built
# against an old glibc for wide compatibility) plus PySide6-Essentials. Nothing
# is taken from the build host, so the result runs on any reasonably modern
# x86_64 or aarch64 Linux regardless of which desktop is installed.
#
# Usage:
#   ./packaging/build-appimage.sh [--no-prune] [--no-ocr]
#                                 [--arch x86_64|aarch64] [--out DIR]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGING="${REPO_ROOT}/packaging"
BUILD_DIR="${REPO_ROOT}/build"
APPDIR="${BUILD_DIR}/Shotpad.AppDir"
CACHE_DIR="${BUILD_DIR}/cache"
OUT_DIR="${REPO_ROOT}/dist"

APP_ID="io.github.TuranIsmayilov.Shotpad"
PYTHON_VERSION="3.12"
PRUNE=1
ARCH="$(uname -m)"
OCR=1

#: Languages bundled for the Grab text tool. Each model is 3-4 MB, so this is
#: a deliberate short list rather than everything Tesseract offers; users can
#: drop more into ~/.local/share/shotpad/tessdata, which shotpad/ocr.py reads
#: first. tessdata_fast is the LSTM-only set - Debian's tesseract-ocr-eng is
#: byte-identical to it, so there is no smaller honest option.
OCR_LANGUAGES=(eng tur aze rus)
TESSDATA_BASE="https://github.com/tesseract-ocr/tessdata_fast/raw/main"

# Pinned fallback if the GitHub API is unreachable or rate-limited.
PBS_FALLBACK_TAG="20250818"
PBS_FALLBACK_PY="3.12.11"

# python-build-standalone publishes a pre-stripped variant. Use it: GNU strip
# corrupts the symbol versioning in the full build and the interpreter then
# dies with "undefined symbol" as soon as it dlopens an extension module.
PBS_FLAVOUR="install_only_stripped"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-prune) PRUNE=0; shift ;;
        --no-ocr) OCR=0; shift ;;
        --arch) ARCH="$2"; shift 2 ;;
        --out) OUT_DIR="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

case "${ARCH}" in
    x86_64)  PBS_TRIPLE="x86_64-unknown-linux-gnu";  AI_ARCH="x86_64" ;;
    aarch64|arm64) PBS_TRIPLE="aarch64-unknown-linux-gnu"; AI_ARCH="aarch64" ;;
    *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;;
esac

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

for tool in curl tar; do
    command -v "${tool}" >/dev/null || die "${tool} is required."
done

mkdir -p "${CACHE_DIR}" "${OUT_DIR}"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/app" "${APPDIR}/usr/share/applications" \
         "${APPDIR}/usr/share/metainfo" \
         "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "${REPO_ROOT}/shotpad/__init__.py")"
[[ -n "${VERSION}" ]] || die "Could not read the version from shotpad/__init__.py"
log "Building Shotpad ${VERSION} for ${AI_ARCH}"

# ---------------------------------------------------------------------------
# 1. Relocatable CPython
# ---------------------------------------------------------------------------
resolve_python_url() {
    local api="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
    local tag
    tag="$(curl -fsSL --max-time 20 "${api}" 2>/dev/null \
           | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)" || true
    if [[ -z "${tag}" ]]; then
        warn "GitHub API unavailable; falling back to the pinned build ${PBS_FALLBACK_TAG}"
        echo "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_FALLBACK_TAG}/cpython-${PBS_FALLBACK_PY}+${PBS_FALLBACK_TAG}-${PBS_TRIPLE}-${PBS_FLAVOUR}.tar.gz"
        return
    fi
    # Ask the release for the exact patch version of our minor series.
    local assets patch
    assets="$(curl -fsSL --max-time 20 \
        "https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/${tag}" || true)"
    patch="$(echo "${assets}" \
        | grep -o "cpython-${PYTHON_VERSION}\.[0-9]\+%2B${tag}-${PBS_TRIPLE}-${PBS_FLAVOUR}\.tar\.gz" \
        | head -1 | sed 's/%2B/+/')"
    if [[ -z "${patch}" ]]; then
        patch="$(echo "${assets}" \
            | grep -o "cpython-${PYTHON_VERSION}\.[0-9]\++${tag}-${PBS_TRIPLE}-${PBS_FLAVOUR}\.tar\.gz" \
            | head -1)"
    fi
    if [[ -z "${patch}" ]]; then
        warn "No ${PYTHON_VERSION} build in ${tag}; falling back to ${PBS_FALLBACK_TAG}"
        echo "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_FALLBACK_TAG}/cpython-${PBS_FALLBACK_PY}+${PBS_FALLBACK_TAG}-${PBS_TRIPLE}-${PBS_FLAVOUR}.tar.gz"
        return
    fi
    echo "https://github.com/astral-sh/python-build-standalone/releases/download/${tag}/${patch}"
}

PY_URL="$(resolve_python_url)"
PY_TARBALL="${CACHE_DIR}/$(basename "${PY_URL}" | sed 's/+/plus/')"

if [[ ! -f "${PY_TARBALL}" ]]; then
    log "Downloading $(basename "${PY_URL}")"
    curl -fL --retry 3 --progress-bar -o "${PY_TARBALL}.part" "${PY_URL}" \
        || die "Could not download the standalone Python build."
    mv "${PY_TARBALL}.part" "${PY_TARBALL}"
else
    log "Using the cached Python build"
fi

log "Unpacking Python into the AppDir"
mkdir -p "${APPDIR}/usr/python"
tar -xzf "${PY_TARBALL}" -C "${APPDIR}/usr/python" --strip-components=1

PY="${APPDIR}/usr/python/bin/python3"
[[ -x "${PY}" ]] || die "The unpacked Python is not executable."

# ---------------------------------------------------------------------------
# 2. Dependencies and the app itself
# ---------------------------------------------------------------------------
log "Installing PySide6-Essentials"
"${PY}" -m pip install --quiet --upgrade pip
# Deliberately narrower than the range in pyproject.toml: the bundle should be
# reproducible, and it pins its own interpreter (PYTHON_VERSION) too. Note that
# each PySide6 release refuses Python newer than it knew about - 6.9 caps at
# 3.13 - so this ceiling has to rise whenever PYTHON_VERSION does, or pip will
# report "no matching distribution" here.
"${PY}" -m pip install --quiet \
    --no-warn-script-location \
    "PySide6-Essentials>=6.7,<6.10" \
    || die "pip could not install PySide6-Essentials."

log "Adding the Shotpad package"
cp -r "${REPO_ROOT}/shotpad" "${APPDIR}/usr/app/"
find "${APPDIR}/usr/app" -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------
# 2b. Text recognition
# ---------------------------------------------------------------------------
# The engine is optional on purpose. shotpad/ocr.py reports it as unavailable
# and the Grab text tool says so rather than failing, so a machine without a
# C++ toolchain can still produce a working AppImage - just one without OCR.
bundle_ocr() {
    local builder="${PACKAGING}/build-tesseract.sh"
    local binary="${CACHE_DIR}/tesseract-5.5.0-static-${AI_ARCH}"

    if [[ ! -x "${binary}" ]]; then
        if ! command -v cmake >/dev/null || ! { command -v c++ >/dev/null || command -v g++ >/dev/null; }; then
            warn "cmake and a C++ compiler are needed to build the OCR engine."
            warn "Building without text recognition; pass --no-ocr to silence this."
            return 0
        fi
        if ! "${builder}" --out "${binary}"; then
            warn "the OCR engine failed to build - continuing without it."
            return 0
        fi
    else
        echo "    reusing the cached OCR engine"
    fi

    # Cross-building cannot run the host's binary, and a binary for the wrong
    # architecture in the bundle is worse than none at all.
    if [[ "${AI_ARCH}" != "$(uname -m)" ]]; then
        warn "cross-building for ${AI_ARCH}; skipping the OCR engine."
        return 0
    fi

    install -Dm755 "${binary}" "${APPDIR}/usr/bin/tesseract"

    mkdir -p "${APPDIR}/usr/share/tessdata" "${CACHE_DIR}/tessdata"
    local missing=0
    for lang in "${OCR_LANGUAGES[@]}"; do
        local cached="${CACHE_DIR}/tessdata/${lang}.traineddata"
        if [[ ! -f "${cached}" ]]; then
            curl -fsSL --retry 3 --max-time 300 -o "${cached}.part" \
                "${TESSDATA_BASE}/${lang}.traineddata" 2>/dev/null \
                && mv "${cached}.part" "${cached}" \
                || { rm -f "${cached}.part"; warn "could not fetch the ${lang} model"; missing=1; continue; }
        fi
        install -Dm644 "${cached}" "${APPDIR}/usr/share/tessdata/${lang}.traineddata"
    done

    if [[ ! -f "${APPDIR}/usr/share/tessdata/eng.traineddata" ]]; then
        warn "no language models were bundled - removing the OCR engine."
        rm -rf "${APPDIR}/usr/bin/tesseract" "${APPDIR}/usr/share/tessdata"
        return 0
    fi
    [[ "${missing}" == "1" ]] && warn "some language models are missing from this build."

    echo "    engine $(du -h "${APPDIR}/usr/bin/tesseract" | cut -f1), models: $(ls "${APPDIR}/usr/share/tessdata" | sed 's/\.traineddata//' | tr '\n' ' ')"
}

if [[ "${OCR}" == "1" ]]; then
    log "Adding text recognition"
    bundle_ocr
fi

# ---------------------------------------------------------------------------
# 3. Trim the bundle
# ---------------------------------------------------------------------------
SITE="$("${PY}" -c 'import site; print(site.getsitepackages()[0])')"
PYSIDE="${SITE}/PySide6"

if [[ "${PRUNE}" == "1" && -d "${PYSIDE}" ]]; then
    log "Pruning Qt modules Shotpad does not use"

    # A keep-list, not a remove-list. Qt ships dozens of libraries whose names
    # are variations on a theme (QuickControls2Imagine, QmlCompiler,
    # WaylandCompositor...), and a remove-list silently misses the ones added
    # by the next Qt release.
    #
    # Shotpad imports QtCore, QtGui, QtWidgets, QtDBus and QtSvg. The rest of
    # this list is what those link against or load at runtime: XcbQpa for X11,
    # WaylandClient for Wayland, Network for the TLS-less bits Core needs,
    # OpenGL for the raster/GL fallback paths.
    KEEP_LIBS=(
        Core Gui Widgets DBus Svg SvgWidgets Network OpenGL OpenGLWidgets
        PrintSupport Xml Concurrent XcbQpa
        WaylandClient WaylandEglClientHwIntegration WlShellIntegration
        EglFSDeviceIntegration EglFsKmsSupport DeviceDiscoverySupport
    )
    keep_pattern="$(IFS='|'; echo "${KEEP_LIBS[*]}")"

    removed=0
    while IFS= read -r lib; do
        base="$(basename "${lib}")"
        module="${base#libQt6}"
        module="${module%%.so*}"
        if [[ ! "${module}" =~ ^(${keep_pattern})$ ]]; then
            rm -f "${lib}"
            removed=$((removed + 1))
        fi
    done < <(find "${PYSIDE}/Qt/lib" -maxdepth 1 -name 'libQt6*.so*' 2>/dev/null)
    echo "    removed ${removed} Qt libraries"

    # The Python bindings for those modules go too. QtOpenGL alone is 8 MB.
    KEEP_BINDINGS=(QtCore QtGui QtWidgets QtDBus QtSvg QtNetwork QtXml)
    keep_bind_pattern="$(IFS='|'; echo "${KEEP_BINDINGS[*]}")"
    while IFS= read -r binding; do
        base="$(basename "${binding}")"
        name="${base%%.abi3.so}"
        if [[ ! "${name}" =~ ^(${keep_bind_pattern})$ ]]; then
            rm -f "${binding}"
        fi
    done < <(find "${PYSIDE}" -maxdepth 1 -name 'Qt*.abi3.so' 2>/dev/null)

    rm -rf "${PYSIDE}/Qt/qml" \
           "${PYSIDE}/Qt/translations" \
           "${PYSIDE}/Qt/resources" \
           "${PYSIDE}/Qt/libexec" \
           "${PYSIDE}/examples" \
           "${PYSIDE}/glue" \
           "${PYSIDE}/include" \
           "${PYSIDE}/typesystems" \
           "${PYSIDE}/scripts" \
           "${PYSIDE}/support" \
           "${PYSIDE}/Assistant.app" \
           2>/dev/null || true

    # Keep only the plugin categories a widgets app actually loads.
    KEEP_PLUGINS=(
        platforms platformthemes platforminputcontexts imageformats
        iconengines xcbglintegrations wayland-shell-integration
        wayland-graphics-integration-client wayland-decoration-client
        egldeviceintegrations generic tls networkinformation
    )
    if [[ -d "${PYSIDE}/Qt/plugins" ]]; then
        for dir in "${PYSIDE}/Qt/plugins"/*/; do
            name="$(basename "${dir}")"
            keep=0
            for wanted in "${KEEP_PLUGINS[@]}"; do
                [[ "${name}" == "${wanted}" ]] && keep=1 && break
            done
            [[ "${keep}" == "0" ]] && rm -rf "${dir}"
        done
    fi

    # Stub files and the developer tools shipped alongside the runtime.
    find "${PYSIDE}" -maxdepth 1 -name '*.pyi' -delete 2>/dev/null || true
    rm -f "${PYSIDE}"/{assistant,designer,linguist,lrelease,lupdate,qmllint,qmlformat,qmlls,qsb,balsam,shiboken6} 2>/dev/null || true
    rm -f "${APPDIR}/usr/python/bin/pyside6-"* 2>/dev/null || true

    log "Pruning the interpreter"
    PYLIB="${APPDIR}/usr/python/lib/python${PYTHON_VERSION}"
    rm -rf "${PYLIB}/test" "${PYLIB}/idlelib" "${PYLIB}/tkinter" \
           "${PYLIB}/lib2to3" "${PYLIB}/ensurepip" "${PYLIB}/turtledemo" \
           "${PYLIB}/pydoc_data" "${PYLIB}/site-packages/pip" \
           "${PYLIB}/site-packages/pip-"*".dist-info" \
           "${APPDIR}/usr/python/include" \
           "${APPDIR}/usr/python/share" \
           2>/dev/null || true
    # Tcl/Tk ships with the standalone build and nothing here uses it.
    rm -rf "${APPDIR}/usr/python/lib"/{tcl*,tk*,itcl*,thread*,libtcl*,libtk*} 2>/dev/null || true
    rm -f  "${PYLIB}/lib-dynload/_tkinter"*.so 2>/dev/null || true
    rm -f  "${APPDIR}/usr/python/bin/"{idle*,2to3*,pydoc*,pip*} 2>/dev/null || true

    find "${APPDIR}/usr/python" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "${APPDIR}" -name '*.a' -delete 2>/dev/null || true

    # The interpreter arrives already stripped (PBS_FLAVOUR). Only the Qt
    # libraries pip installed still carry debug info worth removing, and those
    # are ordinary shared objects that strip handles correctly.
    if command -v strip >/dev/null; then
        log "Stripping debug symbols from the bundled libraries"
        before="$(du -sm "${APPDIR}" | cut -f1)"
        find "${PYSIDE}" \( -name '*.so' -o -name '*.so.*' \) -type f -print0 \
            2>/dev/null | xargs -0 -r strip --strip-unneeded 2>/dev/null || true
        after="$(du -sm "${APPDIR}" | cut -f1)"
        echo "    ${before} MB -> ${after} MB"
    fi
fi

# ---------------------------------------------------------------------------
# 3b. Bundle the X11 libraries distributions keep forgetting
# ---------------------------------------------------------------------------
# Qt 6.5+ made libxcb-cursor a hard requirement of the xcb platform plugin, and
# Debian, Ubuntu and their derivatives split it into a package that is not
# installed by default. Without it Qt aborts with "could not load the Qt
# platform plugin xcb" before a single window appears - which would take out
# XFCE and MATE, both overwhelmingly X11.
#
# These are pulled from Debian stable rather than the build host, so the result
# does not depend on what happens to be installed here.
bundle_x11_libs() {
    local libdir="${APPDIR}/usr/lib"
    mkdir -p "${libdir}"

    case "${AI_ARCH}" in
        x86_64)  local deb_arch="amd64" ;;
        aarch64) local deb_arch="arm64" ;;
        *) return 0 ;;
    esac

    if ! command -v ar >/dev/null; then
        warn "'ar' not found - skipping the libxcb-cursor bundle."
        warn "The AppImage may fail to start on X11 systems without it."
        return 0
    fi

    local base="http://deb.debian.org/debian/pool/main"
    # package-directory : binary-package-name
    local packages=(
        "x/xcb-util-cursor:libxcb-cursor0"
        "x/xcb-util-image:libxcb-image0"
        "x/xcb-util-renderutil:libxcb-render-util0"
        "x/xcb-util:libxcb-util1"
        "x/xcb-util-keysyms:libxcb-keysyms1"
        "x/xcb-util-wm:libxcb-icccm4"
    )

    local tmp
    tmp="$(mktemp -d)"
    local bundled=0
    for entry in "${packages[@]}"; do
        local dir="${entry%%:*}"
        local pkg="${entry##*:}"

        # Resolve the newest version from the pool index rather than pinning a
        # version string that goes stale on the next Debian point release.
        local file
        file="$(curl -fsSL --max-time 30 "${base}/${dir}/" 2>/dev/null \
                | grep -o "${pkg}_[^\"]*_${deb_arch}\.deb" \
                | sort -uV | tail -1)"
        if [[ -z "${file}" ]]; then
            warn "could not resolve a ${pkg} package - continuing without it"
            continue
        fi

        local cached="${CACHE_DIR}/${file}"
        if [[ ! -f "${cached}" ]]; then
            if ! curl -fsSL --max-time 45 -o "${cached}.part" \
                 "${base}/${dir}/${file}" 2>/dev/null; then
                rm -f "${cached}.part"
                warn "could not fetch ${file} - continuing without it"
                continue
            fi
            mv "${cached}.part" "${cached}"
        fi

        ( cd "${tmp}" && ar x "${cached}" && tar -xf data.tar.* 2>/dev/null ) \
            || continue
        bundled=$((bundled + 1))
    done

    # Copy regular files *and* symlinks: Debian ships the real object as
    # libxcb-cursor.so.0.0.0 with libxcb-cursor.so.0 as a symlink beside it,
    # and the SONAME the loader asks for is the symlink.
    find "${tmp}" -name 'libxcb-*.so.*' \( -type f -o -type l \) \
        -exec cp -Pf {} "${libdir}/" \; 2>/dev/null || true
    rm -rf "${tmp}"

    if [[ ! -e "${libdir}/libxcb-cursor.so.0" ]]; then
        warn "libxcb-cursor.so.0 is NOT in the bundle."
        warn "X11 sessions will need the distribution's libxcb-cursor0 package."
    fi
    echo "    bundled $(find "${libdir}" -name 'libxcb-*' | wc -l) xcb libraries from ${bundled} packages"
}

log "Bundling the X11 support libraries"
bundle_x11_libs

# ---------------------------------------------------------------------------
# 4. Verify before packing
# ---------------------------------------------------------------------------
log "Checking the bundle imports cleanly"
PYTHONPATH="${APPDIR}/usr/app" QT_QPA_PLATFORM=offscreen \
    "${PY}" -s -c "
import shotpad
from shotpad import app, model, render, annotations, icons, theme
from shotpad.capture import backends, portal
from PySide6 import QtWidgets, QtDBus, QtSvg
print('  package', shotpad.__version__, 'imports OK')
" || die "The bundled app does not import - check the pruning list."

PYTHONPATH="${APPDIR}/usr/app" QT_QPA_PLATFORM=offscreen \
    "${PY}" -s -m shotpad --version >/dev/null || die "shotpad --version failed inside the bundle."

# A bundled engine that cannot read is worse than no engine: ocr.available()
# would report true and every grab would come back empty. Prove it end to end
# through the same code path the app uses, with APPDIR set as AppRun sets it.
if [[ -x "${APPDIR}/usr/bin/tesseract" ]]; then
    log "Checking text recognition works inside the bundle"
    APPDIR="${APPDIR}" PYTHONPATH="${APPDIR}/usr/app" QT_QPA_PLATFORM=offscreen \
        "${PY}" -s -c "
import sys
from PySide6.QtGui import QColor, QImage, QPainter, QFont
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from shotpad import ocr
if not ocr.available():
    sys.exit('ocr.available() is false with the engine bundled')
image = QImage(420, 90, QImage.Format.Format_ARGB32_Premultiplied)
image.fill(QColor('white'))
painter = QPainter(image)
painter.setPen(QColor('black'))
painter.setFont(QFont('Sans Serif', 32))
painter.drawText(image.rect(), 0x84, 'Shotpad')
painter.end()
result = ocr.recognise(image)
if 'Shotpad' not in result.text:
    sys.exit('the bundled engine read %r instead of Shotpad' % result.text)
print('  read back %r from a rendered sample; languages: %s'
      % (result.text.strip(), ','.join(ocr.languages())))
" || die "the bundled OCR engine does not work."
fi

# Importing under the offscreen platform proves nothing about X11 or Wayland:
# those plugins are dlopened only on a real session, so a missing dependency
# would first surface on a user's machine as "could not load the Qt platform
# plugin". Check them here instead.
if command -v ldd >/dev/null; then
    log "Checking the platform plugins resolve their libraries"
    QT_LIB="${PYSIDE}/Qt/lib"
    export LD_LIBRARY_PATH="${APPDIR}/usr/python/lib:${QT_LIB}:${APPDIR}/usr/lib"
    plugin_problems=0
    for plugin in libqxcb.so libqwayland-generic.so libqwayland-egl.so; do
        path="${PYSIDE}/Qt/plugins/platforms/${plugin}"
        [[ -f "${path}" ]] || continue
        missing="$(ldd "${path}" 2>/dev/null | grep 'not found' || true)"
        if [[ -n "${missing}" ]]; then
            warn "${plugin} is missing libraries:"
            echo "${missing}" | sed 's/^/      /' >&2
            plugin_problems=$((plugin_problems + 1))
        else
            echo "    ${plugin}  ok"
        fi
    done
    unset LD_LIBRARY_PATH
    if [[ "${plugin_problems}" -gt 0 ]]; then
        warn "The AppImage will rely on the host providing the libraries above."
    fi
fi

# ---------------------------------------------------------------------------
# 5. Desktop integration files
# ---------------------------------------------------------------------------
log "Adding the desktop entry, icon and AppStream metadata"
install -m 644 "${PACKAGING}/${APP_ID}.desktop" \
    "${APPDIR}/usr/share/applications/${APP_ID}.desktop"
install -m 644 "${PACKAGING}/${APP_ID}.svg" \
    "${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
# The metainfo lives inside the package so that pip installs carry it too.
install -m 644 "${REPO_ROOT}/shotpad/data/${APP_ID}.metainfo.xml" \
    "${APPDIR}/usr/share/metainfo/${APP_ID}.metainfo.xml"

# ---------------------------------------------------------------------------
# 5b. Licences
# ---------------------------------------------------------------------------
# The bundle ships Shotpad (GPL-3.0), Qt via PySide6 (LGPL-3.0) and CPython.
# Both the GPL and the LGPL require the licence text to travel with the
# binaries, and the PySide6 wheels do not carry one - so supply all of them
# here. This runs after the pruning step on purpose: nothing below is a
# candidate for trimming.
log "Adding the licence texts"
LICENSE_DIR="${APPDIR}/usr/share/licenses"
mkdir -p "${LICENSE_DIR}"
install -m 644 "${PACKAGING}/licenses/README.txt"   "${LICENSE_DIR}/README.txt"
install -m 644 "${PACKAGING}/licenses/GPL-3.0.txt"  "${LICENSE_DIR}/GPL-3.0.txt"
install -m 644 "${PACKAGING}/licenses/LGPL-3.0.txt" "${LICENSE_DIR}/LGPL-3.0.txt"

# CPython's own licence arrives with the interpreter; copy it alongside the
# rest so a user finds every licence in one place.
PY_LICENSE="${APPDIR}/usr/python/lib/python${PYTHON_VERSION}/LICENSE.txt"
if [[ -f "${PY_LICENSE}" ]]; then
    install -m 644 "${PY_LICENSE}" "${LICENSE_DIR}/Python-LICENSE.txt"
else
    warn "CPython's LICENSE.txt was not found - the bundle will ship without it."
fi

# Shotpad's own licence, at the AppDir root where it is impossible to miss.
install -m 644 "${REPO_ROOT}/LICENSE" "${APPDIR}/LICENSE"

# The GPL text must actually be the licence Shotpad is released under; a
# mismatch here would be a compliance bug, not a cosmetic one.
if ! cmp -s "${REPO_ROOT}/LICENSE" "${PACKAGING}/licenses/GPL-3.0.txt"; then
    die "packaging/licenses/GPL-3.0.txt differs from the project's LICENSE."
fi

# appimagetool wants these at the AppDir root as well.
cp "${APPDIR}/usr/share/applications/${APP_ID}.desktop" "${APPDIR}/${APP_ID}.desktop"
cp "${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg" "${APPDIR}/${APP_ID}.svg"
ln -sf "${APP_ID}.svg" "${APPDIR}/.DirIcon"

install -m 755 "${PACKAGING}/AppRun" "${APPDIR}/AppRun"

# A `shotpad` shim so the .desktop Exec line resolves inside the bundle too.
mkdir -p "${APPDIR}/usr/bin"
cat > "${APPDIR}/usr/bin/shotpad" <<'SHIM'
#!/bin/bash
exec "$(dirname "$(readlink -f "$0")")/../../AppRun" "$@"
SHIM
chmod +x "${APPDIR}/usr/bin/shotpad"

# ---------------------------------------------------------------------------
# 6. Pack
# ---------------------------------------------------------------------------
APPIMAGETOOL="${CACHE_DIR}/appimagetool-${AI_ARCH}.AppImage"
if [[ ! -x "${APPIMAGETOOL}" ]]; then
    log "Downloading appimagetool"
    curl -fL --retry 3 --progress-bar -o "${APPIMAGETOOL}" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${AI_ARCH}.AppImage" \
        || die "Could not download appimagetool."
    chmod +x "${APPIMAGETOOL}"
fi

OUTPUT="${OUT_DIR}/Shotpad-${VERSION}-${AI_ARCH}.AppImage"
rm -f "${OUTPUT}"

log "Packing ${OUTPUT##*/}"
# --appimage-extract-and-run avoids needing FUSE on the build machine.
ARCH="${AI_ARCH}" "${APPIMAGETOOL}" --appimage-extract-and-run \
    --no-appstream "${APPDIR}" "${OUTPUT}" \
    || die "appimagetool failed."

chmod +x "${OUTPUT}"

# Checksum, so a download can be verified. Written with a bare filename rather
# than the full path, which is what `sha256sum -c` expects to find next to it.
(cd "${OUT_DIR}" && sha256sum "${OUTPUT##*/}" > "${OUTPUT##*/}.sha256") \
    || die "Could not write the checksum."

log "Done: ${OUTPUT}  ($(du -h "${OUTPUT}" | cut -f1))"
echo
echo "  Try it:      ${OUTPUT} --list-backends"
echo "  Install it:  ${OUTPUT} --install   (adds a menu entry for the current user)"
echo "  Checksum:    ${OUTPUT}.sha256"
echo
echo "  Release it:  gh release create v${VERSION} --title \"Shotpad ${VERSION}\" \\"
echo "                 ${OUTPUT} ${OUTPUT}.sha256"

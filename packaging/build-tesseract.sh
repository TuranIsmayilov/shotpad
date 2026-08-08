#!/usr/bin/env bash
#
# Build the static Tesseract that Shotpad's "Grab text" tool shells out to.
#
# Why from source rather than a distribution package: Debian's libtesseract
# lists libcurl and libarchive as hard DT_NEEDED entries, and following that
# closure drags in OpenSSL, Kerberos and libxml2 - around 23 MB of libraries
# and a standing CVE obligation, for URL and archive input a screenshot tool
# never reaches. Configured without them, and with a Leptonica that has every
# image-format backend switched off, the whole thing is one binary.
#
# Leptonica keeps its PNM reader with no external library, and Qt writes PPM,
# so that is the format shotpad/ocr.py hands over.
#
# Linking -static is what protects the AppImage's reach. A dynamic build made
# on a current distribution demands GLIBC 2.38 (fmod@GLIBC_2.38 and friends)
# where the bundled Qt asks only for 2.28, which would have quietly dropped
# Debian 12, Ubuntu 22.04 and RHEL 9. A static binary has no glibc floor at
# all, so this can be built anywhere.
#
# Usage:  ./packaging/build-tesseract.sh [--out FILE]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${REPO_ROOT}/build/cache"
WORK="${CACHE_DIR}/tesseract-build"

LEPT_VER=1.85.0
TESS_VER=5.5.0
OUT="${CACHE_DIR}/tesseract-${TESS_VER}-static-$(uname -m)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

for tool in cmake curl tar; do
    command -v "${tool}" >/dev/null || die "${tool} is required to build Tesseract."
done
command -v c++ >/dev/null || command -v g++ >/dev/null \
    || die "a C++ compiler is required to build Tesseract."

if [[ -x "${OUT}" ]]; then
    log "Reusing the cached binary: ${OUT##*/}"
    exit 0
fi

mkdir -p "${WORK}"
cd "${WORK}"

log "Fetching Leptonica ${LEPT_VER} and Tesseract ${TESS_VER}"
[[ -f lept.tar.gz ]] || curl -fsSL --retry 3 --max-time 300 -o lept.tar.gz \
    "https://github.com/DanBloomberg/leptonica/releases/download/${LEPT_VER}/leptonica-${LEPT_VER}.tar.gz"
[[ -f tess.tar.gz ]] || curl -fsSL --retry 3 --max-time 300 -o tess.tar.gz \
    "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/${TESS_VER}.tar.gz"
[[ -d "leptonica-${LEPT_VER}" ]] || tar -xzf lept.tar.gz
[[ -d "tesseract-${TESS_VER}" ]] || tar -xzf tess.tar.gz

log "Building Leptonica with no image-format backends"
cmake -S "leptonica-${LEPT_VER}" -B b-lept \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${WORK}/prefix" \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_PROG=OFF \
    -DENABLE_ZLIB=OFF -DENABLE_PNG=OFF -DENABLE_JPEG=OFF \
    -DENABLE_TIFF=OFF -DENABLE_WEBP=OFF -DENABLE_OPENJPEG=OFF -DENABLE_GIF=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON >/dev/null
cmake --build b-lept -j"$(nproc)" >/dev/null
cmake --install b-lept >/dev/null

log "Building Tesseract without curl, archive, OpenMP or the training tools"
cmake -S "tesseract-${TESS_VER}" -B b-tess \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${WORK}/prefix" \
    -DCMAKE_PREFIX_PATH="${WORK}/prefix" \
    -DLeptonica_DIR="${WORK}/prefix/lib/cmake/leptonica" \
    -DDISABLE_CURL=ON -DDISABLE_ARCHIVE=ON -DDISABLE_TIFF=ON \
    -DBUILD_TRAINING_TOOLS=OFF -DBUILD_TESTS=OFF -DGRAPHICS_DISABLED=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_OpenMP=ON -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_EXE_LINKER_FLAGS="-static" >/dev/null
cmake --build b-tess -j"$(nproc)" --target tesseract >/dev/null

BUILT="${WORK}/b-tess/bin/tesseract"
[[ -x "${BUILT}" ]] || die "the build produced no tesseract binary."

cp "${BUILT}" "${OUT}.part"
strip --strip-unneeded "${OUT}.part" 2>/dev/null || true

# The point of the exercise: if this has dynamic dependencies, the -static
# link silently failed and the bundle would inherit a glibc floor.
if readelf -d "${OUT}.part" 2>/dev/null | grep -q NEEDED; then
    rm -f "${OUT}.part"
    die "the binary is not static - it would tie the AppImage to this host's glibc."
fi

mv "${OUT}.part" "${OUT}"
chmod +x "${OUT}"
log "Done: ${OUT##*/}  ($(du -h "${OUT}" | cut -f1), no dynamic dependencies)"

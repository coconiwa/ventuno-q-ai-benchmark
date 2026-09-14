#!/bin/sh
set -eu

BASE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
RUNTIME="$BASE/runtime"
GENIEX_ROOT="$RUNTIME/geniex"
DATA_DIR="$RUNTIME/geniex-data"
MODEL_DIR="$DATA_DIR/models/Qwen/Qwen3-VL-2B-Instruct"
STAGE="$RUNTIME/model-download"
OPENCL_ROOT="$RUNTIME/opencl"

GENIEX_VERSION=0.6.1
GENIEX_ARCHIVE="geniex-cli-linux-arm64-v${GENIEX_VERSION}.tar.gz"
GENIEX_URL="https://github.com/qualcomm/GenieX/releases/download/v${GENIEX_VERSION}/${GENIEX_ARCHIVE}"
GENIEX_SHA256="985c13348990b5c18fed9434ba2ab267aa5a9bb6c9e88bcfa1de94429ca6767a"

MODEL_REV="e84f8ae7ffee8b04793a4ed771609e2b61d3f3cf"
MODEL_FILE="Qwen3-VL-2B-Instruct-Q4_0.gguf"
MODEL_URL="https://huggingface.co/bartowski/Qwen_Qwen3-VL-2B-Instruct-GGUF/resolve/${MODEL_REV}/Qwen_Qwen3-VL-2B-Instruct-Q4_0.gguf"
MODEL_SHA256="010372ad66abfcc876ac882f8b659a36ceae1a349f6a0a76b747c7939846cf95"

MMPROJ_REV="52d6c8ffea26cc873ac5ad116f8631268d7eb503"
MMPROJ_FILE="mmproj-Qwen3VL-2B-Instruct-F16.gguf"
MMPROJ_URL="https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/${MMPROJ_REV}/${MMPROJ_FILE}"
MMPROJ_SHA256="c3d5afbef5287953acd57b4043d2269456e5761a4eaccb3b71b062996970aea5"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

say "Check supported software environment"
"$BASE/scripts/check-environment.sh"

available_kb=$(df -Pk "$BASE" | awk 'NR==2 {print $4}')
required_kb=5000000
if printf '%s  %s\n' "$MODEL_SHA256" "$MODEL_DIR/$MODEL_FILE" | sha256sum -c - >/dev/null 2>&1 && \
   printf '%s  %s\n' "$MMPROJ_SHA256" "$MODEL_DIR/$MMPROJ_FILE" | sha256sum -c - >/dev/null 2>&1; then
  required_kb=500000
fi
test "${available_kb:-0}" -ge "$required_kb" || fail "insufficient free storage (5 GB for first install; 500 MB when the model is already verified)"

mkdir -p "$RUNTIME" "$DATA_DIR" "$STAGE" "$OPENCL_ROOT"

if test -x "$BASE/scripts/control.sh"; then
  "$BASE/scripts/control.sh" stop >/dev/null 2>&1 || true
fi

download() {
  url="$1"
  destination="$2"
  expected="$3"
  if test -s "$destination" && printf '%s  %s\n' "$expected" "$destination" | sha256sum -c - >/dev/null 2>&1; then
    printf 'verified: %s\n' "$(basename "$destination")"
    return
  fi
  if ! curl -fL --retry 5 --retry-delay 2 --continue-at - -o "$destination" "$url" || \
     ! printf '%s  %s\n' "$expected" "$destination" | sha256sum -c -; then
    rm -f "$destination"
    curl -fL --retry 5 --retry-delay 2 -o "$destination" "$url"
    printf '%s  %s\n' "$expected" "$destination" | sha256sum -c -
  fi
}

say "Download Qualcomm GenieX ${GENIEX_VERSION} from the official upstream release"
if test -x "$GENIEX_ROOT/geniex" && test "$(cat "$RUNTIME/geniex-version" 2>/dev/null || true)" = "$GENIEX_VERSION"; then
  printf 'installed: GenieX %s\n' "$GENIEX_VERSION"
else
  download "$GENIEX_URL" "$RUNTIME/$GENIEX_ARCHIVE" "$GENIEX_SHA256"
  rm -rf "$GENIEX_ROOT.new"
  mkdir -p "$GENIEX_ROOT.new"
  tar -xzf "$RUNTIME/$GENIEX_ARCHIVE" -C "$GENIEX_ROOT.new" --strip-components=1
  rm -rf "$GENIEX_ROOT"
  mv "$GENIEX_ROOT.new" "$GENIEX_ROOT"
  rm -f "$RUNTIME/$GENIEX_ARCHIVE"
  printf '%s\n' "$GENIEX_VERSION" >"$RUNTIME/geniex-version"
fi

say "Install user-local OpenCL loader required by GenieX"
if ! find "$OPENCL_ROOT" -name 'libOpenCL.so.1' -print -quit | grep -q .; then
  package_dir="$RUNTIME/opencl-package"
  rm -rf "$package_dir"
  mkdir -p "$package_dir"
  (cd "$package_dir" && apt-get download ocl-icd-libopencl1)
  package=$(find "$package_dir" -maxdepth 1 -name 'ocl-icd-libopencl1_*.deb' -print -quit)
  test -n "$package" || fail "could not download ocl-icd-libopencl1"
  dpkg-deb -x "$package" "$OPENCL_ROOT"
  rm -rf "$package_dir"
fi

rpc_library=$(ldconfig -p 2>/dev/null | awk '/libcdsprpc[.]so[.]1 / {print $NF; exit}')
test -n "$rpc_library" || fail "libcdsprpc.so.1 is missing; install the VENTUNO Q system image/runtime first"
ln -sfn "$rpc_library" "$GENIEX_ROOT/libcdsprpc.so"

say "Create isolated Python environment"
python3 -m venv "$BASE/.venv"
"$BASE/.venv/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
"$BASE/.venv/bin/python" -m pip install --disable-pip-version-check --quiet 'Pillow==10.4.0'

say "Register model with GenieX"
opencl_lib=$(find "$OPENCL_ROOT/usr/lib" -type f -name 'libOpenCL.so.1.*' -printf '%h\n' -quit)
test -n "$opencl_lib" || fail "OpenCL loader installation failed"
export LD_LIBRARY_PATH="$GENIEX_ROOT:$GENIEX_ROOT/llama_cpp:$opencl_lib"
export ADSP_LIBRARY_PATH="$GENIEX_ROOT/llama_cpp;/usr/lib/rfsa/adsp;/dsp"
if ! printf '%s  %s\n' "$MODEL_SHA256" "$MODEL_DIR/$MODEL_FILE" | sha256sum -c - >/dev/null 2>&1 || \
   ! printf '%s  %s\n' "$MMPROJ_SHA256" "$MODEL_DIR/$MMPROJ_FILE" | sha256sum -c - >/dev/null 2>&1; then
  say "Download Qwen3-VL 2B Q4_0 and vision projector"
  download "$MODEL_URL" "$STAGE/$MODEL_FILE" "$MODEL_SHA256"
  download "$MMPROJ_URL" "$STAGE/$MMPROJ_FILE" "$MMPROJ_SHA256"
  rm -rf "$MODEL_DIR"
  "$GENIEX_ROOT/geniex" --skip-update --data-dir "$DATA_DIR" pull \
    Qwen/Qwen3-VL-2B-Instruct --model-hub localfs --local-path "$STAGE" --model-type vlm
fi
printf '%s  %s\n' "$MODEL_SHA256" "$MODEL_DIR/$MODEL_FILE" | sha256sum -c -
printf '%s  %s\n' "$MMPROJ_SHA256" "$MODEL_DIR/$MMPROJ_FILE" | sha256sum -c -
rm -rf "$STAGE"

say "Create offline sample image"
"$BASE/.venv/bin/python" - "$BASE/sample.jpg" <<'PY'
import sys
from PIL import Image, ImageDraw

image = Image.new("RGB", (640, 480), "#e9eee9")
draw = ImageDraw.Draw(image)
draw.rectangle((95, 105, 295, 305), fill="#159570")
draw.ellipse((360, 105, 560, 305), fill="#f0a83a")
draw.text((95, 355), "VENTUNO Q  CPU / NPU", fill="#172019")
image.save(sys.argv[1], "JPEG", quality=90)
PY

chmod +x "$BASE/install.sh" "$BASE/scripts/control.sh"

say "Installation complete"
printf 'Start:  ./scripts/control.sh start\n'
printf 'Open:   http://<VENTUNO-Q-IP>:8096/\n'
printf 'Stop:   ./scripts/control.sh stop\n'

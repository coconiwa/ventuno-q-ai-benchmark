#!/bin/sh
set -u

failures=0
warnings=0

pass() { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf '  [FAIL] %s\n' "$*"; failures=$((failures + 1)); }

printf 'VENTUNO Q benchmark environment check\n\n'

arch=$(uname -m 2>/dev/null || true)
if test "$arch" = aarch64; then
  pass "Architecture: aarch64"
else
  fail "Architecture: ${arch:-unknown} (aarch64 required)"
fi

os_id=""
os_version=""
if test -r /etc/os-release; then
  os_id=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"' | sed -n '1p')
  os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"' | sed -n '1p')
fi
if test "$os_id" = ubuntu && test "$os_version" = 24.04; then
  pass "Operating system: Ubuntu 24.04 LTS"
else
  fail "Operating system: ${os_id:-unknown} ${os_version:-unknown} (official VENTUNO Q Ubuntu 24.04 image required)"
fi

if command -v python3 >/dev/null 2>&1; then
  python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || true)
  if python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
    pass "Python: ${python_version}"
  else
    fail "Python: ${python_version:-unknown} (Python 3.12 from the official Ubuntu 24.04 image required)"
  fi
else
  fail "Python: python3 not found"
fi

if command -v python3 >/dev/null 2>&1 && \
   python3 -c 'import venv' >/dev/null 2>&1 && \
   python3 -m ensurepip --version >/dev/null 2>&1; then
  pass "Python virtual environment: available"
else
  fail "Python virtual environment: unavailable (install python3-venv)"
fi

for command in curl sha256sum tar apt-get dpkg-deb ss ldconfig; do
  if command -v "$command" >/dev/null 2>&1; then
    pass "Command: $command"
  else
    fail "Command: $command not found"
  fi
done

if ldconfig -p 2>/dev/null | grep -q 'libcdsprpc[.]so[.]1'; then
  pass "VENTUNO Q FastRPC runtime: libcdsprpc.so.1"
else
  fail "VENTUNO Q FastRPC runtime: libcdsprpc.so.1 not found"
fi

camera_ready=true
for command in gst-launch-1.0 gst-inspect-1.0; do
  if ! command -v "$command" >/dev/null 2>&1; then
    camera_ready=false
  fi
done
if test "$camera_ready" = true; then
  for plugin in v4l2src jpegdec jpegenc fdsink; do
    if ! gst-inspect-1.0 "$plugin" >/dev/null 2>&1; then
      camera_ready=false
    fi
  done
fi
if test "$camera_ready" = true; then
  pass "USB camera pipeline: GStreamer and required plugins available"
else
  warn "USB camera pipeline unavailable; browser image upload will still work"
  printf '         Install: sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good\n'
fi

printf '\nResult: %s failure(s), %s warning(s)\n' "$failures" "$warnings"
if test "$failures" -ne 0; then
  printf 'Use the current official VENTUNO Q Ubuntu 24.04 image, then run this check again.\n' >&2
  exit 1
fi

printf 'Environment is ready for installation.\n'

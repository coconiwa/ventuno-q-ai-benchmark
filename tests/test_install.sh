#!/bin/sh
set -eu

BASE=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
RUNTIME="$BASE/runtime"
MODEL_DIR="$RUNTIME/geniex-data/models/Qwen/Qwen3-VL-2B-Instruct"

cleanup() {
  "$BASE/scripts/control.sh" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

test -x "$RUNTIME/geniex/geniex"
test -x "$BASE/.venv/bin/python"
printf '%s  %s\n' \
  010372ad66abfcc876ac882f8b659a36ceae1a349f6a0a76b747c7939846cf95 \
  "$MODEL_DIR/Qwen3-VL-2B-Instruct-Q4_0.gguf" | sha256sum -c -
printf '%s  %s\n' \
  c3d5afbef5287953acd57b4043d2269456e5761a4eaccb3b71b062996970aea5 \
  "$MODEL_DIR/mmproj-Qwen3VL-2B-Instruct-F16.gguf" | sha256sum -c -

"$BASE/.venv/bin/python" -m unittest discover -s "$BASE/tests" -p 'test_*.py'
"$BASE/scripts/control.sh" start
curl -fsS http://127.0.0.1:8096/api/status >"$RUNTIME/e2e-status.json"
grep -q 'Found device: HTP0' "$RUNTIME/npu.log"
grep -q 'Using 1 device(s): HTP0' "$RUNTIME/npu.log"

curl -fsS --max-time 240 \
  'http://127.0.0.1:8096/api/run?source=sample&lang=en' \
  >"$RUNTIME/e2e-events.txt"
grep -q '^event: result' "$RUNTIME/e2e-events.txt"
grep -q '"backend": "cpu"' "$RUNTIME/e2e-events.txt"
grep -q '"backend": "npu"' "$RUNTIME/e2e-events.txt"
grep -q '^event: done' "$RUNTIME/e2e-events.txt"

echo "E2E PASS: installer assets, CPU, HTP0, web API, and real VLM inference"

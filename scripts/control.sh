#!/bin/sh
set -eu

BASE=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
RUNTIME="$BASE/runtime"
GENIEX_ROOT="$RUNTIME/geniex"
GENIEX="$GENIEX_ROOT/geniex"
DATA_DIR="$RUNTIME/geniex-data"
OPENCL_LIB="$RUNTIME/opencl/usr/lib/aarch64-linux-gnu"
MODEL_NAME="Qwen/Qwen3-VL-2B-Instruct"
mkdir -p "$RUNTIME"

alive() {
  test -f "$1" || return 1
  pid=$(sed -n '1p' "$1")
  kill -0 "$pid" 2>/dev/null || return 1
  command=$(tr '\000' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)
  case "$command" in
    *"$BASE/"*) return 0 ;;
    *) return 1 ;;
  esac
}

listener_pid() {
  ss -H -ltnp "sport = :$1" 2>/dev/null |
    sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' |
    sed -n '1p'
}

release_managed_port() {
  port="$1"
  listener=$(listener_pid "$port")
  test -n "$listener" || return 0
  command=$(tr '\000' ' ' <"/proc/$listener/cmdline" 2>/dev/null || true)
  case "$command" in
    *"$BASE/"*)
      echo "stopping orphaned process on port $port (pid $listener)"
      kill "$listener"
      count=0
      while kill -0 "$listener" 2>/dev/null && test "$count" -lt 20; do
        count=$((count + 1))
        sleep 1
      done
      if kill -0 "$listener" 2>/dev/null; then
        kill -9 "$listener"
      fi
      ;;
    *)
      echo "port $port is already used by an unrelated process (pid $listener)" >&2
      echo "$command" >&2
      exit 1
      ;;
  esac
}

wait_health() {
  url="$1"
  name="$2"
  count=0
  while test "$count" -lt 120; do
    if curl -fsS --max-time 3 -H 'Connection: close' "$url" >/dev/null 2>&1; then
      echo "$name ready"
      return 0
    fi
    count=$((count + 1))
    sleep 1
  done
  echo "$name failed to become ready" >&2
  return 1
}

start_backend() {
  name="$1"
  port="$2"
  compute="$3"
  pidfile="$RUNTIME/$name.pid"
  logfile="$RUNTIME/$name.log"
  if alive "$pidfile"; then
    echo "$name already running (pid $(sed -n '1p' "$pidfile"))"
    return 0
  fi
  release_managed_port "$port"
  rm -f "$pidfile"
  test -x "$GENIEX" || { echo "missing GenieX runtime; run ./install.sh" >&2; exit 1; }
  LD_LIBRARY_PATH="$GENIEX_ROOT:$GENIEX_ROOT/llama_cpp:$OPENCL_LIB" \
    ADSP_LIBRARY_PATH="$GENIEX_ROOT/llama_cpp;/usr/lib/rfsa/adsp;/dsp" \
    nohup "$GENIEX" --skip-update --data-dir "$DATA_DIR" --log info serve \
      --host "127.0.0.1:$port" --compute "$compute" --keepalive 86400 \
      >"$logfile" 2>&1 &
  echo "$!" >"$pidfile"
  wait_health "http://127.0.0.1:$port/v1/models" "$name"
  alive "$pidfile" || { echo "$name exited during startup; see $logfile" >&2; return 1; }
}

warmup_backend() {
  name="$1"
  port="$2"
  curl -fsS --max-time 180 -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply OK\"}],\"max_tokens\":1,\"enable_think\":false}" \
    "http://127.0.0.1:$port/v1/chat/completions" >/dev/null
  echo "$name model loaded"
}

start_backends() {
  start_backend cpu 18082 cpu
  warmup_backend cpu 18082
  start_backend npu 18083 npu
  warmup_backend npu 18083
}

stop_one() {
  pidfile="$1"
  if alive "$pidfile"; then
    pid=$(sed -n '1p' "$pidfile")
    kill "$pid"
    count=0
    while kill -0 "$pid" 2>/dev/null && test "$count" -lt 20; do
      count=$((count + 1))
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid"
    fi
  fi
  rm -f "$pidfile"
}

start_app() {
  pidfile="$RUNTIME/app.pid"
  if alive "$pidfile"; then
    echo "app already running (pid $(sed -n '1p' "$pidfile"))"
    return 0
  fi
  release_managed_port "${BENCH_PORT:-8096}"
  rm -f "$pidfile"
  BENCH_PORT="${BENCH_PORT:-8096}" \
    BENCH_CPU_URL="${BENCH_CPU_URL:-http://127.0.0.1:18082}" \
    BENCH_NPU_URL="${BENCH_NPU_URL:-http://127.0.0.1:18083}" \
    BENCH_SAMPLE="$BASE/sample.jpg" BENCH_RECYCLE_GB="${BENCH_RECYCLE_GB:-9.0}" \
    nohup "$BASE/.venv/bin/python" "$BASE/app.py" >"$RUNTIME/app.log" 2>&1 &
  echo "$!" >"$pidfile"
  wait_health "http://127.0.0.1:${BENCH_PORT:-8096}/api/status" app
  alive "$pidfile" || { echo "app exited during startup; see $RUNTIME/app.log" >&2; return 1; }
}

show_urls() {
  web_port="${BENCH_PORT:-8096}"
  echo
  echo "Benchmark UI:"
  echo "  VENTUNO Q browser only: http://127.0.0.1:$web_port/"
  if command -v ip >/dev/null 2>&1; then
    ip -o -4 addr show scope global 2>/dev/null | while read -r _ interface _ cidr _; do
      address=${cidr%/*}
      case "$interface" in
        docker*|br-*|veth*|virbr*) continue ;;
        tailscale*) label="Tailscale:" ;;
        *) label="Network:" ;;
      esac
      printf '  %-12s http://%s:%s/\n' "$label" "$address" "$web_port"
    done
  elif command -v hostname >/dev/null 2>&1; then
    for address in $(hostname -I 2>/dev/null || true); do
      case "$address" in
        127.*|*:*|"") ;;
        *.*) echo "  Network:     http://$address:$web_port/" ;;
      esac
    done
  fi
}

case "${1:-status}" in
  start)
    start_backends
    start_app
    show_urls
    ;;
  stop)
    stop_one "$RUNTIME/app.pid"
    stop_one "$RUNTIME/npu.pid"
    stop_one "$RUNTIME/cpu.pid"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  recycle)
    stop_one "$RUNTIME/npu.pid"
    stop_one "$RUNTIME/cpu.pid"
    start_backends
    ;;
  status)
    for name in cpu npu app; do
      if alive "$RUNTIME/$name.pid"; then
        echo "$name: running (pid $(sed -n '1p' "$RUNTIME/$name.pid"))"
      else
        echo "$name: stopped"
      fi
    done
    ;;
  logs)
    tail -n 80 "$RUNTIME"/*.log
    ;;
  url|urls)
    show_urls
    ;;
  *)
    echo "usage: $0 {start|stop|restart|recycle|status|logs|urls}" >&2
    exit 2
    ;;
esac

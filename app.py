#!/usr/bin/env python3
"""VENTUNO Q CPU vs Hexagon NPU VLM benchmark wall.

The same normalized 640x480 JPEG and generation parameters are sent to CPU and
NPU runtimes sequentially. Images remain in memory and are never written.
"""

import base64
import glob
import io
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps


BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
FONT_ASSETS = {"RobotoFlex.woff2", "RobotoMono.woff2", "NotoSansJP.woff2"}
PORT = int(os.environ.get("BENCH_PORT", "8096"))
CPU_URL = os.environ.get("BENCH_CPU_URL", "http://127.0.0.1:18082")
NPU_URL = os.environ.get("BENCH_NPU_URL", "http://127.0.0.1:18083")
SAMPLE_PATH = os.environ.get("BENCH_SAMPLE", str(BASE / "sample.jpg"))
PROMPTS = {
    "ja": os.environ.get(
        "BENCH_PROMPT_JA",
        os.environ.get(
            "BENCH_PROMPT",
            "この画像の主な被写体を、日本語で一文・40文字以内で説明してください。"
            "見えていないものは推測せず、必ず句点で終えてください。",
        ),
    ),
    "en": os.environ.get(
        "BENCH_PROMPT_EN",
        "Describe the main subject of this image in one concise English sentence. "
        "Do not infer anything that is not visible, and end with a period.",
    ),
}
MAX_TOKENS = int(os.environ.get("BENCH_MAX_TOKENS", "48"))
HISTORY_LIMIT = 20
MAX_UPLOAD = 10 * 1024 * 1024
CONTROL_SCRIPT = BASE / "scripts" / "control.sh"
RECYCLE_GB = float(os.environ.get("BENCH_RECYCLE_GB", "9.0"))

MODELS = {
    "2b": {
        "label": "Qwen3-VL 2B", "detail": "GGUF Q4_0 · GenieX 0.6.1", "mode": "SPEED",
        "backends": {
            "cpu": {"model": "Qwen/Qwen3-VL-2B-Instruct", "runtime": "geniex", "proof": "GenieX · GGUF Q4_0 · CPU only"},
            "npu": {"model": "Qwen/Qwen3-VL-2B-Instruct", "runtime": "geniex", "proof": "GenieX · GGUF Q4_0 · HTP0 offload"},
        },
    },
}

BACKENDS = {
    "cpu": {
        "label": "CPU",
        "detail": "8-core Kryo · llama.cpp CPU",
        "url": CPU_URL,
    },
    "npu": {
        "label": "HEXAGON NPU",
        "detail": "Hexagon V75 HTP0 · 40 TOPS",
        "url": NPU_URL,
    },
}

RUN_LOCK = threading.Lock()
DATA_LOCK = threading.Lock()
HISTORY = []
UPLOADED = {"jpeg": None, "name": None}
MAINTENANCE = {"recycles": 0, "last_at": None, "last_before_gb": None, "last_after_gb": None}
RUN_STATS = {"total_runs": {"ja": 0, "en": 0}}


def normalize_language(value):
    return value if value in PROMPTS else "ja"


def active_model():
    try:
        model = (BASE / "runtime" / "active-model").read_text().strip()
    except OSError:
        model = "2b"
    return model if model in MODELS else "2b"


def model_status():
    selected = active_model()
    return {
        "active": selected,
        "label": MODELS[selected]["label"],
        "options": [{"id": key, **value} for key, value in MODELS.items()],
    }


def backend_status():
    model = active_model()
    return {
        kind: {**backend, **MODELS[model]["backends"][kind]}
        for kind, backend in BACKENDS.items()
    }


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def normalize_image(raw):
    """Decode, center-crop to 4:3, and encode a valid 640x480 JPEG in RAM."""
    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, (640, 480), method=Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, "JPEG", quality=88, optimize=False)
        return output.getvalue()


def find_uvc_camera():
    for path in sorted(glob.glob("/dev/video*")):
        name_path = "/sys/class/video4linux/%s/name" % os.path.basename(path)
        try:
            name = Path(name_path).read_text().strip().lower()
        except OSError:
            continue
        if any(word in name for word in ("usb", "uvc", "logitech", "webcam", "camera")):
            return path
    return None


class Camera:
    def __init__(self):
        self.device = find_uvc_camera()
        self.jpeg = None
        self.seq = 0
        self.cv = threading.Condition()
        self.proc = None
        if self.device:
            self._start()

    @property
    def ready(self):
        return self.jpeg is not None and self.proc is not None and self.proc.poll() is None

    def _start(self):
        pipeline = [
            "gst-launch-1.0", "-q", "v4l2src", "device=" + self.device,
            "!", "image/jpeg,width=640,height=480,framerate=15/1",
            "!", "jpegdec", "!", "videoconvert", "!", "jpegenc", "quality=88",
            "!", "fdsink", "fd=1", "sync=false",
        ]
        self.proc = subprocess.Popen(
            pipeline, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while self.proc and self.proc.stdout:
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                start = buf.find(b"\xff\xd8")
                if start < 0:
                    buf = buf[-2:]
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end < 0:
                    buf = buf[start:]
                    break
                frame, buf = buf[start:end + 2], buf[end + 2:]
                with self.cv:
                    self.jpeg = frame
                    self.seq += 1
                    self.cv.notify_all()

    def snapshot(self):
        with self.cv:
            return self.jpeg

    def wait_frame(self, last_seq, timeout=3):
        with self.cv:
            if self.seq == last_seq:
                self.cv.wait(timeout)
            return self.jpeg, self.seq


CAMERA = Camera()


def http_json(url, data=None, timeout=5):
    body = None if data is None else json.dumps(data).encode()
    headers = {"Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def backend_ready(url):
    try:
        http_json(url + "/v1/models", timeout=2)
        return True
    except Exception:
        return False


def run_inference(kind, jpeg, model, language="ja", on_token=None):
    backend = BACKENDS[kind]
    profile = MODELS[model]["backends"][kind]
    image_data = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    payload = {
        "model": profile["model"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "enable_think": False,
        "temperature": 0,
        "seed": 42,
        "max_tokens": MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data}},
                {"type": "text", "text": PROMPTS[normalize_language(language)]},
            ],
        }],
    }
    if profile["runtime"] == "llama":
        payload["cache_prompt"] = False
    request = urllib.request.Request(
        backend["url"] + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first = None
    parts = []
    timings = {}
    usage = {}
    with urllib.request.urlopen(request, timeout=180) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            choices = chunk.get("choices") or []
            token = choices[0].get("delta", {}).get("content") if choices else None
            if token:
                if first is None:
                    first = time.perf_counter()
                parts.append(token)
                if on_token:
                    on_token(token)
            if chunk.get("timings"):
                timings = chunk["timings"]
            if chunk.get("usage"):
                usage = chunk["usage"]
    finished = time.perf_counter()
    ttft_s = (first or finished) - started
    decode_s = max(finished - (first or finished), 0)
    tokens = int(timings.get("predicted_n") or usage.get("completion_tokens") or 0)
    prompt_tokens = int(timings.get("prompt_n") or usage.get("prompt_tokens") or 0)
    if not timings:
        # GenieX exposes usage but not llama.cpp's internal phase timings.
        # Keep the technical breakdown useful and honest: PREFILL is the
        # client-observed time to first token, and DECODE is derived from the
        # streamed completion token count and duration.
        prefill_s = ttft_s
        prefill_tps = prompt_tokens / ttft_s if ttft_s and prompt_tokens else 0
        decode_tps = tokens / decode_s if decode_s and tokens else 0
        metric_source = "client-observed (prefill=TTFT)"
    else:
        prefill_s = float(timings.get("prompt_ms", 0)) / 1000
        prefill_tps = float(timings.get("prompt_per_second", 0))
        decode_tps = float(timings.get("predicted_per_second", 0))
        metric_source = "llama.cpp timings"
    return {
        "backend": kind,
        "answer": "".join(parts).strip(),
        "total_s": round(finished - started, 3),
        "ttft_s": round(ttft_s, 3),
        "prefill_s": round(prefill_s, 3),
        "prefill_tps": round(prefill_tps, 1),
        "decode_tps": round(decode_tps, 1),
        "tokens": tokens,
        "prompt_tokens": prompt_tokens,
        "metric_source": metric_source,
    }


def read_board_status():
    total = available = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) / 1048576
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1]) / 1048576
    except OSError:
        pass
    temps = []
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            temps.append(int(Path(zone, "temp").read_text().strip()) / 1000)
        except (OSError, ValueError):
            pass
    return {
        "memory_used_gb": round(total - available, 1) if total and available else None,
        "memory_total_gb": round(total, 1) if total else None,
        "temperature_c": round(max(temps), 1) if temps else None,
        "camera": CAMERA.ready,
        "camera_device": CAMERA.device,
        "backends": {key: backend_ready(value["url"]) for key, value in BACKENDS.items()},
    }


def recycle_required(board):
    used = board.get("memory_used_gb")
    threshold = recycle_threshold()
    return threshold > 0 and used is not None and used >= threshold


def recycle_threshold():
    return RECYCLE_GB


def recycle_backends(before):
    result = subprocess.run(
        [str(CONTROL_SCRIPT), "recycle", active_model()],
        text=True, capture_output=True, timeout=180,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "バックエンドを再起動できません").strip())
    after = read_board_status()
    with DATA_LOCK:
        MAINTENANCE["recycles"] += 1
        MAINTENANCE["last_at"] = int(time.time())
        MAINTENANCE["last_before_gb"] = before.get("memory_used_gb")
        MAINTENANCE["last_after_gb"] = after.get("memory_used_gb")
        state = dict(MAINTENANCE)
    return {"threshold_gb": recycle_threshold(), "before": before, "after": after, **state}


def history_summary(language="ja"):
    language = normalize_language(language)
    with DATA_LOCK:
        rows = [row for row in HISTORY if row.get("language", "ja") == language]
        total_runs = RUN_STATS["total_runs"][language]
    summary = {"language": language, "runs": total_runs, "window_runs": len(rows), "cpu": {}, "npu": {}}
    for kind in ("cpu", "npu"):
        for metric in ("total_s", "prefill_s", "decode_tps"):
            values = [row[kind][metric] for row in rows if row.get(kind)]
            summary[kind][metric] = {
                "p50": round(percentile(values, 0.50), 2) if values else None,
                "p95": round(percentile(values, 0.95), 2) if values else None,
            }
    return summary


def choose_image(source):
    if source == "upload":
        with DATA_LOCK:
            if UPLOADED["jpeg"]:
                return UPLOADED["jpeg"], UPLOADED["name"] or "uploaded image"
        raise RuntimeError("画像がアップロードされていません")
    if source == "sample":
        raw = Path(SAMPLE_PATH).read_bytes()
        return normalize_image(raw), "sample image"
    frame = CAMERA.snapshot()
    if not frame:
        raise RuntimeError("USBカメラが見つかりません。画像をアップロードしてください")
    return normalize_image(frame), "live camera"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send_bytes(self, code, ctype, body, extra=None, cache_control="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("Connection", "close")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def send_json(self, code, payload):
        self.send_bytes(code, "application/json; charset=utf-8", json.dumps(payload).encode())

    def event(self, name, payload):
        block = "event: %s\ndata: %s\n\n" % (name, json.dumps(payload, ensure_ascii=False))
        self.wfile.write(block.encode())
        self.wfile.flush()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self.send_bytes(200, "text/html; charset=utf-8", (STATIC / "index.html").read_bytes())
        if path == "/style.css":
            return self.send_bytes(200, "text/css; charset=utf-8", (STATIC / "style.css").read_bytes())
        if path == "/app.js":
            return self.send_bytes(200, "text/javascript; charset=utf-8", (STATIC / "app.js").read_bytes())
        if path.startswith("/fonts/"):
            name = path.removeprefix("/fonts/")
            if name in FONT_ASSETS:
                return self.send_bytes(
                    200,
                    "font/woff2",
                    (STATIC / "fonts" / name).read_bytes(),
                    cache_control="public, max-age=31536000, immutable",
                )
        if path == "/api/status":
            query = urllib.parse.parse_qs(parsed.query)
            language = normalize_language(query.get("lang", ["ja"])[0])
            with DATA_LOCK:
                maintenance = {"threshold_gb": recycle_threshold(), **MAINTENANCE}
            return self.send_json(200, {"board": read_board_status(), "history": history_summary(language), "backends": backend_status(), "model": model_status(), "maintenance": maintenance})
        if path == "/api/frame.jpg":
            frame = CAMERA.snapshot()
            if not frame:
                return self.send_json(404, {"error": "camera unavailable"})
            return self.send_bytes(200, "image/jpeg", frame)
        if path == "/api/stream":
            return self.stream_camera()
        if path == "/api/run":
            query = urllib.parse.parse_qs(parsed.query)
            return self.run_benchmark(
                query.get("source", ["camera"])[0],
                normalize_language(query.get("lang", ["ja"])[0]),
            )
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/model":
            return self.switch_model()
        if self.path != "/api/upload":
            return self.send_json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                raise ValueError("画像は10MB以下にしてください")
            payload = json.loads(self.rfile.read(length))
            encoded = payload.get("data", "").split(",", 1)[-1]
            jpeg = normalize_image(base64.b64decode(encoded, validate=True))
            with DATA_LOCK:
                UPLOADED["jpeg"] = jpeg
                UPLOADED["name"] = str(payload.get("name", "uploaded image"))[:100]
            self.send_json(200, {"ok": True, "bytes": len(jpeg), "width": 640, "height": 480})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def switch_model(self):
        if not RUN_LOCK.acquire(blocking=False):
            return self.send_json(409, {"error": "計測中はモデルを変更できません"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            requested = str(payload.get("model", ""))
            if requested not in MODELS:
                return self.send_json(400, {"error": "不明なモデルです"})
            if requested != active_model():
                result = subprocess.run(
                    [str(CONTROL_SCRIPT), "switch", requested],
                    text=True, capture_output=True, timeout=180,
                )
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout or "モデルを起動できません").strip())
                with DATA_LOCK:
                    HISTORY.clear()
                    RUN_STATS["total_runs"] = {"ja": 0, "en": 0}
                    MAINTENANCE.update({"recycles": 0, "last_at": None, "last_before_gb": None, "last_after_gb": None})
            return self.send_json(200, {"ok": True, "model": model_status(), "board": read_board_status()})
        except Exception as exc:
            return self.send_json(500, {"error": str(exc)})
        finally:
            RUN_LOCK.release()

    def stream_camera(self):
        if not CAMERA.device:
            return self.send_json(404, {"error": "camera unavailable"})
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        seq = -1
        try:
            while True:
                frame, seq = CAMERA.wait_frame(seq)
                if not frame:
                    continue
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def run_benchmark(self, source, language="ja"):
        language = normalize_language(language)
        if not RUN_LOCK.acquire(blocking=False):
            return self.send_json(409, {"error": "benchmark already running"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            missing = [key for key, cfg in BACKENDS.items() if not backend_ready(cfg["url"])]
            if missing:
                raise RuntimeError("モデルサーバー未起動: " + ", ".join(missing))
            jpeg, source_name = choose_image(source)
            self.event("snapshot", {
                "source": source_name,
                "jpeg": base64.b64encode(jpeg).decode(),
                "bytes": len(jpeg),
            })
            with DATA_LOCK:
                run_index = RUN_STATS["total_runs"][language]
            model = active_model()
            order = ["cpu", "npu"] if run_index % 2 == 0 else ["npu", "cpu"]
            self.event("order", {"order": order, "reason": "thermal-order bias reduction"})
            results = {}
            for kind in order:
                self.event("phase", {"backend": kind, "name": "running"})
                result = run_inference(kind, jpeg, model, language, lambda token, k=kind: self.event("token", {"backend": k, "text": token}))
                results[kind] = result
                self.event("result", result)
            cpu, npu = results["cpu"], results["npu"]
            record = {
                "time": int(time.time()), "source": source_name, "order": order, "model": model, "language": language,
                "cpu": cpu, "npu": npu,
                "speedup_total": round(cpu["total_s"] / npu["total_s"], 2) if npu["total_s"] else None,
                "speedup_prefill": round(cpu["prefill_s"] / npu["prefill_s"], 2) if npu["prefill_s"] else None,
            }
            with DATA_LOCK:
                HISTORY.append(record)
                del HISTORY[:-HISTORY_LIMIT]
                RUN_STATS["total_runs"][language] += 1
            board = read_board_status()
            maintenance = None
            if recycle_required(board):
                self.event("maintenance", {"state": "starting", "message": "RAMを回復しています", "before": board, "threshold_gb": recycle_threshold()})
                maintenance = recycle_backends(board)
                board = maintenance["after"]
                self.event("maintenance", {"state": "ready", "message": "RAM回復完了", **maintenance})
            self.event("done", {"record": record, "history": history_summary(language), "board": board, "maintenance": maintenance})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.event("error", {"message": "%s: %s" % (type(exc).__name__, exc)})
            except Exception:
                pass
        finally:
            RUN_LOCK.release()


if __name__ == "__main__":
    print("VENTUNO Q AI Benchmark Lab -> http://0.0.0.0:%d" % PORT, flush=True)
    print("camera=%s ready=%s" % (CAMERA.device, CAMERA.ready), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

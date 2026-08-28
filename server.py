#!/usr/bin/env python3
"""Review server with cached Faster-Whisper transcription."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

# The Xet transport can stall on some office networks; plain HTTPS is steadier.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from faster_whisper import WhisperModel
import av


ROOT = Path(__file__).resolve().parent
TRANSCRIPTS = ROOT / "transcripts"
MODELS = ROOT / ".models"
TRANSCRIPTS.mkdir(exist_ok=True)
MODELS.mkdir(exist_ok=True)
JOBS: dict[str, dict] = {}
MODEL = None
MODEL_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "600")) * 1024 * 1024
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_MINUTES", "120")) * 60
MAX_PENDING_JOBS = int(os.getenv("MAX_PENDING_JOBS", "3"))
WORKERS = ThreadPoolExecutor(max_workers=int(os.getenv("ASR_WORKERS", "1")))
SUPPORTED_LANGUAGES = {"zh", "en", "auto"}
LANGUAGE_NAMES = {"zh": "中文课堂", "en": "英语课堂", "auto": "中英双语课堂"}


def job_id(url: str, language: str = "zh") -> str:
    # Keep the original Chinese cache key compatible with existing transcripts.
    value = url if language == "zh" else f"{language}\0{url}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def transcript_path(identifier: str) -> Path:
    return TRANSCRIPTS / f"{identifier}.json"


def load_model():
    global MODEL
    with MODEL_LOCK:
        if MODEL is None:
            model_name = os.getenv("ASR_MODEL", "small")
            MODEL = WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                download_root=str(MODELS),
            )
    return MODEL


def validate_remote_url(url: str) -> None:
    """Reject unsupported and non-public destinations before downloading."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("请填写有效的 HTTP/HTTPS 视频链接")
    if parsed.username or parsed.password:
        raise ValueError("视频链接不能包含账号密码")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        }
    except socket.gaierror as exc:
        raise ValueError("视频地址无法解析") from exc
    if not addresses:
        raise ValueError("视频地址无法解析")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("仅支持可公开访问的视频链接")


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def probe_duration(path: str) -> float:
    with av.open(path) as container:
        if container.duration is not None:
            return float(container.duration / av.time_base)
        durations = [
            float(stream.duration * stream.time_base)
            for stream in container.streams
            if stream.duration is not None and stream.time_base is not None
        ]
        return max(durations, default=0.0)


def transcribe(identifier: str, url: str, requested_language: str) -> None:
    job = JOBS[identifier]
    temporary_video = None
    try:
        language_name = LANGUAGE_NAMES[requested_language]
        job.update(status="working", progress=1, message=f"正在加载{language_name}识别模式…")
        model = load_model()
        job.update(progress=3, message="正在缓存远程视频…")
        validate_remote_url(url)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 Codex-Live-Review/1.0"})
        opener = build_opener(SafeRedirectHandler())
        with opener.open(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length", "0"))
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"视频文件不能超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB")
            downloaded = 0
            with tempfile.NamedTemporaryFile(prefix="live-review-", suffix=".mp4", delete=False) as output_file:
                temporary_video = output_file.name
                while True:
                    chunk = response.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"视频文件不能超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB")
                    output_file.write(chunk)
                    if total:
                        pct = min(35, 3 + (downloaded / total) * 32)
                        job.update(progress=pct, message=f"正在缓存视频 {downloaded / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB")
        media_duration = probe_duration(temporary_video)
        if media_duration <= 0:
            raise ValueError("没有读取到可识别的音视频时长")
        if media_duration > MAX_DURATION_SECONDS:
            raise ValueError(f"单条视频最长支持 {MAX_DURATION_SECONDS // 60} 分钟")
        job.update(progress=36, message="视频缓存完成，正在识别真实音轨…")
        initial_prompts = {
            "en": "This is an English lesson. Preserve correct spelling, capitalization, grammar terms, vocabulary, letters, numbers, and punctuation.",
            "auto": "This is a bilingual Chinese and English lesson. Preserve English words, spelling, letters, numbers, and punctuation.",
        }
        segments, info = model.transcribe(
            temporary_video,
            language=None if requested_language == "auto" else requested_language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=initial_prompts.get(requested_language),
            multilingual=requested_language == "auto",
        )
        duration = float(info.duration or media_duration)
        output = []
        for segment in segments:
            output.append({
                "start": round(float(segment.start), 2),
                "end": round(float(segment.end), 2),
                "text": segment.text.strip(),
            })
            progress = min(99, max(37, 36 + (float(segment.end) / duration) * 63)) if duration else 37
            job.update(progress=progress, message=f"已识别到 {format_time(segment.end)}")
        data = {
            "url": url,
            "duration": duration,
            "language": info.language,
            "requested_language": requested_language,
            "segments": output,
        }
        target = transcript_path(identifier)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        job.update(status="done", progress=100, message="识别完成", data=data)
    except ValueError as exc:
        job.update(status="error", error=str(exc), message="识别失败")
    except Exception as exc:
        print(f"transcription error for {identifier}: {exc}", flush=True)
        job.update(status="error", error="识别服务暂时不可用，请稍后重试", message="识别失败")
    finally:
        if temporary_video:
            try:
                Path(temporary_video).unlink(missing_ok=True)
            except OSError:
                pass


def format_time(seconds: float) -> str:
    value = max(0, int(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/transcribe":
            self.send_json({"error": "接口不存在"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("请求内容无效")
            payload = json.loads(self.rfile.read(length))
            url = str(payload.get("url", "")).strip()
            requested_language = str(payload.get("language", "zh")).strip().lower()
            if requested_language not in SUPPORTED_LANGUAGES:
                raise ValueError("不支持的识别语言")
            validate_remote_url(url)
            identifier = job_id(url, requested_language)
            cached = transcript_path(identifier)
            if cached.exists():
                data = json.loads(cached.read_text(encoding="utf-8"))
                self.send_json({"id": identifier, "status": "done", "data": data})
                return
            with JOBS_LOCK:
                if identifier not in JOBS or JOBS[identifier].get("status") == "error":
                    pending = sum(job.get("status") in {"queued", "working"} for job in JOBS.values())
                    if pending >= MAX_PENDING_JOBS:
                        self.send_json({"error": "当前排队任务较多，请稍后再试"}, 429)
                        return
                    JOBS[identifier] = {"id": identifier, "status": "queued", "progress": 0, "message": f"{LANGUAGE_NAMES[requested_language]}排队中"}
                    WORKERS.submit(transcribe, identifier, url, requested_language)
            self.send_json(JOBS[identifier])
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"status": "ok"})
            return
        if parsed.path == "/api/status":
            identifier = parse_qs(parsed.query).get("id", [""])[0]
            job = JOBS.get(identifier)
            if job:
                self.send_json(job)
                return
            cached = transcript_path(identifier)
            if cached.exists():
                data = json.loads(cached.read_text(encoding="utf-8"))
                self.send_json({"id": identifier, "status": "done", "progress": 100, "data": data})
                return
            self.send_json({"error": "识别任务不存在"}, 404)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    host = os.getenv("HOST", "127.0.0.1")
    print(f"审核台已启动：http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()

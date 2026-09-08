#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地网页下载器：视频号 / B 站 / 小红书 / 抖音。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from downloader import DownloadError, detect_platform, download, extract_url, probe
from douyin import DouyinError
from wechat import WeChatError

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
DATA = ROOT / "data"
SETTINGS_FILE = DATA / "settings.json"
STATIC = ROOT / "static"
INDEX = ROOT / "index.html"

DOWNLOADS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

JOBS = {}
JOB_LOCK = threading.Lock()
DEFAULT_SETTINGS = {
    "yuanbao_cookie": "",
    "bili_cookie": "",
    "xhs_cookie": "",
    "douyin_cookie": "",
}


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    out = dict(DEFAULT_SETTINGS)
    out.update({k: (data.get(k) or "") for k in DEFAULT_SETTINGS})
    return out


def save_settings(payload: dict) -> dict:
    current = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in payload and payload[key] is not None:
            value = str(payload[key]).strip()
            if value.lower().startswith("cookie:"):
                value = value.split(":", 1)[1].strip()
            current[key] = value
    SETTINGS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def cookie_view(value: str) -> dict:
    value = value or ""
    return {
        "configured": bool(value),
        "length": len(value),
        "value": value,
    }


def json_bytes(payload: dict, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, "application/json; charset=utf-8", body


def read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "platform": job.get("platform"),
        "url": job.get("url"),
        "percent": job.get("percent"),
        "message": job.get("message"),
        "error": job.get("error"),
        "file_name": job.get("file_name"),
        "file_size": job.get("file_size"),
        "title": job.get("title"),
        "uploader": job.get("uploader"),
        "quality": job.get("quality"),
        "created_at": job.get("created_at"),
        "download_url": f"/files/{job['file_name']}" if job.get("file_name") else None,
    }


def run_job(job_id: str, url: str, quality=None):
    settings = load_settings()

    def hook(payload: dict):
        with JOB_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            job["message"] = payload.get("message") or job.get("message")
            if payload.get("percent") is not None:
                job["percent"] = payload["percent"]
            elif payload.get("total_bytes") and payload.get("downloaded_bytes") is not None:
                total = payload["total_bytes"]
                if total:
                    job["percent"] = round(payload["downloaded_bytes"] * 100 / total, 1)
            if payload.get("status") == "resolving":
                job["status"] = "resolving"
            elif payload.get("status") == "downloading":
                job["status"] = "downloading"

    try:
        with JOB_LOCK:
            JOBS[job_id]["status"] = "resolving"
            JOBS[job_id]["message"] = "正在解析链接"
        result = download(url, str(DOWNLOADS), settings, progress_hook=hook, quality=quality)
        info = result.get("info") or {}
        path = Path(result["file_path"])
        with JOB_LOCK:
            JOBS[job_id].update(
                {
                    "status": "done",
                    "percent": 100,
                    "message": "下载完成",
                    "file_name": path.name,
                    "file_size": path.stat().st_size,
                    "title": info.get("title") or path.stem,
                    "uploader": info.get("uploader") or "",
                    "quality": info.get("quality") or JOBS[job_id].get("quality"),
                    "error": None,
                }
            )
    except (DownloadError, WeChatError, DouyinError, Exception) as exc:
        with JOB_LOCK:
            JOBS[job_id].update(
                {
                    "status": "error",
                    "error": str(exc),
                    "message": str(exc),
                }
            )


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[server] {self.address_string()} {fmt % args}")

    def _send(self, status, content_type, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status=200):
        self._send(*json_bytes(payload, status))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/", "/index.html"):
            body = INDEX.read_bytes()
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
            return
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            file_path = (STATIC / rel).resolve()
            if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.exists():
                self._send_json({"error": "not found"}, 404)
                return
            ctype = {
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".png": "image/png",
                ".svg": "image/svg+xml",
            }.get(file_path.suffix, "application/octet-stream")
            self._send(200, ctype, file_path.read_bytes())
            return
        if path == "/api/health":
            self._send_json({"ok": True})
            return
        if path == "/api/settings":
            settings = load_settings()
            self._send_json(
                {
                    "yuanbao_cookie": cookie_view(settings["yuanbao_cookie"]),
                    "bili_cookie": cookie_view(settings["bili_cookie"]),
                    "xhs_cookie": cookie_view(settings["xhs_cookie"]),
                    "douyin_cookie": cookie_view(settings["douyin_cookie"]),
                    "downloads_dir": str(DOWNLOADS),
                }
            )
            return
        if path == "/api/jobs":
            with JOB_LOCK:
                jobs = [public_job(j) for j in sorted(JOBS.values(), key=lambda x: x["created_at"], reverse=True)]
            self._send_json({"jobs": jobs})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOB_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send_json({"error": "任务不存在"}, 404)
                return
            self._send_json(public_job(job))
            return
        if path.startswith("/files/"):
            name = path[len("/files/") :]
            file_path = (DOWNLOADS / name).resolve()
            if not str(file_path).startswith(str(DOWNLOADS.resolve())) or not file_path.exists():
                self._send_json({"error": "file not found"}, 404)
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
            self.end_headers()
            self.wfile.write(data)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        payload = read_body(self)
        if path == "/api/settings":
            settings = save_settings(payload)
            self._send_json(
                {
                    "ok": True,
                    "yuanbao_cookie": cookie_view(settings["yuanbao_cookie"]),
                    "bili_cookie": cookie_view(settings["bili_cookie"]),
                    "xhs_cookie": cookie_view(settings["xhs_cookie"]),
                    "douyin_cookie": cookie_view(settings["douyin_cookie"]),
                }
            )
            return
        if path == "/api/probe":
            url = extract_url(payload.get("url") or "")
            try:
                info = probe(url, load_settings())
                self._send_json({"ok": True, "info": info})
            except (DownloadError, WeChatError, DouyinError, Exception) as exc:
                self._send_json({"ok": False, "error": str(exc), "platform": detect_platform(url)}, 400)
            return
        if path == "/api/download":
            url = extract_url(payload.get("url") or "")
            platform = detect_platform(url)
            if platform == "other":
                self._send_json({"ok": False, "error": "只支持视频号、B 站、小红书、抖音链接"}, 400)
                return
            quality = payload.get("quality")
            job_id = uuid.uuid4().hex[:10]
            job = {
                "id": job_id,
                "status": "queued",
                "platform": platform,
                "url": url,
                "percent": 0,
                "message": "排队中",
                "error": None,
                "file_name": None,
                "file_size": None,
                "title": "",
                "uploader": "",
                "quality": None if quality in (None, "", "best") else f"{quality}p",
                "created_at": time.time(),
            }
            with JOB_LOCK:
                JOBS[job_id] = job
            threading.Thread(target=run_job, args=(job_id, url, quality), daemon=True).start()
            self._send_json({"ok": True, "job": public_job(job)})
            return
        self._send_json({"error": "not found"}, 404)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"视频下载器已启动：{url}")
    print(f"文件保存目录：{DOWNLOADS}")
    print("前台运行中，按 Ctrl+C 停止")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()


if __name__ == "__main__":
    main()

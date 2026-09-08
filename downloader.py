# -*- coding: utf-8 -*-
"""B 站 / 小红书走 yt-dlp；视频号走 wechat.py；抖音走 douyin.py。"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from douyin import DouyinError, download_direct, extract_douyin_url, parse_douyin, select_douyin_media
from wechat import download_wechat, extract_wechat_url, probe_wechat

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>【】（）()，。；]+", re.IGNORECASE)
XHS_RE = re.compile(
    r"https?://(?:www\.)?(?:xiaohongshu\.com|xhslink\.com)/[^\s\"'<>]+",
    re.IGNORECASE,
)
BILI_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:bilibili\.com|b23\.tv)/[^\s\"'<>]+",
    re.IGNORECASE,
)
DOUYIN_RE = re.compile(
    r"https?://(?:v\.douyin\.com|[^\s]*douyin\.com)/[^\s\"'<>]+",
    re.IGNORECASE,
)
ProgressHook = Optional[Callable[[dict], None]]

PLATFORM_LABELS = {
    "wechat": "微信视频号",
    "bilibili": "哔哩哔哩",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
}
SUPPORTED_HINT = "只支持微信视频号、B 站、小红书、抖音链接"
QUALITY_CHOICES = (0, 360, 480, 720, 1080, 2160)


def normalize_quality(value) -> Optional[int]:
    if value in (None, "", "best", "highest", 0, "0"):
        return None
    try:
        height = int(value)
    except (TypeError, ValueError):
        return None
    if height <= 0:
        return None
    return height


def format_selector(max_height: Optional[int]) -> str:
    if not max_height:
        return "bv*+ba/b"
    return (
        f"bv*[height<={max_height}]+ba/"
        f"b[height<={max_height}]/"
        f"bv*+ba/b"
    )


def quality_label(max_height: Optional[int]) -> str:
    return "最高" if not max_height else f"{max_height}p"


def _filesize_of(fmt: dict) -> Optional[int]:
    for key in ("filesize", "filesize_approx", "size"):
        value = fmt.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def _ytdlp_formats(data: dict) -> list:
    duration = data.get("duration")
    try:
        duration = int(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    grouped = {}
    for fmt in data.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        height = fmt.get("height")
        vcodec = str(fmt.get("vcodec") or "none")
        acodec = str(fmt.get("acodec") or "none")
        if not height or vcodec == "none":
            continue
        height = int(height)
        size = _filesize_of(fmt)
        bucket = grouped.setdefault(
            height,
            {"id": str(height), "label": f"{height}p", "height": height, "filesize": None, "duration": duration, "has_audio": False},
        )
        if acodec != "none":
            bucket["has_audio"] = True
        if size and (bucket["filesize"] is None or size > bucket["filesize"]):
            bucket["filesize"] = size
    items = list(grouped.values())
    items.sort(key=lambda x: x["height"], reverse=True)
    if not items and data.get("height"):
        items.append(
            {
                "id": str(data["height"]),
                "label": f"{data['height']}p",
                "height": int(data["height"]),
                "filesize": _filesize_of(data),
                "duration": duration,
            }
        )
    for item in items:
        if item.get("filesize") is None and duration and data.get("tbr"):
            try:
                item["filesize"] = int(float(data["tbr"]) * 1000 / 8 * duration)
            except (TypeError, ValueError):
                pass
    return items


class DownloadError(Exception):
    pass


def find_yt_dlp() -> str:
    path = shutil.which("yt-dlp")
    if path:
        return path
    home = Path.home() / ".local" / "bin" / "yt-dlp"
    if home.exists():
        return str(home)
    raise DownloadError("未找到 yt-dlp，请先安装：pip install yt-dlp")


def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def extract_url(text: str) -> str:
    text = (text or "").strip()
    wechat = extract_wechat_url(text)
    if wechat:
        return wechat
    douyin = extract_douyin_url(text)
    if douyin:
        return douyin
    for pattern in (XHS_RE, BILI_RE, DOUYIN_RE, URL_IN_TEXT_RE):
        match = pattern.search(text)
        if match:
            return match.group(0).rstrip(".,;，；")
    return text


def detect_platform(url: str) -> str:
    url = (url or "").lower()
    if "weixin.qq.com/sph/" in url:
        return "wechat"
    if "bilibili.com" in url or "b23.tv" in url:
        return "bilibili"
    if "xiaohongshu.com" in url or "xhslink.com" in url:
        return "xiaohongshu"
    if "douyin.com" in url:
        return "douyin"
    return "other"


def _bilibili_cookiefile(user_cookie: str = "") -> Optional[str]:
    if user_cookie:
        return _header_cookie_to_netscape(user_cookie, "bilibili.com")
    import requests

    try:
        session = requests.Session()
        session.headers.update({"User-Agent": BROWSER_UA, "Referer": "https://www.bilibili.com/"})
        session.get("https://www.bilibili.com", timeout=10)
        if not session.cookies:
            return None
        fd, path = tempfile.mkstemp(prefix="bilibili_cookies_", suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in session.cookies:
                domain = cookie.domain or ".bilibili.com"
                f.write(f"{domain}\tTRUE\t/\tFALSE\t0\t{cookie.name}\t{cookie.value}\n")
        return path
    except Exception as exc:
        logging.warning("获取 B 站 cookie 失败: %s", exc)
        return None


def _header_cookie_to_netscape(cookie_header: str, domain: str) -> Optional[str]:
    cookie_header = (cookie_header or "").strip()
    if not cookie_header:
        return None
    if cookie_header.lower().startswith("cookie:"):
        cookie_header = cookie_header.split(":", 1)[1].strip()
    pairs = [p.strip() for p in cookie_header.split(";") if "=" in p]
    if not pairs:
        return None
    fd, path = tempfile.mkstemp(prefix=f"{domain}_cookies_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for pair in pairs:
            name, value = pair.split("=", 1)
            f.write(f".{domain}\tTRUE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}\n")
    return path


def _yt_dlp_common_args(url: str, platform: str, cookies_file: Optional[str]) -> list:
    referer = {
        "bilibili": "https://www.bilibili.com/",
        "xiaohongshu": "https://www.xiaohongshu.com/",
        "douyin": "https://www.douyin.com/",
    }.get(platform, "https://www.bilibili.com/")
    args = [
        find_yt_dlp(),
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--retries",
        "3",
        "--socket-timeout",
        "20",
        "--user-agent",
        BROWSER_UA,
        "--add-header",
        f"Referer:{referer}",
        "--add-header",
        "Accept-Language:zh-CN,zh;q=0.9,en;q=0.8",
    ]
    if find_ffmpeg():
        args.extend(["--ffmpeg-location", str(Path(find_ffmpeg()).parent)])
    if cookies_file:
        args.extend(["--cookies", cookies_file])
    args.append(url)
    return args


def probe(url: str, settings: dict) -> dict:
    url = extract_url(url)
    platform = detect_platform(url)
    if platform == "other":
        raise DownloadError(SUPPORTED_HINT)
    if platform == "wechat":
        info = probe_wechat(url, settings.get("yuanbao_cookie") or "")
        info["platform_label"] = PLATFORM_LABELS[platform]
        info.setdefault("formats", [])
        return info
    if platform == "douyin":
        try:
            info = parse_douyin(url, settings.get("douyin_cookie") or "")
            info["platform_label"] = PLATFORM_LABELS[platform]
            info.setdefault("formats", [])
            return info
        except DouyinError as exc:
            if not (settings.get("douyin_cookie") or "").strip():
                raise DownloadError(str(exc)) from exc
            logging.warning("抖音直链预览失败，回退 yt-dlp: %s", exc)

    cookie_file = None
    temp_cookie = None
    try:
        if platform == "bilibili":
            cookie_file = _bilibili_cookiefile(settings.get("bili_cookie") or "")
            temp_cookie = cookie_file
        elif platform == "xiaohongshu":
            cookie_file = _header_cookie_to_netscape(
                settings.get("xhs_cookie") or "", "xiaohongshu.com"
            )
            temp_cookie = cookie_file
        elif platform == "douyin":
            cookie_file = _header_cookie_to_netscape(
                settings.get("douyin_cookie") or "", "douyin.com"
            )
            temp_cookie = cookie_file
        cmd = _yt_dlp_common_args(url, platform, cookie_file)
        cmd[1:1] = ["--dump-json", "--skip-download"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "解析失败").strip().splitlines()[-1]
            err = err.split("please report")[0].strip(" ;")
            if platform == "xiaohongshu":
                raise DownloadError(f"小红书解析失败：{err}。可在设置中粘贴 xiaohongshu.com 的 Cookie 后重试")
            if platform == "douyin":
                raise DownloadError(f"抖音解析失败：{err}。可在设置中粘贴 douyin.com 的 Cookie 后重试")
            raise DownloadError(f"解析失败：{err}")
        import json

        data = json.loads(proc.stdout.splitlines()[-1])
        duration = data.get("duration")
        return {
            "id": data.get("id"),
            "title": data.get("title") or "未知标题",
            "uploader": data.get("uploader") or data.get("channel") or data.get("creator") or "",
            "duration": int(duration) if duration else None,
            "thumbnail": data.get("thumbnail") or "",
            "platform": platform,
            "platform_label": PLATFORM_LABELS[platform],
            "webpage_url": data.get("webpage_url") or url,
            "formats": _ytdlp_formats(data),
            "need_cookie": False,
        }
    finally:
        if temp_cookie:
            Path(temp_cookie).unlink(missing_ok=True)


def _parse_progress(line: str) -> Optional[dict]:
    match = re.search(
        r"\[download\]\s+([0-9.]+)%\s+of\s+~?\s*([0-9.]+[KMG]?i?B)",
        line,
    )
    if not match:
        if "[download] Destination" in line or "[Merger]" in line:
            return {"status": "downloading", "message": line.strip()}
        return None
    percent = float(match.group(1))
    return {
        "status": "downloading",
        "percent": percent,
        "total_hint": match.group(2),
        "message": line.strip(),
    }


def download(url: str, target_dir: str, settings: dict, progress_hook: ProgressHook = None, quality=None) -> dict:
    url = extract_url(url)
    platform = detect_platform(url)
    if platform == "other":
        raise DownloadError(SUPPORTED_HINT)
    max_height = normalize_quality(quality)

    def hook(payload: dict):
        if progress_hook:
            try:
                progress_hook(payload)
            except Exception:
                pass

    if platform == "wechat":
        return download_wechat(url, target_dir, settings.get("yuanbao_cookie") or "", hook)
    if platform == "douyin":
        try:
            info = parse_douyin(url, settings.get("douyin_cookie") or "", max_height=max_height)
            info = select_douyin_media(info, max_height)
            hook({"status": "resolving", "message": f"已解析抖音直链（{quality_label(max_height)}），开始下载"})
            path = download_direct(info, target_dir, hook)
            info["platform_label"] = PLATFORM_LABELS[platform]
            info["quality"] = quality_label(max_height)
            return {"file_path": str(path), "info": info}
        except DouyinError as exc:
            if not (settings.get("douyin_cookie") or "").strip():
                raise DownloadError(str(exc)) from exc
            logging.warning("抖音直链下载失败，回退 yt-dlp: %s", exc)
            hook({"status": "resolving", "message": "直链失败，改用 Cookie + yt-dlp"})

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    before = {p.resolve() for p in target.iterdir()}
    cookie_file = None
    temp_cookie = None
    try:
        if platform == "bilibili":
            cookie_file = _bilibili_cookiefile(settings.get("bili_cookie") or "")
            temp_cookie = cookie_file
        elif platform == "xiaohongshu":
            cookie_file = _header_cookie_to_netscape(
                settings.get("xhs_cookie") or "", "xiaohongshu.com"
            )
            temp_cookie = cookie_file
        elif platform == "douyin":
            cookie_file = _header_cookie_to_netscape(
                settings.get("douyin_cookie") or "", "douyin.com"
            )
            temp_cookie = cookie_file
        outtmpl = str(target / "%(title).80s [%(id)s].%(ext)s")
        cmd = _yt_dlp_common_args(url, platform, cookie_file)
        extra = [
            "-o",
            outtmpl,
            "--merge-output-format",
            "mp4",
            "-f",
            format_selector(max_height),
            "--progress",
        ]
        cmd[1:1] = extra
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                parsed = _parse_progress(line)
                if parsed:
                    hook(parsed)
        code = proc.wait()
        if code != 0:
            err = "\n".join(lines[-8:]) or "下载失败"
            if platform == "xiaohongshu" and not (settings.get("xhs_cookie") or "").strip():
                raise DownloadError("小红书下载失败，请在设置中粘贴 xiaohongshu.com 的 Cookie 后重试")
            if platform == "douyin":
                raise DownloadError("抖音下载失败，请在设置中粘贴 douyin.com 的 Cookie 后重试")
            raise DownloadError(err)
        files = sorted(
            [p for p in target.iterdir() if p.resolve() not in before],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        video = next((p for p in files if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
        if not video:
            raise DownloadError("下载完成但未找到视频文件")
        hook({"status": "finished"})
        info = probe(url, settings)
        info["quality"] = quality_label(max_height)
        return {"file_path": str(video), "info": info}
    finally:
        if temp_cookie:
            Path(temp_cookie).unlink(missing_ok=True)

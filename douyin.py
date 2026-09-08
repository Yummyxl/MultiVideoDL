# -*- coding: utf-8 -*-
"""抖音下载：短链展开 → hybrid/tikwm 直链 → Cookie + yt-dlp 兜底。"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

import requests

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DOUYIN_REFERER = "https://www.douyin.com/"
DOUYIN_URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com/[A-Za-z0-9_\-]+/?"
    r"|(?:www\.)?(?:ies)?douyin\.com/[^\s\"'<>]+)",
    re.IGNORECASE,
)
AWEME_ID_RE = re.compile(r"(?:video|note)/(\d{8,})")
ProgressHook = Optional[Callable[[dict], None]]


class DouyinError(Exception):
    pass


def extract_douyin_url(text: str) -> Optional[str]:
    match = DOUYIN_URL_RE.search(text or "")
    return match.group(0).rstrip(".,;，；") if match else None


def douyin_id(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_ids"):
        if query.get(key):
            return query[key][0]
    match = AWEME_ID_RE.search(url)
    if match:
        return match.group(1)
    path = parsed.path.strip("/").replace("/", "_")
    return path or "douyin_video"


def _headers(cookie: str = "") -> dict:
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": DOUYIN_REFERER,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if cookie:
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        headers["Cookie"] = cookie
    return headers


def expand_share_url(url: str) -> str:
    """v.douyin.com 短链跟跳转到 www.douyin.com/video/<id>。"""
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            timeout=15,
            allow_redirects=True,
        )
        final = resp.url or url
        if "douyin.com" in final:
            return final.split("#")[0]
    except Exception as exc:
        logging.warning("抖音短链展开失败: %s", exc)
    return url


def _collect_play_urls(video: dict) -> list:
    found = []
    seen = set()
    for item in video.get("bit_rate") or video.get("bitRate") or []:
        if not isinstance(item, dict):
            continue
        play = item.get("play_addr") or item.get("playAddr") or {}
        urls = play.get("url_list") or play.get("urlList") or []
        if not urls:
            continue
        url = str(urls[0]).replace("playwm", "play")
        if url in seen:
            continue
        seen.add(url)
        height = int(item.get("height") or play.get("height") or 0)
        width = int(item.get("width") or play.get("width") or 0)
        gear = str(item.get("gear_name") or item.get("gearName") or "")
        found.append(
            {
                "url": url,
                "height": height,
                "width": width,
                "label": gear,
                "filesize": int(item.get("play_addr", {}).get("data_size") or play.get("data_size") or 0) or None,
            }
        )
    for key in ("play_addr", "playAddr", "download_addr", "downloadAddr"):
        node = video.get(key) or {}
        urls = node.get("url_list") or node.get("urlList") or []
        if not urls:
            continue
        url = str(urls[0]).replace("playwm", "play")
        if url in seen:
            continue
        seen.add(url)
        found.append(
            {
                "url": url,
                "height": int(node.get("height") or 0),
                "width": int(node.get("width") or 0),
                "label": key,
            }
        )
    found.sort(key=lambda x: x.get("height") or 0, reverse=True)
    return found


def _pick_url_by_height(candidates: list, max_height: Optional[int] = None) -> Optional[str]:
    if not candidates:
        return None
    if max_height:
        capped = [c for c in candidates if (c.get("height") or 0) <= max_height]
        if capped:
            return capped[0]["url"]
    return candidates[0]["url"]


def _formats_from_candidates(candidates: list, duration=None) -> list:
    formats = []
    seen_heights = set()
    for item in candidates:
        height = int(item.get("height") or 0)
        if height and height in seen_heights:
            continue
        if height:
            seen_heights.add(height)
        label = f"{height}p" if height else (item.get("label") or "默认")
        formats.append(
            {
                "id": str(height or "best"),
                "label": label,
                "height": height or None,
                "filesize": item.get("filesize"),
                "duration": duration,
            }
        )
    return formats


def _pick_best_url(video: dict, max_height: Optional[int] = None) -> Optional[str]:
    return _pick_url_by_height(_collect_play_urls(video), max_height)


def _info_from_aweme(aweme: dict, webpage_url: str) -> dict:
    video = aweme.get("video") or {}
    author = aweme.get("author") or {}
    cover = (
        (video.get("cover") or {}).get("url_list")
        or (video.get("origin_cover") or {}).get("url_list")
        or []
    )
    duration = video.get("duration") or aweme.get("duration")
    if isinstance(duration, (int, float)) and duration > 1000:
        duration = int(duration / 1000)
    candidates = _collect_play_urls(video)
    media_url = _pick_url_by_height(candidates)
    vid = str(aweme.get("aweme_id") or douyin_id(webpage_url))
    return {
        "id": vid,
        "title": (aweme.get("desc") or aweme.get("preview_title") or f"抖音 {vid}")[:120],
        "uploader": author.get("nickname") or author.get("unique_id") or "",
        "duration": int(duration) if duration else None,
        "thumbnail": cover[0] if cover else "",
        "platform": "douyin",
        "webpage_url": webpage_url,
        "media_url": media_url,
        "candidates": candidates,
        "formats": _formats_from_candidates(candidates, duration=int(duration) if duration else None),
        "need_cookie": False,
    }


def _hybrid_parse(url: str) -> dict:
    api = "https://douyin.wtf/api/hybrid/video_data?url=" + quote(url, safe="")
    resp = requests.get(api, headers=_headers(), timeout=25)
    if resp.status_code == 422:
        raise DouyinError("hybrid 接口无法解析该链接")
    if not resp.ok:
        raise DouyinError(f"hybrid 接口异常: HTTP {resp.status_code}")
    payload = resp.json() or {}
    data = payload.get("data") or payload
    aweme = data.get("aweme_detail") or data.get("aweme") or data
    if not isinstance(aweme, dict) or not (aweme.get("video") or aweme.get("aweme_id")):
        raise DouyinError("hybrid 接口未返回视频数据")
    info = _info_from_aweme(aweme, url)
    if not info.get("media_url"):
        raise DouyinError("hybrid 接口未返回可下载直链")
    return info


def _tikwm_parse(url: str) -> dict:
    resp = requests.get(
        "https://www.tikwm.com/api/",
        params={"url": url, "hd": 1},
        headers=_headers(),
        timeout=25,
    )
    if not resp.ok:
        raise DouyinError(f"tikwm 接口异常: HTTP {resp.status_code}")
    payload = resp.json() or {}
    if payload.get("code") not in (0, 200, None):
        raise DouyinError(payload.get("msg") or "tikwm 解析失败")
    data = payload.get("data") or {}
    hd = data.get("hdplay")
    sd = data.get("play")
    wm = data.get("wmplay")
    media = hd or sd or wm
    if not media:
        raise DouyinError("tikwm 未返回视频地址")
    size_hd = data.get("hd_size") or data.get("size")
    size_sd = data.get("size")
    try:
        size_hd = int(size_hd) if size_hd else None
    except (TypeError, ValueError):
        size_hd = None
    try:
        size_sd = int(size_sd) if size_sd else None
    except (TypeError, ValueError):
        size_sd = None
    duration = data.get("duration")
    try:
        duration = int(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    formats = []
    if hd:
        formats.append({"id": "1080", "label": "1080p", "height": 1080, "filesize": size_hd, "duration": duration})
    if sd:
        formats.append({"id": "720", "label": "720p", "height": 720, "filesize": size_sd or size_hd, "duration": duration})
    if wm and not formats:
        formats.append({"id": "best", "label": "默认", "height": None, "filesize": size_sd, "duration": duration})
    author = data.get("author") or {}
    duration = data.get("duration")
    vid = str(data.get("id") or douyin_id(url))
    return {
        "id": vid,
        "title": (data.get("title") or data.get("desc") or f"抖音 {vid}")[:120],
        "uploader": author.get("nickname") or author.get("unique_id") or "",
        "duration": int(duration) if duration else None,
        "thumbnail": data.get("cover") or data.get("origin_cover") or "",
        "platform": "douyin",
        "webpage_url": url,
        "media_url": media,
        "media_urls": {"1080": hd, "720": sd, "best": media},
        "formats": formats,
        "need_cookie": False,
    }


def _validate_video(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 10240:
        return False
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            timeout=30,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except FileNotFoundError:
        with open(path, "rb") as f:
            head = f.read(8)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except Exception:
        return False


def parse_douyin(url: str, cookie: str = "", max_height: Optional[int] = None) -> dict:
    expanded = expand_share_url(url)
    errors = []
    for parser, name in ((_hybrid_parse, "hybrid"), (_tikwm_parse, "tikwm")):
        try:
            info = parser(expanded)
            info["webpage_url"] = expanded
            if max_height:
                urls = info.get("media_urls") or {}
                picked = None
                if max_height >= 1080:
                    picked = urls.get("1080") or urls.get("720") or info.get("media_url")
                elif max_height >= 720:
                    picked = urls.get("720") or urls.get("1080") or info.get("media_url")
                else:
                    picked = urls.get("720") or info.get("media_url")
                if picked:
                    info["media_url"] = picked
            return info
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logging.warning("抖音 %s 解析失败: %s", name, exc)
    raise DouyinError("；".join(errors[-2:]) or "抖音解析失败")


def download_direct(info: dict, target_dir: str, progress_hook: ProgressHook = None) -> Path:
    media_url = info.get("media_url")
    if not media_url:
        raise DouyinError("没有可下载的直链")
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    vid = info.get("id") or "douyin_video"
    final_path = target / f"{vid}.mp4"

    def hook(payload: dict):
        if progress_hook:
            try:
                progress_hook(payload)
            except Exception:
                pass

    with requests.get(
        media_url,
        stream=True,
        timeout=(10, 60),
        headers=_headers(),
        allow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": total or None})
        with open(final_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    hook(
                        {
                            "status": "downloading",
                            "downloaded_bytes": done,
                            "total_bytes": total or None,
                        }
                    )
    if not _validate_video(final_path):
        final_path.unlink(missing_ok=True)
        raise DouyinError("直链下载完成但文件不是有效视频")
    hook({"status": "finished"})
    return final_path


def probe_douyin(url: str, cookie: str = "") -> dict:
    return parse_douyin(url, cookie)


def select_douyin_media(info: dict, max_height: Optional[int] = None) -> dict:
    if not max_height:
        return info
    if info.get("candidates"):
        picked = _pick_url_by_height(info["candidates"], max_height)
        if picked:
            info["media_url"] = picked
            return info
    urls = info.get("media_urls") or {}
    if max_height >= 1080:
        info["media_url"] = urls.get("1080") or urls.get("720") or info.get("media_url")
    elif max_height >= 720:
        info["media_url"] = urls.get("720") or urls.get("1080") or info.get("media_url")
    else:
        info["media_url"] = urls.get("720") or info.get("media_url")
    return info


def download_douyin(
    url: str,
    target_dir: str,
    cookie: str = "",
    progress_hook: ProgressHook = None,
) -> dict:
    def hook(payload: dict):
        if progress_hook:
            try:
                progress_hook(payload)
            except Exception:
                pass

    hook({"status": "resolving", "message": "正在解析抖音链接"})
    info = parse_douyin(url, cookie)
    path = download_direct(info, target_dir, progress_hook)
    return {"file_path": str(path), "info": info}

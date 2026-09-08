# -*- coding: utf-8 -*-
"""微信视频号下载：元宝 Cookie 解析 → feed 直链 → 按需 ISAAC64 解密。"""
from __future__ import annotations

import html as html_lib
import logging
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlsplit

import requests

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49"
)

YUANBAO_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
YUANBAO_PARSE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": "https://yuanbao.tencent.com",
    "referer": "https://yuanbao.tencent.com/chat/naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "user-agent": BROWSER_UA,
    "t-userid": "b9575f6b0a8c4a55a08096904a5ef20a",
    "x-agentid": "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "x-commit-tag": "72282a0d",
    "x-device-id": "1921b001708100d7fa31002b9646bd0cc15a3e2e1f",
    "x-id": "b9575f6b0a8c4a55a08096904a5ef20a",
    "x-language": "zh-CN",
    "x-os_version": "Mac OS(10.15.7)-Blink",
    "x-platform": "mac",
    "x-requested-with": "XMLHttpRequest",
    "x-source": "web",
    "x-webversion": "2.69.0",
}
FEED_INFO_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
FEED_INFO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://channels.weixin.qq.com",
    "User-Agent": BROWSER_UA,
}

WECHAT_URL_RE = re.compile(r"https?://weixin\.qq\.com/sph/[A-Za-z0-9_\-]+", re.IGNORECASE)
ENC_LIMIT = 131072
_U64 = (1 << 64) - 1
ProgressHook = Optional[Callable[[dict], None]]


class WeChatError(Exception):
    pass


def extract_wechat_url(text: str) -> Optional[str]:
    match = WECHAT_URL_RE.search(text or "")
    return match.group(0) if match else None


def wechat_id(url: str) -> str:
    match = re.search(r"weixin\.qq\.com/sph/([A-Za-z0-9_\-]+)", url, re.IGNORECASE)
    return match.group(1) if match else "wechat_video"


def _plain_text(value) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", "", text)
    return html_lib.unescape(text).strip()


def _parse_share(share_url: str, cookie: str):
    headers = dict(YUANBAO_PARSE_HEADERS)
    if cookie:
        headers["cookie"] = cookie
    resp = requests.post(
        YUANBAO_PARSE_URL,
        json={"type": "video_channel_url", "url": share_url, "scene": 1},
        headers=headers,
        timeout=30,
    )
    if resp.status_code in (401, 403):
        raise WeChatError("元宝 Cookie 缺失或已过期，请在设置里重新粘贴")
    if not resp.ok:
        raise WeChatError(f"元宝解析接口异常: HTTP {resp.status_code}")
    data = (resp.json() or {}).get("data") or {}
    export_id = data.get("wx_export_id") or ""
    token, eid = "", export_id
    playable = data.get("playable_url") or ""
    if playable:
        query = parse_qs(urlsplit(playable).query)
        token = (query.get("token") or [""])[0]
        eid = (query.get("eid") or [export_id])[0]
    if not eid:
        raise WeChatError("元宝未返回 export id，分享链接可能已失效")
    return eid, token


def _feed_info(eid: str, token: str) -> dict:
    rid = f"{int(time.time()):x}-" + "".join(random.choice("0123456789abcdef") for _ in range(8))
    api = (
        f"{FEED_INFO_URL}?_rid={rid}"
        "&_pageUrl=https%3A%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
    )
    referer = (
        "https://channels.weixin.qq.com/finder-preview/pages/feed"
        f"?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={quote(token)}&entry_scene=0&eid={quote(eid)}"
    )
    resp = requests.post(
        api,
        json={"baseReq": {"generalToken": token}, "exportId": eid},
        headers={**FEED_INFO_HEADERS, "Referer": referer},
        timeout=30,
    )
    if not resp.ok:
        raise WeChatError(f"视频号接口异常: HTTP {resp.status_code}")
    result = resp.json()
    if result.get("errCode"):
        raise WeChatError(f"视频号接口错误: {_plain_text(result.get('errMsg'))}")
    detail = (result.get("data") or {}).get("errMsg") or {}
    title = _plain_text(detail.get("title"))
    content = _plain_text(detail.get("content"))
    if detail.get("type") or title or content:
        raise WeChatError(content and f"{title}: {content}" or title or "内容无法播放")
    return result


def _walk_collect_video_media(node, found=None):
    if found is None:
        found = []
    if isinstance(node, dict):
        url = None
        for key in ("mediaUrl", "videoUrl", "url"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                url = value
                break
        if url and (
            "decodeKey" in node
            or "fileType" in node
            or "mediaUrl" in node
            or "videoUrl" in node
            or "spec" in node
        ):
            token = node.get("urlToken") or ""
            if token and token not in url:
                url = url + token
            decode_key = node.get("decodeKey")
            try:
                decode_key = int(str(decode_key)) if decode_key not in (None, "") else None
            except (TypeError, ValueError):
                decode_key = None
            found.append({"url": url, "decode_key": decode_key})
        for value in node.values():
            _walk_collect_video_media(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_collect_video_media(value, found)
    return found


def _feed_meta(feed: dict) -> dict:
    meta: Dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            if "description" in node:
                meta.setdefault("title", _plain_text(node.get("description"))[:120])
                for key in ("nickname", "objectNickname", "userName"):
                    if node.get(key):
                        meta.setdefault("uploader", str(node[key]))
                        break
                for key in ("coverUrl", "coverImgUrl", "thumbUrl"):
                    if node.get(key):
                        meta.setdefault("thumbnail", str(node[key]))
                        break
            if node.get("nickname") and "headImgUrl" in node:
                meta.setdefault("uploader", str(node["nickname"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(feed)
    return {k: v for k, v in meta.items() if v}


def _isaac64_mix(a, b, c, d, e, f, g, h):
    a = (a - e) & _U64
    f ^= h >> 9
    h = (h + a) & _U64
    b = (b - f) & _U64
    g ^= (a << 9) & _U64
    a = (a + b) & _U64
    c = (c - g) & _U64
    h ^= b >> 23
    b = (b + c) & _U64
    d = (d - h) & _U64
    a ^= (c << 15) & _U64
    c = (c + d) & _U64
    e = (e - a) & _U64
    b ^= d >> 14
    d = (d + e) & _U64
    f = (f - b) & _U64
    c ^= (e << 20) & _U64
    e = (e + f) & _U64
    g = (g - c) & _U64
    d ^= f >> 17
    f = (f + g) & _U64
    h = (h - d) & _U64
    e ^= (g << 14) & _U64
    g = (g + h) & _U64
    return a, b, c, d, e, f, g, h


def _isaac64_keystream(key: int):
    golden = 0x9E3779B97F4A7C13
    seed = [0] * 256
    seed[0] = key & _U64
    mm = [0] * 256
    a = b = c = d = e = f = g = h = golden
    for _ in range(4):
        a, b, c, d, e, f, g, h = _isaac64_mix(a, b, c, d, e, f, g, h)
    for i in range(0, 256, 8):
        a = (a + seed[i]) & _U64
        b = (b + seed[i + 1]) & _U64
        c = (c + seed[i + 2]) & _U64
        d = (d + seed[i + 3]) & _U64
        e = (e + seed[i + 4]) & _U64
        f = (f + seed[i + 5]) & _U64
        g = (g + seed[i + 6]) & _U64
        h = (h + seed[i + 7]) & _U64
        a, b, c, d, e, f, g, h = _isaac64_mix(a, b, c, d, e, f, g, h)
        mm[i : i + 8] = [a, b, c, d, e, f, g, h]
    for i in range(0, 256, 8):
        a = (a + mm[i]) & _U64
        b = (b + mm[i + 1]) & _U64
        c = (c + mm[i + 2]) & _U64
        d = (d + mm[i + 3]) & _U64
        e = (e + mm[i + 4]) & _U64
        f = (f + mm[i + 5]) & _U64
        g = (g + mm[i + 6]) & _U64
        h = (h + mm[i + 7]) & _U64
        a, b, c, d, e, f, g, h = _isaac64_mix(a, b, c, d, e, f, g, h)
        mm[i : i + 8] = [a, b, c, d, e, f, g, h]

    state = {"aa": 0, "bb": 0, "cc": 0}

    def refill():
        state["cc"] = (state["cc"] + 1) & _U64
        state["bb"] = (state["bb"] + state["cc"]) & _U64
        aa, bb = state["aa"], state["bb"]
        for i in range(256):
            if i % 4 == 0:
                aa = ~(aa ^ ((aa << 21) & _U64)) & _U64
            elif i % 4 == 1:
                aa = (aa ^ (aa >> 5)) & _U64
            elif i % 4 == 2:
                aa = (aa ^ ((aa << 12) & _U64)) & _U64
            else:
                aa = (aa ^ (aa >> 33)) & _U64
            aa = (aa + mm[(i + 128) % 256]) & _U64
            x = mm[i]
            y = (mm[(x >> 3) % 256] + aa + bb) & _U64
            mm[i] = y
            bb = (mm[(y >> 11) % 256] + x) & _U64
            seed[i] = bb
        state["aa"], state["bb"] = aa, bb

    refill()
    rand_cnt = 255
    while True:
        result = seed[rand_cnt]
        if rand_cnt == 0:
            refill()
            rand_cnt = 255
        else:
            rand_cnt -= 1
        yield result


def _decrypt_head(path: Path, key: int, enc_len: int = ENC_LIMIT):
    size = path.stat().st_size
    span = min(size, enc_len) // 8 * 8
    if span <= 0:
        return
    with open(path, "r+b") as f:
        head = f.read(span)
        out = bytearray(span)
        i = 0
        for rand_number in _isaac64_keystream(key):
            if i >= span:
                break
            stream = rand_number.to_bytes(8, "big")
            for j in range(8):
                if i + j >= span:
                    break
                out[i + j] = head[i + j] ^ stream[j]
            i += 8
        f.seek(0)
        f.write(bytes(out))


def validate_video(path: Path) -> bool:
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
        if proc.returncode == 0:
            return bool(proc.stdout.strip())
    except FileNotFoundError:
        with open(path, "rb") as f:
            head = f.read(8)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except Exception as exc:
        logging.warning("ffprobe 校验异常: %s", exc)
    return False


def probe_wechat(url: str, cookie: str) -> dict:
    vid = wechat_id(url)
    info = {
        "id": vid,
        "title": f"微信视频号 {vid}",
        "uploader": "",
        "duration": None,
        "thumbnail": "",
        "platform": "wechat",
        "webpage_url": url,
    }
    if not cookie:
        info["need_cookie"] = True
        return info
    eid, token = _parse_share(url, cookie)
    feed = _feed_info(eid, token)
    info.update(_feed_meta(feed))
    info["need_cookie"] = False
    seen = set()
    formats = []
    for item in _walk_collect_video_media(feed):
        url = item.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        formats.append(
            {
                "id": str(len(formats) + 1),
                "label": "原画" if len(formats) == 0 else f"备选{len(formats)}",
                "height": None,
                "filesize": None,
                "duration": info.get("duration"),
            }
        )
    info["formats"] = formats or [{"id": "best", "label": "原画", "height": None, "filesize": None, "duration": info.get("duration")}]
    return info


def download_wechat(
    url: str,
    target_dir: str,
    cookie: str,
    progress_hook: ProgressHook = None,
) -> dict:
    if not cookie:
        raise WeChatError("请先在设置中粘贴腾讯元宝 Cookie")

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    vid = wechat_id(url)
    share_url = extract_wechat_url(url) or url

    def hook(payload: dict):
        if progress_hook:
            try:
                progress_hook(payload)
            except Exception:
                pass

    eid, token = _parse_share(share_url, cookie)
    hook({"status": "resolving", "message": "已解析分享链接，正在取直链"})
    feed = _feed_info(eid, token)
    meta = _feed_meta(feed)
    seen = set()
    candidates: List[dict] = []
    for item in _walk_collect_video_media(feed):
        if item["url"] not in seen:
            seen.add(item["url"])
            candidates.append(item)
    if not candidates:
        raise WeChatError("解析成功但未找到视频流")

    final_path = target / f"{vid}.mp4"
    errors = []
    for cand in candidates:
        media_url, decode_key = cand["url"], cand["decode_key"]
        try:
            if media_url.lower().split("?")[0].endswith(".m3u8"):
                proc = subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        media_url,
                        "-c",
                        "copy",
                        "-bsf:a",
                        "aac_adtstoasc",
                        str(final_path),
                    ],
                    capture_output=True,
                    timeout=600,
                )
                if proc.returncode != 0:
                    raise RuntimeError("m3u8 下载失败")
            else:
                with requests.get(
                    media_url,
                    stream=True,
                    timeout=(10, 60),
                    headers={"User-Agent": MOBILE_UA, "Referer": "https://weixin.qq.com/"},
                ) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    hook(
                        {
                            "status": "downloading",
                            "downloaded_bytes": 0,
                            "total_bytes": total or None,
                        }
                    )
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
            if not validate_video(final_path) and decode_key:
                _decrypt_head(final_path, decode_key)
            if validate_video(final_path):
                hook({"status": "finished"})
                info = {"id": vid, "platform": "wechat", "webpage_url": share_url}
                info.update(meta)
                return {"file_path": str(final_path), "info": info}
            errors.append("候选源校验失败")
        except Exception as exc:
            errors.append(str(exc)[:160])
        finally:
            if final_path.exists() and not validate_video(final_path):
                final_path.unlink(missing_ok=True)
    raise WeChatError("下载失败：" + "；".join(errors[-2:]))

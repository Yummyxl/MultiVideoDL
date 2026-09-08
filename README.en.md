# MultiVideoDL (WeChat Channels / Bilibili / Xiaohongshu / Douyin)

Local web tool: paste a share link or share text, then download the video to `downloads/` on this machine.

Opening the HTML file in a browser is not enough. Cross-origin limits and login cookies require this local server.

Chinese version: [README.md](README.md)

## Start (foreground)

```bash
./start.sh
```

The script stays in the current terminal and opens `http://127.0.0.1:8765/`. Stop it with **Ctrl+C** in that terminal.

You can also run:

```bash
python3 server.py
```

Dependencies: Python 3, `requests`, `yt-dlp`, `ffmpeg`.

## Platforms

- **WeChat Channels**: paste a Yuanbao cookie in Cookie Settings (sign in at [yuanbao.tencent.com](https://yuanbao.tencent.com), then copy the full Cookie header from DevTools).
- **Bilibili**: usually works without a cookie. If the site rate-limits you or you need a higher quality, paste a `www.bilibili.com` cookie.
- **Xiaohongshu**: public pages may download directly. If parsing fails, paste a `xiaohongshu.com` cookie.
- **Douyin**: supports `v.douyin.com` short links and `www.douyin.com/video/...`. Direct-link parsing is tried first; if that fails, paste a `douyin.com` cookie.

Flow: paste a link → click **Parse** → only the qualities this video actually has are listed (size and duration included). Hover a quality to select it and show a download button; click that button once to start. No quality list is shown before parsing. On touch screens the download button stays visible.

Cookies for all four platforms are entered and saved on the home page. Reloading the page fills in saved cookies so you can view or edit them. Clear a field and save to delete it. Cookies are stored in `data/settings.json` and must not be committed.

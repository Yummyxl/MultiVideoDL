# 视频下载器（视频号 / B 站 / 小红书 / 抖音）

English version: [README.en.md](README.en.md)

本地网页工具：粘贴分享链接或分享文案，下载到本机 `downloads/`。

浏览器单独打开 HTML 过不了跨域和登录态，所以页面必须配这个本机服务。

## 启动（前台运行）

```bash
cd /Users/unicorn/aiprojects/shipinxiazai
./start.sh
```

脚本会占住当前终端，浏览器打开 `http://127.0.0.1:8765/`。要停止就在这个终端按 **Ctrl+C**。

也可以直接：

```bash
python3 server.py
```

依赖：Python 3、`requests`、`yt-dlp`、`ffmpeg`。

## 平台

- **微信视频号**：页面「Cookie 设置」里粘贴腾讯元宝 Cookie（登录 [yuanbao.tencent.com](https://yuanbao.tencent.com) → 开发者工具 → 任意请求的 Cookie 整行）。
- **B 站**：一般不用填；风控或要更高清晰度时，粘贴 `www.bilibili.com` 的 Cookie。
- **小红书**：公开页能解析就直接下；失败时粘贴 `xiaohongshu.com` 的 Cookie。
- **抖音**：支持 `v.douyin.com` 短链和 `www.douyin.com/video/...`。优先走直链解析，失败再粘贴 `douyin.com` Cookie。

交互：粘贴链接 → 点「解析链接」→ 只显示这条视频真实有的清晰度（含体积和时长）。鼠标移到某档会选中并出现下载按钮，点一次下载才会开始。未解析前没有清晰度可选。触摸屏会常显下载按钮。

四个平台的 Cookie 都在首页填写并保存。打开页面会载入已保存内容，可直接查看或修改；清空某一项再保存即可删除。内容写在 `data/settings.json`，不要提交到 git。

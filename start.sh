#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

echo "视频下载器（前台运行）"
echo "目录：$(pwd)"
echo "地址：http://127.0.0.1:${PORT:-8765}/"
echo "停止：在这个终端按 Ctrl+C"
echo

exec python3 server.py

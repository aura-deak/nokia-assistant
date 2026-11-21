#!/bin/sh
# 启动诺基亚助手（默认 8000 端口，可用 PORT 环境变量覆盖）
cd "$(dirname "$0")"
exec .venv/bin/python app.py

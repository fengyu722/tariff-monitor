#!/usr/bin/env bash
# 本地手动推送（webhook 从环境变量读取，不写死在文件里，push 到 GitHub 安全）
# 用法：
#   XIZHI_WEBHOOK="你的地址" bash scripts/notify_local.sh

if [ -z "$XIZHI_WEBHOOK" ]; then
  echo "请先设置 XIZHI_WEBHOOK 环境变量，例如："
  echo '  XIZHI_WEBHOOK="https://xizhi.qqoq.net/XZxxx.send" bash scripts/notify_local.sh'
  exit 1
fi
python3 scripts/notify.py

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 data/history.json，把上新/改价/下架推送到微信（息知）、钉钉、飞书、企业微信。

用法（GitHub Actions 里自动调用）：
  python3 scripts/notify.py

支持的 webhook 环境变量（在仓库 Settings → Secrets and variables → Actions → Secrets 里配置）：
  XIZHI_WEBHOOK      息知 webhook 完整地址，如 https://xizhi.qqoq.net/XZxxx.send
  DINGTALK_WEBHOOK   钉钉机器人 webhook 完整地址
  FEISHU_WEBHOOK     飞书自定义机器人 webhook 完整地址
  WECOM_WEBHOOK      企业微信群机器人 webhook 完整地址

四者可任意组合，没配置的自动跳过；都没配置时只打印摘要到日志，不报错。
无变更时不推送（避免每天骚扰）。如需每天播报，设 ALWAYS_NOTIFY=1。
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.parse

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "history.json")

ACTION_ICON = {"新增": "🆕", "改价": "💰", "变更": "✏️", "下架": "❌"}


def load_events():
    if not os.path.exists(DATA_FILE):
        return [], ""
    with open(DATA_FILE, encoding="utf-8") as f:
        j = json.load(f)
    return j.get("events", []), j.get("updated", "")


def group(events):
    g = {"新增": [], "改价": [], "变更": [], "下架": []}
    for e in events:
        a = e.get("action")
        if a in g:
            g[a].append(e)
    return g


def build_text(g, updated):
    total = sum(len(v) for v in g.values())
    today = datetime.date.today().isoformat()
    lines = [
        f"## 📢 资费变更日报 · {today}",
        f"> 更新时间：{updated or today}　|　共 **{total}** 项变更",
        "",
    ]
    if not total:
        lines.append("今日无上新、改价或下架。")
        return "\n".join(lines), total

    for action in ("新增", "改价", "变更", "下架"):
        items = g[action]
        if not items:
            continue
        icon = ACTION_ICON[action]
        lines.append(f"### {icon} {action}（{len(items)}）")
        for e in items[:12]:                      # 每类最多 12 条，避免消息过长
            name = e.get("name", "")
            prov = e.get("province", "")
            if action == "改价":
                lines.append(
                    f"- {name}（{prov}）：¥{e.get('old_price')} → **¥{e.get('new_price')}**"
                )
            elif action == "变更":
                fields = "、".join(e.get("fields", []))
                lines.append(f"- {name}（{prov}）：{fields} 有调整")
            else:
                lines.append(f"- {name}（{prov}）· ¥{e.get('price')}")
        if len(items) > 12:
            lines.append(f"- …另有 {len(items) - 12} 条，详见仓库 history.json")
        lines.append("")

    lines.append("---")
    lines.append("由 GitHub Actions 自动推送")
    return "\n".join(lines), total


def post(url, payload):
    if not url:
        return None
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "tariff-bot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:                        # noqa: BLE001
        return f"ERROR: {e}"


def post_xizhi(url, title, content):
    """息知：GET {webhook}?title=…&content=…，content 支持 Markdown。"""
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    full = url + sep + urllib.parse.urlencode({"title": title, "content": content})
    try:
        req = urllib.request.Request(full, headers={"User-Agent": "tariff-bot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:                        # noqa: BLE001
        return f"ERROR: {e}"


def main():
    events, updated = load_events()
    g = group(events)
    text, total = build_text(g, updated)
    print(text)

    if total == 0 and os.environ.get("ALWAYS_NOTIFY") != "1":
        print("[info] 无变更，跳过推送（设 ALWAYS_NOTIFY=1 可强制播报）")
        return

    title = f"资费变更日报 · {datetime.date.today().isoformat()}"

    # 息知（微信推送）：GET title + content
    xizhi = os.environ.get("XIZHI_WEBHOOK", "")
    if xizhi:
        r = post_xizhi(xizhi, title, text)
        print("[xizhi]", r or "ok")

    # 钉钉：markdown 消息
    ding = os.environ.get("DINGTALK_WEBHOOK", "")
    if ding:
        r = post(ding, {"msgtype": "markdown", "markdown": {"title": title, "text": text}})
        print("[dingtalk]", r or "ok")

    # 飞书：interactive 卡片
    feishu = os.environ.get("FEISHU_WEBHOOK", "")
    if feishu:
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
                "elements": [{"tag": "markdown", "content": text}],
            },
        }
        r = post(feishu, card)
        print("[feishu]", r or "ok")

    # 企业微信：markdown 消息
    wecom = os.environ.get("WECOM_WEBHOOK", "")
    if wecom:
        r = post(wecom, {"msgtype": "markdown", "markdown": {"content": text}})
        print("[wecom]", r or "ok")

    if not (xizhi or ding or feishu or wecom):
        print("[info] 未配置任何 webhook，仅打印摘要")


if __name__ == "__main__":
    main()

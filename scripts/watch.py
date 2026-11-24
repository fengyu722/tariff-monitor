#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监听指定运营商+省份的上新 / 改价 / 下架，写入 data/watch/history.json。

详细数据不进本仓库，只保存精简快照和变更事件；通知交给 scripts/notify.py。
首次运行只建立基线，不会把历史存量当成“新增”刷屏。
"""

import datetime
import hashlib
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "watch")
EXT_BASE = "https://rdtscx.github.io/tariff/data/"

TARGETS = [
    {"key": "unicom_sichuan", "label": "四川联通", "source": "local"},
    {"key": "mobile_sichuan", "label": "四川移动", "source": "mobile", "prov": "sichuan"},
    {"key": "mobile_guizhou", "label": "贵州移动", "source": "mobile", "prov": "guizhou"},
]


def now_cn():
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tariff-watch/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def first_number(s):
    m = re.search(r"(\d+(?:\.\d+)?)", str(s or ""))
    return float(m.group(1)) if m else 0


def snapshot_path(target):
    return os.path.join(OUT, target["key"] + ".json")


def read_local(target):
    """本仓库自抓的四川联通数据。"""
    j = load_json(os.path.join(ROOT, "data", "latest.json"), {})
    items = [x for x in j.get("items", []) if x.get("province") == "四川"]
    return items, j.get("updated", "")


def read_mobile(target):
    """外部移动站：先看 latest.json 的时间戳，没变就不下载 2MB 明细。"""
    meta = fetch_json(EXT_BASE + "latest.json?_t=" + str(int(datetime.datetime.now().timestamp())))
    sec = next((s for s in meta.get("sections", []) if s.get("section") == target["prov"]), {})
    updated = sec.get("updated", "")
    prev = load_json(snapshot_path(target), {})
    if prev.get("updated") and prev["updated"] == updated:
        return None, updated

    j = fetch_json(EXT_BASE + target["prov"] + ".json?_t=" + str(int(datetime.datetime.now().timestamp())))
    items = []
    for it in j.get("items", []):
        f = it.get("fields", {}) or {}
        name = it.get("name", "")
        scope = " ".join([str(f.get("归属", "")), str(f.get("适用范围", ""))])
        owner = "政企" if re.search(r"政企|集团|企业|单位|商务", scope) else "个人"
        uid = hashlib.md5((name + "|" + str(f.get("方案编号", ""))).encode("utf-8")).hexdigest()
        items.append({
            "id": uid,
            "name": name,
            "price": first_number(f.get("资费标准")),
            "flow": f.get("国内通用流量") or "—",
            "voice": f.get("国内通话") or "—",
            "owner": owner,
            "type": f.get("资费类型") or "—",
            "updated": updated,
        })
    return items, updated


def diff_items(prev, cur, label):
    """返回新增/改价/变更/下架事件。"""
    p = {x["id"]: x for x in prev}
    c = {x["id"]: x for x in cur}
    events = []
    for uid, x in c.items():
        old = p.get(uid)
        if not old:
            events.append({"action": "新增", "name": x["name"], "province": label,
                           "price": x["price"], "time": now_cn()})
            continue
        if x.get("price") != old.get("price"):
            events.append({"action": "改价", "name": x["name"], "province": label,
                           "old_price": old.get("price"), "new_price": x.get("price"),
                           "price": x.get("price"), "time": now_cn()})
            continue
        changed = [k for k in ("name", "flow", "voice", "owner", "type") if x.get(k) != old.get(k)]
        if changed:
            cn = {"name": "名称", "flow": "流量", "voice": "通话", "owner": "归属", "type": "类型"}
            events.append({"action": "变更", "name": x["name"], "province": label,
                           "price": x.get("price"), "fields": [cn[k] for k in changed], "time": now_cn()})
    for uid, old in p.items():
        if uid not in c:
            events.append({"action": "下架", "name": old["name"], "province": label,
                           "price": old.get("price"), "time": now_cn()})
    return events


def main():
    os.makedirs(OUT, exist_ok=True)
    all_events = []
    for target in TARGETS:
        snap = load_json(snapshot_path(target), {})
        try:
            if target["source"] == "local":
                items, updated = read_local(target)
            else:
                items, updated = read_mobile(target)
        except Exception as exc:
            print("[warn] %s 读取失败：%s" % (target["label"], exc))
            continue

        if items is None:  # 时间戳未变，跳过
            print("[skip] %s 无更新（%s）" % (target["label"], updated))
            continue

        if snap.get("items") is None:  # 首次只建基线
            print("[seed] %s 建立基线：%d 条" % (target["label"], len(items)))
        else:
            ev = diff_items(snap.get("items", []), items, target["label"])
            all_events.extend(ev)
            print("[check] %s %d 条，变更 %d 项" % (target["label"], len(items), len(ev)))

        save_json(snapshot_path(target), {"label": target["label"], "updated": updated,
                                          "total": len(items), "items": items})

    hist = load_json(os.path.join(OUT, "history.json"), {"events": []})
    events = (hist.get("events", []) + all_events)[-500:]
    save_json(os.path.join(OUT, "history.json"), {"events": events, "updated": now_cn()})
    print("[done] 本轮新增 %d 项事件，累计保留 %d 项" % (len(all_events), len(events)))


if __name__ == "__main__":
    main()

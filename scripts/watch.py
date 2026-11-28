#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监听指定运营商+省份的上新 / 改价 / 下架，写入 data/watch/history.json。

三个目标统一使用 rdtscx/tariff 发布的全量快照，避免本地接口随机子集造成误报。
先用 latest.json 的时间戳判断是否需要下载明细；详细数据不进本仓库，
只保存精简快照和变更事件；通知交给 scripts/notify.py。
"""

import datetime
import hashlib
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "watch")
MOBILE_BASE = "https://rdtscx.github.io/tariff/data/"
UNICOM_BASE = "https://rdtscx.github.io/tariff/unicom/data/"

TARGETS = [
    {"key": "unicom_sichuan", "label": "四川联通", "base": UNICOM_BASE, "prov": "sichuan"},
    {"key": "mobile_sichuan", "label": "四川移动", "base": MOBILE_BASE, "prov": "sichuan"},
    {"key": "mobile_guizhou", "label": "贵州移动", "base": MOBILE_BASE, "prov": "guizhou"},
]

FIELD_CN = {
    "title": "业务名称", "name": "业务名称", "fee": "资费标准", "feesStandard": "资费标准",
    "feeUnit": "资费单位", "firstLevel": "归属", "secondLevel": "资费类型",
    "codeType": "资费类型", "reportNo": "方案编号", "useScope": "适用范围",
    "saleChnl": "销售渠道", "onDate": "上线日期", "offDate": "下线日期",
    "validPeriod": "有效期限", "onlinePeriod": "在网要求", "unsubscribe": "退订方式",
    "responsibility": "违约责任", "extraFees": "超出资费说明",
    "serviceContent": "其他服务内容", "otherNotes": "其他说明",
    "minute": "国内通话", "voice": "国内通话", "commonData": "国内通用流量",
    "dataUnit": "流量单位", "sms": "国内短信", "orientTraffic": "定向流量",
    "broadBand": "宽带", "iptv": "移动高清",
}


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


def cn_fields(o):
    """把源站的英文字段统一成中文键名，并合并数值+单位。"""
    if not isinstance(o, dict):
        return {}
    r, fee, fee_u, dt, dt_u = {}, "", "", "", ""
    for k, v in o.items():
        if v in (None, ""):
            continue
        ck = FIELD_CN.get(k, k)
        if k == "feeUnit":
            fee_u = str(v)
            continue
        if k == "dataUnit":
            dt_u = str(v)
            continue
        if ck == "资费标准":
            fee = str(v)
            continue
        if ck == "国内通用流量":
            dt = str(v)
            continue
        if ck not in r:
            r[ck] = str(v)
    if fee:
        r["资费标准"] = fee if (re.search(r"[元分角]", fee) or not fee_u) else fee + fee_u
    if dt:
        r["国内通用流量"] = dt if re.search(r"GB|MB|TB|KB", dt, re.I) else dt + dt_u
    return r


def snapshot_path(target):
    return os.path.join(OUT, target["key"] + ".json")


def read_external(target):
    """先看 latest.json 的时间戳，没变就不下载 1~3MB 明细。"""
    ts = str(int(datetime.datetime.now().timestamp()))
    meta = fetch_json(target["base"] + "latest.json?_t=" + ts)
    sec = next((s for s in meta.get("sections", []) if s.get("section") == target["prov"]), {})
    updated = sec.get("updated", "")
    prev = load_json(snapshot_path(target), {})
    if prev.get("updated") and prev["updated"] == updated:
        return None, updated

    j = fetch_json(target["base"] + target["prov"] + ".json?_t=" + ts)
    is_mobile = "mobile" in target["key"]
    items = []
    for it in j.get("items", []):
        raw = it.get("fields", {}) if is_mobile else it.get("detail", {})
        f = cn_fields(raw)
        name = it.get("name") or f.get("业务名称") or ""
        scope = " ".join([str(f.get("归属", "")), str(f.get("适用范围", ""))])
        owner = "政企" if re.search(r"政企|集团|企业|单位|商务", scope) else "个人"
        if is_mobile:
            uid = hashlib.md5((name + "|" + str(f.get("方案编号", ""))).encode("utf-8")).hexdigest()
        else:
            uid = str(it.get("id") or hashlib.md5(
                (name + "|" + str(f.get("方案编号", ""))).encode("utf-8")).hexdigest())
        items.append({
            "id": uid,
            "name": name,
            "price": first_number(f.get("资费标准")),
            "flow": f.get("国内通用流量") or "—",
            "voice": f.get("国内通话") or "—",
            "owner": owner,
            "type": f.get("资费类型") or it.get("secondLevel") or "—",
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
            items, updated = read_external(target)
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
            # 保护：单轮变化超过 25% 且不少于 80 条，判定为源站数据波动，
            # 不推送也不更新基线，等下一轮源站恢复后再比对。
            limit = max(80, int(0.25 * max(len(snap.get("items", [])), len(items))))
            if len(ev) > limit:
                print("[guard] %s 本轮 %d 条变更（阈值 %d），疑似源站波动，跳过推送与基线更新" %
                      (target["label"], len(ev), limit))
                continue
            all_events.extend(ev)
            print("[check] %s %d 条，变更 %d 项" % (target["label"], len(items), len(ev)))

        save_json(snapshot_path(target), {"label": target["label"], "updated": updated,
                                          "total": len(items), "items": items})

    hist = load_json(os.path.join(OUT, "history.json"), {"events": []})
    events = (hist.get("events", []) + all_events)[-500:]
    save_json(os.path.join(OUT, "history.json"), {"events": events, "updated": now_cn()})
    # pending.json 只放本轮新增，通知脚本读它，避免历史被反复推送
    save_json(os.path.join(OUT, "pending.json"), {"events": all_events, "updated": now_cn()})
    print("[done] 本轮新增 %d 项事件，累计保留 %d 项" % (len(all_events), len(events)))


if __name__ == "__main__":
    main()

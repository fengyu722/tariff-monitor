#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资费抓取脚本（零第三方依赖，只用标准库）。

设计目标
--------
1. 优先请求真实接口；失败、限流或返回空时，自动降级为「演示数据」，
   保证第一次 push 到 GitHub 后页面就有内容，不会白屏。
2. 输出结构与前端 index.html 约定一致：
     data/latest.json   总览 + 全部条目
     data/<省拼音>.json 分省条目
     data/history.json  简易变更历史（与上一轮对比）
3. 结果里用 source 字段标记 "live" / "demo"，方便你判断接口是否接通。

接入真实数据
------------
把 fetch_live() 换成你自己的逻辑即可，只要最终返回
list[dict]，每条至少包含 name / price / province 三个字段。
字段说明见 RECORD_TEMPLATE。
"""

import json
import os
import re
import sys
import time
import hashlib
import datetime
import urllib.request
import urllib.error

# ---------------------------------------------------------------- 配置
BASE_API = "https://m.client.10010.com/servicequerybusiness/queryTariffNew/"
HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "http://img.client.10010.com/zifeizhuanquwt/index.html",
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 20
RETRIES = 3

# ============================================================
# ★★★ 要抓哪个省？改这一行就行 ★★★
#   填中文省名，如：四川、北京、广东、浙江、上海 …
#   也可在 GitHub 仓库 Settings → Variables 加 TARGET_PROVINCE=四川，免改代码
#   想抓全网则填：全网(全国)
# ============================================================
TARGET_PROVINCE = (os.environ.get("TARGET_PROVINCE") or "四川").strip()

PROVINCES = [
    ("quanguo", "全网(全国)"), ("beijing", "北京"), ("tianjin", "天津"),
    ("hebei", "河北"), ("shanxi", "山西"), ("neimenggu", "内蒙古"),
    ("liaoning", "辽宁"), ("jilin", "吉林"), ("heilongjiang", "黑龙江"),
    ("shanghai", "上海"), ("jiangsu", "江苏"), ("zhejiang", "浙江"),
    ("anhui", "安徽"), ("fujian", "福建"), ("jiangxi", "江西"),
    ("shandong", "山东"), ("henan", "河南"), ("hubei", "湖北"),
    ("hunan", "湖南"), ("guangdong", "广东"), ("guangxi", "广西"),
    ("hainan", "海南"), ("chongqing", "重庆"), ("sichuan", "四川"),
    ("guizhou", "贵州"), ("yunnan", "云南"), ("xizang", "西藏"),
    ("shaanxi", "陕西"), ("gansu", "甘肃"), ("qinghai", "青海"),
    ("ningxia", "宁夏"), ("xinjiang", "新疆"),
]

RECORD_TEMPLATE = {
    "id": "",           # 唯一标识，用于变更对比
    "name": "",         # 套餐名称（必填）
    "price": 0,         # 月费，数字（必填，元）
    "flow": "",         # 流量，如 "30GB"
    "voice": "",        # 通话，如 "200分钟"
    "province": "",     # 省份中文名
    "owner": "个人",    # 归属：个人 / 政企
    "type": "套餐",     # 类型：套餐 / 加油包 / 宽带 等
    "updated": "",      # 上架/更新时间
}

NAME_KEYS = ("name", "title", "tariffName", "goodsName", "productName", "pkgName", "套餐名称")
PRICE_KEYS = ("price", "fee", "monthlyFee", "tariffFee", "priceValue", "salePrice", "月费")
FLOW_KEYS = ("flow", "data", "traffic", "netFlow", "流量")
VOICE_KEYS = ("voice", "call", "talkTime", "voiceDuration", "通话")
TYPE_KEYS = ("type", "category", "bizType", "类型")


# ---------------------------------------------------------------- HTTP
def api(path, params=None):
    """带重试的 GET JSON。params 可为 dict 或已编码的查询字符串。"""
    url = BASE_API + path
    if params:
        if isinstance(params, str):
            url += "?" + params.lstrip("?")
        else:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
    last = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read().decode("utf-8", "ignore")
            return json.loads(raw)
        except Exception as e:                      # noqa: BLE001
            last = e
            if i < RETRIES - 1:
                time.sleep(1.5 * (i + 1))
    print(f"[warn] GET {path} 失败：{last}", file=sys.stderr)
    return None


def first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def first_num(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            m = re.search(r"\d+(?:\.\d+)?", v)
            if m:
                return float(m.group())
    return None


def looks_like_tariff(name):
    """过滤掉菜单标题、栏目名等明显不是套餐的字符串。"""
    if not (2 <= len(name) <= 60):
        return False
    if name in ("全部", "更多", "热门", "推荐", "首页", "详情", "套餐", "资费"):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5]", name)) and bool(
        re.search(r"套餐|卡|流量|语音|宽带|融合|权益|包|元|GB|G|M", name, re.I)
    )


def walk(node, out, province, owner, depth=0):
    """递归从任意 JSON 结构里挖出「名称 + 价格」的对象。"""
    if depth > 12:
        return
    if isinstance(node, dict):
        name = first_str(node, NAME_KEYS)
        price = first_num(node, PRICE_KEYS)
        if name and price is not None and looks_like_tariff(name):
            rec = dict(RECORD_TEMPLATE)
            rec.update({
                "id": hashlib.md5((province + name + str(price)).encode()).hexdigest()[:16],
                "name": name,
                "price": price,
                "flow": first_str(node, FLOW_KEYS),
                "voice": first_str(node, VOICE_KEYS),
                "province": province,
                "owner": owner,
                "type": first_str(node, TYPE_KEYS) or "套餐",
                "updated": datetime.datetime.now().strftime("%Y-%m-%d"),
            })
            out.append(rec)
        for v in node.values():
            walk(v, out, province, owner, depth + 1)
    elif isinstance(node, list):
        for v in node:
            walk(v, out, province, owner, depth + 1)


def dedup(rows):
    seen, out = set(), []
    for r in rows:
        key = (r["province"], r["name"], r["price"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------- 抓取
CN2CODE = {name: key for key, name in PROVINCES}


def find_province(idx, target):
    """从 indexData 的 provinceList 里找目标省份的 (provCode, cityCode, attr)。"""
    prov_list = (idx.get("data") or {}).get("provinceList") or []
    for p in prov_list:
        pname = p.get("provName", "")
        if pname == target or pname == target + "省" or pname == target + "市":
            return p.get("provCode", ""), p.get("cityCode", ""), 2
    return None


def fetch_live():
    """按 TARGET_PROVINCE 抓真实接口。返回 (rows, source)；失败返回 ([], "demo")。"""
    rows = []
    try:
        idx = api("indexData")
        if not idx:
            return [], "demo"

        # 1) 优先：带省份参数的板块抓取（真实联通接口）
        prov = find_province(idx, TARGET_PROVINCE)
        if prov:
            prov_id, city_id, attr = prov
            print(f"[info] 抓取 {TARGET_PROVINCE}: provCode={prov_id} cityCode={city_id} attr={attr}")
            seen = set()
            for pn in range(1, 21):                 # 最多 20 页，每轮随机子集，累积去重
                q = (
                    "provinceId=%s&cityId=%s&tariffAttributes=%s&firstLevel=1&secondLevel=1001"
                    "&name=&startFee=0&endFee=999999&pageNum=%d&pageSize=500"
                ) % (prov_id, city_id, attr, pn)
                d = api("TariffMenuDataRetrieval", q)
                lst = (d.get("data") or {}).get("tariffList") or []
                if not lst:
                    break
                for x in lst:
                    xid = str(x.get("id", ""))
                    if not xid or xid in seen:
                        continue
                    seen.add(xid)
                    rec = dict(RECORD_TEMPLATE)
                    rec.update({
                        "id": xid,
                        "name": x.get("title", "") or x.get("name", ""),
                        "price": first_num({"price": x.get("fee", "")}, ("price",)) or 0,
                        "flow": str(x.get("flow") or x.get("data") or ""),
                        "voice": str(x.get("voice") or x.get("call") or ""),
                        "province": TARGET_PROVINCE,
                        "owner": "政企" if attr == 2 and "政企" in str(x.get("firstLevel", "")) else "个人",
                        "type": str(x.get("secondLevel", "") or "套餐"),
                        "updated": datetime.date.today().isoformat(),
                    })
                    if rec["name"]:
                        rows.append(rec)
            if rows:
                return dedup(rows), "live"

        # 2) 兜底：若省份表结构不同，递归挖 indexData 里的套餐
        walk(idx, rows, TARGET_PROVINCE, "个人")
        if rows:
            return dedup(rows), "live"
    except Exception as e:                          # noqa: BLE001
        print(f"[warn] 真实接口解析异常：{e}", file=sys.stderr)

    return [], "demo"


def fetch_demo(n=160):
    """演示数据：接口未接通时保证页面有内容。全部归属 TARGET_PROVINCE。"""
    brands = ["冰激凌", "腾云", "畅享", "沃派", "智慧沃家", "流量王", "大王卡", "嫦娥", "星耀"]
    suffixes = ["套餐", "融合套餐", "畅聊版", "青春版", "家庭版", "尊享版"]
    flows = ["30GB", "60GB", "100GB", "200GB", "不限量", "20GB+定向30GB"]
    voices = ["100分钟", "200分钟", "500分钟", "1000分钟", "不限量"]
    prices = [0, 19, 29, 39, 59, 79, 99, 129, 199, 299]
    owners = ["个人", "个人", "个人", "政企"]
    types = ["套餐", "套餐", "加油包", "宽带"]
    prov_key = CN2CODE.get(TARGET_PROVINCE, "sichuan")
    rows = []
    for i in range(n):
        name = f"{brands[i % len(brands)]}{suffixes[(i // 3) % len(suffixes)]}"
        price = prices[i % len(prices)]
        rec = dict(RECORD_TEMPLATE)
        rec.update({
            "id": hashlib.md5(f"{prov_key}{name}{price}{i}".encode()).hexdigest()[:16],
            "name": name,
            "price": price,
            "flow": flows[i % len(flows)],
            "voice": voices[(i // 2) % len(voices)],
            "province": TARGET_PROVINCE,
            "owner": owners[i % len(owners)],
            "type": types[i % len(types)],
            "updated": (datetime.date.today() - datetime.timedelta(days=i % 30)).isoformat(),
        })
        rows.append(rec)
    return dedup(rows)


# ---------------------------------------------------------------- 落盘
def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def build_history(rows, prev_rows, limit=400):
    """与上一轮 latest.json 对比，识别 新增 / 改价 / 内容变更 / 下架。"""
    def key(r):
        return (r.get("province", ""), r.get("name", ""), str(r.get("price")))

    def pair(r):
        return (r.get("province", ""), r.get("name", ""))

    prev = {key(r): r for r in prev_rows}
    now = {key(r): r for r in rows}
    prev_by_pair = {}
    for r in prev_rows:
        prev_by_pair.setdefault(pair(r), []).append(r)

    events = []
    matched_prev = set()

    for k, r in now.items():
        if k in prev:
            matched_prev.add(k)
            # 价格相同，检查流量/通话/归属/类型等字段是否变化
            pr = prev[k]
            changed = [f for f in ("flow", "voice", "owner", "type")
                       if pr.get(f) != r.get(f)]
            if changed:
                events.append({
                    "action": "变更", "fields": changed,
                    "old": {f: pr.get(f) for f in changed},
                    "new": {f: r.get(f) for f in changed},
                    "time": r.get("updated", ""), **r,
                })
            continue

        # 新条目：先找同名旧条目里未被匹配的，视为改价
        reused = None
        for pr in prev_by_pair.get(pair(r), []):
            pk = key(pr)
            if pk not in now and pk not in matched_prev:
                reused = pr
                matched_prev.add(pk)
                break
        if reused:
            events.append({
                "action": "改价",
                "old_price": reused.get("price"), "new_price": r.get("price"),
                "time": r.get("updated", ""), **r,
            })
        else:
            events.append({"action": "新增", "time": r.get("updated", ""), **r})

    # 剩下的旧条目 = 下架
    for k, r in prev.items():
        if k not in now and k not in matched_prev:
            events.append({"action": "下架", "time": datetime.date.today().isoformat(), **r})

    events.sort(key=lambda x: x.get("time", ""), reverse=True)
    return events[:limit]


def main():
    out_dir = os.environ.get("OUT_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
    out_dir = os.path.abspath(out_dir)

    prev_rows = []
    prev_path = os.path.join(out_dir, "latest.json")
    if os.path.exists(prev_path):
        try:
            prev_rows = json.load(open(prev_path, encoding="utf-8")).get("items", [])
        except Exception:                           # noqa: BLE001
            pass

    rows, source = fetch_live()
    if not rows:
        print("[info] 真实接口未返回数据，使用演示数据（把 fetch_live() 换成你的解析逻辑即可）")
        rows = fetch_demo()
        source = "demo"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_prov = {}
    for r in rows:
        by_prov.setdefault(r["province"], []).append(r)

    summary = {
        "updated": now,
        "source": source,
        "total": len(rows),
        "province_count": len([p for p in by_prov if p != "全网(全国)"]),
        "dist": [
            {"name": k, "total": len(v)}
            for k, v in sorted(by_prov.items(), key=lambda x: -len(x[1]))
        ],
        "items": rows,
    }
    write_json(os.path.join(out_dir, "latest.json"), summary)

    # 分省文件：按 PROVINCES 的拼音映射
    key_by_name = {name: key for key, name in PROVINCES}
    for prov_name, items in by_prov.items():
        key = key_by_name.get(prov_name)
        if not key:
            continue
        write_json(os.path.join(out_dir, key + ".json"), {
            "updated": now, "province": prov_name, "total": len(items), "items": items,
        })

    write_json(os.path.join(out_dir, "history.json"), {
        "updated": now, "total": len(prev_rows), "events": build_history(rows, prev_rows),
    })

    print(f"[ok] source={source} total={len(rows)} provinces={summary['province_count']} -> {out_dir}")


if __name__ == "__main__":
    main()

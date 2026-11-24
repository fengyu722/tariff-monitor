# 资费监控（纯 GitHub 实现）

零服务器方案：**GitHub Actions 抓数据 → 仓库存 JSON → GitHub Pages 托管页面**。

> **看板数据源**（页面顶部「数据源」下拉切换）：
> - `中国联通 · 全国` / `中国移动 · 全国`：直接读取 [rdtscx/tariff](https://github.com/rdtscx/tariff) 发布的 31 省 + 全网 JSON，页面内可随时切省份，数据由对方仓库每日自动更新。
> - `联通四川 · 本站实时`：本仓库 `scripts/fetch.py` 自抓的四川联通数据（`source=live`，首次实测 711 条），可用「一键抓取」触发更新。
>
> 本仓库的 Actions 只负责第三项；前两项不需要在本仓库配置任何密钥或变量。

## 目录结构

```
tariff-monitor/
├── .nojekyll                  # 让 Pages 直接托管 data/ 目录
├── .github/workflows/build.yml# 定时任务：抓取 + 提交
├── scripts/fetch.py           # 抓取脚本（标准库，无依赖）
├── index.html                 # 前端看板（单文件）
└── data/                      # 运行后自动生成
    ├── latest.json            # 总览 + 全部条目
    ├── history.json           # 变更历史
    └── <省拼音>.json          # 分省数据
```

## 三步上线

**1. 建仓库并开启 Pages**

```bash
cd tariff-monitor
git init && git add . && git commit -m init
git remote add origin https://github.com/fengyu722/tariff-monitor.git
git push -u origin main
```

仓库 **Settings → Pages → Build and deployment**：
- Source 选 `Deploy from a branch`
- Branch 选 `main` / `/(root)`，保存

**2. 改前端配置**

打开 `index.html`，把脚本开头的 `S.owner` 和 `S.repo` 改成你的用户名和仓库名（用于一键抓取）。

**3. 触发第一次抓取**

去 **Actions** 标签页 → 点 `Fetch Tariff` → `Run workflow`。
跑完会看到 `data/` 下生成 JSON，Pages 地址约 1 分钟后生效：
`https://<用户名>.github.io/tariff-monitor/`

## 切换省份（重要）

只改 `scripts/fetch.py` 顶部这一行即可：

```python
TARGET_PROVINCE = "四川"   # 改成想要的省：北京、广东、浙江、上海…或 "全网(全国)"
```

不想改代码也行：在仓库 **Settings → Variables** 新建 `TARGET_PROVINCE = 四川`，脚本会自动读取（环境变量优先级高于代码里的默认值）。

脚本会：
1. 先调 `indexData` 拿到该省的 `provCode / cityCode`（四川是 081 / 810）
2. 带省份参数分页请求 `TariffMenuDataRetrieval`，累积去重
3. 只输出该省数据到 `data/latest.json` 和 `data/sichuan.json`

前端页面的省份筛选、推送通知都会跟着这只省份走。

> ⚠️ 换省份后第一次跑，会把旧省数据全判成「下架」、新省全判成「新增」。建议换省时删掉 `data/` 目录重新抓，建立干净基线（就像我给你做的那样）。

## 数据从哪来

`scripts/fetch.py` 请求中国联通客户端公开接口 `m.client.10010.com/servicequerybusiness/queryTariffNew/`。

- 接口通了 → `latest.json` 里 `source` 为 `live`
- 接口不通/结构变化/限流 → 自动降级为演示数据，`source` 为 `demo`，页面照样能看

**接真实数据**：只需把 `fetch_live()` 改成你的解析逻辑，返回 `list[dict]`，每条至少含 `name` / `price` / `province`，可选 `flow` / `voice` / `owner` / `type` / `updated`。字段模板见脚本里的 `RECORD_TEMPLATE`。

## 定时与手动

- `build.yml` 的 `cron: '0 1 * * *'` 是 **UTC**，即北京时间每天 **09:00 抓取一次**
- 手动触发在 Actions 页面点按钮，或页面「一键抓取」用 PAT 调 API

## 上新/改价推送

每次抓取后，`scripts/notify.py` 会对比上一轮数据，识别出 **新增 / 改价 / 内容变更 / 下架** 四类变化，并推送到 webhook。

**配置渠道**：仓库 → Settings → Secrets and variables → Actions → **Secrets** → New repository secret，按需添加：

| Secret 名 | 渠道 | 获取方式 |
|---|---|---|
| `XIZHI_WEBHOOK` | 微信（息知） | 在 [息知](https://xz.qqoq.net) 创建推送，得到 `https://xizhi.qqoq.net/XZxxx.send` |
| `DINGTALK_WEBHOOK` | 钉钉 | 群设置 → 智能群助手 → 添加机器人 → 自定义，复制 webhook 地址 |
| `FEISHU_WEBHOOK` | 飞书 | 群设置 → 群机器人 → 添加自定义机器人，复制 webhook 地址 |
| `WECOM_WEBHOOK` | 企业微信 | 群右上角 → 群机器人 → 添加机器人，复制 webhook 地址 |

三个可任意组合，没配的自动跳过。**无变更时不推送**，避免每天骚扰；想每天固定播报，在 **Variables** 里加 `ALWAYS_NOTIFY=1`。

数据如果在体量闸门处被判定为缩水，本轮不会提交，也**不会推送通知**，避免误报。

推送内容示例：

```
## 📢 资费变更日报 · 2026-11-24
> 更新时间：2026-11-24 09:00:12　|　共 5 项变更

### 🆕 新增（1）
- 腾云青春版（江苏）· ¥0
### 💰 改价（1）
- 冰激凌青春版（上海）：¥129 → ¥99
### ❌ 下架（2）
- ...
```

前端页面的「资费变更时间线」也会同步展示 `history.json` 里的这些记录。

## 一键抓取的令牌

页面里的令牌只存浏览器本地（localStorage），不会上传。创建方式：
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens →
只选本仓库 → Repository permissions 里 **Actions 设为 Read and write**。

## 防抓崩

`build.yml` 里有「体量闸门」：本轮条数比上轮缩水超过 50% 就跳过提交，避免接口异常时用残缺数据覆盖线上。

## 本地预览

```bash
python3 scripts/fetch.py        # 生成 data/*.json
python3 -m http.server 8000     # 打开 http://localhost:8000
```

> 直接双击 `index.html` 用 `file://` 打开时，浏览器会因安全策略阻止 fetch，请用本地服务器预览。

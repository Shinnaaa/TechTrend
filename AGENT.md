# TechTrend — Agent Context

每次新会话先读此文件，再看代码。

---

## 项目是什么

自动化每日技术情报系统。每天从多个源抓取数据 → DeepSeek 分析总结 → PushPlus 推送微信 → 保存到 history/ → 同步到 Jekyll 网站。周日额外生成周报。

GitHub 仓库：https://github.com/Shinnaaa/TechTrend

---

## 文件职责

| 文件 | 职责 |
|------|------|
| `fetcher.py` | 抓取所有数据源，输出 `raw_intel.json`，维护 `seen_urls.json` 去重 |
| `summarizer.py` | 读 `raw_intel.json`，调 DeepSeek API，输出 `DAILY_REPORT.md` + `history/YYYY-MM-DD.md` |
| `pusher.py` | 读 `DAILY_REPORT.md`，通过 PushPlus 推送到微信 |
| `formatter.py` | 读 `DAILY_REPORT.md`，翻译三语（中/英/日），生成 Jekyll post 到 `_formatted/` |
| `weekly_report.py` | 读最近 7 天 history/，生成周报，推送 PushPlus + 保存 `history/weekly_*.md` |

## Workflow

- `daily-intel.yml`：每天 UTC 00:00（北京 08:00）按序跑 fetcher → summarizer → pusher → formatter，再同步到 `Shinnaaa/Shinnaaa.github.io` 的 `_posts/`
- `weekly-intel.yml`：每周日 UTC 01:00（北京 09:00）跑 weekly_report.py

---

## 数据源（fetcher.py）

| 源 | 方式 | 覆盖内容 |
|----|------|---------|
| GitHub Trending | 爬虫（Python/TypeScript/全语言） | 热门新项目 |
| HuggingFace Daily Papers | REST API | 当日学术论文 |
| HuggingFace Trending Models | REST API `sort=trending` | 热门模型（Hermes 类） |
| Hacker News | Algolia API | 技术热点讨论 |
| Reddit r/LocalLLaMA | Atom RSS（无需 key） | 本地 LLM 社区热帖 |
| Product Hunt | Atom RSS | 新产品 |

---

## GitHub Secrets

| Secret | 用途 |
|--------|------|
| `OPENAI_API_KEY` | DeepSeek 中转 API key |
| `OPENAI_BASE_URL` | 中转 API 的 base URL（非官方 OpenAI） |
| `OPENAI_MODEL` | 当前值：`deepseek-v4-flash`（2026-07 服务商改名，旧名称返回 400） |
| `PUSHPLUS_TOKEN` | PushPlus 微信推送 token |
| `SYNC_PAT` | 用于向 Shinnaaa.github.io 仓库写入的 GitHub PAT |

**注意**：`OPENAI_MODEL` 依赖服务商，服务商改模型名会直接导致 workflow 400 报错。

---

## 已知问题与历史决策

### 2026-07 模型名变更
服务商将模型名改为 `deepseek-v4-pro` / `deepseek-v4-flash`，旧名称全部 400。已在 Secret 中更新为 `deepseek-v4-flash`。

### 写作质量问题
每条 GitHub Trending 分析都套用"这不是又一个X，而是Y，直接攻击了Z的痛点"模板。
- 根因：`summarizer.py` 的格式提示写了"和哪个现有方案比有何不同"，模型据此生成对比结构
- 修复：在 `SYSTEM_PROMPT` 加了 BAD/GOOD 示例，格式提示改为"第一句直接说数字"，禁止列表加入该具体句式

### 漏掉热门模型（如 Hermes）
- 原因：HF 源只拉 Daily Papers（学术论文），模型发布不在此列
- 修复：加了 HF Trending Models 源（`sort=trending`）+ Reddit r/LocalLLaMA RSS

### 没有新内容仍然推送
- 修复：`summarizer.py` 新条目 < 5 时跳过生成（`MIN_NEW_ITEMS = 5`），`pusher.py` 和 `formatter.py` 检测到无报告文件时静默跳过

---

## Prompt 设计要点（summarizer.py）

- 角色：犀利、反套话的首席技术架构师，面向高级工程师
- 禁止：空话套话（"值得关注"、"推动生态"等）
- 禁止句式：`不是又一个X而是Y`、`直接攻击了X的痛点`、`核心差异在于`
- 格式要求：第一句给数字，对比只写"比X快Yms"，结尾说局限/风险
- 有 BAD/GOOD 示例在 SYSTEM_PROMPT 里

---

## 本地开发

```bash
cd /Users/david/Downloads/workbench/TechTrend

# 安装依赖
pip install -r requirements.txt

# 设置环境变量后本地测试
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...
export OPENAI_MODEL=deepseek-v4-flash
export PUSHPLUS_TOKEN=...

python fetcher.py
python summarizer.py
python pusher.py
```

## 手动触发 Workflow

```bash
gh workflow run daily-intel.yml --repo Shinnaaa/TechTrend
gh workflow run weekly-intel.yml --repo Shinnaaa/TechTrend

# 查看结果
gh run list --repo Shinnaaa/TechTrend --limit=3
gh run watch <run-id> --repo Shinnaaa/TechTrend
```

---

## 潜在改进方向

- Reddit 其他子版块（r/MachineLearning、r/programming）按需添加，方式相同
- HF trending 目前仅按 trending 排序，可考虑按 `last_modified` 过滤近 7 天新发布的模型
- `MIN_NEW_ITEMS = 5` 可根据实际情况调整
- formatter.py 翻译失败会 fallback 到纯中文，不影响推送，但 Jekyll 站显示三语时英/日版本为中文

# TechTrend — Agent Context

每次新会话先读此文件，再看代码。

---

## 项目是什么

自动化每日技术情报系统。每天从多个源抓取数据 → LLM（默认 DeepSeek）分析总结 → 推送到已配置的渠道（作者自己用 PushPlus 推微信）→ 保存到 data 分支 → 可选同步到 Jekyll 网站。周日额外生成周报。

2026-09 起按"别人 fork 就能用"的标准通用化：非密钥设置都在 `config.yml`，推送渠道按 Secret 是否存在自动启用，运行数据移到 `data` 分支。仓库目前仍是私有，作者检查后再决定是否公开（许可证已选 MIT）。

GitHub 仓库：https://github.com/Shinnaaa/TechTrend

---

## 文件职责

| 文件 | 职责 |
|------|------|
| `config.yml` / `config.py` | 所有非密钥设置（报告语言、关注方向、数据源开关、LLM、网站语言）；`config.py` 合并默认值、读本地 `.env`、定义路径 |
| `fetcher.py` | 抓取已启用的数据源，输出 `raw_intel.json`，维护 `data/seen_urls.json` 去重 |
| `summarizer.py` | 按启用的数据源动态拼 prompt，调 LLM，输出 `DAILY_REPORT.md` + `data/history/YYYY-MM-DD.md` |
| `notifiers.py` | 11 个推送渠道（PushPlus、Server酱、企业微信、飞书、钉钉、Telegram、Discord、Slack、邮件、ntfy、Webhook），按平台上限分段、转换 Markdown |
| `pusher.py` | 推送日报；`--list` 看哪些渠道已配置，`--test` 发测试消息 |
| `formatter.py` | 可选：翻译成 `website.languages`，生成 Jekyll post 到 `_formatted/`（含 `title_<lang>`、`highlights`、条目行硬换行） |
| `weekly_report.py` | 读最近 7 天 history，生成周报并推送，保存 `data/history/weekly_*.md` |
| `scripts/state.sh` | `load` 把 `data` 分支取到 `data/`（不存在就新建），`save` 提交回去 |
| `tests/` | pytest：渠道请求格式和长度上限、prompt 拼装、配置合并、front matter |

## Workflow

- `daily-intel.yml`：每天 UTC 00:00（北京 08:00）：load state → fetcher → summarizer → **save state** → pusher → formatter（仅当设置了 `WEBSITE_REPO` 变量）→ 同步到网站仓库的 `_posts/`。先存数据再推送，推送失败也不会让第二天重复分析。
- `weekly-intel.yml`：每周日 UTC 01:00（北京 09:00）跑 weekly_report.py
- `test-notify.yml`：手动触发，给所有已配置渠道发测试消息
- 日报和周报都写 `data` 分支，用 `concurrency: techtrend-data` 防止同时推送冲突

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
| `OPENAI_BASE_URL` | 中转 API 的 base URL（非官方 OpenAI），覆盖 `config.yml` 的 `llm.base_url` |
| `OPENAI_MODEL` | 当前值：`deepseek-v4-flash`（2026-07 服务商改名，旧名称返回 400），覆盖 `llm.model` |
| `PUSHPLUS_TOKEN` | PushPlus 微信推送 token（作者唯一启用的渠道；其他渠道的 Secret 名见 README） |
| `SYNC_PAT` | 用于向网站仓库写入的 GitHub PAT |

另有仓库 **Variable** `WEBSITE_REPO = Shinnaaa/Shinnaaa.github.io`，控制是否生成并同步网站文章。

**注意**：`OPENAI_MODEL` 依赖服务商，服务商改模型名会直接导致 workflow 400 报错。

---

## 已知问题与历史决策

### 2026-09 通用化改造
- 推送：`pusher.py` 只支持 PushPlus → `notifiers.py` 多渠道，有 Secret 就启用；所有渠道都失败时才让 job 失败。
- LLM：`thinking` 参数是 DeepSeek 特有的，做成 `llm.thinking: true/false/null`，null 时不发送（适配 OpenAI、Claude、Gemini 等）。周报原来没传 thinking，v4-flash 默认开推理、3000 token 可能全被吃掉，现在明确关掉。
- 数据：`history/`、`seen_urls.json` 从 main 移到 `data` 分支。原因：fork 默认只复制 main，别人拿到的是干净代码；main 也不再被每日 bot 提交刷屏。本地运行时数据在 `./data/`（gitignore）。
- 2026-09-08 ~ 09-24 连续失败是 402 余额不足（不是模型改名），作者已充值；那段时间没有生成报告，没有补。

### 2026-07 模型名变更
服务商将模型名改为 `deepseek-v4-pro` / `deepseek-v4-flash`，旧名称全部 400。已在 Secret 中更新为 `deepseek-v4-flash`。

### 2026-08 迁移后报告变成空文件（formatter.py 报 "DAILY_REPORT.md is empty" 退出 1）
- 根因：`deepseek-v4-flash` 默认开启 thinking 模式（effort=high，且 low/medium 会被服务端静默映射成 high，没法调低），reasoning_content 和最终 content 共用同一个 `max_tokens` 预算。`summarizer.py` 原来设的 `max_tokens=3500` 全被推理吃掉，`message.content` 变成空字符串。API 调用本身是 200 OK，不会报错，只有下游 formatter.py 的空文件检查能发现。
- 修复思路的取舍：
  - 一开始简单粗暴地关了 thinking（`extra_body={"thinking":{"type":"disabled"}}`），能跑但放弃了推理可能带来的分析质量提升。
  - 最终版本：`summarizer.py` 保持 thinking **开启**，`max_tokens` 从 3500 提到 `THINKING_MAX_TOKENS=7000` 给推理留够空间；每次调用把 `usage.completion_tokens_details.reasoning_tokens` 记进日志，方便后续根据实际消耗再调这个数字。如果 content 还是空的（说明某天推理格外啰嗦，7000 token 也不够），自动退化成 `FALLBACK_MAX_TOKENS=3500` + thinking 关闭重试一次，两次都空才真正报错。
  - `formatter.py` 的翻译调用维持 thinking 关闭：纯翻译任务不需要推理，开启只会多等一倍时间。
- 同批修的次要问题：`fetcher.py` 的 HF Trending Models 请求 `sort=trending` 已失效（HuggingFace 把字段改名成 `trendingScore`），会连续 400 三次重试失败，当天该来源直接空缺。已改成 `sort=trendingScore&direction=-1`。

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

"""
summarizer.py — GPT Analysis Engine
Reads raw_intel.json (new items only), injects 7-day history context,
calls DeepSeek/OpenAI, outputs DAILY_REPORT.md + saves to history/.
"""

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

RAW_INTEL_PATH = Path("raw_intel.json")
REPORT_PATH    = Path("DAILY_REPORT.md")
HISTORY_DIR    = Path("history")

MAX_GITHUB_ITEMS = 15
MAX_HF_ITEMS     = 8
MAX_HN_ITEMS     = 8
MAX_PH_ITEMS     = 6
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


SYSTEM_PROMPT = """\
你是一位拥有 20 年系统架构经验的首席技术架构师（CTA），专注于技术趋势洞察与工程决策。
你的分析风格：
- 直击本质，不堆砌术语
- 过滤琐碎 UI 库、简单工具包装、营销性项目
- 聚焦：技术范式突破点、核心痛点解决方案、对现有技术栈的启发
- 输出精炼 Markdown，适合高级工程师快速消费
- 每条分析不超过 3 句话，点到为止
"""

USER_PROMPT_TEMPLATE = """\
今天是 {date}。以下是今日技术情报原始数据（**仅包含过去未见过的新项目**），请生成一份《每日技术情报简报》。

## 格式要求
输出严格遵循以下 Markdown 结构（不要添加其他章节）：

---
# 每日技术情报简报 · {date}

## 🔥 GitHub Trending 精选
> 过滤 UI 库/简单工具，保留有架构价值的项目，每项格式：
> **[项目名](链接)** `语言` ⭐ 今日+N
> 架构师点评：一句话核心价值。

## 🧠 AI/ML 前沿论文
> 保留真正有方法论突破的论文，每项格式：
> **[论文标题](链接)**
> 核心突破：一句话。 启发：对工程实践的影响一句话。

## 💬 Hacker News 技术热点
> 只保留有工程深度的讨论，过滤新闻资讯，每项格式：
> **[标题](链接)** 👍N 💬N
> 架构师点评：一句话。

## 🚀 Product Hunt 今日新品
> 只保留有技术创新或效率工具价值的产品，每项格式：
> **[产品名](链接)**
> 一句话价值判断。

## ⚡ 技术范式变化信号
> 跨越上述所有数据，提炼 2-3 条宏观趋势信号。{trend_context}

## 🛠️ 架构师行动建议
> 给工程团队的 1-3 条具体行动建议。

---

## 今日原始数据

### GitHub Trending（新项目）
{github_data}

### HF Daily Papers（新论文）
{hf_data}

### Hacker News Top（新条目）
{hn_data}

### Product Hunt（新产品）
{ph_data}
"""

TREND_CONTEXT_TEMPLATE = """
> **注意**：以下是最近 7 天的趋势背景，请在"技术范式变化信号"中识别延续性趋势与新兴信号：
{history_summary}"""


def _load_raw_intel() -> dict:
    if not RAW_INTEL_PATH.exists():
        raise FileNotFoundError(f"{RAW_INTEL_PATH} not found. Run fetcher.py first.")
    with open(RAW_INTEL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_history_context() -> str:
    """Read last 7 days of report headlines for trend continuity."""
    HISTORY_DIR.mkdir(exist_ok=True)
    today = date.today()
    snippets = []

    for days_ago in range(1, 8):
        past_date = today - timedelta(days=days_ago)
        hist_file = HISTORY_DIR / f"{past_date}.md"
        if not hist_file.exists():
            continue
        content = hist_file.read_text(encoding="utf-8")
        # Extract only the first 600 chars as a summary snippet
        snippet = content[:600].replace("\n", " ").strip()
        snippets.append(f"- {past_date}: {snippet}...")

    if not snippets:
        return ""
    return TREND_CONTEXT_TEMPLATE.format(history_summary="\n".join(snippets))


def _save_to_history(content: str, report_date: str) -> None:
    HISTORY_DIR.mkdir(exist_ok=True)
    hist_path = HISTORY_DIR / f"{report_date}.md"
    hist_path.write_text(content, encoding="utf-8")
    log.info("History saved to %s", hist_path)


def _format_github(items: list[dict]) -> str:
    lines = []
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        by_cat.setdefault(item.get("category", "all"), []).append(item)
    for cat, cat_items in by_cat.items():
        lines.append(f"\n**{cat.upper()}**")
        for item in cat_items[:MAX_GITHUB_ITEMS]:
            lines.append(
                f"- [{item['name']}]({item['url']}) | "
                f"{item.get('language','?')} | "
                f"⭐{item.get('stars','?')} (+{item.get('stars_today','?')}) | "
                f"{item.get('description','')}"
            )
    return "\n".join(lines) or "（今日无新项目）"


def _format_hf(items: list[dict]) -> str:
    lines = []
    for item in items[:MAX_HF_ITEMS]:
        lines.append(
            f"- [{item['title']}]({item['url']})\n"
            f"  Abstract: {item.get('abstract','')}"
        )
    return "\n".join(lines) or "（今日无新论文）"


def _format_hn(items: list[dict]) -> str:
    lines = []
    for item in items[:MAX_HN_ITEMS]:
        lines.append(
            f"- [{item['title']}]({item['url']}) "
            f"👍{item.get('points',0)} 💬{item.get('comments',0)}"
        )
    return "\n".join(lines) or "（今日无新条目）"


def _format_ph(items: list[dict]) -> str:
    lines = []
    for item in items[:MAX_PH_ITEMS]:
        lines.append(
            f"- [{item['title']}]({item['url']}): {item.get('description','')}"
        )
    return "\n".join(lines) or "（今日无新产品）"


def _call_api(client: OpenAI, prompt: str) -> str:
    log.info("Calling API model=%s ...", MODEL)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.4,
        max_tokens=3500,
    )
    return response.choices[0].message.content or ""


def run() -> None:
    api_key  = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    client_kwargs: dict = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
        log.info("Using custom base URL: %s", base_url)
    client = OpenAI(**client_kwargs)

    data        = _load_raw_intel()
    report_date = data.get("date", str(date.today()))
    all_items   = data["items"]

    # Only analyze new items
    new_items = [i for i in all_items if i.get("is_new", True)]
    log.info(
        "Total items: %d, new: %d, skipped (already seen): %d",
        len(all_items), len(new_items), len(all_items) - len(new_items)
    )

    github_items = [i for i in new_items if i.get("source") == "github_trending"]
    hf_items     = [i for i in new_items if i.get("source") == "hf_daily_papers"]
    hn_items     = [i for i in new_items if i.get("source") == "hacker_news"]
    ph_items     = [i for i in new_items if i.get("source") == "product_hunt"]

    trend_context = _load_history_context()

    prompt = USER_PROMPT_TEMPLATE.format(
        date          = report_date,
        trend_context = trend_context,
        github_data   = _format_github(github_items),
        hf_data       = _format_hf(hf_items),
        hn_data       = _format_hn(hn_items),
        ph_data       = _format_ph(ph_items),
    )

    report_content = _call_api(client, prompt)

    REPORT_PATH.write_text(report_content, encoding="utf-8")
    _save_to_history(report_content, report_date)
    log.info("Report saved to %s and history/%s.md (%d chars)",
             REPORT_PATH, report_date, len(report_content))


if __name__ == "__main__":
    run()

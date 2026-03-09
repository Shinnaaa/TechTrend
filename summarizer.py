"""
summarizer.py — GPT Analysis Engine
Reads raw_intel.json, calls OpenAI-compatible API,
outputs DAILY_REPORT.md with Chief Architect perspective.
"""

import json
import logging
import os
from datetime import date
from pathlib import Path

from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

RAW_INTEL_PATH = Path("raw_intel.json")
REPORT_PATH = Path("DAILY_REPORT.md")

# Tunables
MAX_GITHUB_ITEMS = 15   # per category fed to prompt
MAX_HF_ITEMS = 10
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
今天是 {date}。以下是今日技术情报原始数据，请生成一份《每日技术情报简报》。

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

## ⚡ 本周技术范式变化信号
> 跨越上述数据，提炼 2-3 条宏观技术趋势信号（每条 1-2 句）。

## 🛠️ 架构师行动建议
> 给工程团队的 1-3 条具体行动建议。

---

## 原始数据

### GitHub Trending
{github_data}

### HF Daily Papers
{hf_data}
"""


def _load_raw_intel() -> dict:
    if not RAW_INTEL_PATH.exists():
        raise FileNotFoundError(
            f"{RAW_INTEL_PATH} not found. Run fetcher.py first."
        )
    with open(RAW_INTEL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _format_github_items(items: list[dict]) -> str:
    lines = []
    # Group by category
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        cat = item.get("category", "all")
        by_cat.setdefault(cat, []).append(item)

    for cat, cat_items in by_cat.items():
        lines.append(f"\n**{cat.upper()}**")
        for item in cat_items[:MAX_GITHUB_ITEMS]:
            lines.append(
                f"- [{item['name']}]({item['url']}) | "
                f"{item.get('language','?')} | "
                f"⭐{item.get('stars','?')} (+{item.get('stars_today','?')}) | "
                f"{item.get('description','')}"
            )
    return "\n".join(lines)


def _format_hf_items(items: list[dict]) -> str:
    lines = []
    for item in items[:MAX_HF_ITEMS]:
        lines.append(
            f"- [{item['title']}]({item['url']})\n"
            f"  Abstract: {item.get('abstract','')}"
        )
    return "\n".join(lines)


def _call_api(client: OpenAI, prompt: str) -> str:
    log.info("Calling API model=%s ...", MODEL)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=3000,
    )
    return response.choices[0].message.content or ""


def run() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")  # optional, for DeepSeek or proxy

    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
        log.info("Using custom base URL: %s", base_url)

    client = OpenAI(**client_kwargs)

    data = _load_raw_intel()
    report_date = data.get("date", str(date.today()))

    github_items = [i for i in data["items"] if i.get("source") == "github_trending"]
    hf_items = [i for i in data["items"] if i.get("source") == "hf_daily_papers"]

    log.info(
        "Loaded %d GitHub items, %d HF paper items for analysis",
        len(github_items), len(hf_items)
    )

    prompt = USER_PROMPT_TEMPLATE.format(
        date=report_date,
        github_data=_format_github_items(github_items),
        hf_data=_format_hf_items(hf_items),
    )

    report_content = _call_api(client, prompt)

    REPORT_PATH.write_text(report_content, encoding="utf-8")
    log.info("Report saved to %s (%d chars)", REPORT_PATH, len(report_content))


if __name__ == "__main__":
    run()

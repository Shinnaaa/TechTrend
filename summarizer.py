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
你是一位以"犀利、精准、反套话"著称的首席技术架构师，为高级工程师团队撰写每日技术情报。

## 分析原则

**必须做到：**
1. 每条点评传递一个"非显而易见"的洞见——读者看完项目标题已经知道的，不算洞见
2. 解释"为什么是现在"——什么让这个项目/论文今天有意义，而不是一年前或一年后
3. 给出技术对比——相比现有方案（具体说出名字），解决了哪个具体的技术痛点
4. 行动建议具体到本周可执行的一件事（一次 PoC、一次代码阅读、一个方案评估）
5. 有数据时必须用数据（star 增量、benchmark 提升幅度、延迟数字等）

**严格禁止（出现即为劣质输出）：**
- "值得关注" / "建议关注" / "持续关注"
- "是 X 的重要一步" / "是 X 领域的重要实践"
- "反映了 X 趋势" / "体现了 X 需求"
- "降低了技术门槛" / "推动了生态发展" / "改变了开发范式"
- 将英文描述直译为中文充当点评
- 三个形容词堆叠（"快速、高效、易用"）
- 没有具体对比就说"更好"或"更强"
"""

USER_PROMPT_TEMPLATE = """\
今天是 {date}。以下是今日技术情报原始数据（仅包含过去未分析过的新条目），请生成《每日技术情报简报》。

输出严格遵循以下结构，不增减章节：

---
# 每日技术情报简报 · {date}

## 🔥 GitHub Trending 精选
每个入选项目格式：
**[项目名](链接)** `语言` ⭐今日+N
💡 [洞见：说清它解决了什么具体问题、用什么技术手段、和哪个现有方案比有何不同]
🎯 [行动：本周可以做的一件具体的事；如暂无行动价值，写"观察：关注 X 指标再决策"]

## 🧠 AI/ML 前沿论文
每篇入选论文格式：
**[论文标题](链接)**
🔬 突破：[推翻/改进了哪个现有假设，尽量带量化数字]
⚙️ 工程影响：[对训练/推理/部署流程的具体影响，而非"有助于提升性能"]

## 💬 Hacker News 技术热点
每条入选讨论格式：
**[标题](链接)** 👍N 💬N
🗣 [社区在争论什么，或帖子的核心工程结论是什么]

## 🚀 Product Hunt 今日新品
每个入选产品格式：
**[产品名](链接)**
⚖️ 替代 [现有方案] → [核心差异化技术点；如差异化不足直接写"同质化，跳过"]

## ⚡ 技术范式变化信号
2-3 条，格式：
**[信号标题]**：[什么在变 + 为什么现在变 + 对工程决策的直接影响]{trend_context}

## 🛠️ 本周行动清单
2-3 条，格式：
- [ ] [动词开头 + 做什么 + 预计耗时 + 验证什么假设]

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

"""
weekly_report.py — Weekly Trend Summary
Reads the last 7 daily briefings from data/history/, writes a macro trend
report and delivers it to the configured channels.
"""

import logging
import sys
from datetime import date, timedelta

from config import CONFIG, HISTORY_DIR, LANGUAGES, lang_text, llm_client, thinking_body
from notifiers import send_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

LANG  = CONFIG["report"]["language"]
MODEL = CONFIG["llm"]["model"]

SYSTEM_PROMPT = """\
你是一位拥有 20 年系统架构经验的首席技术架构师（CTA）。
你擅长从大量碎片化的每日技术信息中，提炼出有价值的宏观趋势判断。
输出风格：言简意赅，结论优先，避免罗列信息，注重洞察深度。
"""

WEEKLY_PROMPT_TEMPLATE = """\
以下是过去 7 天（{start} ~ {end}）的每日技术情报摘要，请生成一份《{title}》。

## 格式要求
---
# {title} · {start} ~ {end}

## 🔭 本周最强信号（Top 3）
> 本周最值得关注的 3 个技术信号，每条 2-3 句，说明为何重要、影响什么。

## 📈 持续强化的趋势
> 在多天报告中反复出现、正在加速的技术方向，2-4 条。

## 🆕 本周新冒头的苗头
> 仅在近 1-2 天才出现、可能成为下周焦点的新信号，1-3 条。

## ⚠️ 值得警惕的反向信号
> 有哪些过去热炒的方向本周热度下降？或出现了值得质疑的观点？1-2 条，没有就写"无"。

## 🗓️ 下周关注重点
> 基于趋势预判，给出 2-3 个下周应重点跟踪的方向或项目。

---

## 过去 7 天每日情报摘要

{daily_summaries}
"""


def _load_weekly_reports() -> list[tuple[str, str]]:
    """Returns list of (date_str, content) for the last 7 days."""
    today = date.today()
    reports = []
    for days_ago in range(1, 8):
        past_date = today - timedelta(days=days_ago)
        hist_file = HISTORY_DIR / f"{past_date}.md"
        if hist_file.exists():
            content = hist_file.read_text(encoding="utf-8")
            reports.append((str(past_date), content))
            log.info("Loaded history: %s (%d chars)", past_date, len(content))
        else:
            log.warning("Missing history file for %s", past_date)
    return reports


def run() -> int:
    if not CONFIG["weekly"]["enabled"]:
        print("⏭️  weekly.enabled is false in config.yml, skipping.")
        return 0

    reports = _load_weekly_reports()
    if not reports:
        log.warning("No history files found in last 7 days, skipping weekly report.")
        print("⚠️  No daily reports in the last 7 days, skipping the weekly report.")
        return 0

    # Build daily summaries block
    daily_summaries = ""
    for report_date, content in sorted(reports):
        # Use first 800 chars of each daily report as summary
        snippet = content[:800].strip()
        daily_summaries += f"\n### {report_date}\n{snippet}\n...\n"

    start_date = sorted(d for d, _ in reports)[0]
    end_date   = sorted(d for d, _ in reports)[-1]
    title      = lang_text("weekly_title")

    prompt = WEEKLY_PROMPT_TEMPLATE.format(
        title           = title,
        start           = start_date,
        end             = end_date,
        daily_summaries = daily_summaries,
    )
    if LANG != "zh":
        prompt += f"\n\n整份周报（包括章节标题）用{LANGUAGES[LANG]['name']}撰写。"

    log.info("Generating weekly report with model=%s ...", MODEL)
    try:
        response = llm_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.5,
            max_tokens=3000,
            # A summary doesn't need reasoning; with it on, a 3000-token budget can
            # be spent entirely on thinking and leave the report empty.
            **thinking_body(False),
        )
    except Exception as exc:
        log.error("API call failed (model=%s): %s", MODEL, exc)
        raise
    weekly_content = response.choices[0].message.content or ""
    if not weekly_content.strip():
        raise RuntimeError("Model returned an empty weekly report.")

    weekly_path = HISTORY_DIR / f"weekly_{end_date}.md"
    weekly_path.write_text(weekly_content, encoding="utf-8")
    log.info("Weekly report saved to %s", weekly_path)

    ok, failed = send_all(f"{title} · {start_date} ~ {end_date}", weekly_content)
    print(f"Delivered via: {', '.join(ok) or '—'}" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    sys.exit(run())

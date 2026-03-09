"""
weekly_report.py — Weekly Trend Summary
Reads last 7 days from history/, generates a macro trend report,
pushes via PushPlus.
"""

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HISTORY_DIR   = Path("history")
PUSHPLUS_API  = "http://www.pushplus.plus/send"
TIMEOUT       = 15.0
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """\
你是一位拥有 20 年系统架构经验的首席技术架构师（CTA）。
你擅长从大量碎片化的每日技术信息中，提炼出有价值的宏观趋势判断。
输出风格：言简意赅，结论优先，避免罗列信息，注重洞察深度。
"""

WEEKLY_PROMPT_TEMPLATE = """\
以下是过去 7 天（{start} ~ {end}）的每日技术情报摘要，请生成一份《技术趋势周报》。

## 格式要求
---
# 技术趋势周报 · {start} ~ {end}

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


def _push_to_pushplus(token: str, title: str, content: str) -> bool:
    payload = {
        "token":    token,
        "title":    title,
        "content":  content,
        "template": "markdown",
    }
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(PUSHPLUS_API, json=payload)
            resp.raise_for_status()
            result = resp.json()
        if result.get("code") == 200:
            log.info("PushPlus sent OK")
            return True
        log.error("PushPlus error: %s", result)
        return False
    except httpx.HTTPError as exc:
        log.error("HTTP error: %s", exc)
        return False


def run() -> None:
    api_key  = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    token    = os.getenv("PUSHPLUS_TOKEN")

    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY not set.")
    if not token:
        raise EnvironmentError("PUSHPLUS_TOKEN not set.")

    reports = _load_weekly_reports()
    if not reports:
        log.warning("No history files found in last 7 days, skipping weekly report.")
        print("⚠️  没有找到过去 7 天的历史报告，跳过周报生成。")
        return

    # Build daily summaries block
    daily_summaries = ""
    for report_date, content in sorted(reports):
        # Use first 800 chars of each daily report as summary
        snippet = content[:800].strip()
        daily_summaries += f"\n### {report_date}\n{snippet}\n...\n"

    start_date = sorted(d for d, _ in reports)[0]
    end_date   = sorted(d for d, _ in reports)[-1]

    prompt = WEEKLY_PROMPT_TEMPLATE.format(
        start           = start_date,
        end             = end_date,
        daily_summaries = daily_summaries,
    )

    client_kwargs: dict = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    log.info("Generating weekly report with model=%s ...", MODEL)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.5,
        max_tokens=3000,
    )
    weekly_content = response.choices[0].message.content or ""

    # Save locally
    weekly_path = HISTORY_DIR / f"weekly_{end_date}.md"
    weekly_path.write_text(weekly_content, encoding="utf-8")
    log.info("Weekly report saved to %s", weekly_path)

    # Push
    title = f"技术趋势周报 · {start_date} ~ {end_date}"
    ok = _push_to_pushplus(token, title, weekly_content)
    if ok:
        print(f"✅ 周报推送成功：{title}")
    else:
        print("⚠️  周报推送失败，请检查日志。")


if __name__ == "__main__":
    run()

"""
formatter.py — Jekyll Post Formatter
Reads DAILY_REPORT.md, prepends Jekyll-compatible Front Matter,
saves to _formatted/YYYY-MM-DD-intel.md
"""

import logging
import re
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REPORT_PATH   = Path("DAILY_REPORT.md")
OUTPUT_DIR    = Path("_formatted")


def _build_front_matter(report_date: date) -> str:
    yyyy = report_date.strftime("%Y")
    mm   = report_date.strftime("%m")
    dd   = report_date.strftime("%d")
    slug = f"{yyyy}-{mm}-{dd}-intel"

    return f"""\
---
title: 'Node 1 今日技术情报 | {report_date}'
date: {report_date}
permalink: /posts/{yyyy}/{mm}/{slug}/
tags:
  - AI
  - GitHub
  - Daily-Intel
  - Tech-Intelligence
categories:
  - Technical Intelligence
---
"""


def _strip_existing_front_matter(content: str) -> str:
    """Remove front matter if DAILY_REPORT.md already has one."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].lstrip("\n")
    # Also strip a leading horizontal rule that DeepSeek sometimes outputs
    content = re.sub(r"^\s*---\s*\n", "", content)
    return content


def run() -> None:
    if not REPORT_PATH.exists():
        log.error("%s not found — run summarizer.py first.", REPORT_PATH)
        sys.exit(1)

    raw = REPORT_PATH.read_text(encoding="utf-8")
    if not raw.strip():
        log.error("%s is empty.", REPORT_PATH)
        sys.exit(1)

    today = date.today()
    body  = _strip_existing_front_matter(raw)
    front = _build_front_matter(today)
    post  = front + body

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename    = f"{today}-intel.md"
    output_path = OUTPUT_DIR / filename
    output_path.write_text(post, encoding="utf-8")

    log.info("Formatted post saved → %s (%d chars)", output_path, len(post))
    # Print path so the shell step can reference it
    print(str(output_path))


if __name__ == "__main__":
    run()

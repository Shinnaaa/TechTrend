"""
report.py — read the parts of a finished daily report that other steps reuse.

Sections are found by their emoji, so this works whatever language the report
is written in. Reports from before 2026-09-28 have no 📌 section; they fall
back to the ⚡ signals section, or the start of the report.
"""

from __future__ import annotations

import re

TOP_STORIES = "📌"
SIGNALS     = "⚡"

_HEADLINE = re.compile(r"^\*\*\d+\.\s*(.+?)\*\*\s*$", re.M)


def section(report: str, emoji: str) -> str:
    """Body of the first `## <emoji> …` section, without its heading ('' if absent)."""
    match = re.search(rf"^##\s*{emoji}[^\n]*\n(.*?)(?=^##?\s|^---\s*$|\Z)", report, re.M | re.S)
    return match.group(1).strip() if match else ""


def headlines(report: str) -> list[str]:
    """Titles of the top stories, most important first."""
    return [re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", h).strip()
            for h in _HEADLINE.findall(section(report, TOP_STORIES))]


def digest(report: str, limit: int = 1500) -> str:
    """Top stories + signals: what a later step needs to know about this day."""
    parts = [section(report, TOP_STORIES), section(report, SIGNALS)]
    text = "\n\n".join(p for p in parts if p) or report
    return text[:limit].strip()

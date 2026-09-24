"""
formatter.py — Jekyll Post Formatter
Reads DAILY_REPORT.md, translates to EN and JA via DeepSeek,
wraps in trilingual divs, prepends Jekyll front matter,
saves to _formatted/YYYY-MM-DD-intel.md
"""

import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REPORT_PATH = Path("DAILY_REPORT.md")
OUTPUT_DIR  = Path("_formatted")

_TRANSLATE_SYSTEM = (
    "You are a professional technical translator. "
    "Translate the following Markdown text into {lang}. "
    "Rules: preserve all Markdown syntax (headings, bold, code blocks, bullet points, links, URLs); "
    "do NOT translate proper nouns, code, URLs, or project names; "
    "output ONLY the translated Markdown with no explanation."
)

_LANG_NAMES = {"en": "English", "ja": "Japanese"}


def _strip_existing_front_matter(content: str) -> str:
    """Remove front matter block if DAILY_REPORT.md already has one."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].lstrip("\n")
    # Strip a bare leading horizontal rule that DeepSeek sometimes outputs
    content = re.sub(r"^\s*---\s*\n", "", content)
    return content


def _strip_leading_h1(body: str) -> str:
    """Remove the first # H1 line and any blank lines immediately after it."""
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            rest = lines[i + 1:]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "\n".join(rest)
    return body


def _extract_excerpt(body: str) -> str:
    """Return the first real text sentence, stripped of markdown, max 180 chars."""
    text = re.sub(r"<[^>]+>", "", body)
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("#", "```", "---", "|", ">")):
            continue
        # Remove bold/italic markers and inline links
        line = re.sub(r"\*+([^*]+)\*+", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.strip("- ").strip()
        if len(line) > 20:
            return line[:180]
    return ""


_BLOCK_STARTS = ("#", "-", "*", "+", "|", ">", "<", "```")


def _hard_wrap(body: str) -> str:
    """Keep each entry's lines apart (name / 💡 / 🎯 …) on the website.

    Markdown joins consecutive lines into one paragraph; a trailing double space
    turns the line break into <br>. Lines followed by a block element (list,
    heading, table, HTML) are left alone.
    """
    lines = body.split("\n")
    for i in range(len(lines) - 1):
        cur, nxt = lines[i].rstrip(), lines[i + 1].strip()
        if cur.strip() and nxt and not nxt.startswith(_BLOCK_STARTS) and not re.match(r"\d+\. ", nxt):
            lines[i] = cur + "  "
    return "\n".join(lines)


def _extract_highlights(body: str, n: int = 3) -> list[str]:
    """Names of the first n entries (their bold links), shown on the site's intel list."""
    return re.findall(r"\*\*\[([^\]]+)\]\([^)]+\)\*\*", body)[:n]


def _translate(client, text: str, target_lang: str) -> str:
    """Call DeepSeek to translate markdown to the target language."""
    lang_name = _LANG_NAMES[target_lang]
    system = _TRANSLATE_SYSTEM.format(lang=lang_name)
    log.info("Translating to %s (%d chars)…", lang_name, len(text))
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": text},
        ],
        temperature=0.3,
        # See summarizer.py: deepseek-v4-flash thinks by default, which can
        # leave content empty. Translation just needs the direct output.
        extra_body={"thinking": {"type": "disabled"}},
    )
    return (resp.choices[0].message.content or "").strip()


def _wrap_trilingual(zh: str, en: str, ja: str) -> str:
    """Wrap three language versions in lang-block divs for Jekyll/Kramdown."""
    def _block(css_cls: str, lang: str, content: str, hidden: bool = False) -> str:
        hidden_attr = " hidden" if hidden else ""
        return (
            f'<div class="lang-block {css_cls}" lang="{lang}"{hidden_attr} markdown="1">\n\n'
            f'{content.strip()}\n\n'
            f'</div>'
        )
    return "\n\n".join([
        _block("lang-zh", "zh", zh),
        _block("lang-en", "en", en, hidden=True),
        _block("lang-ja", "ja", ja, hidden=True),
    ])


def _build_front_matter(report_date: date, excerpt: str, highlights: list[str]) -> str:
    yyyy = report_date.strftime("%Y")
    mm   = report_date.strftime("%m")
    slug = f"{yyyy}-{mm}-{report_date.strftime('%d')}-intel"
    safe_excerpt = excerpt.replace("'", "''")
    highlight_lines = "".join(f"\n  - '{h.replace(chr(39), chr(39) * 2)}'" for h in highlights)
    return f"""\
---
title: '今日技术情报 · {report_date}'
title_en: 'Daily Tech Intel · {report_date}'
title_ja: '本日の技術インテリジェンス · {report_date}'
permalink: /posts/{yyyy}/{mm}/{slug}/
tags:
  - AI
  - GitHub
  - Daily-Intel
  - Tech-Intelligence
categories:
  - Technical Intelligence
hide_date: true
trilingual: true
excerpt: '{safe_excerpt}'
highlights:{highlight_lines or " []"}
---
"""


def run() -> None:
    if not REPORT_PATH.exists():
        log.info("%s not found — summarizer skipped, nothing to format.", REPORT_PATH)
        print("⏭️  无报告文件，跳过格式化。")
        sys.exit(0)

    raw = REPORT_PATH.read_text(encoding="utf-8")
    if not raw.strip():
        log.error("%s is empty.", REPORT_PATH)
        sys.exit(1)

    today   = date.today()
    zh_body = _strip_existing_front_matter(raw)
    zh_body = _strip_leading_h1(zh_body)
    if not zh_body.strip():
        log.error("%s has no body after stripping front matter / title.", REPORT_PATH)
        sys.exit(1)
    excerpt = _extract_excerpt(zh_body)

    # Translation — requires API credentials
    api_key  = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")

    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            en_body = _translate(client, zh_body, "en")
            ja_body = _translate(client, zh_body, "ja")
        except Exception as exc:
            log.warning("Translation failed (%s) — using Chinese only for all langs.", exc)
            en_body = zh_body
            ja_body = zh_body
    else:
        log.warning("OPENAI_API_KEY not set — skipping translation (all langs = ZH).")
        en_body = zh_body
        ja_body = zh_body

    body  = _wrap_trilingual(*(_hard_wrap(b) for b in (zh_body, en_body, ja_body)))
    front = _build_front_matter(today, excerpt, _extract_highlights(zh_body))
    post  = front + "\n" + body

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename    = f"{today}-intel.md"
    output_path = OUTPUT_DIR / filename
    output_path.write_text(post, encoding="utf-8")

    log.info("Formatted post saved → %s (%d chars)", output_path, len(post))
    print(str(output_path))


if __name__ == "__main__":
    run()

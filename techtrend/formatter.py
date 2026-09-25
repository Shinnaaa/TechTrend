"""
formatter.py — Jekyll Post Formatter (optional website publishing)
Reads DAILY_REPORT.md, translates it into the other languages in
website.languages, wraps each language in a lang-block div, prepends Jekyll
front matter and saves _formatted/YYYY-MM-DD-intel.md.
"""

import logging
import re
import sys
from datetime import date

from .config import CONFIG, FORMATTED_DIR, LANGUAGES, REPORT_PATH, llm_client, thinking_body

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SOURCE_LANG = CONFIG["report"]["language"]
# Report language first, then the others in configured order
POST_LANGS  = [SOURCE_LANG] + [l for l in CONFIG["website"]["languages"] if l != SOURCE_LANG and l in LANGUAGES]

_TRANSLATE_SYSTEM = (
    "You are a professional technical translator. "
    "Translate the following Markdown text into {lang}. "
    "Rules: preserve all Markdown syntax (headings, bold, code blocks, bullet points, links, URLs); "
    "do NOT translate proper nouns, code, URLs, or project names; "
    "output ONLY the translated Markdown with no explanation."
)

_LANG_NAMES = {"zh": "Simplified Chinese", "en": "English", "ja": "Japanese"}


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
        model=CONFIG["llm"]["model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": text},
        ],
        temperature=0.3,
        # Translation needs no reasoning; with it on the call takes twice as long.
        **thinking_body(False),
    )
    return (resp.choices[0].message.content or "").strip()


def _wrap_languages(bodies: dict[str, str]) -> str:
    """One lang-block div per language; all but the first are hidden until the site's switcher shows them."""
    blocks = []
    for i, (lang, content) in enumerate(bodies.items()):
        hidden_attr = " hidden" if i else ""
        blocks.append(
            f'<div class="lang-block lang-{lang}" lang="{lang}"{hidden_attr} markdown="1">\n\n'
            f'{content.strip()}\n\n'
            f'</div>'
        )
    return "\n\n".join(blocks)


def _yaml_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _build_front_matter(report_date: date, excerpt: str, highlights: list[str]) -> str:
    yyyy = report_date.strftime("%Y")
    mm   = report_date.strftime("%m")
    slug = f"{yyyy}-{mm}-{report_date.strftime('%d')}-intel"
    titles = "".join(
        f"title_{lang}: {_yaml_str(LANGUAGES[lang]['post_title'] + f' · {report_date}')}\n"
        for lang in POST_LANGS[1:]
    )
    highlight_lines = "".join(f"\n  - {_yaml_str(h)}" for h in highlights)
    return f"""\
---
title: {_yaml_str(LANGUAGES[SOURCE_LANG]['post_title'] + f' · {report_date}')}
{titles}permalink: /posts/{yyyy}/{mm}/{slug}/
tags:
  - AI
  - GitHub
  - Daily-Intel
  - Tech-Intelligence
categories:
  - Technical Intelligence
hide_date: true
trilingual: {"true" if len(POST_LANGS) > 1 else "false"}
excerpt: {_yaml_str(excerpt)}
highlights:{highlight_lines or " []"}
---
"""


def run() -> None:
    if not REPORT_PATH.exists():
        log.info("%s not found — summarizer skipped, nothing to format.", REPORT_PATH)
        print("⏭️  No report today, nothing to format.")
        sys.exit(0)

    raw = REPORT_PATH.read_text(encoding="utf-8")
    if not raw.strip():
        log.error("%s is empty.", REPORT_PATH)
        sys.exit(1)

    today   = date.today()
    body = _strip_existing_front_matter(raw)
    body = _strip_leading_h1(body)
    if not body.strip():
        log.error("%s has no body after stripping front matter / title.", REPORT_PATH)
        sys.exit(1)
    excerpt = _extract_excerpt(body)

    bodies = {SOURCE_LANG: body}
    targets = POST_LANGS[1:]
    try:
        client = llm_client() if targets else None
        for lang in targets:
            bodies[lang] = _translate(client, body, lang)
    except Exception as exc:
        log.warning("Translation failed (%s) — publishing the %s text for every language.", exc, SOURCE_LANG)
        bodies = {lang: body for lang in POST_LANGS}

    post = _build_front_matter(today, excerpt, _extract_highlights(body)) + "\n" + \
        _wrap_languages({lang: _hard_wrap(text) for lang, text in bodies.items()})

    FORMATTED_DIR.mkdir(exist_ok=True)
    filename    = f"{today}-intel.md"
    output_path = FORMATTED_DIR / filename
    output_path.write_text(post, encoding="utf-8")

    log.info("Formatted post saved → %s (%d chars)", output_path, len(post))
    print(str(output_path))


if __name__ == "__main__":
    run()

"""
ranker.py — find today's hot topics before the model writes the report.

A topic that shows up in several sources on the same day (a paper on Hugging
Face, a thread on Hacker News, a post on Reddit) is what people are talking
about, even when no single item has big numbers. The model gets these topics as
a hint for the "top stories" section at the head of the report.

Topics are names pulled from titles (Jev, Qwen3.8, MiMo, …): words written
like a name in at least one title, minus a list of generic tech vocabulary.
News headlines are ordinary sentences, so a plain stopword list lets words
like "battery" or "billion" through; requiring a name-like spelling doesn't. Items seen on earlier days
count too, so a story that is still spreading stays visible.
"""

from __future__ import annotations

import re
from collections import defaultdict

SOURCE_LABELS = {
    "github_trending":    "GitHub",
    "hf_daily_papers":    "HF 论文",
    "hf_trending_models": "HF 模型",
    "hacker_news":        "HN",
    "reddit":             "Reddit",
    "product_hunt":       "Product Hunt",
    "news":               "科技媒体",   # all feeds count as one source
}

# Words that appear across sources every day and say nothing about the topic.
STOPWORDS = set("""
a about after all also an and any are as at be been before best better big build
building built but by can could day days did do does don done down each even every
first for from get gets getting go good got has have how i if in into is it its
just last let like live local long made make makes making many me more most much
my need new next no not now of off on one only open or other our out over own
per post real really run running same see should show so some still than that the
their them then there these they thing things this those through time to too top
two up us use used using very via vs want was way we what when where which while
who why will with without would year years yet you your
ai agent agents api app apps benchmark code coding data dataset free framework
language large learning library llm llms ml model models network neural paper
performance reasoning research system systems task tasks tool tools training
generation based efficient scalable towards toward via survey approach method
source open-source release released github hugging face huggingface
ask tell mode world hand law trust human life home work works help world's image images video
videos text audio speech vision flash pro mini linear small tiny fast faster
simple better scale scaling version update guide review study analysis
""".split())

def item_title(item: dict) -> str:
    return item.get("title") or item.get("name", "")


def _words(item: dict) -> list[str]:
    # "owner/repo-name", "Jev-style", "Anthropic’s" → separate words, case kept
    return re.findall(r"[A-Za-z][A-Za-z0-9.+]*[A-Za-z0-9+]|[A-Za-z]", re.sub(r"[/_\-:()\[\],!?\"'’]", " ", item_title(item)))


def _is_title_case(words: list[str]) -> bool:
    """Paper titles capitalise every word, so a capital there says nothing."""
    long = [w for w in words if len(w) >= 4]
    return bool(long) and sum(w[0].isupper() for w in long) / len(long) > 0.6


def _terms(item: dict) -> tuple[set[str], set[str]]:
    """(every candidate word, the ones written like a name in this title).

    Name-like: has a digit (Qwen3.8), capitals past the first letter (MiMo, GPT),
    or a capital in the middle of a sentence-case title ("… in Nvidia stock").
    """
    words = _words(item)
    title_case = _is_title_case(words)
    terms, names = set(), set()
    for i, word in enumerate(words):
        term = word.lower().rstrip(".")
        if len(term) < 3 or term in STOPWORDS or term.isdigit():
            continue
        terms.add(term)
        if (any(c.isdigit() for c in word) or any(c.isupper() for c in word[1:])
                or (word[0].isupper() and i > 0 and not title_case)):
            names.add(term)
    return terms, names


def hot_topics(items: list[dict], max_topics: int = 10) -> list[dict]:
    """Topics that appear in at least two sources, most widespread first.

    Word matching produces some false pairs ("hand" in a robotics paper and in an
    HN title); the prompt asks the model to drop those.

    Returns [{"term", "sources": [labels], "items": [item, ...]}].
    Topics whose items are a subset of a bigger topic's (qwen / qwen3.8) are merged away.
    """
    by_term: dict[str, list[dict]] = defaultdict(list)
    name_like: set[str] = set()
    for item in items:
        terms, names = _terms(item)
        name_like |= names
        for term in terms:
            by_term[term].append(item)

    topics = []
    for term, members in by_term.items():
        sources = {m["source"] for m in members}
        # the word must read as a name somewhere; then every mention counts ("mimo v2.6" on Reddit)
        if len(sources) < 2 or term not in name_like:
            continue
        topics.append({"term": term, "sources": sources, "items": members})
    # widest spread first; among equals, topics with something new today
    topics.sort(key=lambda t: (-len(t["sources"]), not any(m.get("is_new", True) for m in t["items"]),
                               -len(t["items"]), t["term"]))

    kept: list[dict] = []
    for topic in topics:
        urls = {m["url"] for m in topic["items"]}
        if any(urls <= {m["url"] for m in k["items"]} for k in kept):
            continue
        kept.append(topic)
        if len(kept) == max_topics:
            break
    for topic in kept:
        topic["sources"] = [SOURCE_LABELS.get(s, s) for s in SOURCE_LABELS if s in topic["sources"]]
    return kept


def format_hot_topics(topics: list[dict]) -> str:
    """The raw-data block the model reads; items reported on earlier days are marked."""
    if not topics:
        return "（今日没有跨来源重复出现的话题）"
    lines = []
    for topic in topics:
        lines.append(f"- **{topic['term']}**（{len(topic['sources'])} 个来源：{' / '.join(topic['sources'])}）")
        for item in topic["items"][:4]:
            seen = "" if item.get("is_new", True) else "【前几天已报道】"
            label = item.get("site") or SOURCE_LABELS.get(item["source"], item["source"])
            lines.append(f"  - {seen}[{label}] "
                         f"[{item_title(item)}]({item['url']})")
    return "\n".join(lines)

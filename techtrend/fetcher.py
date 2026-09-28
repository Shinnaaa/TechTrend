"""
fetcher.py — Data Collection Engine
Sources (each switchable in config.yml): GitHub Trending, Hugging Face daily
papers and trending models, Hacker News (Algolia API), Reddit (RSS),
Product Hunt (RSS), tech news sites (any RSS/Atom feed).
Outputs: raw_intel.json (items marked is_new),
         data/seen_urls.json (persistent dedup store)
"""

import json
import re
import time
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .config import CONFIG, RAW_INTEL_PATH, SEEN_URLS_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

GITHUB_TRENDING_URL  = "https://github.com/trending{path}?since=daily"
HF_PAPERS_API        = "https://huggingface.co/api/daily_papers"
HF_MODELS_API        = "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit=20&full=False"
HN_ALGOLIA_API       = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20"
PH_RSS_URL           = "https://www.producthunt.com/feed"
REDDIT_TOP_RSS       = "https://www.reddit.com/r/{subreddit}/top/.rss?t=day"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_RETRIES  = 3
RETRY_DELAY  = 2
TIMEOUT      = 20.0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_with_retry(client: httpx.Client, url: str, **kwargs) -> Optional[httpx.Response]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            log.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
    log.error("All retries exhausted for %s", url)
    return None


def _load_seen_urls() -> dict[str, str]:
    """Returns {url: first_seen_date}."""
    if SEEN_URLS_PATH.exists():
        with open(SEEN_URLS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_seen_urls(seen: dict[str, str]) -> None:
    SEEN_URLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_github_trending(language_key: str, url: str) -> list[dict]:
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        resp = _fetch_with_retry(client, url)
        if resp is None:
            return items

    soup = BeautifulSoup(resp.text, "html.parser")
    repos = soup.select("article.Box-row")
    log.info("GitHub Trending [%s]: found %d repos", language_key, len(repos))

    for repo in repos[:20]:
        try:
            name_tag = repo.select_one("h2 a")
            if not name_tag:
                continue
            full_name = _strip_html(name_tag.get_text()).replace("\n", "").replace(" ", "")
            link = "https://github.com" + name_tag["href"].strip()

            desc_tag    = repo.select_one("p")
            stars_tag   = repo.select_one("a[href$='/stargazers']")
            today_tag   = repo.select_one("span.d-inline-block.float-sm-right")
            lang_tag    = repo.select_one("span[itemprop='programmingLanguage']")

            items.append({
                "source":      "github_trending",
                "category":    language_key,
                "name":        full_name,
                "description": _strip_html(desc_tag.get_text()) if desc_tag else "",
                "language":    _strip_html(lang_tag.get_text()) if lang_tag else language_key,
                "stars":       _strip_html(stars_tag.get_text()) if stars_tag else "N/A",
                "stars_today": _strip_html(today_tag.get_text()) if today_tag else "N/A",
                "url":         link,
            })
        except Exception as exc:
            log.debug("Parse error on GitHub repo: %s", exc)

    return items


def fetch_hf_daily_papers() -> list[dict]:
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        resp = _fetch_with_retry(client, HF_PAPERS_API)
        if resp is None:
            return items

    try:
        data = resp.json()
    except Exception as exc:
        log.error("Failed to parse HF papers JSON: %s", exc)
        return items

    log.info("HF Daily Papers: found %d entries", len(data))

    for entry in data[:20]:
        try:
            paper    = entry.get("paper", {})
            paper_id = paper.get("id", "")
            abstract = _strip_html(paper.get("summary", ""))
            if len(abstract) > 600:
                abstract = abstract[:597] + "..."

            items.append({
                "source":    "hf_daily_papers",
                "title":     _strip_html(paper.get("title", "")),
                "abstract":  abstract,
                "upvotes":   entry.get("numComments", 0),
                "published": paper.get("publishedAt", "")[:10],
                "url":       f"https://huggingface.co/papers/{paper_id}",
            })
        except Exception as exc:
            log.debug("Parse error on HF paper: %s", exc)

    return items


def fetch_hf_trending_models() -> list[dict]:
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        resp = _fetch_with_retry(client, HF_MODELS_API)
        if resp is None:
            return items

    try:
        data = resp.json()
    except Exception as exc:
        log.error("Failed to parse HF models JSON: %s", exc)
        return items

    log.info("HF Trending Models: found %d entries", len(data))

    for entry in data[:20]:
        try:
            model_id = entry.get("id", "")
            if not model_id:
                continue
            items.append({
                "source":       "hf_trending_models",
                "title":        model_id,
                "pipeline_tag": entry.get("pipeline_tag", ""),
                "likes":        entry.get("likes", 0),
                "downloads":    entry.get("downloads", 0),
                "tags":         entry.get("tags", [])[:8],
                "url":          f"https://huggingface.co/{model_id}",
            })
        except Exception as exc:
            log.debug("Parse error on HF model: %s", exc)

    return items


def fetch_hn_top() -> list[dict]:
    """Hacker News front page via Algolia API (single request)."""
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        resp = _fetch_with_retry(client, HN_ALGOLIA_API)
        if resp is None:
            return items

    try:
        data = resp.json()
    except Exception as exc:
        log.error("Failed to parse HN JSON: %s", exc)
        return items

    hits = data.get("hits", [])
    log.info("Hacker News: found %d stories", len(hits))

    for hit in hits:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID','')}"
        items.append({
            "source":   "hacker_news",
            "title":    _strip_html(hit.get("title", "")),
            "points":   hit.get("points", 0),
            "comments": hit.get("num_comments", 0),
            "author":   hit.get("author", ""),
            "url":      url,
            "hn_url":   f"https://news.ycombinator.com/item?id={hit.get('objectID','')}",
        })

    return items


def fetch_reddit(subreddit: str) -> list[dict]:
    """A subreddit's top-of-day posts via Atom RSS — no API key needed."""
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        resp = _fetch_with_retry(client, REDDIT_TOP_RSS.format(subreddit=subreddit))
        if resp is None:
            return items

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        log.error("Failed to parse Reddit feed: %s", exc)
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    log.info("Reddit r/%s: found %d entries", subreddit, len(entries))

    for entry in entries[:20]:
        try:
            title = _strip_html(entry.findtext("atom:title", "", ns))
            link_el = entry.find("atom:link[@rel='alternate']", ns)
            if link_el is None:
                link_el = entry.find("atom:link", ns)
            reddit_url = link_el.attrib.get("href", "") if link_el is not None else ""

            # For link posts, extract the external URL from HTML content
            content = entry.findtext("atom:content", "", ns) or ""
            url_match = re.search(
                r'href="(https?://(?!(?:www\.)?reddit\.com)[^"]+)"', content
            )
            external_url = url_match.group(1) if url_match else ""

            if not title or not reddit_url:
                continue

            items.append({
                "source":     "reddit",
                "subreddit":  subreddit,
                "title":      title,
                "url":        external_url or reddit_url,
                "reddit_url": reddit_url,
                "author":     entry.findtext("atom:author/atom:name", "", ns),
            })
        except Exception as exc:
            log.debug("Parse error on Reddit entry: %s", exc)

    return items


def fetch_product_hunt() -> list[dict]:
    """Product Hunt daily top products via Atom feed."""
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        resp = _fetch_with_retry(client, PH_RSS_URL)
        if resp is None:
            return items

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        log.error("Failed to parse PH feed: %s", exc)
        return items

    # Atom namespace
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    log.info("Product Hunt Atom feed: found %d entries", len(entries))

    for entry in entries[:15]:
        try:
            title = _strip_html(entry.findtext("atom:title", "", ns))
            # <link rel="alternate" href="..."/>
            link_el = entry.find("atom:link[@rel='alternate']", ns)
            link = link_el.attrib.get("href", "") if link_el is not None else ""
            summary = _strip_html(entry.findtext("atom:summary", "", ns))
            if len(summary) > 300:
                summary = summary[:297] + "..."

            if not title or not link:
                continue

            items.append({
                "source":      "product_hunt",
                "title":       title,
                "description": summary,
                "url":         link,
            })
        except Exception as exc:
            log.debug("Parse error on PH entry: %s", exc)

    return items


def _parse_date(text: str) -> Optional[datetime]:
    """RSS pubDate (RFC 822) or Atom published/updated (ISO 8601)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text) if not text[:4].isdigit() else datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str, site: str) -> list[dict]:
    """Articles from an RSS 2.0 or Atom feed, newest first."""
    root = ET.fromstring(xml_text)
    atom = "{http://www.w3.org/2005/Atom}"
    items = []
    for entry in root.iter("item"):                       # RSS 2.0
        items.append({
            "title":     entry.findtext("title", ""),
            "url":       (entry.findtext("link", "") or "").strip(),
            "summary":   entry.findtext("description", ""),
            "published": _parse_date(entry.findtext("pubDate", "")),
        })
    for entry in root.iter(f"{atom}entry"):               # Atom
        link = entry.find(f"{atom}link[@rel='alternate']")
        if link is None:
            link = entry.find(f"{atom}link")
        items.append({
            "title":     entry.findtext(f"{atom}title", ""),
            "url":       link.attrib.get("href", "") if link is not None else "",
            "summary":   entry.findtext(f"{atom}summary", "") or entry.findtext(f"{atom}content", ""),
            "published": _parse_date(entry.findtext(f"{atom}published", "") or entry.findtext(f"{atom}updated", "")),
        })

    articles = []
    for item in items:
        title = _strip_html(item["title"])
        if not title or not item["url"]:
            continue
        summary = _strip_html(item["summary"])
        articles.append({
            "source":      "news",
            "site":        site,
            "title":       title,
            "description": summary[:297] + "..." if len(summary) > 300 else summary,
            "published":   item["published"],
            "url":         item["url"],
        })
    articles.sort(key=lambda a: a["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return articles


def fetch_news(feeds: list[dict], max_age_hours: float) -> list[dict]:
    """Recent articles from tech news feeds; a feed that fails is skipped."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        for feed in feeds:
            resp = _fetch_with_retry(client, feed["url"])
            if resp is None:
                continue
            try:
                articles = parse_feed(resp.text, feed["name"])
            except ET.ParseError as exc:
                log.error("Failed to parse feed %s: %s", feed["name"], exc)
                continue
            # Articles without a date are kept; seen_urls stops them repeating.
            recent = [a for a in articles if a["published"] is None or a["published"] >= cutoff]
            log.info("News %s: %d articles, %d within %sh", feed["name"], len(articles), len(recent), max_age_hours)
            for article in recent:
                article["published"] = article["published"].isoformat() if article["published"] else ""
            items.extend(recent)
    return items


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    today = str(date.today())
    seen_urls = _load_seen_urls()
    prev_count = len(seen_urls)

    all_items: list[dict] = []
    sources = CONFIG["sources"]

    if sources["github_trending"]["enabled"]:
        for lang_key in sources["github_trending"]["languages"]:
            log.info("Fetching GitHub Trending: %s", lang_key)
            url = GITHUB_TRENDING_URL.format(path="" if lang_key == "all" else f"/{lang_key}")
            all_items.extend(fetch_github_trending(lang_key, url))

    if sources["huggingface_papers"]["enabled"]:
        log.info("Fetching HF Daily Papers")
        all_items.extend(fetch_hf_daily_papers())

    if sources["huggingface_models"]["enabled"]:
        log.info("Fetching HF Trending Models")
        all_items.extend(fetch_hf_trending_models())

    if sources["hacker_news"]["enabled"]:
        log.info("Fetching Hacker News Top")
        all_items.extend(fetch_hn_top())

    if sources["reddit"]["enabled"]:
        for subreddit in sources["reddit"]["subreddits"]:
            log.info("Fetching Reddit r/%s", subreddit)
            all_items.extend(fetch_reddit(subreddit))

    if sources["product_hunt"]["enabled"]:
        log.info("Fetching Product Hunt")
        all_items.extend(fetch_product_hunt())

    if sources["news"]["enabled"]:
        log.info("Fetching news feeds")
        all_items.extend(fetch_news(sources["news"]["feeds"], sources["news"]["max_age_hours"]))

    # Deduplicate within this batch, mark is_new
    batch_seen: set[str] = set()
    deduped: list[dict] = []
    new_count = 0

    for item in all_items:
        url = item.get("url", "")
        if not url or url in batch_seen:
            continue
        batch_seen.add(url)

        is_new = url not in seen_urls
        item["is_new"] = is_new
        if is_new:
            seen_urls[url] = today
            new_count += 1

        deduped.append(item)

    # Persist seen URLs
    _save_seen_urls(seen_urls)

    output = {
        "date":      today,
        "total":     len(deduped),
        "new_count": new_count,
        "items":     deduped,
    }

    with open(RAW_INTEL_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(
        "Saved %d items (%d new, %d already seen) to raw_intel.json",
        len(deduped), new_count, len(deduped) - new_count
    )
    log.info("seen_urls.json: %d → %d total tracked URLs", prev_count, len(seen_urls))


if __name__ == "__main__":
    run()

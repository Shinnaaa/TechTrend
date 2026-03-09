"""
fetcher.py — Data Collection Engine
Scrapes GitHub Trending and Hugging Face Daily Papers,
outputs cleaned raw_intel.json.
"""

import json
import re
import time
import logging
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

GITHUB_TRENDING_URLS = {
    "python": "https://github.com/trending/python?since=daily",
    "typescript": "https://github.com/trending/typescript?since=daily",
    "all": "https://github.com/trending?since=daily",
}
HF_PAPERS_API = "https://huggingface.co/api/daily_papers"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT = 20.0


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
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
            # Name
            name_tag = repo.select_one("h2 a")
            if not name_tag:
                continue
            full_name = _strip_html(name_tag.get_text()).replace("\n", "").replace(" ", "")
            link = "https://github.com" + name_tag["href"].strip()

            # Description
            desc_tag = repo.select_one("p")
            description = _strip_html(desc_tag.get_text()) if desc_tag else ""

            # Stars total
            stars_tag = repo.select_one("a[href$='/stargazers']")
            stars = _strip_html(stars_tag.get_text()) if stars_tag else "N/A"

            # Stars today
            today_tag = repo.select_one("span.d-inline-block.float-sm-right")
            stars_today = _strip_html(today_tag.get_text()) if today_tag else "N/A"

            # Language tag on card (may differ from query lang)
            lang_tag = repo.select_one("span[itemprop='programmingLanguage']")
            lang = _strip_html(lang_tag.get_text()) if lang_tag else language_key

            items.append({
                "source": "github_trending",
                "category": language_key,
                "name": full_name,
                "description": description,
                "language": lang,
                "stars": stars,
                "stars_today": stars_today,
                "url": link,
            })
        except Exception as exc:
            log.debug("Parse error on repo entry: %s", exc)

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
            paper = entry.get("paper", {})
            paper_id = paper.get("id", "")
            title = _strip_html(paper.get("title", ""))
            abstract = _strip_html(paper.get("summary", ""))
            # Truncate very long abstracts for downstream prompt efficiency
            if len(abstract) > 600:
                abstract = abstract[:597] + "..."
            upvotes = entry.get("numComments", 0)
            pub_date = paper.get("publishedAt", "")[:10]

            items.append({
                "source": "hf_daily_papers",
                "title": title,
                "abstract": abstract,
                "upvotes": upvotes,
                "published": pub_date,
                "url": f"https://huggingface.co/papers/{paper_id}",
            })
        except Exception as exc:
            log.debug("Parse error on HF paper entry: %s", exc)

    return items


def run() -> None:
    all_items: list[dict] = []

    # GitHub Trending
    for lang_key, url in GITHUB_TRENDING_URLS.items():
        log.info("Fetching GitHub Trending: %s", lang_key)
        items = fetch_github_trending(lang_key, url)
        all_items.extend(items)

    # Hugging Face
    log.info("Fetching HF Daily Papers")
    hf_items = fetch_hf_daily_papers()
    all_items.extend(hf_items)

    # Deduplicate GitHub repos by URL
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for item in all_items:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            deduped.append(item)

    output = {
        "date": str(date.today()),
        "total": len(deduped),
        "items": deduped,
    }

    with open("raw_intel.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("Saved %d items to raw_intel.json", len(deduped))


if __name__ == "__main__":
    run()

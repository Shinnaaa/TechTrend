"""
config.py — settings shared by every stage.

Non-secret settings come from config.yml (merged over DEFAULTS, so a missing key
never breaks a run). Secrets and a few overrides come from environment variables.
Persistent state (history, dedup store) lives in DATA_DIR, which GitHub Actions
checks out from the `data` branch.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

# Repository root: config.yml, .env and data/ live here, next to the package.
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Local runs: read KEY=VALUE lines from .env without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
CONFIG_PATH = Path(os.getenv("TECHTREND_CONFIG", ROOT / "config.yml"))

DEFAULTS = {
    "report": {"language": "zh", "focus": "", "min_new_items": 5},
    "llm": {
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "thinking": True,
        "max_tokens": 7000,
    },
    "sources": {
        "github_trending":    {"enabled": True, "languages": ["python", "typescript", "all"], "max_items": 15},
        "huggingface_papers": {"enabled": True, "max_items": 8},
        "huggingface_models": {"enabled": True, "max_items": 10},
        "hacker_news":        {"enabled": True, "max_items": 8},
        "reddit":             {"enabled": True, "subreddits": ["LocalLLaMA"], "max_items": 10},
        "product_hunt":       {"enabled": True, "max_items": 6},
    },
    "notify": {"channels": []},
    "weekly": {"enabled": True},
    "website": {"languages": ["zh", "en", "ja"], "posts_dir": "_posts"},
}

LANGUAGES = {
    "zh": {"name": "简体中文", "daily_title": "每日技术情报简报", "weekly_title": "技术趋势周报",
           "post_title": "今日技术情报"},
    "en": {"name": "English", "daily_title": "Daily Tech Intel", "weekly_title": "Weekly Tech Trends",
           "post_title": "Daily Tech Intel"},
    "ja": {"name": "日本語", "daily_title": "デイリー技術インテル", "weekly_title": "週刊技術トレンド",
           "post_title": "本日の技術インテリジェンス"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    user = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user)

    lang = cfg["report"]["language"]
    if lang not in LANGUAGES:
        raise ValueError(f"report.language must be one of {sorted(LANGUAGES)}, got {lang!r}")

    # Env overrides for the endpoint (GitHub secrets/variables or a local .env)
    cfg["llm"]["model"] = os.getenv("OPENAI_MODEL") or cfg["llm"]["model"]
    cfg["llm"]["base_url"] = os.getenv("OPENAI_BASE_URL") or cfg["llm"]["base_url"]
    return cfg


CONFIG = load()

DATA_DIR = Path(os.getenv("TECHTREND_DATA_DIR", ROOT / "data"))
HISTORY_DIR = DATA_DIR / "history"
SEEN_URLS_PATH = DATA_DIR / "seen_urls.json"

# Per-run working files (not persisted between runs)
RAW_INTEL_PATH = Path("raw_intel.json")
REPORT_PATH = Path("DAILY_REPORT.md")
FORMATTED_DIR = Path("_formatted")


def lang_text(key: str, lang: str | None = None) -> str:
    return LANGUAGES[lang or CONFIG["report"]["language"]][key]


def llm_client():
    """OpenAI-compatible client from OPENAI_API_KEY and the configured base URL."""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=api_key, base_url=CONFIG["llm"]["base_url"] or None)


def thinking_body(enabled: bool | None) -> dict:
    """extra_body for the DeepSeek-style reasoning switch; empty when llm.thinking is null."""
    if CONFIG["llm"]["thinking"] is None or enabled is None:
        return {}
    return {"extra_body": {"thinking": {"type": "enabled" if enabled else "disabled"}}}

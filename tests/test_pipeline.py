from datetime import date

import yaml

from techtrend import config, formatter, pusher, ranker, report, summarizer

BLOCKS = {key: f"<{key} data>" for key in summarizer.SECTION_ORDER}


def test_prompt_contains_every_enabled_section_in_order():
    prompt = summarizer._build_prompt("2026-09-25", "", BLOCKS)
    headings = ["📌 今日要闻", "⚡ 趋势信号", "🛠️ 本周行动", "📂 分源速览", "GitHub Trending", "HuggingFace 热门模型",
                "AI/ML 论文", "Hacker News", "Reddit r/LocalLLaMA", "Product Hunt", "今日原始数据", "### 今日热点候选"]
    positions = [prompt.index(h) for h in headings]
    assert positions == sorted(positions)
    assert all(block in prompt for block in BLOCKS.values())
    assert "# 每日技术情报简报 · 2026-09-25" in prompt
    assert "输出语言" not in prompt


def test_disabled_sources_and_language(monkeypatch):
    monkeypatch.setitem(summarizer.SOURCES["hacker_news"], "enabled", False)
    monkeypatch.setitem(summarizer.SOURCES["reddit"], "subreddits", ["LocalLLaMA", "MachineLearning"])
    monkeypatch.setattr(summarizer, "LANG", "en")
    monkeypatch.setitem(config.CONFIG["report"], "language", "en")
    prompt = summarizer._build_prompt("2026-09-25", "", BLOCKS)
    assert "Hacker News" not in prompt and "<hacker_news data>" not in prompt
    assert "r/LocalLLaMA、r/MachineLearning" in prompt
    assert "用English撰写" in prompt and "# Daily Tech Intel · 2026-09-25" in prompt


def test_focus_goes_into_system_prompt(monkeypatch):
    assert "读者关注" not in summarizer._system_prompt()
    monkeypatch.setitem(config.CONFIG["report"], "focus", "前端和浏览器技术")
    assert "前端和浏览器技术" in summarizer._system_prompt()


def test_thinking_body_modes(monkeypatch):
    assert config.thinking_body(True) == {"extra_body": {"thinking": {"type": "enabled"}}}
    assert config.thinking_body(False) == {"extra_body": {"thinking": {"type": "disabled"}}}
    monkeypatch.setitem(config.CONFIG["llm"], "thinking", None)
    assert config.thinking_body(False) == {}


def test_config_merges_partial_user_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yml"
    path.write_text("report:\n  language: ja\nsources:\n  reddit:\n    enabled: false\n")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    cfg = config.load()
    assert cfg["report"]["language"] == "ja" and cfg["report"]["min_new_items"] == 5
    assert cfg["sources"]["reddit"]["enabled"] is False and cfg["sources"]["reddit"]["subreddits"] == ["LocalLLaMA"]


def test_front_matter_is_valid_yaml_with_titles_per_language():
    fm = formatter._build_front_matter(date(2026, 9, 25), "It's `x`", ["A'b", "C"])
    data = yaml.safe_load(fm.strip().strip("-"))
    assert data["title"] == "今日技术情报 · 2026-09-25"
    assert data["title_en"] == "Daily Tech Intel · 2026-09-25"
    assert data["title_ja"] == "本日の技術インテリジェンス · 2026-09-25"
    assert data["highlights"] == ["A'b", "C"] and data["excerpt"] == "It's `x`"
    assert data["trilingual"] is True


def test_wrap_hides_all_but_first_language():
    html = formatter._wrap_languages({"zh": "中", "en": "en", "ja": "日"})
    assert '<div class="lang-block lang-zh" lang="zh" markdown="1">' in html
    assert 'lang="en" hidden markdown="1"' in html and 'lang="ja" hidden markdown="1"' in html


def test_hard_wrap_keeps_entry_lines_apart():
    body = "## H\n\n**[a](u)** `py`\n💡 one\n🎯 two\n\n- item\n- item"
    out = formatter._hard_wrap(body)
    assert "**[a](u)** `py`  \n💡 one  \n🎯 two\n" in out
    assert "- item\n- item" in out


class _FakeCompletions:
    """Returns queued (content, finish_reason) answers and records each call's kwargs."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def create(self, **kwargs):
        from types import SimpleNamespace as NS
        self.calls.append(kwargs)
        content, finish = self.answers.pop(0)
        usage = NS(completion_tokens=1, completion_tokens_details=NS(reasoning_tokens=1))
        return NS(choices=[NS(message=NS(content=content), finish_reason=finish)], usage=usage,
                  model_dump_json=lambda: "{}")


def _client(answers):
    from types import SimpleNamespace as NS
    completions = _FakeCompletions(answers)
    return NS(chat=NS(completions=completions)), completions


def test_complete_report_needs_one_call():
    client, calls = _client([("# report", "stop")])
    assert summarizer._call_api(client, "p") == "# report"
    assert len(calls.calls) == 1 and calls.calls[0]["extra_body"]["thinking"]["type"] == "enabled"


def test_truncated_report_is_rewritten_without_thinking():
    client, calls = _client([("# half a rep", "length"), ("# full report", "stop")])
    assert summarizer._call_api(client, "p") == "# full report"
    retry = calls.calls[1]
    assert retry["extra_body"]["thinking"]["type"] == "disabled"
    assert retry["max_tokens"] == config.CONFIG["llm"]["max_tokens"]


def test_empty_report_is_retried_and_kept_if_retry_fails():
    client, _ = _client([("", "length"), ("# report", "stop")])
    assert summarizer._call_api(client, "p") == "# report"
    client, _ = _client([("# partial", "length"), ("", "stop")])
    assert summarizer._call_api(client, "p") == "# partial"


def _item(source, title, new=True):
    return {"source": source, "title": title, "url": f"https://x/{source}/{title}", "is_new": new}


def test_hot_topics_finds_a_name_shared_across_sources():
    items = [
        _item("hf_daily_papers", "Just Ask Jev: RL for Calibrated Decisions"),
        _item("hacker_news", "Ollaya – Ollama for open-source, Jev-style decision models"),
        _item("reddit", "Jev vs. Kev: open decision model side by side", new=False),
        _item("reddit", "The best open model for your data"),
        _item("hacker_news", "Show HN: The best way to build a model"),
    ]
    topics = ranker.hot_topics(items)
    assert [t["term"] for t in topics] == ["jev"]
    assert topics[0]["sources"] == ["HF 论文", "HN", "Reddit"]
    block = ranker.format_hot_topics(topics)
    assert block.count("【前几天已报道】") == 1 and "Jev vs. Kev" in block


def test_hot_topics_drops_terms_covered_by_a_wider_one():
    items = [_item("hf_trending_models", "Qwen/Qwen3.8-27B"), _item("reddit", "Qwen Qwen3.8 27b on 16gb VRAM")]
    assert len(ranker.hot_topics(items)) == 1


NEW_REPORT = """# 每日技术情报简报 · 2026-09-28

## 📌 今日要闻
**1. Jev 校准决策模型三源同日出现，单次调用给 10 类风险打分**
审核链路可以从每类一次调用压到一次。 · [HF 论文](https://a) / [HN](https://b)

**2. 持续：[Qwen3.8](https://q) 量化版在 16GB 显卡跑到 9 tok/s**
本地部署门槛再降一档。 · [Reddit](https://c)

## ⚡ 趋势信号
**决策模型成为独立品类**：输出是概率不是文本。

## 📂 分源速览
### 🔥 GitHub Trending
- **[openrig](https://g)** ⭐+80：两个 CLI agent 共享上下文。
"""


def test_report_helpers_read_top_stories_and_signals():
    assert report.headlines(NEW_REPORT) == [
        "Jev 校准决策模型三源同日出现，单次调用给 10 类风险打分",
        "持续：Qwen3.8 量化版在 16GB 显卡跑到 9 tok/s",
    ]
    digest = report.digest(NEW_REPORT)
    assert "Jev" in digest and "决策模型成为独立品类" in digest and "openrig" not in digest
    assert pusher.push_title(NEW_REPORT).startswith("📌 Jev 校准决策模型")
    assert formatter._extract_highlights(NEW_REPORT)[0].startswith("Jev")


def test_report_helpers_fall_back_for_old_reports():
    old = "# 简报\n\n## 🔥 GitHub Trending 精选\n**[a](u)** x\n\n## ⚡ 技术范式变化信号\n**信号**：y\n"
    assert report.headlines(old) == []
    assert report.digest(old) == "**信号**：y"
    assert pusher.push_title(old).startswith(config.lang_text("daily_title"))
    assert formatter._extract_highlights(old) == ["a"]

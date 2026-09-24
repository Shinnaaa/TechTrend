from datetime import date

import yaml

import config
import formatter
import summarizer

BLOCKS = {key: f"<{key} data>" for key in summarizer.SECTION_ORDER}


def test_prompt_contains_every_enabled_section_in_order():
    prompt = summarizer._build_prompt("2026-09-25", "", BLOCKS)
    headings = ["GitHub Trending 精选", "HuggingFace 热门模型", "AI/ML 前沿论文", "Hacker News 技术热点",
                "Reddit r/LocalLLaMA 今日热帖", "Product Hunt 今日新品", "技术范式变化信号", "本周行动清单"]
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

"""
summarizer.py — LLM Analysis Engine
Reads raw_intel.json (new items only), adds 7 days of history as trend context,
calls an OpenAI-compatible model, writes DAILY_REPORT.md and data/history/<date>.md.
Sections follow the sources enabled in config.yml.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from config import CONFIG, HISTORY_DIR, LANGUAGES, RAW_INTEL_PATH, REPORT_PATH, lang_text, llm_client, thinking_body

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SOURCES        = CONFIG["sources"]
LANG           = CONFIG["report"]["language"]
MIN_NEW_ITEMS  = CONFIG["report"]["min_new_items"]   # fewer than this → skip report and push
MODEL          = CONFIG["llm"]["model"]

SYSTEM_PROMPT = """\
你是一位以"犀利、精准、反套话"著称的首席技术架构师，为高级工程师团队撰写每日技术情报。

## 分析原则

**必须做到：**
1. 每条点评传递一个"非显而易见"的洞见——读者看完项目标题已经知道的，不算洞见
2. 解释"为什么是现在"——什么让这个项目/论文今天有意义，而不是一年前或一年后
3. 给出技术对比——相比现有方案（具体说出名字），解决了哪个具体的技术痛点
4. 行动建议具体到本周可执行的一件事（一次 PoC、一次代码阅读、一个方案评估）
5. 有数据时必须用数据（star 增量、benchmark 提升幅度、延迟数字等）

**严格禁止（出现即为劣质输出）：**
- "值得关注" / "建议关注" / "持续关注"
- "是 X 的重要一步" / "是 X 领域的重要实践"
- "反映了 X 趋势" / "体现了 X 需求"
- "降低了技术门槛" / "推动了生态发展" / "改变了开发范式"
- 将英文描述直译为中文充当点评
- 三个形容词堆叠（"快速、高效、易用"）
- 没有具体对比就说"更好"或"更强"

**句式禁令（结构性套话，每出现一次扣分）：**
- "不是又一个 [X]，而是 [Y]" —— 直接说 Y 是什么、数字是多少
- "直接攻击了……的痛点" —— 删掉，直接说它用什么技术手段
- "核心差异在于" —— 删掉这五个字，直接给差异
- "X 杀手" / "X 替代品" —— 说清楚哪个具体场景可替代，哪个不能
- "重新定义了 X" / "颠覆了 X" —— 必须附具体数据，否则整句删掉
- 任何以"这意味着……"结尾的句子 —— 读者自己推断，不需要你代劳

**写法示例（照着这个风格写）：**
❌ 差：这不是又一个"终端文件管理器"（如 ranger、lf），而是将终端文件管理从"单窗口导航"升级为"多标签页预览"，直接攻击了 ranger 依赖 Python、启动慢的痛点。
✅ 好：superfile 用 Go 单二进制实现多标签+图片侧边栏预览，启动 <100ms（ranger 约 300ms），内存占用 12MB；缺点是插件生态远不如 ranger，脚本集成能力弱。

❌ 差：这不是又一个"OpenAI 代理"，而是将 ChatGPT Plus 订阅转化为标准 OAuth 2.0 服务。
✅ 好：openai-oauth 把浏览器登录态转为兼容 OpenAI 格式的接口，让 Plus 账号（80条/3h 限额）可接入自建工具；违反 OpenAI ToS，生产环境不可用。
"""

PROMPT_HEADER = """\
今天是 {date}。以下是今日技术情报原始数据（仅包含过去未分析过的新条目），请生成《{title}》。

只输出下列章节，按顺序，不增减章节：

---
# {title} · {date}
"""

# Instruction block per source: (heading shown in the report, how to write entries).
SECTION_GUIDES = {
    "github_trending": ("🔥 GitHub Trending 精选", """\
每个入选项目格式：
**[项目名](链接)** `语言` ⭐今日+N
💡 [第一句直接说它用什么技术做到了什么，必须带数字（速度/内存/延迟/成功率）；如需对比，只写"比X快Yms"或"比X省ZMB"，禁止"不是X而是Y"结构；最后一句说局限或风险]
🎯 [一件本周可落地的具体行动，格式：动词+做什么+验证什么假设；无行动价值则写"观察：等X指标达到Y再决策"]"""),
    "huggingface_models": ("🤗 HuggingFace 热门模型", """\
从今日 trending 中挑 3-5 个真正值得关注的，其余跳过。格式：
**[模型名](链接)** `任务类型` ❤️N ⬇️N/月
💡 [直接说：参数量、量化级别、在哪个 benchmark 上得了多少分、推理速度是多少；如需对比写"比X高Y分"或"比X快Zms"；最后说适合替换哪个具体工作流环节]
🎯 [一件本周可落地的事，或"同质化，跳过"]"""),
    "huggingface_papers": ("🧠 AI/ML 前沿论文", """\
每篇入选论文格式：
**[论文标题](链接)**
🔬 突破：[推翻/改进了哪个现有假设，尽量带量化数字]
⚙️ 工程影响：[对训练/推理/部署流程的具体影响，而非"有助于提升性能"]"""),
    "hacker_news": ("💬 Hacker News 技术热点", """\
每条入选讨论格式：
**[标题](链接)** 👍N 💬N
🗣 [社区在争论什么，或帖子的核心工程结论是什么]"""),
    "reddit": ("🧵 Reddit {subreddits} 今日热帖", """\
挑 3-5 条有实质内容的，跳过纯问答或已被 GitHub/HN 覆盖的。格式：
**[标题](链接)**
🗣 [核心信息：社区在讨论什么技术结论，或帖子分享了哪个具体测试结果/发现]"""),
    "product_hunt": ("🚀 Product Hunt 今日新品", """\
每个入选产品格式：
**[产品名](链接)**
⚖️ 替代 [现有方案] → [核心差异化技术点；如差异化不足直接写"同质化，跳过"]"""),
}

# Order of sections in the report, and the raw-data label for each
SECTION_ORDER = ["github_trending", "huggingface_models", "huggingface_papers", "hacker_news", "reddit", "product_hunt"]
DATA_LABELS = {
    "github_trending":    "GitHub Trending（新项目）",
    "huggingface_models": "HuggingFace 热门模型（今日 trending）",
    "huggingface_papers": "HF Daily Papers（新论文）",
    "hacker_news":        "Hacker News Top（新条目）",
    "reddit":             "Reddit {subreddits} 今日热帖",
    "product_hunt":       "Product Hunt（新产品）",
}

PROMPT_FOOTER = """
## ⚡ 技术范式变化信号
2-3 条，格式：
**[信号标题]**：[什么在变 + 为什么现在变 + 对工程决策的直接影响]{trend_context}

## 🛠️ 本周行动清单
2-3 条，格式：
- [ ] [动词开头 + 做什么 + 预计耗时 + 验证什么假设]

---
"""

LANGUAGE_DIRECTIVE = """
## 输出语言
整份简报（包括章节标题、点评和行动清单）用{language}撰写。项目名、论文标题、代码和链接保持原文。
"""

FOCUS_DIRECTIVE = """

## 读者关注
{focus}
挑选条目、写点评和行动建议时，优先考虑与此相关的内容。
"""

TREND_CONTEXT_TEMPLATE = """
> **注意**：以下是最近 7 天的趋势背景，请在"技术范式变化信号"中识别延续性趋势与新兴信号：
{history_summary}"""


def _subreddits() -> str:
    return "、".join(f"r/{name}" for name in SOURCES["reddit"]["subreddits"])


def _system_prompt() -> str:
    focus = (CONFIG["report"]["focus"] or "").strip()
    return SYSTEM_PROMPT + (FOCUS_DIRECTIVE.format(focus=focus) if focus else "")


def _build_prompt(report_date: str, trend_context: str, data_blocks: dict[str, str]) -> str:
    """Report skeleton + raw data, limited to the enabled sources."""
    enabled = [key for key in SECTION_ORDER if SOURCES[key]["enabled"]]
    parts = [PROMPT_HEADER.format(date=report_date, title=lang_text("daily_title"))]
    for key in enabled:
        heading, guide = SECTION_GUIDES[key]
        parts.append(f"\n## {heading.format(subreddits=_subreddits())}\n{guide}\n")
    parts.append(PROMPT_FOOTER.format(trend_context=trend_context))
    if LANG != "zh":
        parts.append(LANGUAGE_DIRECTIVE.format(language=LANGUAGES[LANG]["name"]))
    parts.append("\n## 今日原始数据\n")
    for key in enabled:
        parts.append(f"\n### {DATA_LABELS[key].format(subreddits=_subreddits())}\n{data_blocks[key]}\n")
    return "".join(parts)


def _load_raw_intel() -> dict:
    if not RAW_INTEL_PATH.exists():
        raise FileNotFoundError(f"{RAW_INTEL_PATH} not found. Run fetcher.py first.")
    with open(RAW_INTEL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_history_context() -> str:
    """Read last 7 days of report headlines for trend continuity."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    snippets = []

    for days_ago in range(1, 8):
        past_date = today - timedelta(days=days_ago)
        hist_file = HISTORY_DIR / f"{past_date}.md"
        if not hist_file.exists():
            continue
        content = hist_file.read_text(encoding="utf-8")
        # Extract only the first 600 chars as a summary snippet
        snippet = content[:600].replace("\n", " ").strip()
        snippets.append(f"- {past_date}: {snippet}...")

    if not snippets:
        return ""
    return TREND_CONTEXT_TEMPLATE.format(history_summary="\n".join(snippets))


def _save_to_history(content: str, report_date: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = HISTORY_DIR / f"{report_date}.md"
    hist_path.write_text(content, encoding="utf-8")
    log.info("History saved to %s", hist_path)


def _format_github(items: list[dict]) -> str:
    lines = []
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        by_cat.setdefault(item.get("category", "all"), []).append(item)
    for cat, cat_items in by_cat.items():
        lines.append(f"\n**{cat.upper()}**")
        for item in cat_items[:SOURCES['github_trending']['max_items']]:
            lines.append(
                f"- [{item['name']}]({item['url']}) | "
                f"{item.get('language','?')} | "
                f"⭐{item.get('stars','?')} (+{item.get('stars_today','?')}) | "
                f"{item.get('description','')}"
            )
    return "\n".join(lines) or "（今日无新项目）"


def _format_hf_models(items: list[dict]) -> str:
    lines = []
    for item in items[:SOURCES['huggingface_models']['max_items']]:
        pipeline = item.get("pipeline_tag", "unknown")
        likes    = item.get("likes", 0)
        dl       = item.get("downloads", 0)
        dl_str   = f"{dl // 1000}k" if dl >= 1000 else str(dl)
        lines.append(
            f"- [{item['title']}]({item['url']}) "
            f"`{pipeline}` ❤️{likes} ⬇️{dl_str}/月"
        )
    return "\n".join(lines) or "（今日无热门模型）"


def _format_reddit(items: list[dict]) -> str:
    lines = []
    for item in items[:SOURCES['reddit']['max_items']]:
        title      = item.get("title", "")
        url        = item.get("url", "")
        reddit_url = item.get("reddit_url", url)
        # Always show Reddit discussion link so model knows it's community signal
        lines.append(f"- [{title}]({url}) ([讨论]({reddit_url}))")
    return "\n".join(lines) or "（今日无热帖）"


def _format_hf(items: list[dict]) -> str:
    lines = []
    for item in items[:SOURCES['huggingface_papers']['max_items']]:
        lines.append(
            f"- [{item['title']}]({item['url']})\n"
            f"  Abstract: {item.get('abstract','')}"
        )
    return "\n".join(lines) or "（今日无新论文）"


def _format_hn(items: list[dict]) -> str:
    lines = []
    for item in items[:SOURCES['hacker_news']['max_items']]:
        lines.append(
            f"- [{item['title']}]({item['url']}) "
            f"👍{item.get('points',0)} 💬{item.get('comments',0)}"
        )
    return "\n".join(lines) or "（今日无新条目）"


def _format_ph(items: list[dict]) -> str:
    lines = []
    for item in items[:SOURCES['product_hunt']['max_items']]:
        lines.append(
            f"- [{item['title']}]({item['url']}): {item.get('description','')}"
        )
    return "\n".join(lines) or "（今日无新产品）"


# With DeepSeek-style thinking on, reasoning_tokens and the report share one
# max_tokens budget (effort can't be dialed below "high"), so llm.max_tokens must
# cover a full reasoning pass plus the ~3000-token report. If the answer still
# comes back empty, retry once without thinking on a smaller budget.
FALLBACK_MAX_TOKENS = 3500


def _create_completion(client, prompt: str, *, thinking: bool | None, max_tokens: int):
    """thinking=None: leave the provider's default (no thinking parameter sent)."""
    kwargs = dict(
        model=MODEL,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=max_tokens,
        **thinking_body(thinking),
    )
    if not thinking:
        # thinking mode doesn't support sampling params at all
        kwargs["temperature"] = 0.4
    return client.chat.completions.create(**kwargs)


def _log_usage(label: str, response) -> str:
    content = response.choices[0].message.content or ""
    usage = response.usage
    reasoning_tokens = None
    if usage and usage.completion_tokens_details:
        reasoning_tokens = usage.completion_tokens_details.reasoning_tokens
    log.info(
        "%s: %d content chars, completion_tokens=%s, reasoning_tokens=%s, finish_reason=%s",
        label, len(content),
        getattr(usage, "completion_tokens", "?"), reasoning_tokens,
        response.choices[0].finish_reason,
    )
    return content


def _call_api(client, prompt: str) -> str:
    thinking = CONFIG["llm"]["thinking"]   # True / False / None (not sent)
    log.info("Calling API model=%s (thinking=%s) ...", MODEL, thinking)
    try:
        response = _create_completion(client, prompt, thinking=thinking, max_tokens=CONFIG["llm"]["max_tokens"])
    except Exception as exc:
        log.error("API call failed (model=%s): %s", MODEL, exc)
        raise
    content = _log_usage("Response", response)

    if not content.strip() and thinking is not False:
        log.warning(
            "Empty content — reasoning likely consumed the %d-token budget. "
            "Retrying %s.", CONFIG["llm"]["max_tokens"],
            "with thinking disabled" if thinking else "once",
        )
        retry_thinking = False if thinking else None
        try:
            response = _create_completion(client, prompt, thinking=retry_thinking, max_tokens=FALLBACK_MAX_TOKENS)
        except Exception as exc:
            log.error("Retry API call failed (model=%s): %s", MODEL, exc)
            raise
        content = _log_usage("Retry response", response)
        if not content.strip():
            log.error("Retry also returned empty content. Full response: %s", response.model_dump_json())

    return content


def run() -> None:
    client = llm_client()

    data        = _load_raw_intel()
    report_date = data.get("date", str(date.today()))
    all_items   = data["items"]

    # Only analyze new items
    new_items = [i for i in all_items if i.get("is_new", True)]
    log.info(
        "Total items: %d, new: %d, skipped (already seen): %d",
        len(all_items), len(new_items), len(all_items) - len(new_items)
    )

    if len(new_items) < MIN_NEW_ITEMS:
        log.info("Only %d new items (threshold %d) — skipping report.", len(new_items), MIN_NEW_ITEMS)
        print(f"⏭️  新条目不足 {MIN_NEW_ITEMS} 条，跳过今日报告。")
        return

    def of(source: str) -> list[dict]:
        return [i for i in new_items if i.get("source") == source]

    data_blocks = {
        "github_trending":    _format_github(of("github_trending")),
        "huggingface_models": _format_hf_models(of("hf_trending_models")),
        "huggingface_papers": _format_hf(of("hf_daily_papers")),
        "hacker_news":        _format_hn(of("hacker_news")),
        "reddit":             _format_reddit(of("reddit")),
        "product_hunt":       _format_ph(of("product_hunt")),
    }
    prompt = _build_prompt(report_date, _load_history_context(), data_blocks)

    report_content = _call_api(client, prompt)
    if not report_content.strip():
        raise RuntimeError("Model returned an empty report.")

    REPORT_PATH.write_text(report_content, encoding="utf-8")
    _save_to_history(content=report_content, report_date=report_date)
    log.info("Report saved to %s and %s/%s.md (%d chars)",
             REPORT_PATH, HISTORY_DIR, report_date, len(report_content))


if __name__ == "__main__":
    run()

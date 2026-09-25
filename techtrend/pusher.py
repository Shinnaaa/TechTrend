"""
pusher.py — deliver the daily briefing.

  python pusher.py           send DAILY_REPORT.md to every configured channel
  python pusher.py --test    send a short test message (check your setup)
  python pusher.py --list    show which channels are configured

Channels and their secrets are listed in notifiers.py and the README.
"""

import argparse
import logging
import sys
from datetime import date

from .config import CONFIG, REPORT_PATH, lang_text
from .notifiers import CHANNELS, active_channels, send_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TEST_MESSAGE = {
    "zh": "## ✅ TechTrend 推送测试\n\n收到这条消息，说明这个渠道已配置成功。每日简报会以同样的方式送达。",
    "en": "## ✅ TechTrend test message\n\nIf you can read this, the channel works. Daily briefings will arrive the same way.",
    "ja": "## ✅ TechTrend テスト送信\n\nこのメッセージが届いていれば、チャンネルの設定は完了です。毎日のブリーフィングも同じ形で届きます。",
}


def list_channels() -> None:
    active = {c.name for c in active_channels()}
    for c in CHANNELS:
        mark = "✅" if c.name in active else "  "
        secrets = ", ".join(c.required) + (f"  (optional: {', '.join(c.optional)})" if c.optional else "")
        print(f"{mark} {c.name:<11} {c.label:<24} {secrets}")


def deliver(title: str, text: str) -> int:
    channels = active_channels()
    if not channels:
        print("::warning::No push channel configured — set the secrets of at least one channel (see README).")
        return 0
    ok, failed = send_all(title, text)
    print(f"Delivered via: {', '.join(ok) or '—'}" + (f"; failed: {', '.join(failed)}" if failed else ""))
    # Fail the job only when nothing got through, so one broken channel doesn't block the rest.
    return 1 if failed and not ok else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test", action="store_true", help="send a test message")
    parser.add_argument("--list", action="store_true", help="list channels and whether they are configured")
    args = parser.parse_args()

    if args.list:
        list_channels()
        return 0
    if args.test:
        return deliver("TechTrend ✅", TEST_MESSAGE[CONFIG["report"]["language"]])

    if not REPORT_PATH.exists():
        log.info("%s not found — summarizer skipped, nothing to push.", REPORT_PATH)
        print("⏭️  No report today, nothing to push.")
        return 0
    content = REPORT_PATH.read_text(encoding="utf-8")
    log.info("Report loaded: %d chars", len(content))
    return deliver(f"{lang_text('daily_title')} · {date.today()}", content)


if __name__ == "__main__":
    sys.exit(main())

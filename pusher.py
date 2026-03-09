"""
pusher.py — PushPlus Push
Reads DAILY_REPORT.md and sends via PushPlus API.
Env var: PUSHPLUS_TOKEN
"""

import logging
import os
from datetime import date
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REPORT_PATH = Path("DAILY_REPORT.md")
PUSHPLUS_API = "http://www.pushplus.plus/send"
TIMEOUT = 15.0


def run() -> None:
    token = os.getenv("PUSHPLUS_TOKEN")
    if not token:
        raise EnvironmentError("PUSHPLUS_TOKEN environment variable is not set.")

    if not REPORT_PATH.exists():
        raise FileNotFoundError(
            f"{REPORT_PATH} not found. Run summarizer.py first."
        )

    content = REPORT_PATH.read_text(encoding="utf-8")
    log.info("Report loaded: %d chars", len(content))

    payload = {
        "token": token,
        "title": f"每日技术情报简报 · {date.today()}",
        "content": content,
        "template": "markdown",
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(PUSHPLUS_API, json=payload)
            resp.raise_for_status()
            result = resp.json()

        if result.get("code") == 200:
            print("✅ 推送成功：报告已发送到 PushPlus。")
            log.info("PushPlus response: %s", result.get("msg"))
        else:
            print(f"⚠️  推送失败：{result.get('msg', result)}")
            log.error("PushPlus error: %s", result)

    except httpx.HTTPError as exc:
        log.error("HTTP error: %s", exc)
        print(f"❌ 推送异常：{exc}")


if __name__ == "__main__":
    run()

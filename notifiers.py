"""
notifiers.py — delivery channels.

A channel is active when all of its required environment variables (GitHub
secrets) are set; config.yml `notify.channels` can narrow that down. Long reports
are split at section boundaries to fit each platform's message limit, and the
Markdown is converted where a platform speaks a different dialect.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import os
import re
import smtplib
import time
import urllib.parse
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable

import httpx
import markdown as md_lib

from config import CONFIG

log = logging.getLogger(__name__)
TIMEOUT = 20.0


class ChannelError(RuntimeError):
    pass


# ── Text helpers ─────────────────────────────────────────────────────────────

def chunk(text: str, limit: int, size: Callable[[str], int] = len) -> list[str]:
    """Split text into pieces no larger than `limit`, preferring section, then line breaks."""
    pieces: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current.strip():
            pieces.append(current.strip("\n"))
        current = ""

    sections = re.split(r"(?m)^(?=## )", text)
    for section in sections:
        for line in section.splitlines(keepends=True):
            if size(current + line) <= limit:
                current += line
                continue
            if line.startswith("## ") or size(line) > limit or current:
                flush()
            while size(line) > limit:  # a single line longer than the limit
                cut = limit
                while size(line[:cut]) > limit:
                    cut -= max(1, cut // 10)
                pieces.append(line[:cut])
                line = line[cut:]
            current = line
        if size(current) > limit * 0.6:  # start the next section in a fresh message
            flush()
    flush()
    return pieces or [""]


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _inline(text: str, bold: str, link: str, code: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", bold, text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", link, text)
    return re.sub(r"`([^`]+)`", code, text)


def to_telegram_html(text: str) -> str:
    out = []
    for line in html.escape(text, quote=False).splitlines():
        heading = re.match(r"#{1,6}\s+(.*)", line)
        line = f"**{heading.group(1)}**" if heading else line
        line = line.replace("- [ ] ", "☐ ")
        out.append(_inline(line, r"<b>\1</b>", r'<a href="\2">\1</a>', r"<code>\1</code>"))
    return "\n".join(out)


def to_slack_mrkdwn(text: str) -> str:
    out = []
    for line in text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").splitlines():
        heading = re.match(r"#{1,6}\s+(.*)", line)
        line = f"**{heading.group(1)}**" if heading else line
        out.append(_inline(line, r"*\1*", r"<\2|\1>", r"`\1`"))
    return "\n".join(out)


def headings_to_bold(text: str) -> str:
    """For renderers without heading support (Feishu cards)."""
    return re.sub(r"(?m)^#{1,6}\s+(.*)$", r"**\1**", text)


def _post_json(url: str, payload: dict, **kwargs) -> httpx.Response:
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(url, json=payload, **kwargs)
    resp.raise_for_status()
    return resp


def _expect(ok: bool, resp: httpx.Response):
    if not ok:
        raise ChannelError(resp.text[:300])


# ── Channels ─────────────────────────────────────────────────────────────────

def send_pushplus(title: str, text: str, env: dict):
    resp = _post_json("https://www.pushplus.plus/send", {
        "token": env["PUSHPLUS_TOKEN"], "title": title, "content": text, "template": "markdown",
    })
    _expect(resp.json().get("code") == 200, resp)


def send_serverchan(title: str, text: str, env: dict):
    key = env["SERVERCHAN_SENDKEY"]
    match = re.match(r"sctp(\d+)t", key)
    url = (f"https://{match.group(1)}.push.ft07.com/send/{key}.send" if match
           else f"https://sctapi.ftqq.com/{key}.send")
    resp = _post_json(url, {"title": title[:32], "desp": text})
    _expect(resp.json().get("code") == 0, resp)


def send_wecom(title: str, text: str, env: dict):
    for i, part in enumerate(chunk(f"# {title}\n\n{text}", 4000, utf8_len)):
        resp = _post_json(env["WECOM_WEBHOOK_URL"], {"msgtype": "markdown", "markdown": {"content": part}})
        _expect(resp.json().get("errcode") == 0, resp)


def send_feishu(title: str, text: str, env: dict):
    parts = chunk(headings_to_bold(text), 15000, utf8_len)
    for i, part in enumerate(parts):
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title + (f" ({i + 1}/{len(parts)})" if len(parts) > 1 else "")}},
                "elements": [{"tag": "markdown", "content": part}],
            },
        }
        if env.get("FEISHU_SECRET"):
            ts = str(int(time.time()))
            key = f"{ts}\n{env['FEISHU_SECRET']}".encode()
            payload["timestamp"] = ts
            payload["sign"] = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
        resp = _post_json(env["FEISHU_WEBHOOK_URL"], payload)
        body = resp.json()
        _expect(body.get("code", body.get("StatusCode")) == 0, resp)


def send_dingtalk(title: str, text: str, env: dict):
    url = env["DINGTALK_WEBHOOK_URL"]
    for part in chunk(f"# {title}\n\n{text}", 18000, utf8_len):
        target = url
        if env.get("DINGTALK_SECRET"):
            ts = str(int(time.time() * 1000))
            secret = env["DINGTALK_SECRET"]
            digest = hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
            target += f"&timestamp={ts}&sign={urllib.parse.quote_plus(base64.b64encode(digest))}"
        resp = _post_json(target, {"msgtype": "markdown", "markdown": {"title": title, "text": part}})
        _expect(resp.json().get("errcode") == 0, resp)


def send_telegram(title: str, text: str, env: dict):
    url = f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage"
    for part in chunk(f"## {title}\n\n{text}", 3500):
        resp = _post_json(url, {
            "chat_id": env["TELEGRAM_CHAT_ID"], "text": to_telegram_html(part),
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })
        _expect(resp.json().get("ok") is True, resp)


def send_discord(title: str, text: str, env: dict):
    for part in chunk(f"## {title}\n\n{text}", 1900):
        # flags=4 suppresses link previews, otherwise every link becomes an embed
        _post_json(env["DISCORD_WEBHOOK_URL"], {"content": part, "flags": 4})


def send_slack(title: str, text: str, env: dict):
    for part in chunk(f"## {title}\n\n{text}", 3000):
        resp = _post_json(env["SLACK_WEBHOOK_URL"], {"text": to_slack_mrkdwn(part)})
        _expect(resp.text.strip() == "ok", resp)


def send_email(title: str, text: str, env: dict):
    host = env["SMTP_HOST"]
    port = int(env.get("SMTP_PORT") or 465)
    sender = env.get("EMAIL_FROM") or env["SMTP_USERNAME"]
    recipients = [r.strip() for r in env["EMAIL_TO"].split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = title, sender, ", ".join(recipients)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    body = md_lib.markdown(text, extensions=["nl2br", "sane_lists"])
    msg.attach(MIMEText(f'<div style="font-family:sans-serif;line-height:1.6;max-width:720px">{body}</div>', "html", "utf-8"))

    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(host, port, timeout=TIMEOUT) as server:
        if port != 465:
            server.starttls()
        server.login(env["SMTP_USERNAME"], env["SMTP_PASSWORD"])
        server.sendmail(sender, recipients, msg.as_string())


def send_ntfy(title: str, text: str, env: dict):
    server = (env.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {"Authorization": f"Bearer {env['NTFY_TOKEN']}"} if env.get("NTFY_TOKEN") else {}
    parts = chunk(text, 3800, utf8_len)
    for i, part in enumerate(parts):
        _post_json(server, {
            "topic": env["NTFY_TOPIC"], "markdown": True, "message": part,
            "title": title + (f" ({i + 1}/{len(parts)})" if len(parts) > 1 else ""),
        }, headers=headers)


def send_webhook(title: str, text: str, env: dict):
    _post_json(env["WEBHOOK_URL"], {
        "title": title, "content": text, "format": "markdown", "language": CONFIG["report"]["language"],
    })


@dataclass(frozen=True)
class Channel:
    name: str
    label: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    send: Callable[[str, str, dict], None]


CHANNELS = [
    Channel("pushplus",   "WeChat (PushPlus)",        ("PUSHPLUS_TOKEN",), (), send_pushplus),
    Channel("serverchan", "WeChat (ServerChan)",      ("SERVERCHAN_SENDKEY",), (), send_serverchan),
    Channel("wecom",      "WeCom group bot",          ("WECOM_WEBHOOK_URL",), (), send_wecom),
    Channel("feishu",     "Feishu / Lark group bot",  ("FEISHU_WEBHOOK_URL",), ("FEISHU_SECRET",), send_feishu),
    Channel("dingtalk",   "DingTalk group bot",       ("DINGTALK_WEBHOOK_URL",), ("DINGTALK_SECRET",), send_dingtalk),
    Channel("telegram",   "Telegram",                 ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), (), send_telegram),
    Channel("discord",    "Discord",                  ("DISCORD_WEBHOOK_URL",), (), send_discord),
    Channel("slack",      "Slack",                    ("SLACK_WEBHOOK_URL",), (), send_slack),
    Channel("email",      "Email (SMTP)",             ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_TO"),
                                                      ("SMTP_PORT", "EMAIL_FROM"), send_email),
    Channel("ntfy",       "ntfy",                     ("NTFY_TOPIC",), ("NTFY_SERVER", "NTFY_TOKEN"), send_ntfy),
    Channel("webhook",    "Custom webhook",           ("WEBHOOK_URL",), (), send_webhook),
]


def active_channels(environ: dict | None = None) -> list[Channel]:
    environ = os.environ if environ is None else environ
    allow = set(CONFIG["notify"]["channels"] or [])
    unknown = allow - {c.name for c in CHANNELS}
    if unknown:
        log.warning("Unknown channel(s) in notify.channels: %s", ", ".join(sorted(unknown)))
    return [c for c in CHANNELS
            if all(environ.get(v) for v in c.required) and (not allow or c.name in allow)]


def send_all(title: str, text: str, environ: dict | None = None) -> tuple[list[str], list[str]]:
    """Send to every active channel. Returns (succeeded, failed) channel names."""
    environ = dict(os.environ if environ is None else environ)
    ok, failed = [], []
    for channel in active_channels(environ):
        try:
            channel.send(title, text, environ)
            ok.append(channel.name)
            log.info("✅ %s: sent", channel.label)
        except Exception as exc:
            failed.append(channel.name)
            log.error("❌ %s: %s", channel.label, exc)
            print(f"::warning::{channel.label} delivery failed: {exc}")
    return ok, failed

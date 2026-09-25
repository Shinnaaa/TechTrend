import email
import json
import re

import httpx
import pytest

from techtrend import notifiers
from techtrend.notifiers import CHANNELS, active_channels, chunk, send_all, to_slack_mrkdwn, to_telegram_html, utf8_len

REPORT = "\n\n".join(
    f"## Section {s}\n\n" + "\n".join(
        f"**[项目{s}-{i}](https://example.com/{s}/{i})** `Python` ⭐{i}\n💡 一段很长的点评文字，" * 1 + "说明" * 40
        for i in range(12)
    )
    for s in range(6)
)


@pytest.fixture
def captured(monkeypatch):
    """Route every httpx request to a fake server that answers like each platform does on success."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        host = request.url.host
        if "pushplus" in host:
            return httpx.Response(200, json={"code": 200, "msg": "ok"})
        if "ftqq" in host or "ft07" in host:
            return httpx.Response(200, json={"code": 0})
        if "qyapi" in host or "dingtalk" in host:
            return httpx.Response(200, json={"errcode": 0})
        if "feishu" in host or "larksuite" in host:
            return httpx.Response(200, json={"code": 0})
        if "telegram" in host:
            return httpx.Response(200, json={"ok": True})
        if "slack" in host:
            return httpx.Response(200, text="ok")
        return httpx.Response(204)

    real_client = httpx.Client
    monkeypatch.setattr(notifiers.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return requests


ENV = {
    "PUSHPLUS_TOKEN": "t",
    "SERVERCHAN_SENDKEY": "SCT123",
    "WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k",
    "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/x", "FEISHU_SECRET": "s",
    "DINGTALK_WEBHOOK_URL": "https://oapi.dingtalk.com/robot/send?access_token=x", "DINGTALK_SECRET": "s",
    "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42",
    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/x",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/x",
    "NTFY_TOPIC": "techtrend-test",
    "WEBHOOK_URL": "https://example.com/hook",
}


def test_chunk_respects_limit_and_keeps_all_text():
    for limit, size in [(1900, len), (4000, utf8_len), (300, len)]:
        parts = chunk(REPORT, limit, size)
        assert all(size(p) <= limit for p in parts)
        assert re.sub(r"\s", "", "".join(parts)) == re.sub(r"\s", "", REPORT)


def test_chunk_prefers_section_boundaries():
    parts = chunk(REPORT, 6000, utf8_len)
    assert all(p.startswith("## ") for p in parts)


def test_markdown_conversions():
    line = "## Title\n**[name](https://x.io)** `py` 1 < 2 & - [ ] todo"
    tg = to_telegram_html(line)
    assert "<b>Title</b>" in tg and '<b><a href="https://x.io">name</a></b>' in tg
    assert "<code>py</code>" in tg and "1 &lt; 2 &amp;" in tg and "☐ todo" in tg
    slack = to_slack_mrkdwn(line)
    assert "*Title*" in slack and "*<https://x.io|name>*" in slack and "1 &lt; 2 &amp;" in slack


def test_all_http_channels_deliver_within_limits(captured):
    ok, failed = send_all("每日技术情报简报 · 2026-09-25", REPORT, ENV)
    assert failed == []
    assert set(ok) == {c.name for c in CHANNELS} - {"email"}

    by_host = {}
    for r in captured:
        by_host.setdefault(r.url.host, []).append(r)

    for r in by_host["discord.com"]:
        assert len(json.loads(r.content)["content"]) <= 2000
    for r in by_host["api.telegram.org"]:
        body = json.loads(r.content)
        assert len(body["text"]) <= 4096 and body["parse_mode"] == "HTML"
    for r in by_host["qyapi.weixin.qq.com"]:
        assert utf8_len(json.loads(r.content)["markdown"]["content"]) <= 4096
    for r in by_host["oapi.dingtalk.com"]:
        assert "timestamp=" in str(r.url) and "sign=" in str(r.url)
    feishu = json.loads(by_host["open.feishu.cn"][0].content)
    assert feishu["msg_type"] == "interactive" and "sign" in feishu
    ntfy = json.loads(by_host["ntfy.sh"][0].content)
    assert ntfy["topic"] == "techtrend-test" and ntfy["markdown"] is True
    assert len(by_host["discord.com"]) > 1  # long report was split


def test_failed_channel_does_not_stop_others(monkeypatch, captured):
    def boom(title, text, env):
        raise RuntimeError("down")
    monkeypatch.setattr(notifiers, "CHANNELS", [c if c.name != "slack" else c.__class__(
        c.name, c.label, c.required, c.optional, boom) for c in CHANNELS])
    ok, failed = send_all("t", "body", ENV)
    assert failed == ["slack"] and "discord" in ok


def test_active_channels_need_every_required_secret(monkeypatch):
    env = {"TELEGRAM_BOT_TOKEN": "x"}  # chat id missing
    assert active_channels(env) == []
    env["TELEGRAM_CHAT_ID"] = "1"
    assert [c.name for c in active_channels(env)] == ["telegram"]
    monkeypatch.setitem(notifiers.CONFIG["notify"], "channels", ["email"])
    assert active_channels(env) == []


def test_email(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["server"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, pw): sent["login"] = user
        def sendmail(self, frm, to, msg): sent.update(frm=frm, to=to, msg=msg)

    monkeypatch.setattr(notifiers.smtplib, "SMTP_SSL", FakeSMTP)
    notifiers.send_email("标题", "## Hi\n**[a](https://b.c)**", {
        "SMTP_HOST": "smtp.example.com", "SMTP_USERNAME": "me@example.com",
        "SMTP_PASSWORD": "pw", "EMAIL_TO": "a@x.com, b@y.com",
    })
    assert sent["server"] == ("smtp.example.com", 465) and sent["to"] == ["a@x.com", "b@y.com"]
    parts = {p.get_content_type(): p.get_payload(decode=True).decode()
             for p in email.message_from_string(sent["msg"]).walk() if not p.is_multipart()}
    assert '<a href="https://b.c">a</a>' in parts["text/html"] and "<h2>Hi</h2>" in parts["text/html"]
    assert parts["text/plain"].startswith("## Hi")

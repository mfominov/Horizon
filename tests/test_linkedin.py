import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx

from src.models import LinkedInInboxConfig, SourceType
from src.scrapers.linkedin import LinkedInInboxScraper


def run(coro):
    return asyncio.run(coro)


def config(tmp_path, **overrides):
    values = {
        "enabled": True,
        "bot_token_env": "LINKEDIN_INBOX_BOT_TOKEN",
        "allowed_chat_ids": [12345],
        "state_path": str(tmp_path / "linkedin-offset.json"),
        "fetch_limit": 25,
        "category": "linkedin-inbox",
        "profile": ["work-radar", "personal-radar"],
    }
    values.update(overrides)
    return LinkedInInboxConfig(**values)


def test_enabled_inbox_requires_an_allowlisted_chat(tmp_path):
    try:
        config(tmp_path, allowed_chat_ids=[])
    except ValueError as exc:
        assert "allowed_chat_id" in str(exc)
    else:
        raise AssertionError("an enabled inbox without an allow-list must fail")


def test_fetch_requires_token_without_making_a_request(tmp_path, monkeypatch):
    monkeypatch.delenv("LINKEDIN_INBOX_BOT_TOKEN", raising=False)
    requested = False

    def handler(request):
        nonlocal requested
        requested = True
        return httpx.Response(200, json={"ok": True, "result": []})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LinkedInInboxScraper(config(tmp_path), client).fetch(
                datetime.now(timezone.utc) - timedelta(hours=24)
            )

    result = run(scenario())
    assert result.status == "failure"
    assert "LINKEDIN_INBOX_BOT_TOKEN" in result.failures["configuration"]
    assert requested is False


def test_fetch_accepts_only_allowlisted_linkedin_messages_and_persists_offset(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LINKEDIN_INBOX_BOT_TOKEN", "123456:secret-token")
    now = datetime.now(timezone.utc)
    requests = []
    updates = [
        {
            "update_id": 100,
            "message": {
                "message_id": 7,
                "date": int(now.timestamp()),
                "chat": {"id": 12345, "type": "private"},
                "from": {"first_name": "Maksim", "username": "mfominov"},
                "text": "Platform release\nhttps://www.linkedin.com/posts/example-123?utm_source=share",
            },
        },
        {
            "update_id": 101,
            "message": {
                "message_id": 8,
                "date": int(now.timestamp()),
                "chat": {"id": 99999, "type": "private"},
                "text": "https://linkedin.com/posts/not-allowed",
            },
        },
        {
            "update_id": 102,
            "channel_post": {
                "message_id": 9,
                "date": int(now.timestamp()),
                "chat": {"id": 12345, "type": "channel"},
                "text": "Not LinkedIn https://example.com/project",
            },
        },
    ]

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": updates})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LinkedInInboxScraper(config(tmp_path), client).fetch(
                now - timedelta(hours=24)
            )

    result = run(scenario())

    assert result.status == "success"
    assert len(result.items) == 1
    item = result.items[0]
    assert item.source_type == SourceType.LINKEDIN
    assert item.id == "linkedin:12345:7"
    assert str(item.url).startswith("https://www.linkedin.com/posts/example-123")
    assert item.author == "Maksim"
    assert item.profile == ["work-radar", "personal-radar"]
    assert item.metadata["manual_capture"] is True
    assert item.metadata["capture_transport"] == "telegram_bot"
    request_body = json.loads(requests[0].content)
    assert request_body["offset"] == 0
    assert request_body["limit"] == 25
    assert json.loads((tmp_path / "linkedin-offset.json").read_text()) == {
        "next_offset": 103
    }


def test_fetch_resumes_from_saved_offset_and_accepts_lnkd_in(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKEDIN_INBOX_BOT_TOKEN", "token")
    state = tmp_path / "linkedin-offset.json"
    state.write_text('{"next_offset":42}')
    now = datetime.now(timezone.utc)

    def handler(request):
        assert json.loads(request.content)["offset"] == 42
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 42,
                        "message": {
                            "message_id": 3,
                            "date": int(now.timestamp()),
                            "chat": {"id": 12345},
                            "caption": "Worth reading https://lnkd.in/example",
                        },
                    }
                ],
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LinkedInInboxScraper(config(tmp_path), client).fetch(
                now - timedelta(hours=1)
            )

    result = run(scenario())
    assert len(result.items) == 1
    assert str(result.items[0].url) == "https://lnkd.in/example"
    assert json.loads(state.read_text()) == {"next_offset": 43}


def test_api_failures_do_not_expose_the_bot_token(tmp_path, monkeypatch):
    token = "123456:do-not-log-this"
    monkeypatch.setenv("LINKEDIN_INBOX_BOT_TOKEN", token)

    def handler(request):
        return httpx.Response(409, json={"ok": False, "description": "Conflict"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LinkedInInboxScraper(config(tmp_path), client).fetch(
                datetime.now(timezone.utc) - timedelta(hours=1)
            )

    result = run(scenario())
    assert result.status == "failure"
    assert "HTTP 409" in result.failures["telegram_bot"]
    assert token not in result.failures["telegram_bot"]


def test_network_failures_do_not_expose_the_bot_token(tmp_path, monkeypatch):
    token = "123456:do-not-log-this"
    monkeypatch.setenv("LINKEDIN_INBOX_BOT_TOKEN", token)

    def handler(request):
        raise httpx.ConnectError("connection failed", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LinkedInInboxScraper(config(tmp_path), client).fetch(
                datetime.now(timezone.utc) - timedelta(hours=1)
            )

    result = run(scenario())
    assert result.status == "failure"
    assert "ConnectError" in result.failures["telegram_bot"]
    assert token not in result.failures["telegram_bot"]


def test_invalid_state_fails_without_contacting_telegram(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKEDIN_INBOX_BOT_TOKEN", "token")
    (tmp_path / "linkedin-offset.json").write_text("not-json")
    requested = False

    def handler(request):
        nonlocal requested
        requested = True
        return httpx.Response(200, json={"ok": True, "result": []})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LinkedInInboxScraper(config(tmp_path), client).fetch(
                datetime.now(timezone.utc) - timedelta(hours=1)
            )

    result = run(scenario())
    assert result.status == "failure"
    assert "Invalid LinkedIn inbox state file" in result.failures["telegram_bot"]
    assert requested is False

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.models import TelegramUserChannelConfig, TelegramUserConfig
from src.scrapers.telegram_user import (
    TelegramUserScraper,
    session_file_path,
    telegram_credentials,
    validate_session_file,
)
from src.services.telegram_user_cli import _client


def run(coro):
    return asyncio.run(coro)


class AsyncMessages:
    def __init__(self, messages):
        self.messages = messages

    def __aiter__(self):
        self.iterator = iter(self.messages)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeClient:
    def __init__(self, messages=None, *, authorized=True, private=False):
        self.messages = messages or []
        self.authorized = authorized
        self.private = private
        self.connected = False
        self.disconnected = False
        self.entities = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    async def get_entity(self, reference):
        self.entities.append(reference)
        return SimpleNamespace(
            id=123456,
            username=None if self.private else "platform_news",
            title="Platform News",
        )

    async def get_input_entity(self, entity):
        return entity

    def iter_messages(self, entity, limit):
        return AsyncMessages(self.messages[:limit])


def config(tmp_path, **overrides):
    values = {
        "enabled": True,
        "session_path": str(tmp_path / "telegram_user.session"),
        "channels": [
            TelegramUserChannelConfig(
                channel="platform_news",
                fetch_limit=5,
                category="platform",
                profile=["work-radar", "personal-radar"],
            )
        ],
    }
    values.update(overrides)
    return TelegramUserConfig(**values)


def authorize(tmp_path, monkeypatch):
    session = tmp_path / "telegram_user.session"
    session.write_bytes(b"not-a-real-session")
    if os.name != "nt":
        session.chmod(0o600)
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-hash")
    return session


def test_session_path_credentials_and_permissions(tmp_path, monkeypatch):
    cfg = config(tmp_path, session_path=str(tmp_path / "telegram_user"))
    assert session_file_path(cfg.session_path).name == "telegram_user.session"
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    try:
        telegram_credentials(cfg)
    except ValueError as exc:
        assert "TELEGRAM_API_ID" in str(exc)
    else:
        raise AssertionError("missing credentials should fail")

    session = authorize(tmp_path, monkeypatch)
    assert telegram_credentials(cfg) == (12345, "test-hash")
    if os.name != "nt":
        session.chmod(0o644)
        try:
            validate_session_file(session)
        except PermissionError as exc:
            assert "0600" in str(exc)
        else:
            raise AssertionError("broad session permissions should fail")


def test_fetch_requires_credentials_without_creating_a_session(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    fake = FakeClient()
    scraper = TelegramUserScraper(config(tmp_path), lambda *_: fake)
    result = run(scraper.fetch(datetime.now(timezone.utc) - timedelta(hours=1)))
    assert result.status == "failure"
    assert "TELEGRAM_API_ID" in result.failures["configuration"]
    assert not (tmp_path / "telegram_user.session").exists()
    assert not fake.connected


def test_status_client_refuses_to_create_a_missing_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-hash")
    session = tmp_path / "telegram_user.session"

    try:
        _client(config(tmp_path))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("status client should require an existing session")

    assert not session.exists()


def test_fetch_reads_text_only_and_stops_at_time_window(tmp_path, monkeypatch):
    authorize(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    messages = [
        SimpleNamespace(
            id=10,
            date=now,
            raw_text="New release https://example.com/release",
            post_author="Maintainer",
        ),
        SimpleNamespace(id=9, date=now, raw_text="", message="", post_author=None),
        SimpleNamespace(
            id=8,
            date=now - timedelta(days=2),
            raw_text="Old message",
            post_author=None,
        ),
    ]
    fake = FakeClient(messages)
    scraper = TelegramUserScraper(config(tmp_path), lambda *_: fake)
    result = run(scraper.fetch(now - timedelta(hours=24)))

    assert result.status == "success"
    assert fake.connected and fake.disconnected
    assert fake.entities == ["platform_news"]
    assert len(result.items) == 1
    item = result.items[0]
    assert item.source_type.value == "telegram_user"
    assert str(item.url) == "https://example.com/release"
    assert item.metadata["msg_url"] == "https://t.me/platform_news/10"
    assert item.author == "Maintainer"
    assert item.profile == ["work-radar", "personal-radar"]


def test_private_channel_uses_members_only_message_link(tmp_path, monkeypatch):
    authorize(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    fake = FakeClient(
        [SimpleNamespace(id=7, date=now, raw_text="Private update", post_author=None)],
        private=True,
    )
    scraper = TelegramUserScraper(config(tmp_path), lambda *_: fake)
    item = run(scraper.fetch(now - timedelta(hours=1))).items[0]
    assert str(item.url) == "https://t.me/c/123456/7"
    assert item.metadata["authenticated"] is True


def test_unauthorized_session_is_reported_and_disconnected(tmp_path, monkeypatch):
    authorize(tmp_path, monkeypatch)
    fake = FakeClient(authorized=False)
    result = run(TelegramUserScraper(config(tmp_path), lambda *_: fake).fetch(
        datetime.now(timezone.utc) - timedelta(hours=1)
    ))
    assert result.status == "failure"
    assert "not authorized" in result.failures["authorization"]
    assert fake.disconnected

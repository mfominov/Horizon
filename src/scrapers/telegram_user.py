"""Read Telegram channels and groups through an authenticated user session."""

from __future__ import annotations

import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from telethon import TelegramClient

from .base import BaseScraper, FetchResult
from ..models import (
    ContentItem,
    SourceType,
    TelegramUserChannelConfig,
    TelegramUserConfig,
)


ClientFactory = Callable[[str, int, str], Any]
_EXTERNAL_URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}


def session_file_path(configured_path: str) -> Path:
    """Return the concrete SQLite session file used by Telethon."""
    path = Path(configured_path).expanduser()
    return path if path.suffix == ".session" else Path(f"{path}.session")


def telegram_credentials(config: TelegramUserConfig) -> tuple[int, str]:
    """Resolve MTProto application credentials without returning secret values in errors."""
    api_id_raw = os.getenv(config.api_id_env, "").strip()
    api_hash = os.getenv(config.api_hash_env, "").strip()
    if not api_id_raw:
        raise ValueError(f"Missing environment variable: {config.api_id_env}")
    if not api_hash:
        raise ValueError(f"Missing environment variable: {config.api_hash_env}")
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError(f"{config.api_id_env} must be an integer") from exc
    if api_id <= 0:
        raise ValueError(f"{config.api_id_env} must be positive")
    return api_id, api_hash


def validate_session_file(path: Path) -> None:
    """Require an existing private session instead of creating one during collection."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Telegram user session not found: {path}. Run 'horizon-telegram login'."
        )
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionError(f"Telegram user session must have mode 0600: {path}")


class TelegramUserScraper(BaseScraper):
    """Fetch text messages visible to a logged-in Telegram user.

    The adapter intentionally exposes no send, edit, delete, join, or reaction
    operations. Authentication is performed separately by ``horizon-telegram``.
    """

    def __init__(
        self,
        config: TelegramUserConfig,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        super().__init__(config.model_dump(), None)  # type: ignore[arg-type]
        self.telegram_config = config
        self.client_factory = client_factory or TelegramClient

    async def fetch(self, since: datetime) -> FetchResult:
        result = FetchResult()
        if not self.telegram_config.enabled:
            return result

        try:
            api_id, api_hash = telegram_credentials(self.telegram_config)
            session_path = session_file_path(self.telegram_config.session_path)
            validate_session_file(session_path)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            result.fail("configuration", exc)
            return result

        client = self.client_factory(str(session_path), api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                result.fail(
                    "authorization",
                    RuntimeError("Telegram session is not authorized; run 'horizon-telegram login'"),
                )
                return result

            for channel in self.telegram_config.channels:
                if channel.enabled:
                    await result.collect(
                        str(channel.channel),
                        self._fetch_channel(client, channel, since),
                    )
        finally:
            await client.disconnect()
        return result

    async def _fetch_channel(
        self,
        client: Any,
        config: TelegramUserChannelConfig,
        since: datetime,
    ) -> list[ContentItem]:
        reference: str | int = config.channel
        if isinstance(reference, str):
            reference = reference.strip().lstrip("@")
            if re.fullmatch(r"-?\d+", reference):
                reference = int(reference)
        entity = await client.get_entity(reference)
        input_entity = await client.get_input_entity(entity)
        entity_id = int(getattr(entity, "id"))
        username = (getattr(entity, "username", None) or "").strip()
        title = (getattr(entity, "title", None) or username or str(entity_id)).strip()

        items: list[ContentItem] = []
        async for message in client.iter_messages(input_entity, limit=config.fetch_limit):
            published_at = self._as_utc(message.date)
            if published_at < self._as_utc(since):
                break
            text = (
                getattr(message, "raw_text", None)
                or getattr(message, "message", None)
                or ""
            ).strip()
            if not text:
                continue
            message_id = int(message.id)
            message_url = self._message_url(username, entity_id, message_id)
            canonical_url = self._external_url(text) or message_url
            items.append(
                ContentItem(
                    id=self._generate_id("telegram_user", str(entity_id), str(message_id)),
                    source_type=SourceType.TELEGRAM_USER,
                    title=self._make_title(text),
                    url=canonical_url,
                    content=text,
                    author=(getattr(message, "post_author", None) or title),
                    published_at=published_at,
                    profile=config.profile,
                    metadata={
                        "msg_url": message_url,
                        "channel": username or str(entity_id),
                        "channel_id": entity_id,
                        "channel_title": title,
                        "category": config.category,
                        "authenticated": True,
                    },
                )
            )
        return items

    @staticmethod
    def _message_url(username: str, entity_id: int, message_id: int) -> str:
        if username:
            return f"https://t.me/{username}/{message_id}"
        return f"https://t.me/c/{abs(entity_id)}/{message_id}"

    @staticmethod
    def _external_url(text: str) -> Optional[str]:
        from urllib.parse import urlsplit

        for match in _EXTERNAL_URL.finditer(text):
            candidate = match.group(0).rstrip(".,;:!?)]}")
            try:
                host = (urlsplit(candidate).hostname or "").lower()
            except ValueError:
                continue
            if host and host not in _TELEGRAM_HOSTS:
                return candidate
        return None

    @staticmethod
    def _make_title(text: str) -> str:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), text)
        return first_line if len(first_line) <= 120 else f"{first_line[:117]}..."

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

"""Manual LinkedIn intake through Telegram Bot API updates."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from .base import BaseScraper, FetchResult
from ..models import ContentItem, LinkedInInboxConfig, SourceType


_URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_LINKEDIN_HOSTS = {"linkedin.com", "lnkd.in"}


class LinkedInInboxScraper(BaseScraper):
    """Read explicitly shared LinkedIn links from allow-listed Telegram chats.

    The adapter does not automate LinkedIn. Telegram is only an operator-owned
    intake queue, and the stored offset contains no credential or message text.
    """

    def __init__(
        self,
        config: LinkedInInboxConfig,
        http_client: httpx.AsyncClient,
    ) -> None:
        super().__init__(config.model_dump(), http_client)
        self.linkedin_config = config

    async def fetch(self, since: datetime) -> FetchResult:
        result = FetchResult()
        if not self.linkedin_config.enabled:
            return result

        token = os.getenv(self.linkedin_config.bot_token_env, "").strip()
        if not token:
            result.fail(
                "configuration",
                ValueError(
                    f"Missing environment variable: {self.linkedin_config.bot_token_env}"
                ),
            )
            return result

        try:
            offset = self._read_offset()
            updates = await self._get_updates(token, offset)
            items = self._parse_updates(updates, since)
            if updates:
                self._write_offset(max(int(update["update_id"]) for update in updates) + 1)
        except Exception as exc:
            result.fail("telegram_bot", exc)
            return result

        result.items.extend(items)
        result.successful = 1
        return result

    async def _get_updates(self, token: str, offset: int) -> list[dict[str, Any]]:
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{token}/getUpdates",
                json={
                    "offset": offset,
                    "limit": self.linkedin_config.fetch_limit,
                    "timeout": 0,
                    "allowed_updates": ["message", "channel_post"],
                },
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Telegram Bot API getUpdates request failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"Telegram Bot API getUpdates returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Telegram Bot API returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            description = payload.get("description") if isinstance(payload, dict) else None
            detail = f": {description}" if isinstance(description, str) else ""
            raise RuntimeError(f"Telegram Bot API getUpdates failed{detail}")
        updates = payload.get("result")
        if not isinstance(updates, list):
            raise RuntimeError("Telegram Bot API returned an invalid result")
        return [update for update in updates if isinstance(update, dict)]

    def _parse_updates(
        self,
        updates: list[dict[str, Any]],
        since: datetime,
    ) -> list[ContentItem]:
        allowed = set(self.linkedin_config.allowed_chat_ids)
        since_utc = self._as_utc(since)
        items: list[ContentItem] = []
        for update in updates:
            message = update.get("message") or update.get("channel_post")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict) or chat.get("id") not in allowed:
                continue
            text = str(message.get("text") or message.get("caption") or "").strip()
            linkedin_url = self._linkedin_url(text)
            if not text or not linkedin_url:
                continue
            try:
                published_at = datetime.fromtimestamp(
                    int(message["date"]), tz=timezone.utc
                )
                message_id = int(message["message_id"])
                chat_id = int(chat["id"])
                update_id = int(update["update_id"])
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise RuntimeError("Telegram Bot API update is missing required fields") from exc
            if published_at < since_utc:
                continue
            items.append(
                ContentItem(
                    id=self._generate_id("linkedin", str(chat_id), str(message_id)),
                    source_type=SourceType.LINKEDIN,
                    title=self._make_title(text),
                    url=linkedin_url,
                    content=text,
                    author=self._sender_name(message),
                    published_at=published_at,
                    profile=self.linkedin_config.profile,
                    metadata={
                        "category": self.linkedin_config.category,
                        "capture_transport": "telegram_bot",
                        "capture_chat_id": chat_id,
                        "capture_message_id": message_id,
                        "capture_update_id": update_id,
                        "manual_capture": True,
                    },
                )
            )
        return items

    def _read_offset(self) -> int:
        path = Path(self.linkedin_config.state_path).expanduser()
        if not path.exists():
            return 0
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            offset = int(payload["next_offset"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid LinkedIn inbox state file: {path}") from exc
        if offset < 0:
            raise RuntimeError(f"Invalid LinkedIn inbox state file: {path}")
        return offset

    def _write_offset(self, offset: int) -> None:
        path = Path(self.linkedin_config.state_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps({"next_offset": offset}, separators=(",", ":")),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)

    @staticmethod
    def _linkedin_url(text: str) -> Optional[str]:
        for match in _URL.finditer(text):
            candidate = match.group(0).rstrip(".,;:!?)]}")
            try:
                host = (urlsplit(candidate).hostname or "").lower()
            except ValueError:
                continue
            if host in _LINKEDIN_HOSTS or host.endswith(".linkedin.com"):
                return candidate
        return None

    @staticmethod
    def _make_title(text: str) -> str:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), text)
        return first_line if len(first_line) <= 120 else f"{first_line[:117]}..."

    @staticmethod
    def _sender_name(message: dict[str, Any]) -> Optional[str]:
        sender = message.get("from")
        if not isinstance(sender, dict):
            return None
        full_name = " ".join(
            str(sender.get(field) or "").strip()
            for field in ("first_name", "last_name")
        ).strip()
        return full_name or str(sender.get("username") or "").strip() or None

    @staticmethod
    def _as_utc(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

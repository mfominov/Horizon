"""Authenticate and inspect the read-only Telegram user source."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from telethon import TelegramClient

from .._cli import add_data_dir_arguments, add_log_level_argument
from ..logging_config import configure_logging
from ..models import TelegramUserConfig
from ..scrapers.telegram_user import (
    session_file_path,
    telegram_credentials,
    validate_session_file,
)
from ..storage.manager import ConfigError, StorageManager


console = Console(stderr=True)


def _source_config(storage: StorageManager) -> TelegramUserConfig:
    config = storage.load_config()
    source = config.sources.telegram_user
    if source is None:
        raise ConfigError("Configure sources.telegram_user before using horizon-telegram")
    return source


def _client(
    config: TelegramUserConfig, *, create_session: bool = False
) -> tuple[TelegramClient, Path]:
    api_id, api_hash = telegram_credentials(config)
    session_path = session_file_path(config.session_path)
    if create_session:
        session_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        validate_session_file(session_path)
    return TelegramClient(str(session_path), api_id, api_hash), session_path


async def _login(config: TelegramUserConfig) -> None:
    previous_umask = os.umask(0o077)
    client: TelegramClient | None = None
    try:
        client, session_path = _client(config, create_session=True)
        await client.start()
        me = await client.get_me()
    finally:
        if client is not None:
            await client.disconnect()
        os.umask(previous_umask)
    if session_path.exists() and os.name != "nt":
        session_path.chmod(0o600)
    username = getattr(me, "username", None)
    label = f"@{username}" if username else f"user {me.id}"
    console.print(f"[green]Telegram session authorized for {label}.[/green]")
    console.print(f"Session saved to {session_path}. Keep this file secret.", markup=False)


async def _status(config: TelegramUserConfig) -> None:
    client, session_path = _client(config)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized; run 'horizon-telegram login'"
            )
        me = await client.get_me()
    finally:
        await client.disconnect()
    username = getattr(me, "username", None)
    console.print("Authorized: yes")
    console.print(f"Account: @{username}" if username else f"Account ID: {me.id}")
    console.print(f"Session: {session_path}", markup=False)


async def _dialogs(config: TelegramUserConfig, limit: int) -> None:
    client, session_path = _client(config)
    rows: list[tuple[str, str, str]] = []
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized; run 'horizon-telegram login'"
            )
        async for dialog in client.iter_dialogs(limit=limit):
            if not (dialog.is_channel or dialog.is_group):
                continue
            entity = dialog.entity
            rows.append(
                (
                    str(entity.id),
                    dialog.name or "",
                    f"@{entity.username}" if getattr(entity, "username", None) else "private",
                )
            )
    finally:
        await client.disconnect()

    table = Table("channel", "title", "username")
    for row in rows:
        table.add_row(*row)
    console.print(table)
    console.print("Use channel ID or username in sources.telegram_user.channels.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage Horizon's read-only Telegram user session"
    )
    add_data_dir_arguments(parser)
    add_log_level_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Authorize a Telegram user session interactively")
    commands.add_parser("status", help="Check the saved Telegram session")
    dialogs = commands.add_parser("dialogs", help="List readable groups and channels")
    dialogs.add_argument("--limit", type=int, default=100, help="Maximum dialogs to inspect")
    args = parser.parse_args()
    configure_logging(console, level=args.log_level)
    load_dotenv()

    try:
        storage = StorageManager(data_dir=args.data_dir, config_path=args.config)
        config = _source_config(storage)
        if args.command == "login":
            asyncio.run(_login(config))
        elif args.command == "status":
            asyncio.run(_status(config))
        else:
            if args.limit <= 0:
                raise ValueError("--limit must be positive")
            asyncio.run(_dialogs(config, args.limit))
    except (
        ConfigError,
        FileNotFoundError,
        PermissionError,
        RuntimeError,
        ValueError,
    ) as exc:
        console.print(f"Telegram error: {exc}", markup=False)
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("Interrupted.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_DISCORD_CONTENT_CHARS = 1900

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordCommand:
    message_id: str
    author_id: str
    content: str


def _truncate(content: str) -> str:
    cleaned = content.strip()
    if len(cleaned) <= MAX_DISCORD_CONTENT_CHARS:
        return cleaned
    return cleaned[: MAX_DISCORD_CONTENT_CHARS - 20].rstrip() + "\n...[truncated]"


class DiscordNotifier:
    def __init__(
        self,
        *,
        webhook_url: str = "",
        bot_token: str = "",
        channel_id: str = "",
        allowed_user_id: str = "",
        username: str = "TTM Hermes",
    ) -> None:
        self.webhook_url = webhook_url.strip()
        self.bot_token = bot_token.strip()
        self.channel_id = channel_id.strip()
        self.allowed_user_id = allowed_user_id.strip()
        self.username = username
        self.last_seen_message_id: str | None = None

    @property
    def can_send(self) -> bool:
        return bool(self.webhook_url or (self.bot_token and self.channel_id))

    @property
    def can_poll(self) -> bool:
        return bool(self.bot_token and self.channel_id)

    async def send(self, content: str) -> bool:
        if not self.can_send:
            logger.info("discord_not_configured")
            return False
        payload = {
            "content": _truncate(content),
            "allowed_mentions": {"parse": []},
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if self.webhook_url:
                    response = await client.post(
                        self.webhook_url,
                        params={"wait": "true"},
                        json={**payload, "username": self.username},
                    )
                else:
                    response = await client.post(
                        f"{DISCORD_API_BASE}/channels/{self.channel_id}/messages",
                        headers={"Authorization": f"Bot {self.bot_token}"},
                        json=payload,
                    )
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("discord_send_failed", extra={"error": str(exc)})
            return False

    async def fetch_commands(self, *, after_message_id: str | None = None) -> list[DiscordCommand]:
        if not self.can_poll:
            return []
        params: dict[str, str | int] = {"limit": 100}
        if after_message_id:
            params["after"] = after_message_id
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{DISCORD_API_BASE}/channels/{self.channel_id}/messages",
                    headers={"Authorization": f"Bot {self.bot_token}"},
                    params=params,
                )
                response.raise_for_status()
            messages = response.json()
        except Exception as exc:
            logger.warning("discord_poll_failed", extra={"error": str(exc)})
            return []

        if not isinstance(messages, list):
            return []
        self.last_seen_message_id = next(
            (
                str(message.get("id") or "")
                for message in messages
                if isinstance(message, dict) and message.get("id")
            ),
            None,
        )
        commands: list[DiscordCommand] = []
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            author = message.get("author")
            if not isinstance(author, dict):
                continue
            if author.get("bot") is True:
                continue
            author_id = str(author.get("id") or "")
            if self.allowed_user_id and author_id != self.allowed_user_id:
                continue
            content = str(message.get("content") or "").strip()
            lowered = content.lower()
            if lowered.startswith("!ttm") or lowered.startswith("/ttm"):
                commands.append(
                    DiscordCommand(
                        message_id=str(message.get("id") or ""),
                        author_id=author_id,
                        content=content,
                    )
                )
        return commands

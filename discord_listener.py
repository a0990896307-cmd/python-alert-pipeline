"""
Discord signal listener.

ToS-compliant: uses official Bot Account (NOT self-bot / user token).
The bot must be invited to the signal channel; it reads messages via the
Discord API, never via a logged-in user session.
"""
from __future__ import annotations

import asyncio
import logging

import discord

log = logging.getLogger(__name__)


class SignalListener(discord.Client):
    """Listens to a signal channel and forwards raw messages to the parser."""

    def __init__(self, *, channel_id: int, on_signal, intents: discord.Intents):
        super().__init__(intents=intents)
        self.channel_id = channel_id
        self.on_signal = on_signal

    async def on_ready(self) -> None:
        log.info("Discord bot online as %s (id=%s)", self.user, self.user.id)
        channel = self.get_channel(self.channel_id)
        if channel is None:
            log.error("Channel %s not found. Invite the bot to the channel first.", self.channel_id)
            return
        log.info("Listening on channel #%s", channel.name)

    async def on_message(self, message: discord.Message) -> None:
        # Ignore our own messages and non-signal chatter.
        if message.author.id == self.user.id:
            return
        if isinstance(message.channel, discord.DMChannel):
            return
        if message.channel.id != self.channel_id:
            return

        try:
            await self.on_signal(message)
        except Exception:  # noqa: BLE001 - listener must never die silently
            log.exception("Failed to process signal from %s", message.author)


async def run_listener(token: str, channel_id: int, on_signal) -> None:
    """Start the Discord listener (blocking until interrupted)."""
    intents = discord.Intents.default()
    intents.message_content = True  # needed to read message text
    client = SignalListener(channel_id=channel_id, on_signal=on_signal, intents=intents)
    await client.start(token)


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(
        run_listener(
            token=os.environ["DISCORD_TOKEN"],
            channel_id=int(os.environ["SIGNAL_CHANNEL_ID"]),
            on_signal=lambda m: log.info("SIGNAL: %s", m.content),
        )
    )
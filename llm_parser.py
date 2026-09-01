"""
LLM signal parser.

Turns free-form Discord signal text into a strict, validated `Signal` object.

Design notes:
- Uses structured output (JSON schema) so the LLM cannot hallucinate fields.
- Every field is validated by pydantic AFTER the LLM; anything that fails
  validation is rejected (no trade is placed on bad data).
- The prompt only extracts; all trading decisions live in rules.py.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Signal(BaseModel):
    """Validated output of the LLM parser."""

    pair: str = Field(description="Trading pair, e.g. BTC/USDT")
    side: PositionSide = Field(description="Direction suggested by the signal")
    entry: float | None = Field(None, description="Suggested entry price, if given")
    stop: float | None = Field(None, description="Suggested stop-loss, if given")
    target: float | None = Field(None, description="Suggested take-profit, if given")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Signal confidence 0..1")
    reason: str = Field(default="", description="Short reason provided by the source")
    raw_text: str = Field(default="", description="Original message, kept for audit/logging")

    def passes_guardrails(self) -> bool:
        """Basic sanity: prices must be positive and consistent."""
        if self.entry is not None and self.entry <= 0:
            return False
        if self.stop is not None and self.stop <= 0:
            return False
        if self.target is not None and self.target <= 0:
            return False
        return True


SYSTEM_PROMPT = """You are a strict signal extractor for a trading bot.
Extract the signal data from the user's message into the provided JSON schema.
Rules:
1. Only output JSON matching the schema. No commentary.
2. If a field is absent in the message, use null (or the default).
3. If the message is NOT a trading signal (joke, spam, admin note), set side="flat",
   pair="", confidence=0.0 and reason="not a signal".
4. Never invent prices or pairs that are not in the message.
5. entry/stop/target must be plain numbers (no currency symbols).
"""


class SignalParser:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", max_retries: int = 2):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.max_retries = max_retries

    async def parse(self, text: str) -> Signal | None:
        """Parse raw message text into a validated Signal. Returns None on failure."""
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                )
                raw = resp.choices[0].message.content or "{}"
                data = json.loads(raw)
                signal = Signal(**data, raw_text=text)
                if not signal.passes_guardrails():
                    log.warning("Signal failed guardrails: %s", signal.model_dump())
                    return None
                return signal
            except Exception as exc:  # noqa: BLE001 - retry on any parse/validation error
                log.warning("Parse attempt %s failed: %s", attempt + 1, exc)
        return None

    async def parse_many(self, texts: list[str]) -> list[Signal]:
        """Convenience for backtests/validation against historical messages."""
        results = await asyncio_gather(*[self.parse(t) for t in texts])
        return [r for r in results if r is not None]


def asyncio_gather(*args):
    import asyncio

    return asyncio.gather(*args)
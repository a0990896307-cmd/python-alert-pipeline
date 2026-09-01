"""
Trading rules engine.

Every signal passes through the client's rule set BEFORE any order is placed.
Rules are declarative YAML so the client can edit them without touching code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import yaml

from src.llm_parser import Signal

log = logging.getLogger(__name__)


@dataclass
class RuleSet:
    allowed_pairs: set[str] = field(default_factory=set)  # empty = allow all
    min_confidence: float = 0.6
    max_position_usdt: float = 100.0
    max_open_positions: int = 3
    daily_loss_limit_usdt: float = 50.0
    require_stop: bool = False  # refuse signals without a stop-loss

    @classmethod
    def from_yaml(cls, path: str) -> "RuleSet":
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class RulesEngine:
    """Applies a RuleSet to a Signal and returns a decision."""

    def __init__(self, rules: RuleSet):
        self.rules = rules
        self.daily_loss = 0.0

    def decide(self, signal: Signal) -> tuple[bool, str]:
        """Return (allow, reason). No side effects."""
        if not signal.passes_guardrails():
            return False, "guardrails failed"
        if signal.side.value == "flat" or not signal.pair:
            return False, "not a signal"
        if self.rules.allowed_pairs and signal.pair.upper() not in {p.upper() for p in self.rules.allowed_pairs}:
            return False, f"pair {signal.pair} not in allowlist"
        if signal.confidence < self.rules.min_confidence:
            return False, f"confidence {signal.confidence} < {self.rules.min_confidence}"
        if self.rules.require_stop and signal.stop is None:
            return False, "no stop-loss in signal"
        return True, "ok"

    def record_loss(self, amount: float) -> None:
        self.daily_loss += amount
        if self.daily_loss > self.rules.daily_loss_limit_usdt:
            log.error("DAILY LOSS LIMIT HIT: %s > %s — trading halted for the day",
                      self.daily_loss, self.rules.daily_loss_limit_usdt)
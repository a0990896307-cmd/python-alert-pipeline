# Python Alert Pipeline — code sample

Production-style Python modules for a 24/7 signal bot:
**Discord alert listener → LLM parsing (JSON schema) → rules engine → paper broker.**

Selected for this sample: **retry logic, validation layers, structured state,
and env-based secrets (no credentials in code)** — the same engineering patterns
I use for crash-recovery and reliable automation.

## Modules

| File | What it demonstrates |
|---|---|
| `discord_listener.py` | Async event handling, ToS-compliant bot account, error-isolated message processing (a bad signal never kills the listener) |
| `llm_parser.py` | LLM extraction with **strict JSON schema + pydantic validation + retry (max_retries)** — bad data is rejected, not trusted; no hallucinated fields pass |
| `rules.py` | Declarative rule set (YAML-backed), **position caps, daily loss limit, kill-switch logic** — stateful safety rails around every decision |

## Key engineering points

- **Crash-resilience:** every failure path is caught and logged; the listener survives individual errors (message-level isolation).
- **Structured output:** LLM responses are validated twice (schema + guardrails) before anything is executed.
- **Safety-first execution:** Phase 1 runs paper-only — nothing touches real money until validated.
- **Secrets:** all credentials come from environment variables — none are in the repository.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env   # fill tokens
python -m src.bot      # paper mode
```

Full project (docker deployment, healthchecks, broker adapters) available on request.
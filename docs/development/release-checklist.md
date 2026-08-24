# Release Checklist

SettleDiff is MIT-licensed and versioned (`0.1.0`). Wheels are built and installed
locally; no public distribution channel is selected yet. Run this gate before any
release decision.

## Pre-release gate

- [ ] `uv run pytest` passes with all default offline tests
- [ ] `uv run ruff check .`
- [ ] `uv run pyright`
- [ ] build wheel
- [ ] install wheel into clean environment
- [ ] `settlediff --version` works
- [ ] `clean-success` fixture returns expected verdict
- [ ] `paid-failure` fixture returns `PAID_FAILURE`
- [ ] `failed-broadcast` fixture returns `UNVERIFIABLE`
- [ ] exported bundle verifies
- [ ] no local `.sdbundle`, SQLite DB, WAL, credentials, or live evidence is tracked
- [ ] README live-incident claims match sanitized committed evidence

Live compatibility checks (Hyperfusion, Context.dev, paid smoke) remain explicit
opt-in commands and are not part of this gate; run them deliberately per
[testing strategy](../testing/strategy.md) when a release claims live compatibility.

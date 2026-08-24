# Live Test Cycle Report — 2026-08-21

**Scope:** First live paid runs against real Perflo/MPP vendors, the defects they
exposed, the fixes they drove, and the resulting regression coverage.
**Primary evidence:** `perflo-402-chain-mismatch.sdbundle` (run
`live_5c80dd4388804ae08762a5ea3c2442cb`, local-only artifact, untracked).

## Summary

The first live cycle validated the core thesis: a tool that moves money cannot be
trusted to report whether the purchase worked. Every ambiguous or failed paid run
returned `UNVERIFIABLE` with evidence-cited findings; no verdict was guessed and no
money-moving command was retried after an uncertain submission.

## Observed live defects

| Defect | Evidence | Deterministic outcome |
|---|---|---|
| Advertised chain (`base`) differs from executed chain (`tempo`) | contract vs. execution artifacts | `check:chain` DIFF |
| Vendor replayed the 402 challenge after credential submission; broadcast failed | Activity record `broadcast_failed`, error "payment credential rejected by vendor (402 replay)" | `check:service_execution` FAIL (upstream 402) |
| Failed broadcast produced no charge and no transaction hash | execution `charge: null`, `txHash: null` | `check:budget`/`check:price` UNKNOWN |
| No confirmed Activity record to settle against | matching found the failed record but it is not a charge | verdict `UNVERIFIABLE` |
| Perflo 4.1 envelope drift (aliases, missing timestamps, minor-unit budgets) | live CLI responses across runs | parse notes, `unknown` states preserved |
| Context.dev cold scrapes exceed the previous 10 s client timeout | provider-documented 60 s window | timeout configuration aligned |

## Fixes driven by the cycle

- `ad33652` — align paid preflight with Perflo 4.1 (envelopes, absent vendor identity,
  embedded schemas, minor-unit budget conversion)
- `4c30112` — preserve missing execution time; disable time-based fallback matching
  when execution time is unavailable
- `55750f4`, `1f7c1d1` — recognize current activity recovery and upstream response shapes
- `71b0c5b` — verify charges from confirmed activity only
- `6bf966b` — preserve missing uncertain execution evidence
- `7221c63` — charge the quoted price at the paid boundary
- `6c99ea4` — keep uncorrelated activity recovery unresolved
- `50acfb7` — accept the current transaction hash alias
- `06d73ce`, `e1788fe` — propagate the Context.dev 60 s cold-scrape window

## Regression coverage added

`fixtures/failed-broadcast/` (commit `e76ba71`) distills the 402-replay run into the
offline corpus. It pins three behaviors:

1. A `broadcast_failed` Activity record matches its own transaction ID
   (`activity_persistence: PASS`).
2. The failed record is not counted as a charge (`budget`/`price: UNKNOWN`).
3. The run remains `UNVERIFIABLE` rather than guessing settlement.

Offline gate after the change: 397 passed, 2 skipped (live/paid opt-ins excluded);
ruff and pyright clean.

## Product assessment

- The target problem occurred with real money within the first live sessions:
  settlement ambiguity, paid-but-failed service, and advertised-vs-actual contract
  drift.
- SettleDiff's independent deterministic verdict behaved correctly in each case.
- Open questions are market-level, not engineering-level: ecosystem size and timing,
  value of detection without automated recourse, and current coupling to the
  Perflo/MPP rail.

## Open issues

- Vendor-side 402 replay rejection and the base-vs-tempo chain mismatch on
  `parallelmpp.dev` remain unresolved upstream; any paid re-run requires fresh
  explicit authorization and must not retry the uncertain submission.
- Live compatibility remains an opt-in pre-release gate per ADR 0005.

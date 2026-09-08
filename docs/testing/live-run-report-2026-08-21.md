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

1. A `broadcast_failed` Activity record matches by session plus the authoritative contract vendor when execution omits vendor identity and Activity `id` is only the ledger-record ID (`activity_persistence: PASS`).
2. The failed record is not counted as a charge (`budget`/`price: UNKNOWN`).
3. The run remains `UNVERIFIABLE` rather than guessing settlement.

### 2026-09-07 reproduction — matcher defect exposed

A later reproduction (`live_6369…2d3b`) reproduced the original failure: Base was
advertised, execution used Tempo, the service returned HTTP 402, and Activity recorded
`broadcast_failed` without a transaction hash. The exported Activity artifact already
contained the correct candidate, but the report said no matching persisted record was found.

This was not an Activity visibility race. Execution omitted `vendor_slug`, while the
selected contract retained the authoritative vendor. Session/vendor matching now uses the
execution vendor or, when execution omits it, the authoritative selected contract vendor.
It never derives vendor identity from an arbitrary URL or Activity candidate. A subsequent
live run returned `activity_persistence: PASS` and validated the corrected correlation path.

### 2026-09-07 successful payment — pending Activity at verification time

The subsequent run (`live_880f…4fd`) matched its Activity record by transaction hash. At
verification time, the raw record contained:

```text
status = broadcast
confirmedAt = null
amount = $0.01
transaction hash = present
```

The transaction later appeared as `confirmed`. The verifier correctly treats the earlier
`broadcast` snapshot as `PENDING` and does not use its amount as proven charge evidence.
Canonical Activity status mapping is conservative:

```text
broadcast        → PENDING
broadcast_failed → FAILED
confirmed        → CONFIRMED
settled          → CONFIRMED
```

The `confirmed-activity-charge` fixture separately proves that only a high-confidence
matched `CONFIRMED` record with a normalized amount can supply missing execution-charge
evidence.

### 2026-09-08 validation — upstream contract now aligns on Tempo

On 2026-09-08, `perflo check` for `https://parallelmpp.dev/api/search` advertised Tempo and
`$0.01 USDC`. SettleDiff's preflight independently displayed Tempo, a `0.010000 USDC` quote,
and a `0.01 USDC` budget. One newly authorized paid request then produced:

```text
PASS: Recorded Activity amount is within the authorized budget.
PASS: Quoted price matches the recorded Activity amount.
PASS: Asset values agree across available evidence.
PASS: Protocol values agree across available evidence.
PASS: Chain values agree across available evidence.
WARN: Recipient representations differ; no provider defect is inferred.
PASS: Payment settled.
PASS: Purchased service returned a successful HTTP response.
PASS: No settled payment with a failed service response was observed.
PASS: Persisted Activity and service outcome require no additional consistency warning.
PASS: A deterministic Activity record match was found.
```

The final verdict was `VERIFIED_WITH_WARNINGS`. This run did not reproduce the historical
Base-to-Tempo disagreement.

### Three-run chronology

```text
Run 1 — original and reproduced failure
Base advertised → Tempo execution → HTTP 402 → broadcast_failed
→ settlement UNKNOWN → UNVERIFIABLE

Run 2 — matcher and pending-state validation
Base advertised → Tempo execution → successful service response
→ Activity matched after the matcher fix → broadcast/PENDING at verification
→ budget and price UNKNOWN → UNVERIFIABLE → Activity later confirmed

Run 3 — current successful validation
Tempo advertised → Tempo execution → confirmed $0.01 Activity
→ budget PASS → price PASS → settlement PASS → service PASS
→ activity persistence PASS → recipient WARN → VERIFIED_WITH_WARNINGS
```

Offline gate after the change: 664 passed, 2 skipped (live/paid opt-ins excluded); Ruff and
Pyright clean.

## Product assessment

- The target problem occurred with real money within the first live sessions:
  settlement ambiguity, paid-but-failed service, and advertised-vs-actual contract
  drift.
- SettleDiff's independent deterministic verdict behaved correctly in each case.
- Open questions are market-level, not engineering-level: ecosystem size and timing,
  value of detection without automated recourse, and current coupling to the
  Perflo/MPP rail.

## Current status

The original Base-to-Tempo disagreement and 402 replay failure remain preserved as
historical live evidence and regression fixtures. The disagreement reproduced again on
2026-09-07. By 2026-09-08, Perflo's curated contract for the same endpoint advertised
Tempo, and a fresh paid run aligned contract, execution, and Activity on Tempo.

The historical root cause was never conclusively assigned to Perflo, the vendor, MPP
routing, metadata, or another boundary. It remains an observed evidence disagreement, not
a confirmed provider defect. The previously observed disagreement no longer reproduced
after the advertised contract changed to Tempo.

Live compatibility remains an opt-in pre-release gate per ADR 0005. Every paid re-run
requires fresh explicit authorization, and an uncertain submission must never be retried.

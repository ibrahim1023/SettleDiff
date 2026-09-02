# Context.dev Live Compatibility — 2026-09-02

## Scope

The opt-in Context.dev contract used the documented Markdown scrape endpoint with an owner-approved public IANA page. It did not invoke Perflo, x402 payment or signer code, or Hyperfusion.

| Field | Authorized value |
|---|---|
| Source URL | `https://www.iana.org/help/example-domains` |
| Exact claim | `example.com and example.org` |
| Context.dev endpoint | `https://api.context.dev/v1/web/scrape/markdown` |
| Authorized requests | two separately authorized requests |
| Cost | two Context.dev credits total |

An initial local test launch failed before client construction because the contract read only the process environment instead of the application settings boundary. It made no Context.dev request and consumed no credit. The contract now obtains the key, base URL, and timeout through `Settings.require_contextdev()` while retaining separately supplied live URL, claim, and opt-in gate.

## Observed result

The first authorized request established that the current response could be parsed into coherent typed evidence. Review then showed that the contract also accepted a typed source-failure result, so it did not prove the positive scrape shape. The assertion was tightened before a separately authorized second request.

The strict request passed and established that:

- the public source was reachable through Context.dev;
- the exact claim was present;
- the returned excerpt contained the claim;
- the provider returned no error note;
- the response body was nonempty;
- the collection timestamp was timezone-aware.

The strict contract completed in 1.26 seconds. This single observation confirms compatibility with the response shape seen on that date; it does not guarantee future provider availability or response stability.

## Safety and retention

No API key, authorization header, raw provider response, or unbounded page content was printed or committed. The committed evidence is limited to the public URL and claim, bounded assertions, request count, elapsed test time, and pass/fail result. The test remains excluded from default CI and requires `SETTLEDIFF_LIVE_CONTEXTDEV=1` plus owner-supplied URL and claim. Each invocation consumes one Context.dev credit.

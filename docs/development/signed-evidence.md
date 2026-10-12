# Signed seller evidence

`src/settlediff/x402/signed.py` contains bounded, offline JWS helpers for captured
Ed25519 seller keys, signed offers, and signed receipts. Tests exercise signature
validity separately from did:web assertion authorization, seller-origin matching,
offer terms/expiry, and receipt transaction/payer correlation.

The key document is supplied as captured evidence; these helpers perform no live
DID lookup. A mathematically valid signature does not by itself establish that a
key is authorized for the requested seller origin. Both offer and receipt assessment
require the captured DID to match the authorized resource's HTTPS origin.

These helpers are not wired into the live collector, payment authorization, findings,
or verdicts. They are the first tested increment of the signed-evidence work on this
branch, not a claim that live signed-offer enforcement is complete. Provider-signed
receipts remain provider assertions rather than independent settlement proof.

```bash
uv run pytest tests/unit/x402/test_signed.py -q
```

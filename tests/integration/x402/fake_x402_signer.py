from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import cast

if sys.argv[-1] == "--version":
    print(json.dumps({"schema_version": 2, "payer": "0x3333333333333333333333333333333333333333"}))
    raise SystemExit(0)

mode = sys.argv[1]
count_path = Path(sys.argv[2])
count = int(count_path.read_text()) if count_path.exists() else 0
count_path.write_text(str(count + 1))
request = cast(dict[str, object], json.loads(sys.stdin.read()))

if mode == "timeout":
    time.sleep(1)
if mode == "invalid":
    print("not-json")
    raise SystemExit(0)
if mode in {"oversized", "oversized-sleep"}:
    print("x" * 10_000, flush=True)
    if mode == "oversized-sleep":
        time.sleep(1)
    raise SystemExit(0)

result: dict[str, object] = {
    "schema_version": 2,
    "adapter": "x402",
    "submission_state": "submitted_confirmed",
    "challenge": {"x402Version": 2},
    "provider_settlement": {"success": True},
    "service_response": {
        "status": 200,
        "body": {
            "body_digest": request["body_digest"],
            "private_key_visible": "X402_PRIVATE_KEY" in os.environ,
        },
    },
    "payment_reference": "syn_payment",
    "transaction_reference": "syn_transaction",
    "payer": "0x3333333333333333333333333333333333333333",
    "notes": [],
}
if mode == "pipeline":
    transaction = "0x" + "2" * 64
    result["challenge"] = json.loads(Path(sys.argv[3]).read_text())
    result["provider_settlement"] = {
        "success": True,
        "errorReason": None,
        "payer": "0x3333333333333333333333333333333333333333",
        "transaction": transaction,
        "network": "eip155:84532",
        "amount": "1000",
        "extensions": {},
    }
    result["transaction_reference"] = transaction
if mode == "secret":
    result["challenge"] = {"PAYMENT-SIGNATURE": "syn_signature"}
if mode == "uncertain":
    result["submission_state"] = "submission_uncertain"
    result["provider_settlement"] = None
    result["transaction_reference"] = "syn_uncertain_transaction"
if mode == "nonzero":
    result["submission_state"] = "not_submitted"
    result["provider_settlement"] = None
    result["transaction_reference"] = None

print(json.dumps(result))
if mode == "nonzero":
    print("synthetic signer diagnostic", file=sys.stderr)
    raise SystemExit(2)

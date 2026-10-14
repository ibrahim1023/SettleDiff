"""Read-only seller-proof preflight for the locally installed external signer.

Input and output travel through pipes; no seller proof or key document is saved.
The caller must bind the returned proof digest into its full payment-terms digest.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from datetime import UTC, datetime

import httpx
from pydantic import JsonValue

from settlediff.application.auth import HttpResourceReference, PaidExecutionRequest
from settlediff.application.payment_rails import AdapterProtocolError
from settlediff.domain.models import ExpectedContract
from settlediff.x402.adapter import X402Adapter
from settlediff.x402.client_contract import ExternalSignerRequest
from settlediff.x402.http import X402ResourceClient, X402ResourceResponse
from settlediff.x402.signed import parse_seller_json_object


class _SignerChallenge(X402ResourceClient):
    def __init__(
        self, client: httpx.AsyncClient, challenge: dict[str, JsonValue], now: datetime
    ) -> None:
        super().__init__(client)
        self._challenge = X402ResourceResponse(
            status_code=402,
            payment_required=base64.b64encode(json.dumps(challenge).encode()).decode(),
            body=None,
            observed_at=now,
        )

    async def challenge(self, request: PaidExecutionRequest) -> X402ResourceResponse:
        return self._challenge


async def verified_offer_digest(
    challenge: dict[str, JsonValue],
    request: ExternalSignerRequest,
    client: httpx.AsyncClient,
    *,
    now: datetime | None = None,
) -> str | None:
    paid = PaidExecutionRequest(
        run_id=request.run_id,
        resource=HttpResourceReference(
            url=request.target, method=request.method, body=request.body
        ),
        budget=request.max_budget,
    )
    adapter = X402Adapter(
        _SignerChallenge(client, challenge, now or datetime.now(UTC)),
        clock=(lambda: now) if now is not None else None,
    )
    inspected = await adapter.inspect(paid)
    return ExpectedContract.model_validate_json(json.dumps(inspected.data)).signed_offer_digest


async def main() -> None:
    content = sys.stdin.buffer.read(1_048_577)
    if len(content) > 1_048_576:
        raise ValueError("seller preflight input exceeds its bound")
    payload = parse_seller_json_object(content)
    if set(payload) != {"challenge", "request"} or not isinstance(payload["challenge"], dict):
        raise ValueError("seller preflight input fields are invalid")
    request = ExternalSignerRequest.model_validate_json(json.dumps(payload["request"]))
    async with httpx.AsyncClient(trust_env=False) as client:
        digest = await verified_offer_digest(payload["challenge"], request, client)
    print(json.dumps({"signed_offer_digest": digest}, separators=(",", ":")))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, httpx.HTTPError, AdapterProtocolError):
        sys.stderr.write("Seller proof preflight refused.\n")
        sys.exit(1)

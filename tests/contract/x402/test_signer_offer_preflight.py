"""The external signer can recheck a seller proof without exposing signing tools."""

from datetime import timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.x402_signer_offer import verified_offer_digest
from settlediff.application.auth import PaidExecutionCapability
from settlediff.application.payment_rails import AdapterProtocolError
from settlediff.x402.adapter import X402Adapter
from settlediff.x402.client_contract import ExternalSignerRequest
from tests.unit.x402.test_adapter import NOW, PAYER, request
from tests.unit.x402.test_signed_adapter import SignedResource, challenge


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "expired", "revoked", "redirect", "oversized"])
async def test_signer_offer_preflight_matches_authorized_proof_or_refuses(change: str) -> None:
    key = Ed25519PrivateKey.generate()
    value = challenge(key)
    resource = SignedResource(key, value)
    adapter = X402Adapter(resource, expected_payer=PAYER, clock=lambda: NOW)
    inspected = await adapter.inspect(request())
    paid = request()
    signer_request = ExternalSignerRequest(
        run_id=paid.run_id,
        target=paid.target,
        method=paid.method,
        body=paid.body,
        body_digest=PaidExecutionCapability.body_digest_for(paid.body),
        max_budget=paid.budget,
        network="eip155:84532",
        scheme="exact",
        payment_terms_digest="0" * 64,
    )
    document = resource.identity.document.copy()
    if change == "revoked":
        document["assertionMethod"] = []
    if change == "expired":
        value = challenge(key, validUntil=int((NOW - timedelta(seconds=1)).timestamp()))

    def respond(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert str(req.url) == "https://example.invalid/.well-known/did.json"
        if change == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.invalid/key"})
        if change == "oversized":
            return httpx.Response(200, content=b" " * 16385)
        return httpx.Response(200, json=document)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if change != "none":
            with pytest.raises((ValueError, AdapterProtocolError)):
                await verified_offer_digest(value, signer_request, client, now=NOW)
        else:
            digest = await verified_offer_digest(value, signer_request, client, now=NOW)
            assert isinstance(inspected.data, dict)
            assert digest == inspected.data["signed_offer_digest"]


@pytest.mark.asyncio
async def test_unsigned_preflight_does_not_fetch_seller_identity() -> None:
    from tests.unit.x402.test_adapter import required_payload

    paid = request()
    signer_request = ExternalSignerRequest(
        run_id=paid.run_id,
        target=paid.target,
        method=paid.method,
        body=paid.body,
        body_digest=PaidExecutionCapability.body_digest_for(paid.body),
        max_budget=paid.budget,
        network="eip155:84532",
        scheme="exact",
        payment_terms_digest="0" * 64,
    )

    def forbidden(req: httpx.Request) -> httpx.Response:
        pytest.fail("unsigned preflight must not contact a seller")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        assert (
            await verified_offer_digest(required_payload(), signer_request, client, now=NOW) is None
        )


def test_malformed_preflight_stdin_emits_no_input_material() -> None:
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[3] / "scripts/x402_signer_offer.py")],
        input=b'{"secret":"synthetic-do-not-repeat"}',
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == b"Seller proof preflight refused.\n"

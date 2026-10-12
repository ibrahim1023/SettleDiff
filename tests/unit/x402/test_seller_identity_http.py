from __future__ import annotations

import base64

import httpx
import pytest

from settlediff.x402.http import X402ResourceClient, X402ResourceError

DID = "did:web:seller.example"
KID = DID + "#key-1"
DOCUMENT = {
    "id": DID,
    "assertionMethod": [KID],
    "verificationMethod": [
        {
            "id": KID,
            "controller": DID,
            "publicKeyJwk": {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": base64.urlsafe_b64encode(b"1" * 32).rstrip(b"=").decode(),
            },
        }
    ],
}


@pytest.mark.asyncio
async def test_identity_fetch_is_read_only_same_origin_and_bounded() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=DOCUMENT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        identity = await X402ResourceClient(client).seller_identity(
            "https://seller.example/data", KID
        )
    assert identity.did == DID
    assert calls[0].method == "GET"
    assert str(calls[0].url) == "https://seller.example/.well-known/did.json"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["redirect", "large", "wrong_identity"])
async def test_invalid_identity_is_refused_without_redirect_or_payment(problem: str) -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if problem == "redirect":
            return httpx.Response(302, headers={"location": "https://other.example/did.json"})
        if problem == "large":
            return httpx.Response(200, content=b"x" * 16385)
        return httpx.Response(200, json=DOCUMENT | {"id": "did:web:other.example"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(X402ResourceError):
            await X402ResourceClient(client).seller_identity("https://seller.example/data", KID)
    assert calls == 1


@pytest.mark.asyncio
async def test_duplicate_identity_keys_are_refused() -> None:
    import json

    content = '{"id":"did:web:other.example",' + json.dumps(DOCUMENT)[1:]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=content))
    ) as client:
        with pytest.raises(X402ResourceError):
            await X402ResourceClient(client).seller_identity("https://seller.example/data", KID)

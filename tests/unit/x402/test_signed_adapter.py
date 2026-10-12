from __future__ import annotations

import base64
import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import JsonValue

from settlediff.application.auth import PaidExecutionCapability
from settlediff.application.payment_rails import AdapterProtocolError, SubmissionUncertainError
from settlediff.application.run import LiveEvidenceCollector
from settlediff.contextdev.client import ContextEvidencePort
from settlediff.domain.integrity import canonical_json_bytes
from settlediff.domain.models import ExpectedContract, Verdict
from settlediff.x402.adapter import X402Adapter
from settlediff.x402.client_contract import ExternalSignerResult
from settlediff.x402.signed import SellerIdentityEvidence
from tests.unit.x402.test_adapter import (
    NOW,
    PAYER,
    TARGET,
    TX_HASH,
    FakeResource,
    FakeRpc,
    FakeSigner,
    receipt,
    request,
    required_header,
    required_payload,
    response,
    signer_result,
)

DID = "did:web:example.invalid"
KID = DID + "#key-1"


def signed(key: Ed25519PrivateKey, payload: dict[str, JsonValue]) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    content = (
        b64(canonical_json_bytes({"alg": "EdDSA", "kid": KID}))
        + "."
        + b64(canonical_json_bytes(payload))
    )
    return content + "." + b64(key.sign(content.encode()))


class SignedResource(FakeResource):
    def __init__(self, key: Ed25519PrivateKey, *challenges: dict[str, JsonValue]) -> None:
        super().__init__(*(response(required_header(value)) for value in challenges))
        public = (
            base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
            .rstrip(b"=")
            .decode()
        )
        self.identity = SellerIdentityEvidence.capture(
            DID,
            KID,
            {
                "id": DID,
                "assertionMethod": [KID],
                "verificationMethod": [
                    {
                        "id": KID,
                        "controller": DID,
                        "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": public},
                    }
                ],
            },
            observed_at=NOW,
        )

    async def seller_identity(self, resource_url: str, kid: str) -> SellerIdentityEvidence:
        assert resource_url == TARGET
        return self.identity


def challenge(key: Ed25519PrivateKey, **changes: JsonValue) -> dict[str, JsonValue]:
    value = required_payload()
    primary = cast(list[dict[str, JsonValue]], value["accepts"])[0]
    offer: dict[str, JsonValue] = {
        "version": 1,
        "resourceUrl": TARGET,
        "scheme": primary["scheme"],
        "network": primary["network"],
        "asset": primary["asset"],
        "payTo": primary["payTo"],
        "amount": primary["amount"],
        "validUntil": int((NOW + timedelta(minutes=5)).timestamp()),
    }
    value["extensions"] = {
        "offer-receipt": {
            "info": {
                "offers": [
                    {
                        "format": "jws",
                        "acceptIndex": 0,
                        "signature": signed(key, offer | changes),
                    }
                ]
            }
        }
    }
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"amount": "2000"}, {"validUntil": 0}, {"version": True}])
async def test_bad_signed_offer_is_refused_before_authorization_or_signer(
    changed: dict[str, JsonValue],
) -> None:
    key = Ed25519PrivateKey.generate()
    signer = FakeSigner(signer_result())
    adapter = X402Adapter(
        SignedResource(key, challenge(key, **changed)),
        signer,
        FakeRpc(receipt()),
        expected_payer=PAYER,
        clock=lambda: NOW,
    )
    with pytest.raises(AdapterProtocolError, match="signed offer"):
        await adapter.inspect(request())
    assert signer.requests == []


@pytest.mark.asyncio
async def test_signed_offer_proof_change_is_refused_before_signer_launch() -> None:
    key = Ed25519PrivateKey.generate()
    first = challenge(key)
    second = challenge(key, validUntil=int((NOW + timedelta(minutes=4)).timestamp()))
    signer = FakeSigner(signer_result())
    adapter = X402Adapter(
        SignedResource(key, first, second),
        signer,
        FakeRpc(receipt()),
        expected_payer=PAYER,
        clock=lambda: NOW,
    )
    collector = LiveEvidenceCollector(adapter, cast(ContextEvidencePort, object()))
    await collector.preflight(request())
    terms = collector.payment_terms
    assert terms.signed_offer_digest is not None
    token = await PaidExecutionCapability.issue(
        request(), payment_terms=terms, expires_at=NOW + timedelta(minutes=1)
    ).consume(request(), payment_terms=terms, now=NOW)
    with pytest.raises(AdapterProtocolError, match="payment terms"):
        await adapter.execute_once(token, request(), terms.quoted_price)
    assert signer.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("valid", Verdict.VERIFIED),
        ("payer_diff", Verdict.UNVERIFIABLE),
        ("transaction_diff", Verdict.UNVERIFIABLE),
        ("missing", Verdict.UNVERIFIABLE),
        ("no_transaction", Verdict.VERIFIED_WITH_WARNINGS),
        ("old", Verdict.UNVERIFIABLE),
        ("bad_signature", Verdict.UNVERIFIABLE),
        ("uncertain_payer", Verdict.UNVERIFIABLE),
    ],
)
async def test_signed_offer_and_receipt_flow_preserves_independent_settlement_and_redacts_jws(
    scenario: str,
    expected: Verdict,
    tmp_path: Path,
) -> None:
    key = Ed25519PrivateKey.generate()
    value = challenge(key)
    result = signer_result()
    provider = deepcopy(result.provider_settlement)
    assert provider is not None
    receipt_payload: dict[str, JsonValue] = {
        "version": 1,
        "resourceUrl": TARGET,
        "network": "eip155:84532",
        "payer": PAYER,
        "issuedAt": int(NOW.timestamp()),
        "transaction": TX_HASH,
    }
    if scenario == "payer_diff":
        receipt_payload["payer"] = "0x" + "4" * 40
    elif scenario == "transaction_diff":
        receipt_payload["transaction"] = "0x" + "5" * 64
    elif scenario == "no_transaction":
        receipt_payload.pop("transaction")
    elif scenario == "old":
        receipt_payload["issuedAt"] = int((NOW - timedelta(hours=2)).timestamp())
    jws = signed(
        Ed25519PrivateKey.generate() if scenario == "bad_signature" else key, receipt_payload
    )
    provider["extensions"] = {
        "offer-receipt": {"info": {"receipt": {"format": "jws", "signature": jws}}}
    }
    if scenario in {"missing", "uncertain_payer"}:
        provider["extensions"] = {}
    result = ExternalSignerResult.model_validate(
        result.model_dump()
        | {"challenge": value, "provider_settlement": provider}
        | ({"payer": "0x" + "4" * 40} if scenario == "uncertain_payer" else {})
    )
    signer = FakeSigner(result)
    adapter = X402Adapter(
        SignedResource(key, value, value),
        signer,
        FakeRpc(receipt()),
        expected_payer=PAYER,
        clock=lambda: NOW,
    )
    collector = LiveEvidenceCollector(adapter, cast(ContextEvidencePort, object()))
    await collector.preflight(request())
    terms = collector.payment_terms
    token = await PaidExecutionCapability.issue(
        request(), payment_terms=terms, expires_at=NOW + timedelta(minutes=1)
    ).consume(request(), payment_terms=terms, now=NOW)
    if scenario == "uncertain_payer":
        with pytest.raises(SubmissionUncertainError):
            await collector.execute(token, request())
    else:
        await collector.execute(token, request())
    report = await collector.verify(request())
    assert report.verdict is expected
    assert len(report.provider_signatures) == 2
    assert len(signer.requests) == 1
    assert jws not in report.model_dump_json()
    assert all(jws not in artifact.model_dump_json() for artifact in collector.artifacts)
    contract = ExpectedContract.model_validate_json(json.dumps(collector.artifacts[0].data))
    assert contract.signed_offer_digest == terms.signed_offer_digest
    if scenario == "valid":
        from settlediff.application.bundle import export_bundle, serialize_bundle, verify_bundle
        from settlediff.application.publication import build_public_report
        from settlediff.storage.sqlite import SQLiteReportRepository

        repository = SQLiteReportRepository(tmp_path / "signed.sqlite3")
        repository.save(report, artifacts=collector.artifacts)
        restored = repository.get(report.run_id)
        assert restored is not None
        assert restored.provider_signatures == report.provider_signatures
        bundle = export_bundle(repository, report.run_id)
        assert verify_bundle(bundle).provider_signatures == report.provider_signatures
        assert jws.encode() not in serialize_bundle(bundle)
        public = build_public_report(restored, repository.timeline(report.run_id))
        assert "signature_digest" not in public.model_dump_json()
        assert "provider_signatures" not in public.model_dump(mode="json")
        repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["identity", "revoked", "expired", "removed"])
async def test_reinspection_rejects_identity_or_offer_drift_before_write(drift: str) -> None:
    key = Ed25519PrivateKey.generate()
    value = challenge(key)
    resource = SignedResource(key, value, required_payload() if drift == "removed" else value)
    signer = FakeSigner(signer_result())
    current = NOW
    adapter = X402Adapter(
        resource, signer, FakeRpc(receipt()), expected_payer=PAYER, clock=lambda: current
    )
    collector = LiveEvidenceCollector(adapter, cast(ContextEvidencePort, object()))
    await collector.preflight(request())
    terms = collector.payment_terms
    token = await PaidExecutionCapability.issue(
        request(), payment_terms=terms, expires_at=NOW + timedelta(minutes=1)
    ).consume(request(), payment_terms=terms, now=NOW)
    if drift in {"identity", "revoked"}:
        document = resource.identity.document | (
            {"assertionMethod": []} if drift == "revoked" else {"alsoKnownAs": ["synthetic"]}
        )
        resource.identity = SellerIdentityEvidence.capture(
            DID, KID, cast(dict[str, JsonValue], document), observed_at=NOW
        )
    elif drift == "expired":
        current = NOW + timedelta(minutes=10)
    with pytest.raises(AdapterProtocolError):
        await adapter.execute_once(token, request(), terms.quoted_price)
    assert signer.requests == []


@pytest.mark.parametrize("location", ["service", "wrong_extension", "wallet_signature"])
def test_signer_result_rejects_public_jws_outside_exact_seller_namespace(location: str) -> None:
    from pydantic import ValidationError

    key = Ed25519PrivateKey.generate()
    value = challenge(key)
    result = signer_result().model_dump() | {"challenge": value}
    if location == "service":
        service = cast(dict[str, JsonValue], result["service_response"])
        service["parsed_body"] = {"format": "jws", "signature": signed(key, {})}
    elif location == "wrong_extension":
        result["challenge"] = {"extensions": {"other": {"signature": signed(key, {})}}}
    else:
        result["challenge"] = value | {"paymentSignature": "synthetic-payment-material"}
    with pytest.raises(ValidationError, match="secret-bearing"):
        ExternalSignerResult.model_validate(result)

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import JsonValue

from settlediff.application.auth import PaymentTerms
from settlediff.domain.integrity import sha256_digest
from settlediff.domain.models import AssetIdentity
from settlediff.domain.money import Money
from settlediff.x402.signed import (
    SellerBindingStatus,
    SellerIdentityEvidence,
    SignatureValidity,
    assess_signed_offer,
    assess_signed_receipt,
    did_for_resource,
    verify_seller_jws,
)

NOW = datetime(2026, 10, 5, tzinfo=UTC)
DID = "did:web:seller.example"
KID = f"{DID}#key-1"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed(payload: Mapping[str, object], private: Ed25519PrivateKey, *, kid: str = KID) -> str:
    header = _b64(json.dumps({"alg": "EdDSA", "kid": kid}, separators=(",", ":")).encode())
    content = _b64(json.dumps(payload, separators=(",", ":")).encode())
    message = f"{header}.{content}"
    return f"{message}.{_b64(private.sign(message.encode()))}"


def _identity(private: Ed25519PrivateKey, *, bound: bool = True) -> SellerIdentityEvidence:
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    document: dict[str, JsonValue] = {
        "id": DID,
        "verificationMethod": [
            {
                "id": KID,
                "controller": DID,
                "type": "JsonWebKey2020",
                "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": _b64(public)},
            }
        ],
        "assertionMethod": [KID] if bound else [],
    }
    return SellerIdentityEvidence.capture(DID, KID, document, observed_at=NOW)


def _offer() -> dict[str, object]:
    return {
        "version": 1,
        "resourceUrl": "https://seller.example/data",
        "scheme": "exact",
        "network": "eip155:84532",
        "asset": "0x" + "1" * 40,
        "payTo": "0x" + "2" * 40,
        "amount": "1000",
        "validUntil": 1791248400,
    }


def _terms() -> PaymentTerms:
    asset = AssetIdentity(
        symbol="USDC", network="eip155:84532", reference="0x" + "1" * 40, decimals=6
    )
    return PaymentTerms(
        schema_version=4,
        adapter_id="x402",
        protocol_version="2",
        scheme="exact",
        network="eip155:84532",
        chain=None,
        asset=asset,
        asset_symbol="USDC",
        recipient="0x" + "2" * 40,
        quoted_price=Money(amount=Decimal("0.001"), unit="USDC"),
        max_timeout_seconds=300,
        resource_url="https://seller.example/data",
        method="GET",
        body_digest=sha256_digest(None),
        payer="0x" + "3" * 40,
        settlement_profile_digest="a" * 64,
    )


def test_offer_signed_fields_match_authorization_and_expiry() -> None:
    private = Ed25519PrivateKey.generate()
    offer = assess_signed_offer(_signed(_offer(), private), _identity(private), _terms(), now=NOW)
    assert offer.diagnostic == "SIGNED_OFFER_VALID"
    assert offer.verification.signature_validity is SignatureValidity.VALID
    assert offer.verification.seller_identity_binding is SellerBindingStatus.BOUND


def test_offer_cannot_bind_key_for_a_different_seller_origin() -> None:
    private = Ed25519PrivateKey.generate()
    other_did = "did:web:other.example"
    other_kid = other_did + "#key-1"
    document = _identity(private).document.copy()
    methods = cast(list[JsonValue], document["verificationMethod"])
    method = cast(dict[str, JsonValue], methods[0]).copy()
    method.update({"id": other_kid, "controller": other_did})
    document.update(
        {"id": other_did, "verificationMethod": [method], "assertionMethod": [other_kid]}
    )
    other = SellerIdentityEvidence.capture(other_did, other_kid, document, observed_at=NOW)
    assessment = assess_signed_offer(
        _signed(_offer(), private, kid=other_kid), other, _terms(), now=NOW
    )
    assert assessment.diagnostic == "SIGNED_OFFER_SELLER_BINDING_FAILED"
    receipt = assess_signed_receipt(
        _signed(
            {
                "version": 1,
                "resourceUrl": "https://seller.example/data",
                "network": "eip155:84532",
                "payer": "0x" + "3" * 40,
                "issuedAt": int(NOW.timestamp()),
                "transaction": "0x" + "4" * 64,
            },
            private,
            kid=other_kid,
        ),
        other,
        _terms(),
        provider_reference="0x" + "4" * 64,
        observer_reference="0x" + "4" * 64,
    )
    assert receipt.diagnostic == "SIGNED_RECEIPT_SELLER_BINDING_FAILED"
    assert receipt.correlation == "NOT_COMPARABLE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "2000"),
        ("asset", "0x" + "4" * 40),
        ("resourceUrl", "https://seller.example/elsewhere"),
        ("validUntil", 0),
    ],
)
def test_offer_mismatch_or_expiry_fails_before_payment(field: str, value: object) -> None:
    private = Ed25519PrivateKey.generate()
    assessment = assess_signed_offer(
        _signed(_offer() | {field: value}, private), _identity(private), _terms(), now=NOW
    )
    assert assessment.diagnostic in {"SIGNED_OFFER_TERMS_DIFF", "SIGNED_OFFER_EXPIRED"}


def test_receipt_correlation_requires_matching_transaction_and_payer() -> None:
    private = Ed25519PrivateKey.generate()
    reference = "0x" + "4" * 64
    receipt: dict[str, object] = {
        "version": 1,
        "resourceUrl": "https://seller.example/data",
        "network": "eip155:84532",
        "payer": "0x" + "3" * 40,
        "issuedAt": int(NOW.timestamp()),
        "transaction": reference,
    }
    valid = assess_signed_receipt(
        _signed(receipt, private),
        _identity(private),
        _terms(),
        provider_reference=reference,
        observer_reference=reference,
    )
    assert valid.diagnostic == "SIGNED_RECEIPT_VALID"
    assert valid.correlation == "TRANSACTION_BOUND"
    mismatch = assess_signed_receipt(
        _signed(receipt | {"transaction": "0x" + "5" * 64}, private),
        _identity(private),
        _terms(),
        provider_reference=reference,
        observer_reference=reference,
    )
    assert mismatch.diagnostic == "SIGNED_RECEIPT_TRANSACTION_DIFF"
    assert mismatch.correlation == "CONTRADICTED"


def test_valid_jws_and_authorized_did_key_are_distinct_results() -> None:
    private = Ed25519PrivateKey.generate()
    signed = _signed(_offer(), private)
    result = verify_seller_jws(signed, _identity(private))
    assert result.signature_validity is SignatureValidity.VALID
    assert result.seller_identity_binding is SellerBindingStatus.BOUND
    assert result.payload == _offer()
    assert result.payload_digest


def test_mathematically_valid_key_not_authorized_for_assertions() -> None:
    private = Ed25519PrivateKey.generate()
    result = verify_seller_jws(_signed(_offer(), private), _identity(private, bound=False))
    assert result.signature_validity is SignatureValidity.VALID
    assert result.seller_identity_binding is SellerBindingStatus.NOT_BOUND


def test_tampered_signed_content_is_invalid_even_with_bound_key() -> None:
    private = Ed25519PrivateKey.generate()
    signed = _signed(_offer(), private)
    head, _payload, signature = signed.split(".")
    other = _b64(json.dumps(_offer() | {"amount": "2000"}).encode())
    result = verify_seller_jws(f"{head}.{other}.{signature}", _identity(private))
    assert result.signature_validity is SignatureValidity.INVALID
    assert result.seller_identity_binding is SellerBindingStatus.BOUND


@pytest.mark.parametrize("signed", ["malformed", "a.b.c", "", "a" * 9000])
def test_malformed_or_oversized_jws_cannot_be_accepted(signed: str) -> None:
    identity = _identity(Ed25519PrivateKey.generate())
    result = verify_seller_jws(signed, identity)
    assert result.signature_validity is SignatureValidity.UNVERIFIABLE


def test_did_is_bound_to_https_origin_not_arbitrary_kid_or_ip() -> None:
    assert did_for_resource("https://seller.example/data") == DID
    assert did_for_resource("https://seller.example:8443/data") == "did:web:seller.example%3A8443"
    for url in (
        "http://seller.example/data",
        "https://127.0.0.1/data",
        "https://seller.example./data",
    ):
        with pytest.raises(ValueError):
            did_for_resource(url)


def test_identity_document_rejects_mismatch_private_key_and_oversized_document() -> None:
    private = Ed25519PrivateKey.generate()
    original = _identity(private)
    with pytest.raises(ValueError):
        SellerIdentityEvidence.capture(
            DID, KID, original.document | {"id": "did:web:other.example"}, observed_at=NOW
        )
    method = original.document["verificationMethod"]
    assert isinstance(method, list)
    private_method = dict(cast(dict[str, JsonValue], method[0]))
    private_method["publicKeyJwk"] = {"kty": "OKP", "crv": "Ed25519", "x": "AAAA", "d": "secret"}
    with pytest.raises(ValueError):
        SellerIdentityEvidence.capture(
            DID,
            KID,
            cast(
                dict[str, JsonValue], original.document | {"verificationMethod": [private_method]}
            ),
            observed_at=NOW,
        )
    with pytest.raises(ValueError):
        SellerIdentityEvidence.capture(
            DID, KID, original.document | {"padding": "x" * 20000}, observed_at=NOW
        )


def test_signed_kid_must_belong_to_intended_seller() -> None:
    private = Ed25519PrivateKey.generate()
    result = verify_seller_jws(
        _signed(_offer(), private, kid="did:web:other.example#key-1"), _identity(private)
    )
    assert result.seller_identity_binding is SellerBindingStatus.NOT_BOUND
    assert result.signature_validity is SignatureValidity.UNVERIFIABLE


@pytest.mark.parametrize("missing", ["provider", "observer"])
def test_available_transaction_contradiction_survives_missing_other_source(missing: str) -> None:
    private = Ed25519PrivateKey.generate()
    reference = "0x" + "3" * 64
    payload = {
        "version": 1,
        "network": "eip155:84532",
        "resourceUrl": "https://seller.example/data",
        "payer": _terms().payer,
        "issuedAt": int(NOW.timestamp()),
        "transaction": "0x" + "5" * 64,
    }
    assessment = assess_signed_receipt(
        _signed(payload, private),
        _identity(private),
        _terms(),
        provider_reference=None if missing == "provider" else reference,
        observer_reference=None if missing == "observer" else reference,
    )
    assert assessment.diagnostic == "SIGNED_RECEIPT_TRANSACTION_DIFF"
    assert assessment.correlation == "CONTRADICTED"


@pytest.mark.parametrize("location", ["header", "payload"])
def test_duplicate_signed_json_keys_are_unverifiable(location: str) -> None:
    private = Ed25519PrivateKey.generate()
    header = json.dumps({"alg": "EdDSA", "kid": KID})
    payload = json.dumps(_offer())
    if location == "header":
        header = '{"alg":"unsupported",' + header[1:]
    else:
        payload = '{"amount":"9999",' + payload[1:]
    message = f"{_b64(header.encode())}.{_b64(payload.encode())}"
    jws = f"{message}.{_b64(private.sign(message.encode()))}"
    result = verify_seller_jws(jws, _identity(private))
    assert result.signature_validity is SignatureValidity.UNVERIFIABLE


@pytest.mark.parametrize("transaction", [None, 42, True, "", [], {}])
def test_present_malformed_receipt_transaction_is_not_optional_omission(
    transaction: object,
) -> None:
    private = Ed25519PrivateKey.generate()
    payload = {
        "version": 1,
        "resourceUrl": "https://seller.example/data",
        "network": "eip155:84532",
        "payer": _terms().payer,
        "issuedAt": int(NOW.timestamp()),
        "transaction": transaction,
    }
    result = assess_signed_receipt(
        _signed(payload, private),
        _identity(private),
        _terms(),
        provider_reference=None,
        observer_reference=None,
    )
    assert result.diagnostic == "SIGNED_RECEIPT_MALFORMED"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_nonfinite_signed_json_numbers_are_unverifiable(number: str) -> None:
    private = Ed25519PrivateKey.generate()
    header = _b64(json.dumps({"alg": "EdDSA", "kid": KID}).encode())
    payload = '{"extra":' + number + "," + json.dumps(_offer())[1:]
    message = f"{header}.{_b64(payload.encode())}"
    result = verify_seller_jws(
        f"{message}.{_b64(private.sign(message.encode()))}", _identity(private)
    )
    assert result.signature_validity is SignatureValidity.UNVERIFIABLE

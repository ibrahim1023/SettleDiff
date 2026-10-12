"""Bounded JWS verification and captured did:web seller-key authorization."""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, JsonValue, StringConstraints, model_validator

from settlediff.application.auth import PaymentTerms
from settlediff.domain.integrity import Sha256Digest, canonical_json_bytes, sha256_digest
from settlediff.domain.models import CanonicalModel, NonEmptyStr, UtcDatetime

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_DID = re.compile(r"^did:web:([a-z0-9.-]+)(?:%3A[0-9]{1,5})?$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_JWS_BYTES = 8192
_MAX_DID_BYTES = 16384


def _fqdn(host: str) -> bool:
    labels = host.split(".")
    return (
        len(labels) >= 2
        and all(_DOMAIN_LABEL.fullmatch(label) for label in labels)
        and any(char.isalpha() for char in labels[-1])
    )


class SignatureValidity(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNVERIFIABLE = "UNVERIFIABLE"


class SellerBindingStatus(StrEnum):
    BOUND = "BOUND"
    NOT_BOUND = "NOT_BOUND"
    UNVERIFIABLE = "UNVERIFIABLE"


class SellerIdentityEvidence(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    did: NonEmptyStr
    kid: NonEmptyStr
    document: dict[str, JsonValue]
    document_digest: Sha256Digest
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def require_document_identity(self) -> Self:
        did_match = _DID.fullmatch(self.did)
        if (
            did_match is None
            or not _fqdn(did_match.group(1))
            or not self.kid.startswith(self.did + "#")
        ):
            raise ValueError("seller identity requires a matching did:web key identifier")
        if len(canonical_json_bytes(self.document)) > _MAX_DID_BYTES:
            raise ValueError("seller identity document exceeds its bound")
        if self.document.get("id") != self.did or self.document_digest != sha256_digest(
            self.document
        ):
            raise ValueError("seller identity document or digest does not match the intended DID")
        methods = self.document.get("verificationMethod")
        assertions = self.document.get("assertionMethod")
        if (
            not isinstance(methods, list)
            or len(methods) > 16
            or not isinstance(assertions, list)
            or len(assertions) > 16
        ):
            raise ValueError("seller identity methods are not a bounded array")
        for method in methods:
            if not isinstance(method, dict):
                raise ValueError("seller identity method is malformed")
            jwk = method.get("publicKeyJwk")
            if not isinstance(jwk, dict):
                raise ValueError("seller identity method is malformed")
            if "d" in jwk:
                raise ValueError("seller identity document cannot contain private key material")
        return self

    @classmethod
    def capture(
        cls,
        did: str,
        kid: str,
        document: dict[str, JsonValue],
        *,
        observed_at: datetime,
    ) -> SellerIdentityEvidence:
        return cls(
            did=did,
            kid=kid,
            document=document,
            document_digest=sha256_digest(document),
            observed_at=observed_at,
        )


class SignatureVerificationResult(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    signature_validity: SignatureValidity
    seller_identity_binding: SellerBindingStatus
    diagnostic: NonEmptyStr
    payload: dict[str, JsonValue] | None
    payload_digest: Sha256Digest | None
    kid: NonEmptyStr | None


BoundedJws = Annotated[str, StringConstraints(min_length=1, max_length=_MAX_JWS_BYTES)]


class SignedProviderOffer(CanonicalModel):
    schema_version: Literal[1] = 1
    format: Literal["jws"] = "jws"
    jws: BoundedJws
    identity: SellerIdentityEvidence
    verification: SignatureVerificationResult
    signed_terms_match: bool | None
    captured_at: UtcDatetime
    diagnostic: NonEmptyStr


class SignedProviderReceipt(CanonicalModel):
    schema_version: Literal[1] = 1
    format: Literal["jws"] = "jws"
    jws: BoundedJws
    identity: SellerIdentityEvidence
    verification: SignatureVerificationResult
    correlation: Literal["TRANSACTION_BOUND", "CONTRADICTED", "NOT_COMPARABLE"]
    captured_at: UtcDatetime
    diagnostic: NonEmptyStr


def did_for_resource(resource_url: str) -> str:
    parts = urlsplit(resource_url)
    host = parts.hostname
    if (
        parts.scheme != "https"
        or host is None
        or not _fqdn(host)
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("did:web seller identity requires an HTTPS domain")
    port = f"%3A{parts.port}" if parts.port is not None else ""
    did = f"did:web:{host.lower()}{port}"
    if _DID.fullmatch(did) is None:
        raise ValueError("resource host cannot identify a did:web seller")
    return did


def _base64url(part: str, *, max_bytes: int) -> bytes:
    if _B64URL.fullmatch(part) is None or len(part) > max_bytes * 2:
        raise ValueError("JWS component is malformed or oversized")
    try:
        decoded = base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("JWS component is not valid base64url") from error
    if len(decoded) > max_bytes or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != part:
        raise ValueError("JWS component is noncanonical or oversized")
    return decoded


def _result(
    validity: SignatureValidity,
    binding: SellerBindingStatus,
    diagnostic: str,
    *,
    payload: dict[str, JsonValue] | None = None,
    kid: str | None = None,
) -> SignatureVerificationResult:
    return SignatureVerificationResult(
        signature_validity=validity,
        seller_identity_binding=binding,
        diagnostic=diagnostic,
        payload=payload,
        payload_digest=sha256_digest(payload) if payload is not None else None,
        kid=kid,
    )


def verify_seller_jws(jws: str, identity: SellerIdentityEvidence) -> SignatureVerificationResult:
    if len(jws.encode("utf-8")) > _MAX_JWS_BYTES:
        return _result(
            SignatureValidity.UNVERIFIABLE, SellerBindingStatus.UNVERIFIABLE, "SIGNED_JWS_MALFORMED"
        )
    try:
        header_part, payload_part, signature_part = jws.split(".")
        header_value: object = json.loads(_base64url(header_part, max_bytes=512))
        payload_value: object = json.loads(_base64url(payload_part, max_bytes=4096))
        signature = _base64url(signature_part, max_bytes=64)
        if (
            len(signature) != 64
            or not isinstance(header_value, dict)
            or not isinstance(payload_value, dict)
        ):
            raise ValueError("JWS structure is malformed")
        header = cast(dict[str, object], header_value)
        payload = cast(dict[str, JsonValue], payload_value)
        if set(header) != {"alg", "kid"} or header["alg"] != "EdDSA":
            raise ValueError("unsupported seller JWS header")
        kid = header["kid"]
        if not isinstance(kid, str) or not kid:
            raise ValueError("seller JWS key identifier is malformed")
    except (ValueError, UnicodeDecodeError, TypeError):
        return _result(
            SignatureValidity.UNVERIFIABLE, SellerBindingStatus.UNVERIFIABLE, "SIGNED_JWS_MALFORMED"
        )
    if kid != identity.kid:
        return _result(
            SignatureValidity.UNVERIFIABLE,
            SellerBindingStatus.NOT_BOUND,
            "SIGNED_JWS_SELLER_BINDING_FAILED",
            payload=payload,
            kid=kid,
        )
    methods_value = identity.document.get("verificationMethod")
    assertions_value = identity.document.get("assertionMethod")
    if not isinstance(methods_value, list) or not isinstance(assertions_value, list):
        return _result(
            SignatureValidity.UNVERIFIABLE,
            SellerBindingStatus.UNVERIFIABLE,
            "SELLER_IDENTITY_DOCUMENT_MALFORMED",
            payload=payload,
            kid=kid,
        )
    methods = [
        method for method in methods_value if isinstance(method, dict) and method.get("id") == kid
    ]
    if len(methods) != 1:
        return _result(
            SignatureValidity.UNVERIFIABLE,
            SellerBindingStatus.NOT_BOUND,
            "SIGNED_JWS_SELLER_BINDING_FAILED",
            payload=payload,
            kid=kid,
        )
    method = cast(dict[str, JsonValue], methods[0])
    jwk_value = method.get("publicKeyJwk")
    if not isinstance(jwk_value, dict):
        return _result(
            SignatureValidity.UNVERIFIABLE,
            SellerBindingStatus.UNVERIFIABLE,
            "SELLER_IDENTITY_DOCUMENT_MALFORMED",
            payload=payload,
            kid=kid,
        )
    jwk = cast(dict[str, JsonValue], jwk_value)
    if (
        method.get("controller") != identity.did
        or set(jwk) != {"kty", "crv", "x"}
        or jwk.get("kty") != "OKP"
        or jwk.get("crv") != "Ed25519"
        or not isinstance(jwk.get("x"), str)
    ):
        return _result(
            SignatureValidity.UNVERIFIABLE,
            SellerBindingStatus.UNVERIFIABLE,
            "SELLER_IDENTITY_DOCUMENT_MALFORMED",
            payload=payload,
            kid=kid,
        )
    try:
        key_bytes = _base64url(cast(str, jwk["x"]), max_bytes=32)
        key = Ed25519PublicKey.from_public_bytes(key_bytes)
        key.verify(signature, f"{header_part}.{payload_part}".encode("ascii"))
    except InvalidSignature:
        validity = SignatureValidity.INVALID
    except (ValueError, TypeError):
        return _result(
            SignatureValidity.UNVERIFIABLE,
            SellerBindingStatus.UNVERIFIABLE,
            "SELLER_IDENTITY_DOCUMENT_MALFORMED",
            payload=payload,
            kid=kid,
        )
    else:
        validity = SignatureValidity.VALID
    bound = kid in assertions_value
    return _result(
        validity,
        SellerBindingStatus.BOUND if bound else SellerBindingStatus.NOT_BOUND,
        "SIGNED_JWS_VALID"
        if validity is SignatureValidity.VALID and bound
        else (
            "SIGNED_JWS_SIGNATURE_INVALID"
            if validity is SignatureValidity.INVALID
            else "SIGNED_JWS_SELLER_BINDING_FAILED"
        ),
        payload=payload,
        kid=kid,
    )


def seller_key_id(jws: str) -> str:
    if len(jws.encode()) > _MAX_JWS_BYTES:
        raise ValueError("seller JWS exceeds its bound")
    parts = jws.split(".")
    if len(parts) != 3:
        raise ValueError("seller JWS is malformed")
    decoded: object = json.loads(_base64url(parts[0], max_bytes=512))
    if not isinstance(decoded, dict):
        raise ValueError("seller JWS header must be an object")
    header = cast(dict[str, object], decoded)
    if set(header) != {"alg", "kid"} or header.get("alg") != "EdDSA":
        raise ValueError("seller JWS algorithm or header is unsupported")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise ValueError("seller JWS key identifier is missing")
    return kid


def assess_signed_offer(
    jws: str, identity: SellerIdentityEvidence, terms: PaymentTerms, *, now: datetime
) -> SignedProviderOffer:
    verification = verify_seller_jws(jws, identity)
    diagnostic = "SIGNED_OFFER_VALID"
    match: bool | None = None
    payload = verification.payload
    if verification.signature_validity is SignatureValidity.INVALID:
        diagnostic = "SIGNED_OFFER_SIGNATURE_INVALID"
    elif verification.signature_validity is not SignatureValidity.VALID:
        diagnostic = "SIGNED_OFFER_MALFORMED"
    elif verification.seller_identity_binding is not SellerBindingStatus.BOUND or (
        terms.resource_url is None or identity.did != did_for_resource(terms.resource_url)
    ):
        diagnostic = "SIGNED_OFFER_SELLER_BINDING_FAILED"
    elif payload is None or terms.asset is None:
        diagnostic = "SIGNED_OFFER_MALFORMED"
    else:
        valid_until = payload.get("validUntil")
        amount = terms.quoted_price.amount.scaleb(terms.asset.decimals)
        if (
            set(payload)
            - {
                "version",
                "resourceUrl",
                "scheme",
                "network",
                "asset",
                "payTo",
                "amount",
                "validUntil",
            }
            or "validUntil" in payload
            and (isinstance(valid_until, bool) or not isinstance(valid_until, int))
        ):
            diagnostic = "SIGNED_OFFER_MALFORMED"
        elif isinstance(valid_until, int) and valid_until <= now.timestamp():
            diagnostic = "SIGNED_OFFER_EXPIRED"
        else:
            match = (
                type(payload.get("version")) is int
                and payload.get("version") == 1
                and payload.get("resourceUrl") == terms.resource_url
                and payload.get("scheme") == terms.scheme
                and payload.get("network") == terms.network
                and isinstance(payload.get("asset"), str)
                and cast(str, payload["asset"]).casefold() == terms.asset.reference.casefold()
                and isinstance(payload.get("payTo"), str)
                and terms.recipient is not None
                and cast(str, payload["payTo"]).casefold() == terms.recipient.casefold()
                and payload.get("amount") == str(int(amount))
                and amount == amount.to_integral_value()
            )
            if not match:
                diagnostic = "SIGNED_OFFER_TERMS_DIFF"
    return SignedProviderOffer(
        jws=jws,
        identity=identity,
        verification=verification,
        signed_terms_match=match,
        captured_at=now,
        diagnostic=diagnostic,
    )


def assess_signed_receipt(
    jws: str,
    identity: SellerIdentityEvidence,
    terms: PaymentTerms,
    *,
    provider_reference: str | None,
    observer_reference: str | None,
    observed_at: datetime | None = None,
) -> SignedProviderReceipt:
    verification = verify_seller_jws(jws, identity)
    correlation: Literal["TRANSACTION_BOUND", "CONTRADICTED", "NOT_COMPARABLE"] = "NOT_COMPARABLE"
    diagnostic = "SIGNED_RECEIPT_VALID"
    payload = verification.payload
    captured_at = observed_at or identity.observed_at
    if verification.signature_validity is SignatureValidity.INVALID:
        diagnostic = "SIGNED_RECEIPT_SIGNATURE_INVALID"
    elif verification.signature_validity is not SignatureValidity.VALID:
        diagnostic = "SIGNED_RECEIPT_MALFORMED"
    elif verification.seller_identity_binding is not SellerBindingStatus.BOUND or (
        terms.resource_url is None or identity.did != did_for_resource(terms.resource_url)
    ):
        diagnostic = "SIGNED_RECEIPT_SELLER_BINDING_FAILED"
    elif (
        payload is None
        or bool(
            set(payload) - {"version", "network", "resourceUrl", "payer", "issuedAt", "transaction"}
        )
        or not isinstance(payload.get("issuedAt"), int)
        or isinstance(payload.get("issuedAt"), bool)
    ):
        diagnostic = "SIGNED_RECEIPT_MALFORMED"
    elif (
        type(payload.get("version")) is not int
        or payload.get("version") != 1
        or payload.get("resourceUrl") != terms.resource_url
        or payload.get("network") != terms.network
        or not isinstance(payload.get("payer"), str)
        or terms.payer is None
        or cast(str, payload["payer"]).casefold() != terms.payer.casefold()
    ):
        diagnostic = "SIGNED_RECEIPT_TERMS_DIFF"
        correlation = "CONTRADICTED"
    elif not -30 <= captured_at.timestamp() - cast(int, payload["issuedAt"]) <= 3600:
        diagnostic = "SIGNED_RECEIPT_TIME_DIFF"
        correlation = "CONTRADICTED"
    else:
        transaction = payload.get("transaction")
        if not isinstance(transaction, str) or not transaction:
            diagnostic = "SIGNED_RECEIPT_TRANSACTION_MISSING"
        elif any(
            transaction.casefold() != reference.casefold()
            for reference in (provider_reference, observer_reference)
            if reference is not None
        ):
            correlation = "CONTRADICTED"
            diagnostic = "SIGNED_RECEIPT_TRANSACTION_DIFF"
        elif provider_reference is None or observer_reference is None:
            diagnostic = "SIGNED_RECEIPT_TRANSACTION_UNAVAILABLE"
        else:
            correlation = "TRANSACTION_BOUND"
    return SignedProviderReceipt(
        jws=jws,
        identity=identity,
        verification=verification,
        correlation=correlation,
        captured_at=captured_at,
        diagnostic=diagnostic,
    )

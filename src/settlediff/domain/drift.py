"""Content-addressed contract snapshots and deterministic semantic drift comparison."""

from __future__ import annotations

from enum import StrEnum
from typing import cast

from pydantic import Field, JsonValue, model_validator

from settlediff.domain.integrity import Sha256Digest, canonical_json_bytes, sha256_digest
from settlediff.domain.models import (
    CanonicalModel,
    ExpectedContract,
    NonEmptyStr,
)
from settlediff.domain.redaction import redact_value

PRICE_CHANGED = "PRICE_CHANGED"
NETWORK_CHANGED = "NETWORK_CHANGED"
ASSET_CHANGED = "ASSET_CHANGED"
RECIPIENT_CHANGED = "RECIPIENT_CHANGED"
SCHEME_CHANGED = "SCHEME_CHANGED"
PROTOCOL_VERSION_CHANGED = "PROTOCOL_VERSION_CHANGED"
INPUT_CONTRACT_CHANGED = "INPUT_CONTRACT_CHANGED"
RESPONSE_CONTRACT_CHANGED = "RESPONSE_CONTRACT_CHANGED"
FACILITATOR_CHANGED = "FACILITATOR_CHANGED"
MEDIA_TYPE_CHANGED = "MEDIA_TYPE_CHANGED"
SETTLEMENT_TERMS_CHANGED = "SETTLEMENT_TERMS_CHANGED"
NORMALIZATION_DIAGNOSTICS_CHANGED = "NORMALIZATION_DIAGNOSTICS_CHANGED"
SOURCE_CHANGED = "SOURCE_CHANGED"

_MAX_SOURCE_BYTES = 1_048_576

_COMPONENT_TO_CODE = {
    "price": PRICE_CHANGED,
    "network": NETWORK_CHANGED,
    "asset": ASSET_CHANGED,
    "recipient": RECIPIENT_CHANGED,
    "scheme": SCHEME_CHANGED,
    "protocol_version": PROTOCOL_VERSION_CHANGED,
    "input_contract": INPUT_CONTRACT_CHANGED,
    "response_contract": RESPONSE_CONTRACT_CHANGED,
    "response_media_type": MEDIA_TYPE_CHANGED,
    "facilitator": FACILITATOR_CHANGED,
    "settlement_terms": SETTLEMENT_TERMS_CHANGED,
    "normalization_diagnostics": NORMALIZATION_DIAGNOSTICS_CHANGED,
}
_COMPONENT_NAMES = frozenset(_COMPONENT_TO_CODE)
_SEMANTIC_CODES = frozenset(_COMPONENT_TO_CODE.values())


def _snapshot_digest_projection(
    target: str, rail: str, semantic_fingerprint: str, source_digest: str
) -> dict[str, str]:
    return {
        "target": target,
        "rail": rail,
        "semantic_fingerprint": semantic_fingerprint,
        "source_digest": source_digest,
    }


class DriftStatus(StrEnum):
    MATCH = "MATCH"
    DIFF = "DIFF"
    UNAVAILABLE = "UNAVAILABLE"


class ContractSnapshot(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    snapshot_digest: Sha256Digest
    target: NonEmptyStr
    rail: NonEmptyStr
    semantic_fingerprint: Sha256Digest
    source_digest: Sha256Digest
    component_fingerprints: dict[NonEmptyStr, Sha256Digest]
    contract: ExpectedContract
    source_contract: JsonValue

    @model_validator(mode="after")
    def require_self_consistent_digests(self) -> ContractSnapshot:
        if frozenset(self.component_fingerprints) != _COMPONENT_NAMES:
            raise ValueError("component fingerprints must match the closed component map")
        if self.semantic_fingerprint != sha256_digest(
            cast(JsonValue, dict(self.component_fingerprints))
        ):
            raise ValueError("semantic fingerprint does not match component fingerprints")
        if self.source_digest != sha256_digest(self.source_contract):
            raise ValueError("source digest does not match source contract")
        expected = sha256_digest(
            _snapshot_digest_projection(
                self.target, self.rail, self.semantic_fingerprint, self.source_digest
            )
        )
        if self.snapshot_digest != expected:
            raise ValueError("snapshot digest does not match snapshot content")
        return self


class ContractDrift(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    status: DriftStatus
    previous_snapshot_digest: Sha256Digest | None
    current_snapshot_digest: Sha256Digest
    change_codes: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def require_consistent_status(self) -> ContractDrift:
        if self.status is DriftStatus.UNAVAILABLE:
            if self.previous_snapshot_digest is not None or self.change_codes:
                raise ValueError("UNAVAILABLE drift requires no baseline and no change codes")
        elif self.previous_snapshot_digest is None:
            raise ValueError(f"{self.status} drift requires a previous snapshot digest")
        elif self.status is DriftStatus.MATCH:
            if self.previous_snapshot_digest != self.current_snapshot_digest or self.change_codes:
                raise ValueError("MATCH drift requires equal digests and no change codes")
        elif self.previous_snapshot_digest == self.current_snapshot_digest or not self.change_codes:
            raise ValueError("DIFF drift requires different digests and change codes")
        return self


def _semantic_components(contract: ExpectedContract) -> dict[str, JsonValue]:
    response_contract = contract.response_contract
    response_media_type = response_contract.media_type if response_contract else None
    response_rest = (
        response_contract.model_copy(update={"media_type": None}).model_dump(mode="json")
        if response_contract is not None
        else None
    )
    return {
        "price": cast(JsonValue, contract.price.model_dump(mode="json"))
        if contract.price is not None
        else None,
        "network": cast(
            JsonValue,
            {"network": contract.network, "chain": contract.chain},
        ),
        "asset": cast(
            JsonValue,
            {
                "asset": contract.asset,
                "asset_identity": (
                    contract.asset_identity.model_dump(mode="json")
                    if contract.asset_identity is not None
                    else None
                ),
            },
        ),
        "recipient": contract.recipient,
        "scheme": contract.scheme,
        "protocol_version": contract.protocol,
        "input_contract": cast(JsonValue, contract.request_schema),
        "response_contract": cast(JsonValue, response_rest),
        "response_media_type": response_media_type,
        "facilitator": None,
        "settlement_terms": contract.max_timeout_seconds,
        "normalization_diagnostics": cast(JsonValue, list(contract.normalization_notes)),
    }


def build_contract_snapshot(
    target: str,
    rail: str,
    contract: ExpectedContract,
    source_contract: JsonValue,
) -> ContractSnapshot:
    """Fingerprint a normalized contract plus its redacted provider source."""
    if contract.url != target:
        raise ValueError("snapshot target does not match the normalized contract URL")
    if len(canonical_json_bytes(source_contract)) > _MAX_SOURCE_BYTES:
        raise ValueError("source contract exceeds the 1 MiB canonical bound")
    redacted_source = redact_value(source_contract)
    if len(canonical_json_bytes(redacted_source)) > _MAX_SOURCE_BYTES:
        raise ValueError("redacted source contract exceeds the 1 MiB canonical bound")
    components = _semantic_components(contract)
    component_fingerprints = {name: sha256_digest(components[name]) for name in _COMPONENT_TO_CODE}
    semantic_fingerprint = sha256_digest(cast(JsonValue, component_fingerprints))
    source_digest = sha256_digest(redacted_source)
    snapshot_digest = sha256_digest(
        _snapshot_digest_projection(target, rail, semantic_fingerprint, source_digest)
    )
    return ContractSnapshot(
        snapshot_digest=snapshot_digest,
        target=target,
        rail=rail,
        semantic_fingerprint=semantic_fingerprint,
        source_digest=source_digest,
        component_fingerprints=component_fingerprints,
        contract=contract,
        source_contract=redacted_source,
    )


def _validated(snapshot: ContractSnapshot) -> ContractSnapshot:
    return ContractSnapshot.model_validate_json(snapshot.model_dump_json(), strict=True)


def compare_contract_snapshots(
    previous: ContractSnapshot | None, current: ContractSnapshot
) -> ContractDrift:
    """Compare snapshots conservatively; a semantic difference always reports a code."""
    current = _validated(current)
    previous = _validated(previous) if previous is not None else None
    if previous is not None and (
        previous.target != current.target or previous.rail != current.rail
    ):
        raise ValueError("drift comparison requires the same target and rail")
    if previous is None:
        return ContractDrift(
            status=DriftStatus.UNAVAILABLE,
            previous_snapshot_digest=None,
            current_snapshot_digest=current.snapshot_digest,
            change_codes=(),
        )
    if previous.snapshot_digest == current.snapshot_digest:
        return ContractDrift(
            status=DriftStatus.MATCH,
            previous_snapshot_digest=previous.snapshot_digest,
            current_snapshot_digest=current.snapshot_digest,
            change_codes=(),
        )
    codes: set[str] = set()
    for name, code in _COMPONENT_TO_CODE.items():
        if previous.component_fingerprints.get(name) != current.component_fingerprints.get(name):
            codes.add(code)
    if previous.source_digest != current.source_digest:
        codes.add(SOURCE_CHANGED)
    if previous.semantic_fingerprint != current.semantic_fingerprint and not (
        codes & _SEMANTIC_CODES
    ):
        raise ValueError("semantic fingerprint differed without a component change")
    return ContractDrift(
        status=DriftStatus.DIFF,
        previous_snapshot_digest=previous.snapshot_digest,
        current_snapshot_digest=current.snapshot_digest,
        change_codes=tuple(sorted(codes)),
    )

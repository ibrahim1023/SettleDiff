from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import JsonValue

from settlediff.domain.drift import (
    ASSET_CHANGED,
    FACILITATOR_CHANGED,
    INPUT_CONTRACT_CHANGED,
    MEDIA_TYPE_CHANGED,
    NETWORK_CHANGED,
    NORMALIZATION_DIAGNOSTICS_CHANGED,
    PRICE_CHANGED,
    PROTOCOL_VERSION_CHANGED,
    RECIPIENT_CHANGED,
    RESPONSE_CONTRACT_CHANGED,
    SCHEME_CHANGED,
    SETTLEMENT_TERMS_CHANGED,
    SOURCE_CHANGED,
    ContractSnapshot,
    DriftStatus,
    build_contract_snapshot,
    compare_contract_snapshots,
)
from settlediff.domain.integrity import sha256_digest
from settlediff.domain.models import (
    AssetIdentity,
    ExpectedContract,
    ResponseContract,
)
from settlediff.domain.money import Money

TARGET = "https://example.invalid/search"


def contract(**overrides: object) -> ExpectedContract:
    values: dict[str, object] = {
        "schema_version": 3,
        "vendor_slug": "synthetic-search",
        "url": TARGET,
        "price": Money(amount=Decimal("0.01"), unit="USDC"),
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
        "request_schema": {"type": "object"},
        "scheme": "exact",
        "network": "eip155:84532",
        "asset_identity": AssetIdentity(
            symbol="USDC",
            network="eip155:84532",
            reference="syn_usdc_base_sepolia",
            decimals=6,
        ),
        "recipient": "syn_recipient",
        "max_timeout_seconds": 300,
        "response_contract": ResponseContract(
            media_type="application/json",
            json_schema={
                "type": "object",
                "required": ["result"],
                "properties": {"result": {"type": "string"}},
            },
            source_fields=("response.body",),
        ),
    }
    values.update(overrides)
    return ExpectedContract.model_validate(values)


def snapshot(
    target: str = TARGET,
    rail: str = "perflo",
    contract_value: ExpectedContract | None = None,
    source: JsonValue = None,
) -> ContractSnapshot:
    return build_contract_snapshot(
        target,
        rail,
        contract_value if contract_value is not None else contract(),
        source if source is not None else {"raw": "contract"},
    )


def test_snapshot_is_deterministic_and_timestamp_free() -> None:
    first = snapshot()
    second = snapshot()
    assert first == second
    assert first.snapshot_digest == second.snapshot_digest
    assert first.component_fingerprints
    assert first.semantic_fingerprint != first.source_digest


def test_snapshot_rejects_target_mismatch() -> None:
    with pytest.raises(ValueError, match="target"):
        snapshot(target="https://example.invalid/other")


def test_snapshot_rejects_oversized_source() -> None:
    with pytest.raises(ValueError, match="1 MiB"):
        snapshot(source={"blob": "x" * 1_100_000})


def test_snapshot_rejects_oversized_source_that_would_redact_small() -> None:
    with pytest.raises(ValueError, match="1 MiB"):
        snapshot(source={"api_key": "x" * 1_100_000})


def test_source_contract_is_redacted_before_digest_and_storage() -> None:
    built = snapshot(
        source={
            "url": TARGET,
            "api_key": "syn_secret_key",
            "payTo": "0x3333333333333333333333333333333333333333",
        }
    )
    assert "syn_secret_key" not in str(built.source_contract)
    assert built.source_contract == {
        "url": TARGET,
        "api_key": "[REDACTED]",
        "payTo": "0x3333333333333333333333333333333333333333"[:6] + "…" + "3333",
    }
    assert built.source_digest == sha256_digest(built.source_contract)


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"price": Money(amount=Decimal("0.02"), unit="USDC")}, PRICE_CHANGED),
        ({"network": "eip155:8453"}, NETWORK_CHANGED),
        ({"chain": "base"}, NETWORK_CHANGED),
        ({"asset": "USDT"}, ASSET_CHANGED),
        ({"recipient": "syn_other_recipient"}, RECIPIENT_CHANGED),
        ({"scheme": "deferred"}, SCHEME_CHANGED),
        ({"protocol": "other-protocol"}, PROTOCOL_VERSION_CHANGED),
        ({"request_schema": {"type": "array"}}, INPUT_CONTRACT_CHANGED),
        ({"max_timeout_seconds": 600}, SETTLEMENT_TERMS_CHANGED),
        (
            {"normalization_notes": ("unknown chain at data.chain",)},
            NORMALIZATION_DIAGNOSTICS_CHANGED,
        ),
    ],
)
def test_each_semantic_component_produces_its_change_code(
    override: dict[str, object], expected: str
) -> None:
    previous = snapshot()
    current = snapshot(contract_value=contract(**override))
    result = compare_contract_snapshots(previous, current)
    assert result.status is DriftStatus.DIFF
    assert expected in result.change_codes


def test_media_type_change_uses_media_code() -> None:
    previous_contract = contract()
    response = previous_contract.response_contract
    assert response is not None
    changed = contract(response_contract=response.model_copy(update={"media_type": "text/plain"}))
    result = compare_contract_snapshots(snapshot(), snapshot(contract_value=changed))
    assert result.status is DriftStatus.DIFF
    assert MEDIA_TYPE_CHANGED in result.change_codes
    assert RESPONSE_CONTRACT_CHANGED not in result.change_codes


def test_non_media_response_contract_change_uses_response_code() -> None:
    previous_contract = contract()
    response = previous_contract.response_contract
    assert response is not None
    changed = contract(
        response_contract=response.model_copy(
            update={
                "json_schema": {
                    "type": "object",
                    "required": ["result", "extra"],
                    "properties": {
                        "result": {"type": "string"},
                        "extra": {"type": "integer"},
                    },
                }
            }
        )
    )
    result = compare_contract_snapshots(snapshot(), snapshot(contract_value=changed))
    assert result.status is DriftStatus.DIFF
    assert RESPONSE_CONTRACT_CHANGED in result.change_codes
    assert MEDIA_TYPE_CHANGED not in result.change_codes


def test_source_only_change_reports_source_changed() -> None:
    previous = snapshot(source={"raw": "contract", "note": "a"})
    current = snapshot(source={"raw": "contract", "note": "b"})
    result = compare_contract_snapshots(previous, current)
    assert result.status is DriftStatus.DIFF
    assert result.change_codes == (SOURCE_CHANGED,)


def test_facilitator_component_maps_to_its_code() -> None:
    previous = snapshot()
    components = dict(previous.component_fingerprints)
    components["facilitator"] = sha256_digest("syn_facilitator")
    semantic = sha256_digest(components)
    current = previous.model_copy(
        update={
            "component_fingerprints": components,
            "semantic_fingerprint": semantic,
            "snapshot_digest": sha256_digest(
                {
                    "target": previous.target,
                    "rail": previous.rail,
                    "semantic_fingerprint": semantic,
                    "source_digest": previous.source_digest,
                }
            ),
        }
    )
    result = compare_contract_snapshots(previous, current)
    assert result.status is DriftStatus.DIFF
    assert result.change_codes == (FACILITATOR_CHANGED,)


def test_first_observation_is_unavailable_without_baseline() -> None:
    current = snapshot()
    result = compare_contract_snapshots(None, current)
    assert result.status is DriftStatus.UNAVAILABLE
    assert result.previous_snapshot_digest is None
    assert result.current_snapshot_digest == current.snapshot_digest
    assert result.change_codes == ()


def test_identical_snapshot_matches() -> None:
    previous = snapshot()
    current = snapshot()
    result = compare_contract_snapshots(previous, current)
    assert result.status is DriftStatus.MATCH
    assert result.change_codes == ()


def test_normalization_diagnostics_prevent_false_match() -> None:
    noisy = contract(normalization_notes=("unknown asset at data.asset",))
    result = compare_contract_snapshots(snapshot(), snapshot(contract_value=noisy))
    assert result.status is DriftStatus.DIFF
    assert NORMALIZATION_DIAGNOSTICS_CHANGED in result.change_codes


def test_compare_rejects_target_or_rail_mismatch() -> None:
    previous = snapshot()
    other_target = build_contract_snapshot(
        "https://example.invalid/other",
        "perflo",
        contract(url="https://example.invalid/other"),
        {"raw": "contract"},
    )
    with pytest.raises(ValueError, match="target and rail"):
        compare_contract_snapshots(previous, other_target)
    other_rail = snapshot(rail="x402")
    with pytest.raises(ValueError, match="target and rail"):
        compare_contract_snapshots(previous, other_rail)


def test_change_codes_are_sorted_and_closed_component_map_enforced() -> None:
    previous = snapshot()
    current = snapshot(
        contract_value=contract(
            price=Money(amount=Decimal("0.02"), unit="USDC"), scheme="deferred"
        ),
        source={"raw": "changed"},
    )
    result = compare_contract_snapshots(previous, current)
    assert result.status is DriftStatus.DIFF
    assert result.change_codes == tuple(sorted(result.change_codes))
    assert set(result.change_codes) == {
        PRICE_CHANGED,
        SCHEME_CHANGED,
        SOURCE_CHANGED,
    }
    components = dict(previous.component_fingerprints)
    components["injected"] = sha256_digest("x")
    with pytest.raises(ValueError, match="component"):
        ContractSnapshot.model_validate(
            {**previous.model_dump(), "component_fingerprints": components}, strict=True
        )


def test_component_map_reorder_is_accepted() -> None:
    built = snapshot()
    reordered = dict(reversed(list(built.component_fingerprints.items())))
    assert tuple(reordered) != tuple(built.component_fingerprints)
    validated = ContractSnapshot.model_validate(
        {**built.model_dump(), "component_fingerprints": reordered}, strict=True
    )
    assert validated == built


def test_tampered_digests_are_rejected() -> None:
    built = snapshot()
    with pytest.raises(ValueError, match="snapshot digest"):
        ContractSnapshot.model_validate(
            {**built.model_dump(), "snapshot_digest": sha256_digest("other")}, strict=True
        )
    with pytest.raises(ValueError, match="semantic fingerprint"):
        ContractSnapshot.model_validate(
            {**built.model_dump(), "semantic_fingerprint": sha256_digest("other")},
            strict=True,
        )
    with pytest.raises(ValueError, match="source digest"):
        ContractSnapshot.model_validate(
            {**built.model_dump(), "source_digest": sha256_digest("other")}, strict=True
        )


def test_post_construction_mutation_is_rejected_during_compare() -> None:
    previous = snapshot()
    current = snapshot(contract_value=contract(price=Money(amount=Decimal("0.02"), unit="USDC")))
    previous.component_fingerprints["price"] = sha256_digest("tampered")
    with pytest.raises(ValueError, match="semantic fingerprint"):
        compare_contract_snapshots(previous, current)


def test_snapshot_digest_binds_target_and_rail() -> None:
    assert snapshot(rail="x402").snapshot_digest != snapshot(rail="perflo").snapshot_digest
    other = build_contract_snapshot(
        "https://example.invalid/other",
        "perflo",
        contract(url="https://example.invalid/other"),
        {"raw": "contract"},
    )
    assert other.snapshot_digest != snapshot().snapshot_digest


def test_url_backed_contract_rejects_vendor_slug_target() -> None:
    with pytest.raises(ValueError, match="target"):
        snapshot(target="synthetic-search")
    assert snapshot(target=TARGET).target == TARGET


def test_catalog_contract_without_url_accepts_slug_target() -> None:
    contract_value = contract(url=None, schema_version=4)

    snap = snapshot(target="synthetic-search", contract_value=contract_value)

    assert snap.target == "synthetic-search"

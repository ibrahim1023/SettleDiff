from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.domain.models import (
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    ExpectedContract,
    MachineReport,
    PurchaseIntent,
    Verdict,
)
from settlediff.domain.money import Money
from settlediff.x402.bazaar import (
    BazaarAssessment,
    BazaarFieldCheck,
    BazaarStatus,
    assess_bazaar,
)
from settlediff.x402.models import PaymentRequired
from settlediff.x402.normalize import normalize_payment_required

FIXTURE = Path("tests/contract/x402/fixtures/payment-required-bazaar-v2.json")
NOW = datetime(2026, 9, 10, tzinfo=UTC)
REQUEST_SCHEMA = {"method": "GET", "body": None}


def fixture_payload() -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads(FIXTURE.read_text()))


def required(**bazaar_edits: object) -> PaymentRequired:
    payload = fixture_payload()
    extensions = cast(dict[str, JsonValue], payload["extensions"])
    for key, value in bazaar_edits.items():
        if value is None:
            extensions.pop("bazaar", None)
        else:
            bazaar = cast(dict[str, JsonValue], deepcopy(extensions["bazaar"]))
            bazaar[key] = cast(JsonValue, value)
            extensions["bazaar"] = bazaar
    return PaymentRequired.model_validate(payload)


def contract_for(req: PaymentRequired) -> ExpectedContract:
    return normalize_payment_required(req, request_schema=dict(REQUEST_SCHEMA))


def current_contract() -> ExpectedContract:
    return contract_for(required())


def paid_report(
    contract: ExpectedContract | None,
    *,
    adapter_id: str = "x402",
    observation_media: str | None = None,
    delivery_digest: str | None = None,
    include_observation: bool = False,
) -> MachineReport:
    delivery = None
    if observation_media is not None or delivery_digest is not None or include_observation:
        delivery = DeliveryAssessment(
            status=DeliveryStatus.SATISFIED,
            reason_code="VALID_DELIVERY",
            evidence_ids=("run_syn_001:service_response",),
            observation=DeliveryObservation(
                observed_at=NOW,
                status_code=200,
                media_type=observation_media,
                received_bytes=16,
                truncated=False,
                parsed_body={"result": "ok"},
                evidence_ids=("run_syn_001:service_response",),
            ),
            response_contract_digest=delivery_digest
            or (
                contract.response_contract.digest
                if contract and contract.response_contract
                else "0" * 64
            ),
        )
    return MachineReport(
        schema_version=3,
        run_id="run_syn_001",
        intent=PurchaseIntent(
            run_id="run_syn_001",
            task="Inspect",
            max_budget=Money(amount=Decimal(1), unit="USDC"),
            requested_service=None,
            created_at=NOW,
        ),
        contract=contract,
        execution=None,
        ledger=None,
        findings=(),
        verdict=Verdict.UNVERIFIABLE,
        adapter_id=adapter_id,
        delivery=delivery,
    )


def check_map(assessment: BazaarAssessment) -> dict[str, BazaarStatus]:
    return {check.check_id: check.status for check in assessment.checks}


def test_absent_extension_is_unavailable() -> None:
    assessment = assess_bazaar(required(bazaar=None), request_method="GET", current_contract=None)
    assert assessment.status is BazaarStatus.UNAVAILABLE
    assert [check.check_id for check in assessment.checks] == ["BAZAAR_EXTENSION"]
    assert assessment.checks[0].status is BazaarStatus.UNAVAILABLE
    assert assessment.checks[0].evidence_ids == ("bazaar:challenge",)


def test_supported_extension_matches_current_contract() -> None:
    assessment = assess_bazaar(
        required(), request_method="GET", current_contract=current_contract()
    )
    statuses = check_map(assessment)
    assert assessment.status is BazaarStatus.MATCH
    assert statuses["BAZAAR_EXTENSION"] is BazaarStatus.MATCH
    assert statuses["PRIMARY_REQUIREMENT"] is BazaarStatus.MATCH
    assert statuses["INPUT_METHOD"] is BazaarStatus.MATCH
    assert statuses["RESPONSE_SCHEMA"] is BazaarStatus.MATCH
    assert statuses["MEDIA_TYPE"] is BazaarStatus.MATCH
    assert statuses["PAID_EVIDENCE"] is BazaarStatus.UNAVAILABLE


def test_method_difference_and_malformed_input() -> None:
    different = assess_bazaar(required(), request_method="POST", current_contract=None)
    assert check_map(different)["INPUT_METHOD"] is BazaarStatus.DIFF
    assert different.status is BazaarStatus.DIFF

    malformed = assess_bazaar(
        required(info={"input": {"type": "http", "method": "OPTIONS"}, "output": {"type": "json"}}),
        request_method="GET",
        current_contract=None,
    )
    assert check_map(malformed)["INPUT_METHOD"] is BazaarStatus.UNSUPPORTED
    assert malformed.status is BazaarStatus.UNSUPPORTED


def test_media_type_missing_or_different() -> None:
    payload = fixture_payload()
    payload["resource"]["mimeType"] = "text/plain"  # type: ignore[index]
    req = PaymentRequired.model_validate(payload)
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    assert check_map(assessment)["MEDIA_TYPE"] is BazaarStatus.DIFF

    payload["resource"].pop("mimeType")  # type: ignore[index]
    req = PaymentRequired.model_validate(payload)
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    assert check_map(assessment)["MEDIA_TYPE"] is BazaarStatus.UNAVAILABLE


def test_unsupported_schema_keyword_is_unsupported() -> None:
    req = required(
        schema={
            "type": "object",
            "properties": {"result": {"type": "string", "pattern": "^ok$"}},
        }
    )
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    statuses = check_map(assessment)
    assert statuses["BAZAAR_EXTENSION"] is BazaarStatus.MATCH
    assert statuses["RESPONSE_SCHEMA"] is BazaarStatus.UNSUPPORTED
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_malformed_extension_is_unsupported() -> None:
    req = required(schema="not-an-object")
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    statuses = check_map(assessment)
    assert statuses["BAZAAR_EXTENSION"] is BazaarStatus.UNSUPPORTED
    assert statuses["PRIMARY_REQUIREMENT"] is BazaarStatus.MATCH
    assert statuses["RESPONSE_SCHEMA"] is BazaarStatus.UNAVAILABLE
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_non_object_extension_is_unsupported() -> None:
    payload = fixture_payload()
    payload["extensions"]["bazaar"] = "bad"  # type: ignore[index]
    req = PaymentRequired.model_validate(payload)
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    assert check_map(assessment)["BAZAAR_EXTENSION"] is BazaarStatus.UNSUPPORTED
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_oversized_schema_bounds_is_unsupported() -> None:
    req = required(
        schema={
            "type": "object",
            "properties": {f"field_{index}": {"type": "string"} for index in range(200)},
        }
    )
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    assert check_map(assessment)["BAZAAR_EXTENSION"] is BazaarStatus.UNSUPPORTED
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_unsupported_primary_requirement() -> None:
    payload = fixture_payload()
    payload["accepts"] = [  # type: ignore[index]
        {
            "scheme": "deferred",
            "network": "eip155:8453",
            "amount": "1000",
            "asset": "0x2222222222222222222222222222222222222222",
            "payTo": "0x1111111111111111111111111111111111111111",
            "maxTimeoutSeconds": 300,
            "extra": {},
        }
    ]
    req = PaymentRequired.model_validate(payload)
    assessment = assess_bazaar(req, request_method="GET", current_contract=None)
    statuses = check_map(assessment)
    assert statuses["PRIMARY_REQUIREMENT"] is BazaarStatus.UNSUPPORTED
    assert statuses["BAZAAR_EXTENSION"] is BazaarStatus.MATCH
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_paid_economics_diff_and_match() -> None:
    paid_contract = current_contract()
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(paid_contract),
    )
    statuses = check_map(assessment)
    assert assessment.run_id == "run_syn_001"
    assert statuses["RESOURCE"] is BazaarStatus.MATCH
    assert statuses["PRICE"] is BazaarStatus.MATCH
    assert statuses["NETWORK"] is BazaarStatus.MATCH
    assert statuses["ASSET"] is BazaarStatus.MATCH
    assert statuses["RECIPIENT"] is BazaarStatus.MATCH
    assert statuses["SCHEME"] is BazaarStatus.MATCH
    assert statuses["PROTOCOL_VERSION"] is BazaarStatus.MATCH
    assert statuses["INPUT_CONTRACT"] is BazaarStatus.MATCH
    assert statuses["PAID_RESPONSE_SCHEMA"] is BazaarStatus.MATCH
    assert statuses["PAID_MEDIA_TYPE"] is BazaarStatus.MATCH
    assert statuses["PAID_DELIVERY_CONTRACT"] is BazaarStatus.UNAVAILABLE
    assert assessment.status is BazaarStatus.MATCH
    price_check = next(check for check in assessment.checks if check.check_id == "PRICE")
    assert "run_syn_001:service_contract" in price_check.evidence_ids


def edited_contract(**edits: object) -> ExpectedContract:
    payload = current_contract().model_dump(mode="json")
    for key, value in edits.items():
        payload[key] = cast(JsonValue, value)
    return ExpectedContract.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("field", "stale_value", "check_id"),
    [
        ("price", {"amount": "0.02", "unit": "USDC"}, "PRICE"),
        ("network", "eip155:8453", "NETWORK"),
        ("asset", "USDT", "ASSET"),
        ("recipient", "0x2222222222222222222222222222222222222222", "RECIPIENT"),
        ("scheme", "deferred", "SCHEME"),
        ("protocol", "other-protocol", "PROTOCOL_VERSION"),
        ("request_schema", {"method": "POST", "body": {}}, "INPUT_CONTRACT"),
    ],
)
def test_each_paid_economic_field_reports_stale_diff(
    field: str, stale_value: object, check_id: str
) -> None:
    stale = edited_contract(**{field: stale_value})
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(stale),
    )
    assert check_map(assessment)[check_id] is BazaarStatus.DIFF
    assert assessment.status is BazaarStatus.DIFF


def test_resource_diff_suppresses_other_paid_comparisons() -> None:
    stale = edited_contract(url="https://example.invalid/other")
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(stale),
    )
    statuses = check_map(assessment)
    assert statuses["RESOURCE"] is BazaarStatus.DIFF
    assert statuses["PRICE"] is BazaarStatus.UNAVAILABLE
    assert statuses["PAID_DELIVERY_CONTRACT"] is BazaarStatus.UNAVAILABLE
    assert assessment.status is BazaarStatus.DIFF


def test_absent_grouped_fields_are_unavailable() -> None:
    sparse = edited_contract(network=None, asset=None, asset_identity=None)
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(sparse),
    )
    statuses = check_map(assessment)
    assert statuses["NETWORK"] is BazaarStatus.UNAVAILABLE
    assert statuses["ASSET"] is BazaarStatus.UNAVAILABLE


def test_persisted_redacted_report_never_fakes_identifier_comparison(
    tmp_path: Path,
) -> None:
    from settlediff.storage.sqlite import SQLiteReportRepository

    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    repository.save(paid_report(current_contract()))
    persisted = repository.get("run_syn_001")
    repository.close()
    assert persisted is not None
    assert persisted.contract is not None
    assert "…" in (persisted.contract.recipient or "")

    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=persisted,
    )
    statuses = check_map(assessment)
    assert statuses["RESOURCE"] is BazaarStatus.MATCH
    assert statuses["RECIPIENT"] is BazaarStatus.UNAVAILABLE
    assert statuses["ASSET"] is BazaarStatus.UNAVAILABLE
    assert statuses["PRICE"] is BazaarStatus.MATCH
    assert statuses["SCHEME"] is BazaarStatus.MATCH


def test_paid_schema_and_media_stale() -> None:
    paid_contract = current_contract()
    stale_schema = paid_contract.model_dump(mode="json")
    stale_schema["response_contract"]["json_schema"] = {"type": "object"}
    stale_schema["response_contract"]["media_type"] = "text/plain"
    stale = ExpectedContract.model_validate_json(json.dumps(stale_schema))
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(stale, observation_media="application/json"),
    )
    statuses = check_map(assessment)
    assert statuses["PAID_RESPONSE_SCHEMA"] is BazaarStatus.DIFF
    assert statuses["PAID_MEDIA_TYPE"] is BazaarStatus.DIFF
    assert statuses["PAID_DELIVERY_CONTRACT"] is BazaarStatus.DIFF
    delivery_check = next(
        check for check in assessment.checks if check.check_id == "PAID_DELIVERY_CONTRACT"
    )
    assert "run_syn_001:service_response" in delivery_check.evidence_ids


def test_paid_observation_media_mismatch() -> None:
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(current_contract(), observation_media="text/plain"),
    )
    assert check_map(assessment)["PAID_MEDIA_TYPE"] is BazaarStatus.DIFF


def test_paid_observation_without_media_is_unavailable() -> None:
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(
            current_contract(), observation_media=None, include_observation=True
        ),
    )
    assert check_map(assessment)["PAID_MEDIA_TYPE"] is BazaarStatus.UNAVAILABLE


def test_paid_observation_malformed_media_is_unsupported() -> None:
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(current_contract(), observation_media="not a media type"),
    )
    assert check_map(assessment)["PAID_MEDIA_TYPE"] is BazaarStatus.UNSUPPORTED
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_wrong_rail_paid_report_is_unsupported() -> None:
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(current_contract(), adapter_id="perflo"),
    )
    statuses = check_map(assessment)
    assert statuses["PRICE"] is BazaarStatus.UNSUPPORTED
    assert statuses["PAID_DELIVERY_CONTRACT"] is BazaarStatus.UNSUPPORTED
    assert assessment.status is BazaarStatus.UNSUPPORTED


def test_missing_paid_contract_is_unavailable() -> None:
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=current_contract(),
        paid_report=paid_report(None),
    )
    statuses = check_map(assessment)
    assert statuses["PRICE"] is BazaarStatus.UNAVAILABLE
    assert assessment.status is BazaarStatus.MATCH


def test_no_current_contract_paid_checks_unavailable() -> None:
    assessment = assess_bazaar(
        required(),
        request_method="GET",
        current_contract=None,
        paid_report=paid_report(current_contract()),
    )
    assert check_map(assessment)["PRICE"] is BazaarStatus.UNAVAILABLE


def test_assessment_validator_recomputes_status_and_rejects_duplicates() -> None:
    match = BazaarFieldCheck(check_id="A", status=BazaarStatus.MATCH, evidence_ids=())
    unavailable = BazaarFieldCheck(check_id="B", status=BazaarStatus.UNAVAILABLE, evidence_ids=())
    diff = BazaarFieldCheck(check_id="C", status=BazaarStatus.DIFF, evidence_ids=())
    unsupported = BazaarFieldCheck(check_id="D", status=BazaarStatus.UNSUPPORTED, evidence_ids=())
    assessment = BazaarAssessment(
        status=BazaarStatus.UNSUPPORTED, checks=(match, diff, unsupported)
    )
    assert assessment.status is BazaarStatus.UNSUPPORTED
    assert (
        BazaarAssessment(status=BazaarStatus.DIFF, checks=(match, unavailable, diff)).status
        is BazaarStatus.DIFF
    )
    assert (
        BazaarAssessment(status=BazaarStatus.MATCH, checks=(match, unavailable)).status
        is BazaarStatus.MATCH
    )
    with pytest.raises(ValueError, match="status"):
        BazaarAssessment(status=BazaarStatus.MATCH, checks=(unsupported,))
    with pytest.raises(ValueError, match="duplicate"):
        BazaarAssessment(status=BazaarStatus.MATCH, checks=(match, match))

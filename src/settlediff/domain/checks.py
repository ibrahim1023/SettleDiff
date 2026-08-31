"""Pure, independent consistency checks over canonical purchase evidence."""

from __future__ import annotations

from settlediff.domain.matching import MatchConfidence, MatchResult, MatchStatus
from settlediff.domain.models import (
    CheckStatus,
    EvidenceValue,
    ExecutionRecord,
    ExpectedContract,
    Finding,
    LedgerStatus,
    PaymentReceipt,
    PurchaseIntent,
    SettlementStatus,
    Severity,
)
from settlediff.domain.money import Money


def run_checks(
    intent: PurchaseIntent,
    contract: ExpectedContract | None,
    execution: ExecutionRecord | None,
    match: MatchResult,
    *,
    receipt: PaymentReceipt | None = None,
) -> tuple[Finding, ...]:
    """Run the fixed verification suite without I/O, model calls, or check dependencies."""
    network_present = any(
        value is not None
        for value in (
            contract.network if contract is not None else None,
            execution.network if execution is not None else None,
            receipt.network if receipt is not None else None,
            match.matched.network if match.matched is not None else None,
        )
    )
    identity_present = any(
        value is not None
        for value in (
            contract.asset_identity if contract is not None else None,
            execution.asset_identity if execution is not None else None,
            receipt.asset_identity if receipt is not None else None,
            match.matched.asset_identity if match.matched is not None else None,
        )
    )
    field_findings = (
        _field_consistency("asset", contract, execution, match, receipt),
        *((_asset_identity(contract, execution, match, receipt),) if identity_present else ()),
        _field_consistency("protocol", contract, execution, match, receipt),
        _field_consistency(
            "network" if network_present else "chain", contract, execution, match, receipt
        ),
    )
    return (
        _budget(intent, execution, match),
        _price(contract, execution, match),
        *field_findings,
        _recipient(contract, execution, match, receipt),
        _settlement(execution, match, receipt),
        _service_execution(execution),
        _paid_failure(execution, match, receipt),
        _ledger_outcome(execution, match, receipt),
        _activity_persistence(match),
    )


def _budget(
    intent: PurchaseIntent, execution: ExecutionRecord | None, match: MatchResult
) -> Finding:
    charge, artifact_id, field_path = _actual_charge(execution, match)
    if charge is None or artifact_id is None or field_path is None:
        return _unknown("budget", "No execution or matched Activity charge is available.")
    if charge.unit != intent.max_budget.unit:
        return _unknown("budget", "Charge and authorized budget use different units.")
    status = CheckStatus.PASS if charge.is_within(intent.max_budget) else CheckStatus.FAIL
    charge_name = "Execution charge" if artifact_id == "execution" else "Recorded Activity amount"
    return _finding(
        "budget",
        Severity.ERROR if status is CheckStatus.FAIL else Severity.INFO,
        status,
        intent.max_budget,
        charge,
        f"{charge_name} is within the authorized budget."
        if status is CheckStatus.PASS
        else f"{charge_name} exceeds the authorized budget.",
        (
            f"{intent.run_id}:intent",
            f"{intent.run_id}:execution" if artifact_id == "execution" else artifact_id,
        ),
        ("intent.max_budget", field_path),
    )


def _price(
    contract: ExpectedContract | None, execution: ExecutionRecord | None, match: MatchResult
) -> Finding:
    charge, artifact_id, field_path = _actual_charge(execution, match)
    if contract is None or contract.price is None or charge is None:
        return _unknown("price", "Quoted price or actual charge is unavailable.")
    if artifact_id is None or field_path is None:
        raise AssertionError("charge evidence must preserve its source")
    if contract.price.unit != charge.unit:
        return _finding(
            "price",
            Severity.WARNING,
            CheckStatus.DIFF,
            contract.price,
            charge,
            "Quoted and charged prices use different units."
            if artifact_id == "execution"
            else "Quoted price and recorded Activity amount use different units.",
            ("contract", artifact_id),
            ("contract.price", field_path),
        )
    status = CheckStatus.PASS if contract.price == charge else CheckStatus.DIFF
    charge_name = "execution charge" if artifact_id == "execution" else "recorded Activity amount"
    return _finding(
        "price",
        Severity.WARNING if status is CheckStatus.DIFF else Severity.INFO,
        status,
        contract.price,
        charge,
        f"Quoted price matches the {charge_name}."
        if status is CheckStatus.PASS
        else f"Quoted price differs from the {charge_name}.",
        ("contract", artifact_id),
        ("contract.price", field_path),
    )


def _actual_charge(
    execution: ExecutionRecord | None, match: MatchResult
) -> tuple[Money | None, str | None, str | None]:
    if execution is not None and execution.charge is not None:
        return execution.charge, "execution", "execution.charge"
    if (
        match.status is MatchStatus.MATCHED
        and match.confidence is MatchConfidence.HIGH
        and match.matched is not None
        and match.matched.status is LedgerStatus.CONFIRMED
        and match.matched.amount is not None
    ):
        return match.matched.amount, "activity", "activity.amount"
    return None, None, None


def _field_consistency(
    field: str,
    contract: ExpectedContract | None,
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> Finding:
    expected = getattr(contract, field) if contract is not None else None
    observed = getattr(execution, field) if execution is not None else None
    receipt_value = getattr(receipt, field) if receipt is not None else None
    ledger_value = getattr(match.matched, field) if match.matched is not None else None
    values = tuple(
        value
        for value in (expected, observed, receipt_value, ledger_value)
        if value is not None and value != "unknown"
    )
    if len(values) < 2:
        return _unknown(field, f"Insufficient {field} evidence is available.")
    status = CheckStatus.PASS if len(set(values)) == 1 else CheckStatus.DIFF
    artifact_ids = tuple(
        name
        for name, value in (
            ("contract", expected),
            ("execution", observed),
            ("receipt", receipt_value),
            ("activity", ledger_value),
        )
        if value is not None
    )
    observed_value = (
        observed
        if observed is not None
        else receipt_value
        if receipt_value is not None
        else ledger_value
    )
    return _finding(
        field,
        Severity.WARNING if status is CheckStatus.DIFF else Severity.INFO,
        status,
        expected,
        observed_value,
        f"{field.title()} values agree across available evidence."
        if status is CheckStatus.PASS
        else f"{field.title()} values differ across available evidence.",
        artifact_ids,
        (
            f"contract.{field}",
            f"execution.{field}",
            f"receipt.{field}",
            f"activity.{field}",
        ),
    )


def _asset_identity(
    contract: ExpectedContract | None,
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> Finding:
    expected = contract.asset_identity if contract is not None else None
    observed = execution.asset_identity if execution is not None else None
    receipt_value = receipt.asset_identity if receipt is not None else None
    ledger_value = match.matched.asset_identity if match.matched is not None else None
    values = tuple(
        value for value in (expected, observed, receipt_value, ledger_value) if value is not None
    )
    if len(values) < 2:
        return _unknown("asset_identity", "Insufficient asset identity evidence is available.")
    status = CheckStatus.PASS if len(set(values)) == 1 else CheckStatus.DIFF
    artifact_ids = tuple(
        name
        for name, value in (
            ("contract", expected),
            ("execution", observed),
            ("receipt", receipt_value),
            ("activity", ledger_value),
        )
        if value is not None
    )
    observed_value = (
        observed
        if observed is not None
        else receipt_value
        if receipt_value is not None
        else ledger_value
    )
    return _finding(
        "asset_identity",
        Severity.WARNING if status is CheckStatus.DIFF else Severity.INFO,
        status,
        expected.model_dump(mode="json") if expected is not None else None,
        observed_value.model_dump(mode="json") if observed_value is not None else None,
        "Asset identities agree across available evidence."
        if status is CheckStatus.PASS
        else "Asset identities differ across available evidence.",
        artifact_ids,
        (
            "contract.asset_identity",
            "execution.asset_identity",
            "receipt.asset_identity",
            "activity.asset_identity",
        ),
    )


def _recipient(
    contract: ExpectedContract | None,
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> Finding:
    contract_value = contract.recipient if contract is not None else None
    execution_value = execution.recipient if execution is not None else None
    receipt_value = receipt.recipient if receipt is not None else None
    ledger_value = match.matched.recipient if match.matched is not None else None
    values = tuple(
        value
        for value in (contract_value, execution_value, receipt_value, ledger_value)
        if value is not None
    )
    if len(values) < 2:
        return _unknown("recipient", "Insufficient recipient evidence is available.")
    status = (
        CheckStatus.PASS if len({value.casefold() for value in values}) == 1 else CheckStatus.WARN
    )
    expected = contract_value if contract_value is not None else execution_value
    observed = (
        ledger_value
        if ledger_value is not None
        else receipt_value
        if receipt_value is not None
        else execution_value
    )
    artifact_ids = tuple(
        name
        for name, value in (
            ("contract", contract_value),
            ("execution", execution_value),
            ("receipt", receipt_value),
            ("activity", ledger_value),
        )
        if value is not None
    )
    return _finding(
        "recipient",
        Severity.WARNING if status is CheckStatus.WARN else Severity.INFO,
        status,
        expected,
        observed,
        "Recipient values match."
        if status is CheckStatus.PASS
        else "Recipient representations differ; no provider defect is inferred.",
        artifact_ids,
        (
            "contract.recipient",
            "execution.recipient",
            "receipt.recipient",
            "activity.recipient",
        ),
    )


def _effective_settlement_status(
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> SettlementStatus:
    if receipt is None:
        return execution.settlement_status if execution is not None else SettlementStatus.UNKNOWN
    ledger_status = match.matched.status if match.matched is not None else LedgerStatus.UNKNOWN
    if ledger_status is LedgerStatus.CONFIRMED:
        return (
            SettlementStatus.UNKNOWN
            if receipt.settlement_status is SettlementStatus.FAILED
            else SettlementStatus.SETTLED
        )
    if ledger_status is LedgerStatus.FAILED:
        return (
            SettlementStatus.UNKNOWN
            if receipt.settlement_status is SettlementStatus.SETTLED
            else SettlementStatus.FAILED
        )
    return SettlementStatus.UNKNOWN


def _settlement(
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> Finding:
    status = _effective_settlement_status(execution, match, receipt)
    if status is SettlementStatus.UNKNOWN:
        return _unknown("settlement", "Financial settlement evidence is unavailable or conflicts.")
    artifact_ids = (
        ("receipt", "activity")
        if receipt is not None and match.matched is not None
        else ("execution",)
    )
    field_paths = (
        ("receipt.settlement_status", "activity.status")
        if receipt is not None and match.matched is not None
        else ("execution.settlement_status",)
    )
    if status is SettlementStatus.SETTLED:
        return _finding(
            "settlement",
            Severity.INFO,
            CheckStatus.PASS,
            SettlementStatus.SETTLED,
            status,
            "Payment settled.",
            artifact_ids,
            field_paths,
        )
    if status is SettlementStatus.FAILED:
        return _finding(
            "settlement",
            Severity.ERROR,
            CheckStatus.FAIL,
            SettlementStatus.SETTLED,
            status,
            "Payment failed.",
            artifact_ids,
            field_paths,
        )
    return _unknown("settlement", "Payment settlement is still pending.")


def _service_execution(execution: ExecutionRecord | None) -> Finding:
    if execution is None or execution.upstream_http_status is None:
        return _unknown("service_execution", "Upstream HTTP status is unavailable.")
    status = CheckStatus.PASS if 200 <= execution.upstream_http_status < 300 else CheckStatus.FAIL
    return _finding(
        "service_execution",
        Severity.ERROR if status is CheckStatus.FAIL else Severity.INFO,
        status,
        "2xx",
        execution.upstream_http_status,
        "Purchased service returned a successful HTTP response."
        if status is CheckStatus.PASS
        else "Purchased service returned a non-success HTTP response.",
        ("execution",),
        ("execution.upstream_http_status",),
    )


def _paid_failure(
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> Finding:
    settlement_status = _effective_settlement_status(execution, match, receipt)
    if (
        execution is None
        or execution.upstream_http_status is None
        or settlement_status is SettlementStatus.UNKNOWN
    ):
        return _unknown("paid_failure", "Settlement or service outcome is unavailable.")
    failed_service = not 200 <= execution.upstream_http_status < 300
    artifact_ids = ("execution", "receipt", "activity") if receipt is not None else ("execution",)
    field_paths = (
        (
            "receipt.settlement_status",
            "activity.status",
            "execution.upstream_http_status",
        )
        if receipt is not None
        else ("execution.settlement_status", "execution.upstream_http_status")
    )
    if settlement_status is SettlementStatus.SETTLED and failed_service:
        return _finding(
            "paid_failure",
            Severity.HIGH,
            CheckStatus.FAIL,
            SettlementStatus.SETTLED,
            execution.upstream_http_status,
            "Financial settlement succeeded, but the purchased service failed.",
            artifact_ids,
            field_paths,
        )
    return _finding(
        "paid_failure",
        Severity.INFO,
        CheckStatus.PASS,
        SettlementStatus.SETTLED,
        execution.upstream_http_status,
        "No settled payment with a failed service response was observed.",
        artifact_ids,
        field_paths,
    )


def _ledger_outcome(
    execution: ExecutionRecord | None,
    match: MatchResult,
    receipt: PaymentReceipt | None,
) -> Finding:
    if execution is None or execution.upstream_http_status is None or match.matched is None:
        return _unknown("ledger_outcome", "Activity record or service outcome is unavailable.")
    provider_status = (
        receipt.settlement_status if receipt is not None else execution.settlement_status
    )
    settled_vs_failed = (
        provider_status is SettlementStatus.SETTLED and match.matched.status is LedgerStatus.FAILED
    )
    failed_vs_confirmed = (
        provider_status is SettlementStatus.FAILED
        and match.matched.status is LedgerStatus.CONFIRMED
    )
    if settled_vs_failed or failed_vs_confirmed:
        provider_name = "Receipt" if receipt is not None else "Execution"
        provider_artifact = "receipt" if receipt is not None else "execution"
        provider_path = (
            "receipt.settlement_status" if receipt is not None else "execution.settlement_status"
        )
        return _finding(
            "ledger_outcome",
            Severity.ERROR,
            CheckStatus.FAIL,
            provider_status,
            match.matched.status,
            f"{provider_name} reports settlement, but the persisted Activity record marks the "
            "payment failed."
            if settled_vs_failed
            else f"{provider_name} reports payment failure, but the persisted Activity record "
            "confirms settlement.",
            (provider_artifact, "activity"),
            (provider_path, "activity.status"),
        )
    if (
        match.matched.status is LedgerStatus.CONFIRMED
        and not 200 <= execution.upstream_http_status < 300
    ):
        return _finding(
            "ledger_outcome",
            Severity.WARNING,
            CheckStatus.WARN,
            match.matched.status,
            execution.upstream_http_status,
            "Persisted Activity confirms settlement without exposing the failed service outcome.",
            ("execution", "activity"),
            ("activity.status", "execution.upstream_http_status"),
        )
    return _finding(
        "ledger_outcome",
        Severity.INFO,
        CheckStatus.PASS,
        match.matched.status,
        execution.upstream_http_status,
        "Persisted Activity and service outcome require no additional consistency warning.",
        ("execution", "activity"),
        ("activity.status", "execution.upstream_http_status"),
    )


def _activity_persistence(match: MatchResult) -> Finding:
    if match.status is MatchStatus.MATCHED:
        return _finding(
            "activity_persistence",
            Severity.INFO,
            CheckStatus.PASS,
            "matched",
            match.matched_id,
            "A deterministic Activity record match was found.",
            ("activity",),
            ("activity.ledger_id",),
        )
    if match.status is MatchStatus.MISSING:
        return _finding(
            "activity_persistence",
            Severity.WARNING,
            CheckStatus.WARN,
            "matched",
            None,
            "No matching persisted Activity record was found.",
            (),
            ("activity",),
        )
    return _finding(
        "activity_persistence",
        Severity.WARNING,
        CheckStatus.WARN,
        "matched",
        list(match.candidate_ids),
        "Activity record matching is ambiguous.",
        ("activity",),
        ("activity",),
    )


def _unknown(check_id: str, message: str) -> Finding:
    return _finding(
        check_id, Severity.WARNING, CheckStatus.UNKNOWN, None, None, message, (), (check_id,)
    )


def _finding(
    check_id: str,
    severity: Severity,
    status: CheckStatus,
    expected: EvidenceValue | None,
    observed: EvidenceValue | None,
    message: str,
    artifact_ids: tuple[str, ...],
    field_paths: tuple[str, ...],
) -> Finding:
    return Finding(
        finding_id=f"check:{check_id}",
        check_id=check_id,
        severity=severity,
        status=status,
        expected=expected,
        observed=observed,
        message=message,
        artifact_ids=artifact_ids,
        field_paths=field_paths,
    )

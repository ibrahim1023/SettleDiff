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
) -> tuple[Finding, ...]:
    """Run the fixed verification suite without I/O, model calls, or check dependencies."""
    return (
        _budget(intent, execution, match),
        _price(contract, execution, match),
        _field_consistency("asset", contract, execution, match),
        _field_consistency("protocol", contract, execution, match),
        _field_consistency("chain", contract, execution, match),
        _recipient(execution, match),
        _settlement(execution),
        _service_execution(execution),
        _paid_failure(execution),
        _ledger_outcome(execution, match),
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
) -> Finding:
    expected = getattr(contract, field) if contract is not None else None
    observed = getattr(execution, field) if execution is not None else None
    ledger_value = getattr(match.matched, field) if match.matched is not None else None
    values = tuple(
        value
        for value in (expected, observed, ledger_value)
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
            ("activity", ledger_value),
        )
        if value is not None
    )
    return _finding(
        field,
        Severity.WARNING if status is CheckStatus.DIFF else Severity.INFO,
        status,
        expected,
        observed,
        f"{field.title()} values agree across available evidence."
        if status is CheckStatus.PASS
        else f"{field.title()} values differ across available evidence.",
        artifact_ids,
        (f"contract.{field}", f"execution.{field}", f"activity.{field}"),
    )


def _recipient(execution: ExecutionRecord | None, match: MatchResult) -> Finding:
    expected = execution.recipient if execution is not None else None
    observed = match.matched.recipient if match.matched is not None else None
    if expected is None or observed is None:
        return _unknown("recipient", "Execution or Activity recipient is unavailable.")
    status = CheckStatus.PASS if expected.casefold() == observed.casefold() else CheckStatus.WARN
    return _finding(
        "recipient",
        Severity.WARNING if status is CheckStatus.WARN else Severity.INFO,
        status,
        expected,
        observed,
        "Recipient values match."
        if status is CheckStatus.PASS
        else "Recipient representations differ; no provider defect is inferred.",
        ("execution", "activity"),
        ("execution.recipient", "activity.recipient"),
    )


def _settlement(execution: ExecutionRecord | None) -> Finding:
    if execution is None or execution.settlement_status is SettlementStatus.UNKNOWN:
        return _unknown("settlement", "Financial settlement evidence is unavailable.")
    if execution.settlement_status is SettlementStatus.SETTLED:
        return _finding(
            "settlement",
            Severity.INFO,
            CheckStatus.PASS,
            SettlementStatus.SETTLED,
            execution.settlement_status,
            "Payment settled.",
            ("execution",),
            ("execution.settlement_status",),
        )
    if execution.settlement_status is SettlementStatus.FAILED:
        return _finding(
            "settlement",
            Severity.ERROR,
            CheckStatus.FAIL,
            SettlementStatus.SETTLED,
            execution.settlement_status,
            "Payment failed.",
            ("execution",),
            ("execution.settlement_status",),
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


def _paid_failure(execution: ExecutionRecord | None) -> Finding:
    if (
        execution is None
        or execution.upstream_http_status is None
        or execution.settlement_status is SettlementStatus.UNKNOWN
    ):
        return _unknown("paid_failure", "Settlement or service outcome is unavailable.")
    failed_service = not 200 <= execution.upstream_http_status < 300
    if execution.settlement_status is SettlementStatus.SETTLED and failed_service:
        return _finding(
            "paid_failure",
            Severity.HIGH,
            CheckStatus.FAIL,
            SettlementStatus.SETTLED,
            execution.upstream_http_status,
            "Financial settlement succeeded, but the purchased service failed.",
            ("execution",),
            ("execution.settlement_status", "execution.upstream_http_status"),
        )
    return _finding(
        "paid_failure",
        Severity.INFO,
        CheckStatus.PASS,
        SettlementStatus.SETTLED,
        execution.upstream_http_status,
        "No settled payment with a failed service response was observed.",
        ("execution",),
        ("execution.settlement_status", "execution.upstream_http_status"),
    )


def _ledger_outcome(execution: ExecutionRecord | None, match: MatchResult) -> Finding:
    if execution is None or execution.upstream_http_status is None or match.matched is None:
        return _unknown("ledger_outcome", "Activity record or service outcome is unavailable.")
    settled_vs_failed = (
        execution.settlement_status is SettlementStatus.SETTLED
        and match.matched.status is LedgerStatus.FAILED
    )
    failed_vs_confirmed = (
        execution.settlement_status is SettlementStatus.FAILED
        and match.matched.status is LedgerStatus.CONFIRMED
    )
    if settled_vs_failed or failed_vs_confirmed:
        return _finding(
            "ledger_outcome",
            Severity.ERROR,
            CheckStatus.FAIL,
            execution.settlement_status,
            match.matched.status,
            "Execution reports settlement, but the persisted Activity record marks the "
            "payment failed."
            if settled_vs_failed
            else "Execution reports payment failure, but the persisted Activity record "
            "confirms settlement.",
            ("execution", "activity"),
            ("execution.settlement_status", "activity.status"),
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

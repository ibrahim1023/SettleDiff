from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn, cast

from settlediff.application.publication import (
    PublicationRepository,
    build_public_report,
    build_publication,
)
from settlediff.application.replay import replay_fixture
from settlediff.application.timeline import EvidenceTimelineEvent
from settlediff.domain.models import (
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    MachineReport,
    RetryAssessment,
    RetrySafety,
)
from settlediff.domain.verdict import derive_verdict
from settlediff.storage.sqlite import SQLiteReportRepository

FIXTURES = Path(__file__).parents[2] / "fixtures"

CANARY_TASK = "CANARY-intent-task-local-/Users/alice/secret-project"
CANARY_URL = "CANARY-private-url-https://internal.example/paid?api_key=CANARYQUERY"
CANARY_TXN = "CANARY-exec-txn-0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
CANARY_SESSION = "CANARY-exec-session-sess_live_12345"
CANARY_LEDGER = "CANARY-ledger-id-ledger_private_999"
CANARY_RECEIPT = "CANARY-receipt-hash-0x" + "ab" * 32
CANARY_MESSAGE = "CANARY-finding-message-secret-detail"
CANARY_ARTIFACT = "CANARY-artifact-id-run:raw-headers"
CANARY_DELIVERY_EVIDENCE = "CANARY-evidence-id-req-body-1"
CANARY_RETRY_EVIDENCE = "CANARY-retry-evidence-submission-7"
CANARY_TIMELINE_ARTIFACT = "CANARY-timeline-artifact-run:payload"
CANARY_LICENSED_OUTPUT = "CANARY-licensed-low-entropy-output"
CANARY_RAW_HEADERS = "CANARY-raw-headers-payment-signature"
CANARY_REJECTED_OUTPUT = "CANARY-explanation-rejected-output"
CANARY_SNAPSHOT_SOURCE = "CANARY-snapshot-source-privatekey"

CANARIES = (
    CANARY_TASK,
    CANARY_URL,
    CANARY_TXN,
    CANARY_SESSION,
    CANARY_LEDGER,
    CANARY_RECEIPT,
    CANARY_MESSAGE,
    CANARY_ARTIFACT,
    CANARY_DELIVERY_EVIDENCE,
    CANARY_RETRY_EVIDENCE,
    CANARY_TIMELINE_ARTIFACT,
    CANARY_LICENSED_OUTPUT,
    CANARY_RAW_HEADERS,
    CANARY_REJECTED_OUTPUT,
    CANARY_SNAPSHOT_SOURCE,
)


class _CanaryRepository:
    def __init__(self, report: MachineReport, timeline: tuple[EvidenceTimelineEvent, ...]) -> None:
        self._report = report
        self._timeline = timeline

    def get(self, run_id: str) -> MachineReport | None:
        return self._report

    def timeline(self, run_id: str) -> tuple[EvidenceTimelineEvent, ...]:
        return self._timeline

    def artifacts(self, run_id: str) -> NoReturn:
        raise AssertionError(f"publication must not read artifacts {CANARY_RAW_HEADERS}")

    def explanation(self, run_id: str) -> NoReturn:
        raise AssertionError(f"publication must not read explanation {CANARY_REJECTED_OUTPUT}")

    def contract_snapshots(self, target: str, rail: str) -> NoReturn:
        raise AssertionError(f"publication must not read snapshots {CANARY_SNAPSHOT_SOURCE}")

    def events(self, run_id: str) -> NoReturn:
        raise AssertionError("publication must not read run events")


def _canary_report() -> MachineReport:
    report = replay_fixture(FIXTURES / "x402-clean-success")
    updates: dict[str, object] = {"intent": report.intent.model_copy(update={"task": CANARY_TASK})}
    if report.contract is not None:
        updates["contract"] = report.contract.model_copy(update={"url": CANARY_URL})
    if report.execution is not None:
        updates["execution"] = report.execution.model_copy(
            update={
                "transaction_id": CANARY_TXN,
                "session_id": CANARY_SESSION,
                "response_body": {"payload": CANARY_LICENSED_OUTPUT},
            }
        )
    if report.ledger is not None:
        updates["ledger"] = report.ledger.model_copy(update={"ledger_id": CANARY_LEDGER})
    if report.receipt is not None:
        updates["receipt"] = report.receipt.model_copy(update={"transaction_hash": CANARY_RECEIPT})
    updates["findings"] = tuple(
        finding.model_copy(
            update={
                "message": CANARY_MESSAGE,
                "expected": CANARY_MESSAGE,
                "artifact_ids": (CANARY_ARTIFACT,),
            }
        )
        for finding in report.findings
    )
    updates["delivery"] = DeliveryAssessment(
        status=DeliveryStatus.FAILED,
        reason_code="DELIVERY_FAILED",
        evidence_ids=(CANARY_DELIVERY_EVIDENCE,),
        observation=DeliveryObservation(
            observed_at=report.intent.created_at,
            status_code=200,
            media_type="application/json",
            received_bytes=64,
            truncated=False,
            parsed_body={"licensed": CANARY_LICENSED_OUTPUT},
            evidence_ids=(CANARY_DELIVERY_EVIDENCE,),
        ),
        response_contract_digest="ab" * 32,
    )
    updates["retry"] = RetryAssessment(
        safety=RetrySafety.DO_NOT_RETRY,
        reason_codes=("UNCERTAIN_SUBMISSION",),
        evidence_ids=(CANARY_RETRY_EVIDENCE,),
    )
    tampered = report.model_copy(update=updates)
    return tampered.model_copy(
        update={"verdict": derive_verdict(tampered.findings, delivery=tampered.delivery)}
    )


def test_publication_never_discloses_excluded_surfaces(tmp_path: Path) -> None:
    report = replay_fixture(FIXTURES / "x402-clean-success")
    repository = SQLiteReportRepository(tmp_path / "r.sqlite3")
    repository.save(report)
    timeline = repository.timeline(report.run_id)
    repository.close()

    tampered = _canary_report()
    canary_timeline = tuple(
        event.model_copy(
            update={
                "artifact_ids": (CANARY_TIMELINE_ARTIFACT,),
                "attributes": dict(event.attributes)
                | {"service_url": "https://internal.example/x"},
            }
        )
        for event in timeline
    )
    fake = cast(PublicationRepository, _CanaryRepository(tampered, canary_timeline))

    files = build_publication(fake, tampered.run_id)
    outputs = files.report_json + files.index_html + files.manifest_json

    for canary in CANARIES:
        assert canary.encode() not in outputs, canary
    assert b"internal.example" not in outputs
    assert b"api_key" not in outputs
    public = build_public_report(tampered, canary_timeline)
    assert "CANARY" not in json.dumps(public.model_dump(mode="json"))


def test_publication_contains_no_private_bundle_fields(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "r.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)

    files = build_publication(repository, report.run_id)
    repository.close()

    payload = json.loads(files.report_json)
    for private_field in (
        "integrity",
        "bundle_sha256",
        "artifacts",
        "objects",
        "manifest",
        "explanation",
        "intent",
        "contract",
        "execution",
        "ledger",
        "receipt",
        "run_id",
    ):
        assert private_field not in payload, private_field
    assert "syn_run_clean" not in files.report_json.decode()

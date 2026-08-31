from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from settlediff.application.auth import ConsumedPaidAuthorization, PaidExecutionRequest
from settlediff.application.payment_rails import (
    AdapterEvidence,
    PaymentRailAdapter,
    SchemaEvidencePort,
    TransactionEvidencePort,
)
from settlediff.domain.models import ArtifactType
from settlediff.domain.money import Money


class MinimalRail:
    adapter_id = "synthetic"

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        return AdapterEvidence(
            adapter_id=self.adapter_id,
            operation="inspect",
            source="synthetic.contract",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            data={"url": request.target, "request_schema": {"type": "object"}},
        )

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence:
        del authorization, request, quoted_price
        raise AssertionError("not exercised by this protocol test")

    async def collect_activity(self) -> AdapterEvidence:
        return AdapterEvidence(
            adapter_id=self.adapter_id,
            operation="activity",
            source="synthetic.activity",
            artifact_type=ArtifactType.ACTIVITY,
            data=[],
        )


def test_adapter_evidence_is_strict_and_preserves_operation_identity() -> None:
    evidence = AdapterEvidence(
        adapter_id="synthetic",
        operation="inspect",
        source="synthetic.contract",
        artifact_type=ArtifactType.SERVICE_CONTRACT,
        data={"raw": "evidence"},
        submission_uncertain=False,
        payment_reference=None,
        transaction_reference=None,
    )

    assert evidence.model_dump(mode="json") == {
        "schema_version": 1,
        "adapter_id": "synthetic",
        "operation": "inspect",
        "source": "synthetic.contract",
        "artifact_type": "service_contract",
        "data": {"raw": "evidence"},
        "observed_at": None,
        "submission_uncertain": False,
        "payment_reference": None,
        "transaction_reference": None,
    }
    with pytest.raises(ValidationError):
        AdapterEvidence.model_validate({**evidence.model_dump(), "invented": True})
    with pytest.raises(ValidationError, match="UTC"):
        AdapterEvidence(
            adapter_id="synthetic",
            operation="inspect",
            source="synthetic.contract",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            data={},
            observed_at=datetime(2026, 8, 31),
        )


def test_optional_capabilities_are_not_forced_onto_every_adapter() -> None:
    adapter = MinimalRail()

    assert isinstance(adapter, PaymentRailAdapter)
    assert not isinstance(adapter, SchemaEvidencePort)
    assert not isinstance(adapter, TransactionEvidencePort)


def test_paid_request_shape_remains_independent_of_adapter_results() -> None:
    request = PaidExecutionRequest(
        run_id="syn_run",
        target="https://example.invalid",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )

    assert request.target == "https://example.invalid"

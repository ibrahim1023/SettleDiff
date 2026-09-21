from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.auth import PaidExecutionRequest
from settlediff.domain.money import Money
from settlediff.perflo.adapter import PerfloAdapter, PerfloClientPort
from settlediff.perflo.parser import PerfloSuccessEnvelope

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _envelope(result: JsonValue) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload={"ok": True, "result": result},
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )


@pytest.mark.asyncio
async def test_perflo_inspect_carries_raw_source_contract() -> None:
    contract_data: dict[str, JsonValue] = {
        "vendor_slug": "synthetic-search",
        "url": "https://example.invalid/search",
        "price": {"amount": "0.01", "unit": "USDC"},
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
    }

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            assert target == "https://example.invalid/search"
            return _envelope(contract_data)

    adapter = PerfloAdapter(cast(PerfloClientPort, FakePerflo()))
    request = PaidExecutionRequest(
        run_id="syn_inspect",
        target="https://example.invalid/search",
        method="POST",
        body={},
        budget=Money(amount=Decimal(1), unit="USDC"),
    )

    evidence = await adapter.inspect(request)

    assert evidence.data == contract_data
    assert evidence.source_contract == contract_data

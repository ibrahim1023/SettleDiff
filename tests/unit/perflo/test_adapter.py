from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.auth import (
    CatalogResourceReference,
    ConsumedPaidAuthorization,
    HttpResourceReference,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.application.payment_rails import AdapterProtocolError
from settlediff.domain.money import Money
from settlediff.perflo.adapter import PerfloAdapter, PerfloClientPort
from settlediff.perflo.parser import PerfloSuccessEnvelope

NOW = datetime(2026, 9, 10, tzinfo=UTC)
FIXTURES = Path("tests/contract/perflo")
TX_HASH = "0x1111111111111111111111111111111111111111111111111111111111111111"


def _payload(name: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads((FIXTURES / name).read_text()))


def _envelope(payload: dict[str, JsonValue]) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload=payload,
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )


def _request() -> PaidExecutionRequest:
    return PaidExecutionRequest(
        run_id="syn_inspect",
        resource=CatalogResourceReference(
            slug="synthetic-weather", input={"city": "Exampleville"}, query={}
        ),
        budget=Money(amount=Decimal("0.05"), unit="USD"),
    )


async def _authorization(request: PaidExecutionRequest) -> ConsumedPaidAuthorization:
    return await PaidExecutionCapability.issue(
        request, expires_at=NOW + timedelta(minutes=5)
    ).consume(request, now=NOW)


def _adapter(client: object) -> PerfloAdapter:
    return PerfloAdapter(cast(PerfloClientPort, client))


@pytest.mark.asyncio
async def test_inspect_extracts_only_top_level_vendor() -> None:
    vendor = _payload("vendor.json")["vendor"]

    class FakePerflo:
        async def inspect_service(self, slug: str) -> PerfloSuccessEnvelope:
            assert slug == "synthetic-weather"
            return _envelope(_payload("vendor.json"))

    evidence = await _adapter(FakePerflo()).inspect(_request())

    assert evidence.data == vendor
    assert evidence.source == "perflo.vendor"
    assert evidence.source_contract == vendor
    assert cast(dict[str, JsonValue], evidence.data)["futureField"] == {"preserved": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": True, "result": {"slug": "synthetic-weather"}},
        {"ok": True, "vendor": None},
        {"ok": True, "vendor": [{"slug": "synthetic-weather"}]},
        {"ok": True, "vendor": "synthetic-weather"},
    ],
)
async def test_inspect_rejects_envelopes_without_a_vendor_object(
    payload: dict[str, JsonValue],
) -> None:
    class FakePerflo:
        async def inspect_service(self, _slug: str) -> PerfloSuccessEnvelope:
            return _envelope(payload)

    with pytest.raises(AdapterProtocolError):
        await _adapter(FakePerflo()).inspect(_request())


@pytest.mark.asyncio
async def test_execute_extracts_result_and_separates_payment_and_transaction_references() -> None:
    result = _payload("pay_wallet_success.json")["result"]

    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope(_payload("pay_wallet_success.json"))

    request = _request()
    evidence = await _adapter(FakePerflo()).execute_once(
        await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
    )

    assert evidence.data == result
    assert evidence.source == "perflo.pay"
    assert evidence.payment_reference == "syn_tx_wallet_001"
    assert evidence.transaction_reference == TX_HASH
    assert cast(dict[str, JsonValue], evidence.data)["additiveField"] == {"preserved": True}


@pytest.mark.asyncio
async def test_execute_credit_charge_never_creates_chain_or_hash_facts() -> None:
    result = _payload("pay_credit_success.json")["result"]
    assert isinstance(result, dict)
    assert result["chargedTo"] == "credit"

    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope(_payload("pay_credit_success.json"))

    request = _request()
    evidence = await _adapter(FakePerflo()).execute_once(
        await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
    )

    assert evidence.data == result
    assert evidence.payment_reference == "syn_tx_credit_001"
    assert evidence.transaction_reference is None


@pytest.mark.asyncio
async def test_execute_credit_suppresses_hash_even_in_additive_evidence() -> None:
    payload = _payload("pay_credit_success.json")
    result = cast(dict[str, JsonValue], payload["result"])
    result["settlement"] = cast(dict[str, JsonValue], result["settlement"]) | {"txHash": TX_HASH}

    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope(payload)

    request = _request()
    evidence = await _adapter(FakePerflo()).execute_once(
        await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
    )

    assert evidence.transaction_reference is None
    assert (
        cast(dict[str, JsonValue], cast(dict[str, JsonValue], evidence.data)["settlement"])[
            "txHash"
        ]
        == TX_HASH
    )


@pytest.mark.asyncio
async def test_execute_terminal_failure_preserves_bounded_output() -> None:
    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope(_payload("pay_failed.json"))

    request = _request()
    evidence = await _adapter(FakePerflo()).execute_once(
        await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
    )

    assert cast(dict[str, JsonValue], evidence.data)["status"] == "failed"
    assert cast(dict[str, JsonValue], evidence.data)["upstream"] == {"httpStatus": 500}
    assert evidence.payment_reference == "syn_tx_failed_001"
    assert evidence.transaction_reference is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["task_running.json", "task_indeterminate.json", "task_failed.json"]
)
async def test_execute_preserves_task_result_variants(name: str) -> None:
    result = _payload(name)["result"]

    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope(_payload(name))

    request = _request()
    evidence = await _adapter(FakePerflo()).execute_once(
        await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
    )

    assert evidence.data == result
    assert evidence.transaction_reference is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "output"),
    [
        (
            "pay_capped_output.json",
            {
                "truncated": True,
                "bytes": 512,
                "preview": "synthetic-capped-preview",
                "note": "output capped by CLI default projection",
            },
        ),
        (
            "pay_file_output.json",
            {
                "savedTo": "/synthetic/perflo-output.json",
                "bytes": 512,
                "preview": "synthetic-file-preview",
            },
        ),
    ],
)
async def test_execute_preserves_capped_and_file_output_projections(
    name: str, output: JsonValue
) -> None:
    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope(_payload(name))

    request = _request()
    evidence = await _adapter(FakePerflo()).execute_once(
        await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
    )

    assert cast(dict[str, JsonValue], evidence.data)["output"] == output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": True, "vendor": {"slug": "synthetic-weather"}},
        {"ok": True, "result": None},
        {"ok": True, "result": [{"slug": "synthetic-weather"}]},
        {"ok": True, "result": "synthetic-weather"},
    ],
)
async def test_execute_rejects_missing_or_non_object_result(
    payload: dict[str, JsonValue],
) -> None:
    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload=payload,
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

    request = _request()
    with pytest.raises(AdapterProtocolError):
        await _adapter(FakePerflo()).execute_once(
            await _authorization(request), request, Money(amount=Decimal("0.01"), unit="USD")
        )


@pytest.mark.asyncio
async def test_activity_preserves_whole_agent_object_and_excludes_money_feed() -> None:
    agent = _payload("activity.json")["agent"]

    class FakePerflo:
        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _envelope(_payload("activity.json"))

    evidence = await _adapter(FakePerflo()).collect_activity()

    assert evidence.source == "perflo.activity.agent"
    assert evidence.data == agent
    assert cast(dict[str, JsonValue], evidence.data)["meta"] == {
        "requestId": "syn_req_001",
        "total": 3,
        "limit": 50,
        "offset": 0,
    }
    assert "money" not in cast(dict[str, JsonValue], evidence.data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": True, "result": [{"id": "legacy"}]},
        {"ok": True, "agent": "not-an-object"},
        {"ok": True, "agent": {"rows": "not-a-list", "meta": {}}},
        {"ok": True, "agent": {"rows": [], "meta": "not-an-object"}},
        {"ok": True, "agent": {"rows": []}},
    ],
)
async def test_activity_rejects_missing_or_malformed_agent_evidence(
    payload: dict[str, JsonValue],
) -> None:
    class FakePerflo:
        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _envelope(payload)

    with pytest.raises(AdapterProtocolError):
        await _adapter(FakePerflo()).collect_activity()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "tx_submitted.json",
        "tx_processing.json",
        "tx_executing.json",
        "tx_success.json",
        "tx_failed.json",
    ],
)
async def test_transaction_status_spreads_top_level_object(name: str) -> None:
    payload = _payload(name)

    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            assert transaction_hash == TX_HASH
            return _envelope(payload)

    evidence = await _adapter(FakePerflo()).collect_transaction(TX_HASH)

    assert evidence.source == "perflo.tx_status"
    assert evidence.data == {key: value for key, value in payload.items() if key != "ok"}
    assert cast(dict[str, JsonValue], evidence.data)["status"] == payload["status"]
    assert evidence.transaction_reference == TX_HASH


@pytest.mark.asyncio
async def test_transaction_status_compares_0x_hashes_case_insensitively() -> None:
    payload = _payload("tx_success.json") | {"txHash": TX_HASH.upper().replace("0X", "0x")}

    class FakePerflo:
        async def transaction_status(self, _hash: str) -> PerfloSuccessEnvelope:
            return _envelope(payload)

    evidence = await _adapter(FakePerflo()).collect_transaction(TX_HASH)

    assert evidence.transaction_reference == TX_HASH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _payload("tx_success.json")
        | {"txHash": "0x9999999999999999999999999999999999999999999999999999999999999999"},
        _payload("tx_success.json") | {"txHash": "syn_other"},
        _payload("tx_success.json") | {"txHash": 5},
        {"ok": True, "status": "success"},
        {"ok": True, "result": {"txHash": TX_HASH, "status": "success"}},
    ],
)
async def test_transaction_status_rejects_mismatched_or_wrapped_tx_hash(
    payload: dict[str, JsonValue],
) -> None:
    class FakePerflo:
        async def transaction_status(self, _hash: str) -> PerfloSuccessEnvelope:
            return _envelope(payload)

    with pytest.raises(AdapterProtocolError):
        await _adapter(FakePerflo()).collect_transaction(TX_HASH)


@pytest.mark.asyncio
async def test_http_resource_fails_before_any_client_call() -> None:
    calls: list[str] = []

    class FakeClient:
        def __getattr__(self, name: str) -> object:
            def record(*_args: object, **_kwargs: object) -> object:
                calls.append(name)
                raise AssertionError(f"client must not be called: {name}")

            return record

    request = PaidExecutionRequest(
        run_id="syn_http",
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USD"),
    )
    adapter = PerfloAdapter(cast(PerfloClientPort, FakeClient()))

    with pytest.raises(AdapterProtocolError, match="requires a catalog resource reference"):
        await adapter.inspect(request)
    with pytest.raises(AdapterProtocolError, match="requires a catalog resource reference"):
        await adapter.reinspect(request)
    assert calls == []

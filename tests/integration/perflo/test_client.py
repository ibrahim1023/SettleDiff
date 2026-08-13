from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from settlediff.application.auth import (
    AuthorizationError,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.domain.money import Money
from settlediff.perflo.client import (
    PerfloClient,
    PerfloCommandError,
    PerfloMutationUncertainError,
    PerfloOutputLimitError,
)
from settlediff.perflo.parser import PerfloSuccessEnvelope

FAKE = Path(__file__).with_name("fake_perflo.py")
NOW = datetime(2026, 8, 13, 10, tzinfo=UTC)


def client(mode: str, *prefix_args: str, timeout: float = 1, limit: int = 2048) -> PerfloClient:
    return PerfloClient(
        command=(sys.executable, str(FAKE), mode, *prefix_args),
        timeout_seconds=timeout,
        max_output_bytes=limit,
    )


def paid_request() -> PaidExecutionRequest:
    return PaidExecutionRequest(
        run_id="syn_run",
        target="https://example.invalid/search?value=a b;$(ignored)",
        body={"query": "synthetic value; $(ignored)"},
        budget=Money(amount=Decimal("0.05"), unit="USDC"),
    )


def capability(request: PaidExecutionRequest) -> PaidExecutionCapability:
    return PaidExecutionCapability.issue(request, expires_at=NOW + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_arguments_are_preserved_without_shell_interpretation() -> None:
    request = paid_request()

    authorization = await capability(request).consume(request, now=NOW)
    envelope = await client("success").execute(authorization, request)

    assert isinstance(envelope, PerfloSuccessEnvelope)
    result = envelope.payload["result"]
    assert isinstance(result, dict)
    assert result["argv"] == [
        "fetch",
        request.target,
        "-b",
        '{"query":"synthetic value; $(ignored)"}',
        "--price",
        "0.05",
        "--asset",
        "USDC",
        "--json",
    ]
    assert envelope.stderr_bytes == len(b"synthetic stderr")


@pytest.mark.asyncio
async def test_authorization_mismatch_fails_before_process_start() -> None:
    request = paid_request()
    changed = request.model_copy(update={"target": "https://example.invalid/changed"})
    authorization = await capability(request).consume(request, now=NOW)

    with pytest.raises(AuthorizationError):
        await client("success").execute(authorization, changed)


@pytest.mark.asyncio
async def test_clean_refusal_preserves_typed_error_and_certainty() -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)

    with pytest.raises(PerfloCommandError) as raised:
        await client("refusal").execute(authorization, request)

    assert raised.value.error.code == "GUARDRAIL_DENIED"
    assert raised.value.error.details == {"limit": "0.05"}
    assert raised.value.submission_uncertain is False


@pytest.mark.asyncio
async def test_timeout_is_uncertain_and_consumed_capability_cannot_retry(tmp_path: Path) -> None:
    request = paid_request()
    authorized = capability(request)
    counter = tmp_path / "count.txt"
    timed_client = client("count-sleep", str(counter), timeout=0.05)
    authorization = await authorized.consume(request, now=NOW)

    with pytest.raises(PerfloMutationUncertainError):
        await timed_client.execute(authorization, request)
    with pytest.raises(AuthorizationError, match="already consumed"):
        await authorized.consume(request, now=NOW)

    assert counter.read_text() == "1"


@pytest.mark.asyncio
async def test_malformed_mutation_output_is_submission_uncertain() -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)
    with pytest.raises(PerfloMutationUncertainError):
        await client("malformed").execute(authorization, request)


@pytest.mark.asyncio
async def test_output_is_bounded() -> None:
    with pytest.raises(PerfloOutputLimitError):
        await client("large", limit=128).get_activity()


@pytest.mark.asyncio
async def test_read_methods_use_narrow_fixed_commands() -> None:
    assert (await client("success").inspect_service("https://example.invalid/x")).payload[
        "result"
    ] == {"argv": ["check", "https://example.invalid/x", "--json"]}
    assert (await client("success").get_schema("synthetic-slug")).payload["result"] == {
        "argv": ["schema", "synthetic-slug", "--json"]
    }
    assert (await client("success").get_activity()).payload["result"] == {
        "argv": ["activity", "--json"]
    }
    assert (await client("success").transaction_status("syn_hash")).payload["result"] == {
        "argv": ["tx", "status", "syn_hash", "--json"]
    }

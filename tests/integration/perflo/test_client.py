from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from settlediff.application.auth import (
    AuthorizationError,
    CatalogResourceReference,
    HttpResourceReference,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.domain.money import Money
from settlediff.perflo.client import (
    PerfloClient,
    PerfloCliVersion,
    PerfloCommandError,
    PerfloMutationUncertainError,
    PerfloOutputLimitError,
    PerfloVersionError,
)
from settlediff.perflo.parser import PerfloSuccessEnvelope

FAKE = Path(__file__).with_name("fake_perflo.py")
NOW = datetime(2026, 8, 13, 10, tzinfo=UTC)
QUOTED_PRICE = Money(amount=Decimal("0.01"), unit="USD")


def client(mode: str, *prefix_args: str, timeout: float = 1, limit: int = 2048) -> PerfloClient:
    return PerfloClient(
        command=(sys.executable, str(FAKE), mode, *prefix_args),
        timeout_seconds=timeout,
        max_output_bytes=limit,
    )


def paid_request() -> PaidExecutionRequest:
    return PaidExecutionRequest(
        run_id="syn_run",
        resource=CatalogResourceReference(
            slug="synthetic-search",
            input={"query": "synthetic value; $(ignored)"},
            query={"limit": 2},
            sub_account="syn-sub;$(ignored)",
        ),
        budget=Money(amount=Decimal("0.05"), unit="USD"),
    )


def capability(request: PaidExecutionRequest) -> PaidExecutionCapability:
    return PaidExecutionCapability.issue(request, expires_at=NOW + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_arguments_are_preserved_without_shell_interpretation() -> None:
    request = paid_request()

    authorization = await capability(request).consume(request, now=NOW)
    envelope = await client("success").execute(authorization, request, QUOTED_PRICE)

    assert isinstance(envelope, PerfloSuccessEnvelope)
    result = envelope.payload["result"]
    assert isinstance(result, dict)
    assert result["argv"] == [
        "pay",
        "synthetic-search",
        "--input",
        '{"query":"synthetic value; $(ignored)"}',
        "--query",
        '{"limit":2}',
        "--max-charge",
        "0.05",
        "--sub-account",
        "syn-sub;$(ignored)",
        "--json",
    ]
    assert envelope.stderr_bytes == len(b"synthetic stderr")


@pytest.mark.asyncio
async def test_pay_omits_empty_input_query_and_absent_sub_account() -> None:
    request = PaidExecutionRequest(
        run_id="syn_run",
        resource=CatalogResourceReference(slug="synthetic-search", input={}, query={}),
        budget=Money(amount=Decimal("0.05"), unit="USD"),
    )
    authorization = await capability(request).consume(request, now=NOW)

    envelope = await client("success").execute(authorization, request, QUOTED_PRICE)

    result = envelope.payload["result"]
    assert isinstance(result, dict)
    assert result["argv"] == [
        "pay",
        "synthetic-search",
        "--max-charge",
        "0.05",
        "--json",
    ]


@pytest.mark.asyncio
async def test_pay_uses_authorized_budget_not_quoted_price() -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)

    envelope = await client("success").execute(
        authorization, request, Money(amount=Decimal("0.03"), unit="USD")
    )

    result = envelope.payload["result"]
    assert isinstance(result, dict)
    argv = result["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("--max-charge") + 1] == "0.05"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quoted_price",
    [
        Money(amount=Decimal("0.06"), unit="USD"),
        Money(amount=Decimal("0.01"), unit="USDC"),
        Money(amount=Decimal("0"), unit="USD"),
        Money(amount=Decimal("-0.01"), unit="USD"),
    ],
)
async def test_invalid_quote_fails_before_process_start(
    quoted_price: Money, tmp_path: Path
) -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)
    counter = tmp_path / "count.txt"

    with pytest.raises(ValueError, match="quote|USD|budget"):
        await client("count-version", str(counter)).execute(authorization, request, quoted_price)

    assert not counter.exists()


@pytest.mark.asyncio
async def test_non_catalog_resource_fails_before_process_start(tmp_path: Path) -> None:
    request = PaidExecutionRequest(
        run_id="syn_run",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.05"), unit="USD"),
    )
    authorization = await capability(request).consume(request, now=NOW)
    counter = tmp_path / "count.txt"

    with pytest.raises(ValueError, match="catalog"):
        await client("count-version", str(counter)).execute(authorization, request, QUOTED_PRICE)

    assert not counter.exists()


@pytest.mark.asyncio
async def test_non_usd_budget_fails_before_process_start(tmp_path: Path) -> None:
    request = paid_request().model_copy(
        update={"budget": Money(amount=Decimal("0.05"), unit="USDC")}
    )
    authorization = await capability(request).consume(request, now=NOW)
    counter = tmp_path / "count.txt"

    with pytest.raises(ValueError, match="USD"):
        await client("count-version", str(counter)).execute(
            authorization, request, Money(amount=Decimal("0.01"), unit="USDC")
        )

    assert not counter.exists()


@pytest.mark.asyncio
async def test_authorization_mismatch_fails_before_process_start() -> None:
    request = paid_request()
    changed = request.model_copy(
        update={
            "resource": HttpResourceReference(
                url="https://example.invalid/changed",
                method="POST",
                body={"query": "synthetic value; $(ignored)"},
            )
        }
    )
    authorization = await capability(request).consume(request, now=NOW)

    with pytest.raises(AuthorizationError):
        await client("success").execute(authorization, changed, QUOTED_PRICE)


@pytest.mark.asyncio
async def test_clean_refusal_preserves_typed_error_and_certainty() -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)

    with pytest.raises(PerfloCommandError) as raised:
        await client("refusal").execute(authorization, request, QUOTED_PRICE)

    assert raised.value.error.code == "GUARDRAIL_DENIED"
    assert raised.value.error.details == {"limit": "0.05"}
    assert raised.value.submission_uncertain is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["uncertain", "unknown-certainty"])
async def test_mutation_error_without_explicit_non_submission_is_uncertain(mode: str) -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)

    with pytest.raises(PerfloMutationUncertainError):
        await client(mode).execute(authorization, request, QUOTED_PRICE)


@pytest.mark.asyncio
async def test_timeout_is_uncertain_and_consumed_capability_cannot_retry(tmp_path: Path) -> None:
    request = paid_request()
    authorized = capability(request)
    counter = tmp_path / "count.txt"
    timed_client = client("count-sleep", str(counter), timeout=0.05)
    authorization = await authorized.consume(request, now=NOW)

    with pytest.raises(PerfloMutationUncertainError):
        await timed_client.execute(authorization, request, QUOTED_PRICE)
    with pytest.raises(AuthorizationError, match="already consumed"):
        await authorized.consume(request, now=NOW)

    assert counter.read_text() == "1"


@pytest.mark.asyncio
async def test_cancellation_after_mutation_launch_is_uncertain(tmp_path: Path) -> None:
    request = paid_request()
    counter = tmp_path / "count.txt"
    paid_client = client("count-sleep", str(counter))
    authorization = await capability(request).consume(request, now=NOW)
    task = asyncio.create_task(paid_client.execute(authorization, request, QUOTED_PRICE))
    async with asyncio.timeout(1):
        while not counter.exists():
            await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(PerfloMutationUncertainError):
        await task


@pytest.mark.asyncio
async def test_malformed_mutation_output_is_submission_uncertain() -> None:
    request = paid_request()
    authorization = await capability(request).consume(request, now=NOW)
    with pytest.raises(PerfloMutationUncertainError):
        await client("malformed").execute(authorization, request, QUOTED_PRICE)


@pytest.mark.asyncio
async def test_output_is_bounded() -> None:
    with pytest.raises(PerfloOutputLimitError):
        await client("large", limit=128).get_activity()
    with pytest.raises(PerfloOutputLimitError):
        await client("large-sleep", timeout=1, limit=128).get_activity()


@pytest.mark.asyncio
async def test_probe_version_parses_stable_semver() -> None:
    version = await client("version", "8.1.2").probe_version()

    assert version == PerfloCliVersion(major=8, minor=1, patch=2)
    assert str(version) == "8.1.2"
    assert version.contract_family == "v8"
    assert version.is_supported is True


@pytest.mark.asyncio
async def test_probe_version_parses_but_marks_other_families_unsupported() -> None:
    version = await client("version", "7.9.0").probe_version()

    assert version.contract_family == "v7"
    assert version.is_supported is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    ["v8.1.2", "8.1.2-beta.1", "8.1", "8.01.2", "8.1.2\nextra", ""],
)
async def test_probe_version_rejects_malformed_output(output: str) -> None:
    with pytest.raises(PerfloVersionError, match="version"):
        await client("version", output).probe_version()


@pytest.mark.asyncio
async def test_probe_version_rejects_nonzero_exit() -> None:
    with pytest.raises(PerfloVersionError, match="version"):
        await client("version-fail").probe_version()


@pytest.mark.asyncio
async def test_probe_version_rejects_timeout() -> None:
    with pytest.raises(PerfloVersionError, match="timed out"):
        await client("sleep", timeout=0.05).probe_version()


@pytest.mark.asyncio
async def test_probe_version_rejects_oversized_output() -> None:
    with pytest.raises(PerfloVersionError, match="limit"):
        await client("large", limit=128).probe_version()


@pytest.mark.asyncio
async def test_probe_version_rejects_unavailable_executable() -> None:
    unavailable = PerfloClient(command=("/nonexistent/syn-perflo",), timeout_seconds=1)

    with pytest.raises(PerfloVersionError, match="unavailable"):
        await unavailable.probe_version()


@pytest.mark.asyncio
async def test_probe_version_cancellation_terminates_without_uncertainty() -> None:
    task = asyncio.create_task(client("sleep").probe_version())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_probe_version_never_runs_a_paid_invocation(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"

    version = await client("count-version", str(counter)).probe_version()

    assert str(version) == "8.0.0"
    assert not counter.exists()


@pytest.mark.asyncio
async def test_read_methods_use_narrow_fixed_commands() -> None:
    assert (await client("success").inspect_service("synthetic-search")).payload["result"] == {
        "argv": ["vendor", "synthetic-search", "--json"]
    }
    assert (await client("success").get_schema("synthetic-slug")).payload["result"] == {
        "argv": ["vendor", "synthetic-slug", "--json"]
    }
    assert (await client("success").get_activity()).payload["result"] == {
        "argv": ["activity", "--json"]
    }
    assert (await client("success").transaction_status("syn_hash")).payload["result"] == {
        "argv": ["tx", "status", "syn_hash", "--json"]
    }

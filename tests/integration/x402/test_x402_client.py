from __future__ import annotations

import asyncio
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import JsonValue

from settlediff.domain.money import Money
from settlediff.x402.client import (
    X402ClientError,
    X402ExternalClient,
    X402SubmissionUncertainError,
    probe_x402_signer,
)
from settlediff.x402.client_contract import (
    ExternalSignerRequest,
    SignerSubmissionState,
    body_digest_for,
)

FAKE = Path(__file__).with_name("fake_x402_signer.py")


def request() -> ExternalSignerRequest:
    body: JsonValue = {"query": "synthetic"}
    return ExternalSignerRequest(
        run_id="syn_run",
        target="https://example.invalid/paid",
        method="POST",
        body=body,
        body_digest=body_digest_for(body),
        max_budget=Money(amount=Decimal("0.001"), unit="USDC"),
        network="eip155:84532",
        scheme="exact",
        payment_terms_digest=hashlib.sha256(b"synthetic terms").hexdigest(),
    )


def client(
    mode: str,
    count_path: Path,
    *,
    timeout_seconds: float = 30,
    max_input_bytes: int = 1_048_576,
    max_output_bytes: int = 1_048_576,
) -> X402ExternalClient:
    return X402ExternalClient(
        command=(sys.executable, str(FAKE), mode, str(count_path)),
        timeout_seconds=timeout_seconds,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )


@pytest.mark.asyncio
async def test_signer_metadata_probe_requires_schema_two_and_public_payer(tmp_path: Path) -> None:
    metadata = await probe_x402_signer(
        (sys.executable, str(FAKE), "success", str(tmp_path / "count"))
    )

    assert metadata.schema_version == 2
    assert metadata.payer == "0x3333333333333333333333333333333333333333"
    assert not (tmp_path / "count").exists()


@pytest.mark.asyncio
async def test_external_client_sends_one_bounded_request_with_controlled_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count_path = tmp_path / "count"
    monkeypatch.setenv("X402_PRIVATE_KEY", "syn_secret_never_inherited")

    result = await client("success", count_path).execute_once(request())

    assert count_path.read_text() == "1"
    assert result.submission_state is SignerSubmissionState.SUBMITTED_CONFIRMED
    assert result.transaction_reference == "syn_transaction"
    assert result.service_response.body == {
        "body_digest": request().body_digest,
        "private_key_visible": False,
    }


@pytest.mark.asyncio
async def test_external_client_instance_cannot_launch_twice(tmp_path: Path) -> None:
    count_path = tmp_path / "count"
    signer = client("success", count_path)

    await signer.execute_once(request())
    with pytest.raises(X402ClientError, match="already launched") as error:
        await signer.execute_once(request())

    assert error.value.submission_uncertain is False
    assert count_path.read_text() == "1"


@pytest.mark.asyncio
async def test_external_client_preserves_structured_uncertainty(tmp_path: Path) -> None:
    result = await client("uncertain", tmp_path / "count").execute_once(request())

    assert result.submission_state is SignerSubmissionState.SUBMISSION_UNCERTAIN
    assert result.transaction_reference == "syn_uncertain_transaction"


@pytest.mark.asyncio
async def test_oversized_input_is_rejected_before_signer_launch(tmp_path: Path) -> None:
    count_path = tmp_path / "count"
    body: JsonValue = {"value": "x" * 2_000}
    oversized = ExternalSignerRequest(
        run_id="syn_run",
        target="https://example.invalid/paid",
        method="POST",
        body=body,
        body_digest=body_digest_for(body),
        max_budget=Money(amount=Decimal("0.001"), unit="USDC"),
        network="eip155:84532",
        scheme="exact",
        payment_terms_digest=hashlib.sha256(b"synthetic terms").hexdigest(),
    )

    with pytest.raises(X402ClientError, match="input") as error:
        await client("success", count_path, max_input_bytes=256).execute_once(oversized)

    assert error.value.submission_uncertain is False
    assert not count_path.exists()


@pytest.mark.asyncio
async def test_cancellation_after_signer_launch_is_uncertain(tmp_path: Path) -> None:
    count_path = tmp_path / "count"
    signer = client("timeout", count_path)
    task = asyncio.create_task(signer.execute_once(request()))
    async with asyncio.timeout(1):
        while not count_path.exists():
            await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(X402SubmissionUncertainError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "invalid", "oversized", "secret"])
async def test_post_launch_failures_are_uncertain_and_never_retried(
    mode: str, tmp_path: Path
) -> None:
    count_path = tmp_path / "count"
    signer = client(
        mode,
        count_path,
        timeout_seconds=0.05,
        max_output_bytes=1024,
    )

    with pytest.raises(X402SubmissionUncertainError) as error:
        await signer.execute_once(request())

    assert count_path.read_text() == "1"
    assert "syn_signature" not in str(error.value)
    with pytest.raises(X402ClientError, match="already launched"):
        await signer.execute_once(request())
    assert count_path.read_text() == "1"


@pytest.mark.asyncio
async def test_oversized_output_stops_before_signer_completion(tmp_path: Path) -> None:
    signer = client(
        "oversized-sleep",
        tmp_path / "count",
        timeout_seconds=0.5,
        max_output_bytes=1024,
    )

    with pytest.raises(X402SubmissionUncertainError, match="output"):
        await signer.execute_once(request())


@pytest.mark.asyncio
async def test_proven_pre_submission_refusal_is_not_marked_uncertain(
    tmp_path: Path,
) -> None:
    count_path = tmp_path / "count"

    with pytest.raises(X402ClientError) as error:
        await client("nonzero", count_path).execute_once(request())

    assert error.value.submission_uncertain is False
    assert count_path.read_text() == "1"
    assert "synthetic signer diagnostic" not in str(error.value)

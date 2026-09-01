from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import JsonValue

from settlediff.application.auth import PaidExecutionCapability, PaidExecutionRequest
from settlediff.application.run import LiveEvidenceCollector
from settlediff.contextdev.client import ContextEvidencePort
from settlediff.domain.models import LedgerStatus, SettlementStatus, Verdict
from settlediff.domain.money import Money
from settlediff.x402.adapter import X402Adapter
from settlediff.x402.client import X402ExternalClient
from settlediff.x402.http import X402ResourceClient
from settlediff.x402.recovery import TRANSFER_TOPIC
from settlediff.x402.rpc import X402RpcClient

FIXTURE = Path(__file__).parents[2] / "contract/x402/fixtures/payment-required-v2.json"
SIGNER = Path(__file__).with_name("fake_x402_signer.py")
TARGET = "https://example.invalid/paid"
TX_HASH = "0x" + "2" * 64
PAYER = "0x3333333333333333333333333333333333333333"
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


@pytest.mark.asyncio
async def test_offline_pipeline_composes_http_signer_rpc_and_canonical_verifier(
    tmp_path: Path,
) -> None:
    challenge_value = json.loads(FIXTURE.read_text())
    assert isinstance(challenge_value, dict)
    challenge = cast(dict[str, JsonValue], challenge_value)
    resource = cast(dict[str, JsonValue], challenge["resource"])
    resource["url"] = TARGET
    challenge_path = tmp_path / "challenge.json"
    challenge_path.write_text(json.dumps(challenge))
    encoded = base64.b64encode(json.dumps(challenge).encode()).decode()
    selected = cast(dict[str, JsonValue], cast(list[JsonValue], challenge["accepts"])[0])
    pay_to = cast(str, selected["payTo"])
    asset = cast(str, selected["asset"])
    amount = int(cast(str, selected["amount"]))
    resource_calls = 0
    rpc_methods: list[str] = []

    async def resource_handler(request: httpx.Request) -> httpx.Response:
        nonlocal resource_calls
        resource_calls += 1
        assert request.url == TARGET
        return httpx.Response(
            402,
            headers={"PAYMENT-REQUIRED": encoded},
            json={"error": "payment required"},
        )

    async def rpc_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = cast(str, payload["method"])
        rpc_methods.append(method)
        if method == "eth_chainId":
            result: JsonValue = "0x14a34"
        else:
            result = {
                "transactionHash": TX_HASH,
                "status": "0x1",
                "from": "0x5555555555555555555555555555555555555555",
                "logs": [
                    {
                        "address": asset.lower(),
                        "topics": [
                            TRANSFER_TOPIC,
                            address_topic(PAYER),
                            address_topic(pay_to),
                        ],
                        "data": "0x" + amount.to_bytes(32, "big").hex(),
                    }
                ],
            }
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
        )

    count_path = tmp_path / "signer-count"
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(resource_handler)) as resource_http,
        httpx.AsyncClient(
            transport=httpx.MockTransport(rpc_handler),
            base_url="https://rpc.example.invalid",
        ) as rpc_http,
    ):
        adapter = X402Adapter(
            X402ResourceClient(resource_http),
            X402ExternalClient(
                command=(
                    sys.executable,
                    str(SIGNER),
                    "pipeline",
                    str(count_path),
                    str(challenge_path),
                )
            ),
            X402RpcClient(rpc_http),
        )
        collector = LiveEvidenceCollector(adapter, cast(ContextEvidencePort, object()))
        request = PaidExecutionRequest(
            run_id="syn_x402_pipeline",
            target=TARGET,
            method="POST",
            body={"query": "synthetic"},
            budget=Money(amount=Decimal("0.01"), unit="USDC"),
        )
        await collector.preflight(request)
        terms = collector.payment_terms
        authorization = await PaidExecutionCapability.issue(
            request,
            payment_terms=terms,
            expires_at=NOW + timedelta(minutes=5),
        ).consume(request, payment_terms=terms, now=NOW)
        await collector.execute(authorization, request)
        report = await collector.verify(request)

    assert resource_calls == 2
    assert count_path.read_text() == "1"
    assert rpc_methods == ["eth_chainId", "eth_getTransactionReceipt"]
    assert report.adapter_id == "x402"
    assert report.verdict is Verdict.VERIFIED
    assert report.receipt is not None
    assert report.receipt.settlement_status is SettlementStatus.SETTLED
    assert report.ledger is not None
    assert report.ledger.status is LedgerStatus.CONFIRMED

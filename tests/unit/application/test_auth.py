from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from settlediff.application.auth import (
    AuthorizationError,
    CatalogResourceReference,
    HttpResourceReference,
    PaidExecutionCapability,
    PaidExecutionRequest,
    PaymentTerms,
)
from settlediff.domain.models import AssetIdentity
from settlediff.domain.money import Money

NOW = datetime(2026, 8, 13, 10, tzinfo=UTC)


def request(**overrides: object) -> PaidExecutionRequest:
    values: dict[str, object] = {
        "run_id": "syn_run_001",
        "resource": HttpResourceReference(
            url="https://example.invalid/search",
            method="POST",
            body={"query": "synthetic"},
        ),
        "budget": Money(amount=Decimal("0.05"), unit="USDC"),
    }
    return PaidExecutionRequest(**(values | overrides))  # type: ignore[arg-type]


def payment_terms(**overrides: object) -> PaymentTerms:
    values: dict[str, object] = {
        "schema_version": 2,
        "adapter_id": "x402",
        "protocol_version": "2",
        "scheme": "exact",
        "network": "eip155:84532",
        "chain": None,
        "asset": AssetIdentity(
            symbol="USDC",
            network="eip155:84532",
            reference="syn_usdc_base_sepolia",
            decimals=6,
        ),
        "asset_symbol": "USDC",
        "recipient": "syn_recipient",
        "quoted_price": Money(amount=Decimal("0.001"), unit="USDC"),
        "max_timeout_seconds": 300,
        "resource_url": "https://example.invalid/search",
        "method": "POST",
        "body_digest": PaidExecutionCapability.body_digest_for({"query": "synthetic"}),
        "response_contract_digest": "a" * 64,
    }
    return PaymentTerms.model_validate(values | overrides)


def capability() -> PaidExecutionCapability:
    return PaidExecutionCapability.issue(request(), expires_at=NOW + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_exact_authorization_consumes_once() -> None:
    authorized = capability()

    token = await authorized.consume(request(), now=NOW)

    assert token.run_id == "syn_run_001"
    assert token.target == "https://example.invalid/search"
    with pytest.raises(AuthorizationError, match="already consumed"):
        await authorized.consume(request(), now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"run_id": "syn_run_other"}, "run"),
        (
            {
                "resource": HttpResourceReference(
                    url="https://example.invalid/other",
                    method="POST",
                    body={"query": "synthetic"},
                )
            },
            "exact resource",
        ),
        (
            {
                "resource": HttpResourceReference(
                    url="https://example.invalid/search",
                    method="POST",
                    body={"query": "changed"},
                )
            },
            "exact resource",
        ),
        ({"budget": Money(amount=Decimal("0.06"), unit="USDC")}, "budget"),
        ({"budget": Money(amount=Decimal("0.04"), unit="USDC")}, "budget"),
        ({"budget": Money(amount=Decimal("0.05"), unit="USD")}, "budget"),
    ],
)
async def test_mismatch_fails_without_consuming(override: dict[str, object], message: str) -> None:
    authorized = capability()

    with pytest.raises(AuthorizationError, match=message):
        await authorized.consume(request(**override), now=NOW)

    assert (await authorized.consume(request(), now=NOW)).run_id == "syn_run_001"


@pytest.mark.asyncio
@pytest.mark.parametrize("checked_at", [NOW + timedelta(minutes=5), NOW + timedelta(minutes=6)])
async def test_expired_authorization_fails_closed(checked_at: datetime) -> None:
    authorized = capability()

    with pytest.raises(AuthorizationError, match="expired"):
        await authorized.consume(request(), now=checked_at)


def test_canonical_body_digest_ignores_object_key_order() -> None:
    first = request(
        resource=HttpResourceReference(
            url="https://example.invalid/search",
            method="POST",
            body={"query": "synthetic", "limit": 3},
        )
    )
    reordered = request(
        resource=HttpResourceReference(
            url="https://example.invalid/search",
            method="POST",
            body={"limit": 3, "query": "synthetic"},
        )
    )
    authorized = PaidExecutionCapability.issue(first, expires_at=NOW + timedelta(minutes=5))

    assert authorized.body_digest == PaidExecutionCapability.body_digest_for(reordered.body)


def catalog_request(**overrides: object) -> PaidExecutionRequest:
    values: dict[str, object] = {
        "run_id": "syn_run_001",
        "resource": CatalogResourceReference(
            slug="synthetic-search",
            input={"query": "synthetic"},
            query={"limit": 3},
            sub_account="synthetic-sub",
        ),
        "budget": Money(amount=Decimal("0.05"), unit="USDC"),
    }
    return PaidExecutionRequest(**(values | overrides))  # type: ignore[arg-type]


def catalog_resource(**overrides: object) -> CatalogResourceReference:
    values: dict[str, object] = {
        "slug": "synthetic-search",
        "input": {"query": "synthetic"},
        "query": {"limit": 3},
        "sub_account": "synthetic-sub",
    }
    return CatalogResourceReference(**(values | overrides))  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource",
    [
        catalog_resource(slug="synthetic-other"),
        catalog_resource(input={"query": "changed"}),
        catalog_resource(query={"limit": 4}),
        catalog_resource(sub_account="other-sub"),
        catalog_resource(sub_account=None),
        catalog_resource(input={"query": "synthetic", "limit": 3}, query={}),
    ],
)
async def test_catalog_resource_changes_invalidate_capability(
    resource: CatalogResourceReference,
) -> None:
    authorized = PaidExecutionCapability.issue(
        catalog_request(), expires_at=NOW + timedelta(minutes=5)
    )

    with pytest.raises(AuthorizationError, match="exact resource"):
        await authorized.consume(catalog_request(resource=resource), now=NOW)

    assert (await authorized.consume(catalog_request(), now=NOW)).run_id == "syn_run_001"


@pytest.mark.asyncio
async def test_catalog_budget_unit_remains_an_exact_check() -> None:
    authorized = PaidExecutionCapability.issue(
        catalog_request(), expires_at=NOW + timedelta(minutes=5)
    )

    with pytest.raises(AuthorizationError, match="exact budget"):
        await authorized.consume(
            catalog_request(budget=Money(amount=Decimal("0.05"), unit="USD")), now=NOW
        )


@pytest.mark.asyncio
async def test_consumed_catalog_authorization_rejects_changed_resource() -> None:
    token = await PaidExecutionCapability.issue(
        catalog_request(), expires_at=NOW + timedelta(minutes=5)
    ).consume(catalog_request(), now=NOW)

    with pytest.raises(AuthorizationError, match="exact resource"):
        token.require_exact_request(catalog_request(resource=catalog_resource(query={"limit": 4})))


def test_catalog_resource_digest_covers_input_and_query_separately() -> None:
    combined = catalog_resource(input={"query": "synthetic", "limit": 3}, query={})
    assert catalog_request(resource=combined).resource_digest != catalog_request().resource_digest


def test_resource_digest_ignores_canonical_key_ordering() -> None:
    ordered = catalog_resource(input={"a": 1, "b": 2}, query={"x": True, "y": None})
    reordered = catalog_resource(input={"b": 2, "a": 1}, query={"y": None, "x": True})

    assert (
        catalog_request(resource=ordered).resource_digest
        == catalog_request(resource=reordered).resource_digest
    )


def test_http_get_resource_cannot_contain_a_body() -> None:
    with pytest.raises(ValueError, match="GET"):
        HttpResourceReference(url="https://example.invalid", method="GET", body={})


@pytest.mark.asyncio
async def test_consumed_authorization_rejects_changed_request() -> None:
    exact_request = request()
    token = await capability().consume(exact_request, now=NOW)

    with pytest.raises(AuthorizationError, match="exact budget"):
        token.require_exact_request(
            exact_request.model_copy(update={"budget": Money(amount=Decimal("0.04"), unit="USDC")})
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"resource_url": "https://example.invalid/other"},
        {"method": "GET"},
        {"body_digest": "b" * 64},
        {"quoted_price": Money(amount=Decimal("0.06"), unit="USDC")},
    ],
)
def test_capability_rejects_payment_terms_inconsistent_with_request(
    updates: dict[str, object],
) -> None:
    with pytest.raises(AuthorizationError, match="payment terms"):
        PaidExecutionCapability.issue(
            request(),
            payment_terms=PaymentTerms.model_validate({**payment_terms().model_dump(), **updates}),
            expires_at=NOW + timedelta(minutes=5),
        )


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.01")])
def test_payment_terms_reject_nonpositive_quote(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        payment_terms(quoted_price=Money(amount=amount, unit="USDC"))


def test_payment_terms_digest_is_canonical_and_covers_all_selected_terms() -> None:
    first = payment_terms()
    same = PaymentTerms.model_validate_json(first.model_dump_json())

    assert first.digest == same.digest
    assert len(first.digest) == 64


def test_payment_terms_schema_v1_remains_readable_without_response_contract_digest() -> None:
    legacy = payment_terms().model_dump(mode="json")
    legacy["schema_version"] = 1
    legacy.pop("response_contract_digest")

    restored = PaymentTerms.model_validate_json(json.dumps(legacy))

    assert restored.schema_version == 1
    assert restored.response_contract_digest is None
    assert "response_contract_digest" not in restored.model_dump(mode="json")
    with pytest.raises(ValueError, match="schema version 1"):
        PaymentTerms.model_validate_json(json.dumps(legacy | {"response_contract_digest": None}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"adapter_id": "other"},
        {"protocol_version": "1"},
        {"scheme": "upto"},
        {
            "network": "eip155:8453",
            "asset": AssetIdentity(
                symbol="USDC",
                network="eip155:8453",
                reference="syn_usdc_base",
                decimals=6,
            ),
        },
        {"chain": "tempo"},
        {
            "asset": AssetIdentity(
                symbol="USDC",
                network="eip155:84532",
                reference="syn_other_usdc",
                decimals=6,
            )
        },
        {
            "asset": AssetIdentity(
                symbol="USDT",
                network="eip155:84532",
                reference="syn_usdt_base_sepolia",
                decimals=6,
            ),
            "asset_symbol": "USDT",
            "quoted_price": Money(amount=Decimal("0.001"), unit="USDT"),
        },
        {"recipient": "syn_other_recipient"},
        {"quoted_price": Money(amount=Decimal("0.002"), unit="USDC")},
        {"max_timeout_seconds": 301},
        {"resource_url": "https://example.invalid/other"},
        {"method": "GET"},
        {"body_digest": "b" * 64},
        {"response_contract_digest": "b" * 64},
    ],
)
async def test_payment_terms_drift_fails_without_consuming(
    updates: dict[str, object],
) -> None:
    exact_request = request()
    exact_terms = payment_terms()
    authorized = PaidExecutionCapability.issue(
        exact_request,
        payment_terms=exact_terms,
        expires_at=NOW + timedelta(minutes=5),
    )

    with pytest.raises(AuthorizationError, match="payment terms"):
        await authorized.consume(
            exact_request,
            payment_terms=PaymentTerms.model_validate({**exact_terms.model_dump(), **updates}),
            now=NOW,
        )

    token = await authorized.consume(exact_request, payment_terms=exact_terms, now=NOW)
    token.require_exact_payment_terms(exact_terms)


@pytest.mark.asyncio
async def test_bound_terms_cannot_be_omitted_during_consumption() -> None:
    exact_request = request()
    authorized = PaidExecutionCapability.issue(
        exact_request,
        payment_terms=payment_terms(),
        expires_at=NOW + timedelta(minutes=5),
    )

    with pytest.raises(AuthorizationError, match="payment terms"):
        await authorized.consume(exact_request, now=NOW)


@pytest.mark.asyncio
async def test_consumed_authorization_rejects_changed_payment_terms() -> None:
    exact_request = request()
    exact_terms = payment_terms()
    token = await PaidExecutionCapability.issue(
        exact_request,
        payment_terms=exact_terms,
        expires_at=NOW + timedelta(minutes=5),
    ).consume(exact_request, payment_terms=exact_terms, now=NOW)

    with pytest.raises(AuthorizationError, match="payment terms"):
        token.require_exact_payment_terms(
            PaymentTerms.model_validate(
                {**exact_terms.model_dump(), "recipient": "syn_other_recipient"}
            )
        )


def test_get_request_with_absent_body_has_stable_digest() -> None:
    get_request = request(
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="GET", body=None
        )
    )

    assert PaidExecutionCapability.body_digest_for(
        get_request.body
    ) == PaidExecutionCapability.body_digest_for(None)


def catalog_terms(req: PaidExecutionRequest, **overrides: object) -> PaymentTerms:
    values: dict[str, object] = {
        "schema_version": 3,
        "adapter_id": "perflo",
        "protocol_version": "8",
        "scheme": None,
        "network": None,
        "chain": None,
        "asset": None,
        "asset_symbol": None,
        "recipient": None,
        "quoted_price": Money(amount=Decimal("0.01"), unit=req.budget.unit),
        "resource_digest": req.resource_digest,
        "contract_digest": "b" * 64,
        "maximum_charge": req.budget,
        "required_max_charge": Money(amount=Decimal("0.05"), unit=req.budget.unit),
    }
    return PaymentTerms.model_validate(values | overrides)


@pytest.mark.asyncio
async def test_schema3_terms_authorize_catalog_request() -> None:
    req = catalog_request()
    terms = catalog_terms(req)
    capability = PaidExecutionCapability.issue(
        req, payment_terms=terms, expires_at=NOW + timedelta(minutes=5)
    )

    token = await capability.consume(req, payment_terms=terms, now=NOW)

    assert token.run_id == req.run_id
    token.require_exact_payment_terms(terms)


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_url": "https://example.invalid"},
        {"method": "POST"},
        {"body_digest": "a" * 64},
        {"response_contract_digest": "a" * 64},
    ],
    ids=["resource_url", "method", "body_digest", "response_contract_digest"],
)
def test_schema3_rejects_http_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        catalog_terms(catalog_request(), **overrides)


@pytest.mark.parametrize(
    "missing",
    ["resource_digest", "contract_digest", "maximum_charge", "required_max_charge"],
)
def test_schema3_requires_catalog_fields(missing: str) -> None:
    req = catalog_request()
    values = {
        "resource_digest": req.resource_digest,
        "contract_digest": "b" * 64,
        "maximum_charge": req.budget,
        "required_max_charge": Money(amount=Decimal("0.05"), unit=req.budget.unit),
    }
    values.pop(missing)
    with pytest.raises(ValueError, match="catalog payment terms require"):
        PaymentTerms(
            schema_version=3,
            adapter_id="perflo",
            protocol_version="8",
            scheme=None,
            network=None,
            chain=None,
            asset=None,
            asset_symbol=None,
            recipient=None,
            quoted_price=Money(amount=Decimal("0.01"), unit=req.budget.unit),
            **values,  # type: ignore[arg-type]
        )


def test_schema3_rejects_quote_above_maximum_charge() -> None:
    req = catalog_request()
    with pytest.raises(ValueError, match="exceeds the required max charge"):
        catalog_terms(req, quoted_price=Money(amount=Decimal("0.09"), unit="USDC"))


def test_schema3_rejects_required_max_charge_above_maximum() -> None:
    req = catalog_request()
    with pytest.raises(ValueError, match="required max charge exceeds the maximum charge"):
        catalog_terms(req, required_max_charge=Money(amount=Decimal("0.09"), unit="USDC"))


def test_schema3_rejects_nonpositive_required_max_charge() -> None:
    req = catalog_request()
    with pytest.raises(ValueError, match="required max charge must be positive"):
        catalog_terms(req, required_max_charge=Money(amount=Decimal("0"), unit="USDC"))


def test_schema3_rejects_required_max_charge_unit_mismatch() -> None:
    req = catalog_request()
    with pytest.raises(ValueError, match="unit"):
        catalog_terms(req, required_max_charge=Money(amount=Decimal("0.05"), unit="EUR"))


def test_schema3_rejects_maximum_charge_unit_mismatch() -> None:
    req = catalog_request()
    with pytest.raises(ValueError, match="unit"):
        catalog_terms(req, maximum_charge=Money(amount=Decimal("0.05"), unit="EUR"))


def test_schema2_rejects_catalog_fields_and_missing_http_fields() -> None:
    with pytest.raises(ValueError, match="schema version 3"):
        payment_terms(resource_digest="b" * 64)
    with pytest.raises(ValueError, match="schema version 3"):
        payment_terms(required_max_charge=Money(amount=Decimal("0.05"), unit="USDC"))
    with pytest.raises(ValueError, match="HTTP payment terms require"):
        payment_terms(resource_url=None)


def test_schema3_capability_rejects_http_request() -> None:
    req = request()
    terms = catalog_terms(req)
    with pytest.raises(AuthorizationError, match="payment terms do not match"):
        PaidExecutionCapability.issue(
            req, payment_terms=terms, expires_at=NOW + timedelta(minutes=5)
        )


def test_schema3_capability_rejects_resource_drift() -> None:
    req = catalog_request()
    other = catalog_request(resource=CatalogResourceReference(slug="other", input={}, query={}))
    terms = catalog_terms(req)
    with pytest.raises(AuthorizationError, match="payment terms do not match"):
        PaidExecutionCapability.issue(
            other, payment_terms=terms, expires_at=NOW + timedelta(minutes=5)
        )


def test_schema3_capability_rejects_budget_change() -> None:
    req = catalog_request()
    terms = catalog_terms(req)
    drifted = catalog_request(budget=Money(amount=Decimal("0.10"), unit="USDC"))
    with pytest.raises(AuthorizationError, match="payment terms do not match"):
        PaidExecutionCapability.issue(
            drifted, payment_terms=terms, expires_at=NOW + timedelta(minutes=5)
        )

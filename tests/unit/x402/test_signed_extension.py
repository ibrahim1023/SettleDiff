from __future__ import annotations

import pytest

from settlediff.x402.signed_extension import extract_offer, extract_receipt


def test_absent_extension_is_distinct_from_malformed_advertised_extension() -> None:
    assert extract_offer({}) is None
    assert extract_receipt({}) is None
    with pytest.raises(ValueError):
        extract_offer({"offer-receipt": {}})


def test_official_jws_envelopes_select_only_the_primary_accept_index() -> None:
    extension = {
        "offer-receipt": {
            "info": {
                "offers": [
                    {"format": "jws", "acceptIndex": 1, "signature": "a.b.c"},
                    {"format": "jws", "acceptIndex": 0, "signature": "d.e.f"},
                ]
            }
        }
    }
    assert extract_offer(extension) == "d.e.f"
    assert (
        extract_receipt(
            {
                "offer-receipt": {
                    "info": {
                        "receipt": {
                            "format": "jws",
                            "signature": "a.b.c",
                        }
                    }
                }
            }
        )
        == "a.b.c"
    )


@pytest.mark.parametrize(
    "offers",
    [
        [],
        [{"format": "eip712", "payload": {}, "signature": "0x123"}],
        [{"format": "jws", "signature": "a.b.c", "acceptIndex": True}],
        [{"format": "jws", "signature": "a.b.c", "payload": {}}],
        [{"format": "jws", "signature": "a.b.c"}] * 2,
    ],
)
def test_unsupported_ambiguous_and_mutated_envelopes_are_refused(offers: object) -> None:
    with pytest.raises(ValueError):
        extract_offer({"offer-receipt": {"info": {"offers": offers}}})

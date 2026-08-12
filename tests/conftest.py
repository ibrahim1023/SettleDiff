from __future__ import annotations

import pytest
from pydantic_ai import models


def pytest_configure() -> None:
    models.ALLOW_MODEL_REQUESTS = False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "live_hyperfusion" not in item.keywords and "paid" not in item.keywords:
            item.add_marker(pytest.mark.offline)

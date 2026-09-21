from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from settlediff.domain.drift import build_contract_snapshot
from settlediff.domain.integrity import sha256_digest
from settlediff.domain.models import ExpectedContract
from settlediff.domain.money import Money
from settlediff.storage.sqlite import SQLiteReportRepository

TARGET = "https://example.invalid/search"
NOW = datetime(2026, 9, 10, tzinfo=UTC)


def contract(url: str = TARGET, **overrides: object) -> ExpectedContract:
    values: dict[str, object] = {
        "schema_version": 2,
        "vendor_slug": "synthetic-search",
        "url": url,
        "price": Money(amount=Decimal("0.01"), unit="USDC"),
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
        "recipient": "0x3333333333333333333333333333333333333333",
    }
    values.update(overrides)
    return ExpectedContract.model_validate(values)


def snapshot(target: str = TARGET, **overrides: object):
    return build_contract_snapshot(
        target,
        "perflo",
        contract(url=target, **overrides),
        {"raw": "contract", "api_key": "syn_secret_snapshot_key"},
    )


def test_migration_006_is_applied_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    repository.close()
    reopened = SQLiteReportRepository(database)
    with closing(sqlite3.connect(database)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        triggers = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
    assert {"contract_snapshots", "contract_snapshot_observations"} <= tables
    assert {
        "contract_snapshots_no_update",
        "contract_snapshots_no_delete",
        "contract_snapshot_observations_no_update",
        "contract_snapshot_observations_no_delete",
    } <= triggers
    reopened.close()


def test_snapshot_round_trips_with_redacted_display_contract(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    built = snapshot()

    repository.save_contract_snapshot(built, NOW)
    stored = repository.contract_snapshots(TARGET, "perflo")

    assert len(stored) == 1
    assert stored[0].snapshot_digest == built.snapshot_digest
    assert stored[0].semantic_fingerprint == built.semantic_fingerprint
    assert stored[0].contract.recipient == "0x3333…3333"
    assert built.contract.recipient == "0x3333333333333333333333333333333333333333"
    with closing(sqlite3.connect(tmp_path / "reports.sqlite3")) as connection:
        raw = connection.execute("SELECT snapshot_json FROM contract_snapshots").fetchone()[0]
    assert "syn_secret_snapshot_key" not in raw
    repository.close()


def test_observations_append_without_rewriting_snapshots(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    built = snapshot()
    repository.save_contract_snapshot(built, NOW)
    repository.save_contract_snapshot(built, datetime(2026, 9, 11, tzinfo=UTC))

    assert repository.contract_snapshot_observation_count(built.snapshot_digest) == 2
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT COUNT(DISTINCT snapshot_json) FROM contract_snapshots"
        ).fetchone()[0]
        observations = connection.execute(
            "SELECT observed_at FROM contract_snapshot_observations ORDER BY observation_id"
        ).fetchall()
    assert rows == 1
    assert [row[0] for row in observations] == [NOW.isoformat(), "2026-09-11T00:00:00+00:00"]
    repository.close()


def test_snapshots_order_by_first_observation(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    older = snapshot()
    newer = snapshot(price=Money(amount=Decimal("0.02"), unit="USDC"))
    repository.save_contract_snapshot(newer, NOW)
    repository.save_contract_snapshot(older, NOW)

    ordered = repository.contract_snapshots(TARGET, "perflo")
    assert [item.snapshot_digest for item in ordered] == [
        newer.snapshot_digest,
        older.snapshot_digest,
    ]
    repository.close()


def test_latest_contract_snapshot_uses_last_observation(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    first = snapshot()
    second = snapshot(price=Money(amount=Decimal("0.02"), unit="USDC"))
    repository.save_contract_snapshot(first, NOW)
    repository.save_contract_snapshot(second, NOW)
    repository.save_contract_snapshot(first, datetime(2026, 9, 12, tzinfo=UTC))

    latest = repository.latest_contract_snapshot(TARGET, "perflo")
    assert latest is not None
    assert latest.snapshot_digest == first.snapshot_digest
    assert repository.latest_contract_snapshot("https://example.invalid/other", "perflo") is None
    repository.close()


def test_snapshot_observation_requires_aware_utc(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    with pytest.raises(ValueError, match="UTC"):
        repository.save_contract_snapshot(snapshot(), datetime(2026, 9, 10))
    repository.close()


def test_snapshot_tables_reject_update_and_delete(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    built = snapshot()
    repository.save_contract_snapshot(built, NOW)
    repository.close()

    with closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE contract_snapshots SET target = 'x' WHERE snapshot_digest = ?",
                (built.snapshot_digest,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM contract_snapshots WHERE snapshot_digest = ?",
                (built.snapshot_digest,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE contract_snapshot_observations SET observed_at = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM contract_snapshot_observations")


def test_save_rejects_post_construction_mutation(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    built = snapshot()
    built.component_fingerprints["price"] = sha256_digest("tampered")
    with pytest.raises(ValueError, match="semantic fingerprint"):
        repository.save_contract_snapshot(built, NOW)
    repository.close()


def test_corrupted_snapshot_json_fails_retrieval(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    built = snapshot()
    repository.save_contract_snapshot(built, NOW)
    tampered_digest = "f" * 64
    tampered = built.model_dump(mode="json")
    tampered["snapshot_digest"] = tampered_digest
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO contract_snapshots(snapshot_digest, target, rail, "
            "semantic_fingerprint, source_digest, snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                tampered_digest,
                TARGET,
                "perflo",
                built.semantic_fingerprint,
                built.source_digest,
                json.dumps(tampered),
            ),
        )
        connection.execute(
            "INSERT INTO contract_snapshot_observations(snapshot_digest, observed_at) "
            "VALUES (?, ?)",
            (tampered_digest, NOW.isoformat()),
        )
        connection.commit()
    with pytest.raises(ValueError, match="digest"):
        repository.contract_snapshots(TARGET, "perflo")
    with pytest.raises(ValueError, match="digest"):
        repository.latest_contract_snapshot(TARGET, "perflo")
    repository.close()

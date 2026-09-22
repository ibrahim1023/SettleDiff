from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError

from settlediff.application import publication
from settlediff.application.publication import (
    PublicationError,
    PublicManifest,
    build_public_report,
    build_publication,
    render_public_report,
    write_publication,
)
from settlediff.application.replay import replay_fixture
from settlediff.domain.integrity import canonical_json_bytes
from settlediff.domain.models import (
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    RetryAssessment,
    RetrySafety,
    Verdict,
)
from settlediff.domain.redaction import mask_identifier
from settlediff.domain.verdict import derive_verdict
from settlediff.storage.sqlite import SQLiteReportRepository

FIXTURES = Path(__file__).parents[3] / "fixtures"


def _persisted(tmp_path: Path, scenario: str = "clean-success"):
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / scenario)
    repository.save(report)
    return repository, report


def test_public_report_contains_only_allowlisted_fields(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)

    public = build_public_report(report, repository.timeline(report.run_id))

    assert set(public.model_dump()) == {
        "schema_version",
        "source_report_schema_version",
        "public_run_id",
        "evidence_through",
        "verdict",
        "findings",
        "delivery",
        "retry",
        "timeline",
    }
    assert public.public_run_id == mask_identifier(report.run_id)
    assert public.public_run_id != report.run_id
    assert public.schema_version == 1
    assert public.source_report_schema_version == report.schema_version
    assert public.verdict is report.verdict
    for finding in public.findings:
        assert set(finding.model_dump()) == {
            "schema_version",
            "finding_id",
            "check_id",
            "severity",
            "status",
            "field_paths",
        }
    assert [f.finding_id for f in public.findings] == sorted(f.finding_id for f in public.findings)
    repository.close()


def test_public_report_is_reproducible_across_reopen(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    first = build_publication(repository, report.run_id)
    repository.close()

    reopened = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    second = build_publication(reopened, report.run_id)

    assert first == second
    reopened.close()


def test_evidence_through_uses_latest_observation_not_wall_clock(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    timeline = repository.timeline(report.run_id)
    expected = max(event.observed_at for event in timeline)

    public = build_public_report(report, timeline)

    assert public.evidence_through == expected
    assert public.evidence_through < datetime.now(UTC)
    repository.close()


def test_public_report_rejects_run_intent_mismatch(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    tampered = report.model_copy(
        update={"intent": report.intent.model_copy(update={"run_id": "run:other"})}
    )

    with pytest.raises(PublicationError, match="run IDs"):
        build_public_report(tampered, repository.timeline(report.run_id))
    repository.close()


def test_public_report_rejects_inconsistent_verdict(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    inconsistent = report.model_copy(update={"verdict": Verdict.PAID_FAILURE})

    with pytest.raises(PublicationError, match="verdict"):
        build_public_report(inconsistent, repository.timeline(report.run_id))
    repository.close()


def test_public_report_rejects_delivery_inconsistent_verdict(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    delivery = DeliveryAssessment(
        status=DeliveryStatus.FAILED,
        reason_code="RESPONSE_CONTRACT_MISMATCH",
        evidence_ids=(f"{report.run_id}:service_response",),
        observation=DeliveryObservation(
            observed_at=report.intent.created_at,
            status_code=200,
            media_type="application/json",
            received_bytes=128,
            truncated=False,
            parsed_body=None,
            evidence_ids=(f"{report.run_id}:service_response",),
        ),
        response_contract_digest="ab" * 32,
    )
    inconsistent = report.model_copy(update={"delivery": delivery})

    with pytest.raises(PublicationError, match="verdict"):
        build_public_report(inconsistent, repository.timeline(report.run_id))
    repository.close()


def test_public_report_requires_exact_timeline_sequence(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    timeline = list(repository.timeline(report.run_id))
    timeline[0] = timeline[0].model_copy(update={"sequence": 7})

    with pytest.raises(PublicationError, match="sequence"):
        build_public_report(report, tuple(timeline))
    repository.close()


def test_public_delivery_and_retry_surfaces(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    assert report.delivery is None and report.retry is None
    base = build_public_report(report, repository.timeline(report.run_id))
    assert base.delivery is None and base.retry is None

    delivery = DeliveryAssessment(
        status=DeliveryStatus.FAILED,
        reason_code="RESPONSE_CONTRACT_MISMATCH",
        evidence_ids=(f"{report.run_id}:service_response",),
        observation=DeliveryObservation(
            observed_at=report.intent.created_at,
            status_code=200,
            media_type="application/json",
            received_bytes=128,
            truncated=False,
            parsed_body={"private": "not published"},
            evidence_ids=(f"{report.run_id}:service_response",),
        ),
        response_contract_digest="ab" * 32,
    )
    retry = RetryAssessment(
        safety=RetrySafety.DO_NOT_RETRY,
        reason_codes=("UNCERTAIN_SUBMISSION",),
        evidence_ids=(f"{report.run_id}:execution",),
    )
    enriched = report.model_copy(
        update={
            "delivery": delivery,
            "retry": retry,
            "verdict": derive_verdict(report.findings, delivery=delivery),
        }
    )

    public = build_public_report(enriched, repository.timeline(report.run_id))

    assert public.delivery is not None
    assert set(public.delivery.model_dump()) == {
        "schema_version",
        "status",
        "reason_code",
        "status_code",
        "media_type",
        "received_bytes",
        "truncated",
    }
    assert public.delivery.status is DeliveryStatus.FAILED
    assert public.delivery.status_code == 200
    assert public.delivery.media_type == "application/json"
    assert public.delivery.received_bytes == 128
    assert public.delivery.truncated is False
    assert "not published" not in json.dumps(public.model_dump(mode="json"))
    assert public.retry is not None
    assert public.retry.safety is RetrySafety.DO_NOT_RETRY
    assert public.retry.reason_codes == ("UNCERTAIN_SUBMISSION",)
    repository.close()


def test_unsafe_timeline_source_maps_to_other(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    timeline = list(repository.timeline(report.run_id))
    timeline[0] = timeline[0].model_copy(update={"source": "attacker.example.org"})

    public = build_public_report(report, tuple(timeline))

    assert public.timeline[0].source == "other"
    repository.close()


def test_unsafe_attribute_values_are_omitted(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    timeline = list(repository.timeline(report.run_id))
    attributes = dict(timeline[0].attributes)
    attributes["event"] = "bad value https://evil.example"
    attributes["injected"] = "dropped"
    timeline[0] = timeline[0].model_copy(update={"attributes": attributes})

    public = build_public_report(report, tuple(timeline))

    assert "injected" not in public.timeline[0].attributes
    assert "event" not in public.timeline[0].attributes
    repository.close()


def test_unsafe_public_code_is_rejected(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    timeline = list(repository.timeline(report.run_id))
    timeline[0] = timeline[0].model_copy(update={"source": "x" * 200, "sequence": 0})
    public = build_public_report(report, tuple(timeline))
    assert public.timeline[0].source == "other"

    bad_finding = report.findings[0].model_copy(update={"finding_id": "bad finding id!"})
    tampered = report.model_copy(update={"findings": (bad_finding, *report.findings[1:])})
    with pytest.raises(PublicationError):
        build_public_report(tampered, repository.timeline(report.run_id))
    repository.close()


def test_disclosure_guard_rejects_embedded_url(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    finding = report.findings[0].model_copy(update={"check_id": "see https://evil.example/x"})
    tampered = report.model_copy(update={"findings": (finding, *report.findings[1:])})

    with pytest.raises(PublicationError, match="disclosive|schema"):
        build_public_report(tampered, repository.timeline(report.run_id))
    repository.close()


def test_rendered_html_is_standalone_and_escaped(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    public = build_public_report(report, repository.timeline(report.run_id))
    finding = public.findings[0].model_copy(update={"check_id": "xss<script>alert(1)</script>"})
    escaped = public.model_copy(update={"findings": (finding, *public.findings[1:])})

    html = render_public_report(escaped)
    lowered = html.lower()

    assert b"<script>alert(1)</script>" not in html
    assert b"&lt;script&gt;" in html
    for disallowed in (b"<script", b"src=", b"href=", b"https://", b"http://"):
        assert disallowed not in lowered
    assert b"Content-Security-Policy" in html
    assert b"default-src 'none'" in html
    assert b"form-action 'none'" in html
    assert b'name="referrer" content="no-referrer"' in html
    assert lowered.count(b"table-wrap") >= 2
    repository.close()


def test_publication_files_and_manifest(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)

    files = build_publication(repository, report.run_id)

    manifest = PublicManifest.model_validate_json(files.manifest_json)
    assert [entry.path for entry in manifest.objects] == ["index.html", "report.json"]
    assert manifest.objects[0].sha256 == sha256(files.index_html).hexdigest()
    assert manifest.objects[1].sha256 == sha256(files.report_json).hexdigest()
    payload = json.loads(files.report_json)
    assert "integrity" not in payload
    assert "bundle_sha256" not in payload
    repository.close()


def test_build_publication_missing_run(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    with pytest.raises(PublicationError, match="not found"):
        build_publication(repository, "run:missing")
    repository.close()


def test_write_publication_rejects_traversal_and_bad_targets(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    root = tmp_path.resolve()

    with pytest.raises(PublicationError, match="\\.\\."):
        write_publication(files, root / "a" / ".." / "out")
    with pytest.raises(PublicationError, match="root or current"):
        write_publication(files, Path("."))
    with pytest.raises(PublicationError, match="not a directory"):
        write_publication(files, root / "missing" / "out")
    with pytest.raises(PublicationError, match="symlink"):
        link = root / "link"
        link.symlink_to(root)
        write_publication(files, link / "out")


def test_write_publication_rejects_symlink_output(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    root = tmp_path.resolve()
    target_dir = root / "real"
    target_dir.mkdir()
    link = root / "out"
    link.symlink_to(target_dir)

    with pytest.raises(PublicationError, match="symlink"):
        write_publication(files, link, force=True)


def test_write_publication_refuses_existing_without_force(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    root = tmp_path.resolve()
    output = root / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("stale")

    with pytest.raises(PublicationError, match="force"):
        write_publication(files, output)
    assert (output / "stale.txt").read_text() == "stale"

    file_target = root / "file"
    file_target.write_text("file")
    with pytest.raises(PublicationError, match="not a directory"):
        write_publication(files, file_target, force=True)


def test_write_publication_installs_exactly_three_files(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    write_publication(files, output)

    assert sorted(entry.name for entry in output.iterdir()) == [
        "index.html",
        "public-manifest.json",
        "report.json",
    ]
    assert (output / "report.json").read_bytes() == files.report_json
    assert (output / "index.html").read_bytes() == files.index_html
    assert (output / "public-manifest.json").read_bytes() == files.manifest_json
    assert all(not entry.is_symlink() for entry in output.iterdir())
    assert not any(p.name.startswith(".pub") for p in output.parent.iterdir())


def test_write_publication_rejects_nested_symlink_in_existing(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    nested = output / "inner" / "deep"
    nested.mkdir(parents=True)
    (nested / "link.txt").symlink_to(tmp_path / "elsewhere.txt")

    with pytest.raises(PublicationError, match="symlink"):
        write_publication(files, output, force=True)
    assert (nested / "link.txt").is_symlink()
    assert not any(p.name.startswith(".pub") for p in output.parent.iterdir())


def test_verify_installed_failure_keeps_absent_target_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    def fail_verify(*_args: object, **_kwargs: object) -> NoReturn:
        raise PublicationError("injected verify failure")

    monkeypatch.setattr(publication, "_verify_installed", fail_verify)
    with pytest.raises(PublicationError, match="injected verify"):
        write_publication(files, output)
    assert not output.exists()
    assert not any(p.name.startswith(".pub") for p in output.parent.iterdir())


def test_verify_installed_failure_restores_existing_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    (output / "inner").mkdir(parents=True)
    (output / "inner" / "keep.txt").write_text("original-bytes")

    def fail_verify(*_args: object, **_kwargs: object) -> NoReturn:
        raise PublicationError("injected verify failure")

    monkeypatch.setattr(publication, "_verify_installed", fail_verify)
    with pytest.raises(PublicationError, match="injected verify"):
        write_publication(files, output, force=True)

    assert sorted(p.relative_to(output).as_posix() for p in output.rglob("*")) == [
        "inner",
        "inner/keep.txt",
    ]
    assert (output / "inner" / "keep.txt").read_text() == "original-bytes"
    assert not any(p.name.startswith(".pub") for p in output.parent.iterdir())


def test_installed_manifest_hashes_match_installed_bytes(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("stale")

    write_publication(files, output, force=True)

    manifest = PublicManifest.model_validate_json((output / "public-manifest.json").read_bytes())
    digests = {entry.path: entry.sha256 for entry in manifest.objects}
    assert digests["index.html"] == sha256((output / "index.html").read_bytes()).hexdigest()
    assert digests["report.json"] == sha256((output / "report.json").read_bytes()).hexdigest()


def test_write_publication_force_replaces_and_removes_stale(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("stale")

    write_publication(files, output, force=True)

    assert sorted(entry.name for entry in output.iterdir()) == [
        "index.html",
        "public-manifest.json",
        "report.json",
    ]


def test_write_failure_leaves_absent_target_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("injected write failure")

    monkeypatch.setattr(publication, "_write_files", fail)
    with pytest.raises(OSError, match="injected"):
        write_publication(files, output)
    assert not output.exists()
    assert not any(p.name.startswith(".pub") for p in tmp_path.resolve().iterdir())


def test_install_failure_restores_existing_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("stale-content")

    original_replace = os.replace

    def fail_install(src: object, dst: object) -> None:
        name = Path(str(src)).name
        if name.startswith(".pub.") and ".backup." not in name:
            raise OSError("injected install failure")
        original_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(publication.os, "replace", fail_install)
    with pytest.raises(OSError, match="injected install"):
        write_publication(files, output, force=True)

    assert output.is_dir()
    assert (output / "stale.txt").read_text() == "stale-content"
    assert not any(p.name.startswith(".pub") for p in tmp_path.resolve().iterdir())


def test_publication_remains_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def block_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("publication attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)
    repository, report = _persisted(tmp_path)

    files = build_publication(repository, report.run_id)
    write_publication(files, tmp_path.resolve() / "pub")
    repository.close()


def test_manifest_rejects_unsorted_or_duplicate() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    entry = {"path": "index.html", "sha256": "0" * 64}
    other = {"path": "report.json", "sha256": "1" * 64}
    with pytest.raises(ValidationError):
        PublicManifest.model_validate(
            {"source_timestamp": now.isoformat(), "objects": [other, entry]}
        )
    with pytest.raises(ValidationError):
        PublicManifest.model_validate(
            {"source_timestamp": now.isoformat(), "objects": [entry, entry]}
        )


def test_write_publication_rejects_tampered_report_json(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    payload = json.loads(files.report_json)
    payload["verdict"] = "PAID_FAILURE"
    tampered = publication.PublicationFiles(
        report_json=json.dumps(payload).encode(),
        index_html=files.index_html,
        manifest_json=files.manifest_json,
    )
    with pytest.raises(PublicationError):
        write_publication(tampered, output)
    assert not output.exists()


def test_write_publication_rejects_tampered_manifest(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    manifest = PublicManifest.model_validate_json(files.manifest_json)
    entries = {entry.path: entry.sha256 for entry in manifest.objects}
    bad_hashes = manifest.model_copy(
        update={
            "objects": tuple(
                publication.PublicManifestEntry(
                    path=entry.path,
                    sha256=("0" * 64 if entry.path == "report.json" else entry.sha256),
                )
                for entry in manifest.objects
            )
        }
    )
    tampered = publication.PublicationFiles(
        report_json=files.report_json,
        index_html=files.index_html,
        manifest_json=canonical_json_bytes(bad_hashes.model_dump(mode="json")),
    )
    with pytest.raises(PublicationError, match="hash"):
        write_publication(tampered, output)
    assert not output.exists()
    assert entries["report.json"] == sha256(files.report_json).hexdigest()

    bad_time = manifest.model_copy(update={"source_timestamp": datetime(2030, 1, 1, tzinfo=UTC)})
    tampered = publication.PublicationFiles(
        report_json=files.report_json,
        index_html=files.index_html,
        manifest_json=canonical_json_bytes(bad_time.model_dump(mode="json")),
    )
    with pytest.raises(PublicationError, match="timestamp"):
        write_publication(tampered, output)
    assert not output.exists()


def test_write_publication_rejects_external_html(tmp_path: Path) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    html = files.index_html.replace(
        b"</body>", b'<script src="https://evil.example/x.js"></script></body>'
    )
    manifest = PublicManifest(
        source_timestamp=PublicManifest.model_validate_json(files.manifest_json).source_timestamp,
        objects=(
            publication.PublicManifestEntry(path="index.html", sha256=sha256(html).hexdigest()),
            publication.PublicManifestEntry(
                path="report.json", sha256=sha256(files.report_json).hexdigest()
            ),
        ),
    )
    tampered = publication.PublicationFiles(
        report_json=files.report_json,
        index_html=html,
        manifest_json=canonical_json_bytes(manifest.model_dump(mode="json")),
    )
    with pytest.raises(PublicationError, match="does not match|disallowed|invalid"):
        write_publication(tampered, output)
    assert not output.exists()


def test_parent_fsync_failure_after_backup_restores_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("original-bytes")

    calls = {"count": 0}
    real_fsync = vars(publication)["_fsync_directory"]

    def fail_second_fsync(path: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected parent fsync failure")
        real_fsync(path)

    monkeypatch.setattr(publication, "_fsync_directory", fail_second_fsync)
    with pytest.raises(OSError, match="injected parent fsync"):
        write_publication(files, output, force=True)

    assert (output / "stale.txt").read_text() == "original-bytes"
    assert not any(p.name.startswith(".pub") for p in output.parent.iterdir())


def test_restore_failure_preserves_backup_and_raises_rollback_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("original-bytes")

    def fail_verify(*_args: object, **_kwargs: object) -> NoReturn:
        raise PublicationError("injected verify failure")

    monkeypatch.setattr(publication, "_verify_installed", fail_verify)

    real_replace = os.replace
    restore_attempts = {"count": 0}

    def fail_restore(src: object, dst: object) -> None:
        name = Path(str(src)).name
        if ".backup." in name:
            restore_attempts["count"] += 1
            raise OSError("injected restore failure")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(publication.os, "replace", fail_restore)
    with pytest.raises(PublicationError, match="rollback failed"):
        write_publication(files, output, force=True)

    assert restore_attempts["count"] == 1
    assert not output.exists()
    backups = [p for p in output.parent.iterdir() if ".backup." in p.name]
    assert len(backups) == 1
    assert (backups[0] / "stale.txt").read_text() == "original-bytes"


def test_write_publication_rejects_disclosive_html_matching_manifest(
    tmp_path: Path,
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"

    html = files.index_html.replace(b"</body>", b"<p>CANARY-private-/Users/alice/secret</p></body>")
    manifest = PublicManifest(
        source_timestamp=PublicManifest.model_validate_json(files.manifest_json).source_timestamp,
        objects=(
            publication.PublicManifestEntry(path="index.html", sha256=sha256(html).hexdigest()),
            publication.PublicManifestEntry(
                path="report.json", sha256=sha256(files.report_json).hexdigest()
            ),
        ),
    )
    tampered = publication.PublicationFiles(
        report_json=files.report_json,
        index_html=html,
        manifest_json=canonical_json_bytes(manifest.model_dump(mode="json")),
    )

    with pytest.raises(PublicationError, match="does not match"):
        write_publication(tampered, output)
    assert not output.exists()


def test_restore_parent_fsync_failure_reports_restored_truthfully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, report = _persisted(tmp_path)
    files = build_publication(repository, report.run_id)
    repository.close()
    output = tmp_path.resolve() / "pub"
    output.mkdir()
    (output / "stale.txt").write_text("original-bytes")

    def fail_verify(*_args: object, **_kwargs: object) -> NoReturn:
        raise PublicationError("injected verify failure")

    monkeypatch.setattr(publication, "_verify_installed", fail_verify)

    calls = {"count": 0}
    real_fsync = vars(publication)["_fsync_directory"]

    def fail_restore_sync(path: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 4:
            raise OSError("injected post-restore fsync failure")
        real_fsync(path)

    monkeypatch.setattr(publication, "_fsync_directory", fail_restore_sync)
    with pytest.raises(PublicationError, match="restored.*durability"):
        write_publication(files, output, force=True)

    assert (output / "stale.txt").read_text() == "original-bytes"
    assert not any(".backup." in p.name for p in output.parent.iterdir())

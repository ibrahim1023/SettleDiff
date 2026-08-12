#!/usr/bin/env python3
"""Validate tracked project documentation without third-party dependencies."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ADR_REQUIRED_FIELDS = (
    re.compile(r"^\*\*Status:\*\*\s+.+$", re.MULTILINE),
    re.compile(r"^\*\*Date:\*\*\s+\d{4}-\d{2}-\d{2}$", re.MULTILINE),
    re.compile(r"^## Context$", re.MULTILINE),
    re.compile(r"^## Decision$", re.MULTILINE),
    re.compile(r"^## Consequences$", re.MULTILINE),
)
BANNED_MARKERS = re.compile(
    r"\b(?:TBD|TODO|FIXME|implement later|fill in details|Similar to Task)\b",
    re.IGNORECASE,
)
MARKER_POLICY_EXCEPTIONS = {("AGENTS.md", "placeholder/TODO prose")}
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def markdown_files() -> tuple[Path, ...]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return tuple(path for path in files if path.is_file())


def check_links(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip().strip("<>").split("#", maxsplit=1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = path.parent / unquote(target)
        if not resolved.exists():
            errors.append(f"{path.relative_to(ROOT)}: missing link target {target!r}")
    return errors


def check_markers(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    relative_path = str(path.relative_to(ROOT))
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not BANNED_MARKERS.search(line):
            continue
        if any(
            relative_path == allowed_path and allowed_text in line
            for allowed_path, allowed_text in MARKER_POLICY_EXCEPTIONS
        ):
            continue
        errors.append(f"{relative_path}:{line_number}: unfinished marker: {line.strip()}")
    return errors


def check_adrs() -> list[str]:
    errors: list[str] = []
    for path in sorted((ROOT / "docs" / "decisions").glob("[0-9][0-9][0-9][0-9]-*.md")):
        text = path.read_text(encoding="utf-8")
        for required in ADR_REQUIRED_FIELDS:
            if not required.search(text):
                errors.append(
                    f"{path.relative_to(ROOT)}: missing ADR field matching {required.pattern!r}"
                )
    return errors


def check_local_inputs_untracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "Product-spec.md", "task.md"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    tracked = result.stdout.splitlines()
    return [f"local-only input is tracked: {path}" for path in tracked]


def main() -> int:
    errors: list[str] = []
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        errors.extend(check_links(path, text))
        errors.extend(check_markers(path, text))
    errors.extend(check_adrs())
    errors.extend(check_local_inputs_untracked())

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("documentation checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

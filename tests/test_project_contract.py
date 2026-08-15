from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]


def load_project_config() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as config_file:
        return cast(dict[str, object], tomllib.load(config_file))


def object_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def string_list(value: object) -> list[str]:
    assert isinstance(value, list)
    items = cast(list[object], value)
    assert all(isinstance(item, str) for item in items)
    return cast(list[str], items)


def test_local_product_inputs_are_ignored_and_untracked() -> None:
    ignore_patterns = (ROOT / ".gitignore").read_text().splitlines()
    assert "Product-spec.md" in ignore_patterns
    assert "task.md" in ignore_patterns

    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "Product-spec.md", "task.md"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_environment_example_contains_only_blank_safe_assignments() -> None:
    lines = (ROOT / ".env.example").read_text().splitlines()
    assignments = [line for line in lines if line and not line.startswith("#")]
    assert assignments

    parsed = dict(line.split("=", maxsplit=1) for line in assignments)
    assert parsed == {
        "SETTLEDIFF_CONTEXTDEV_API_KEY": "",
        "SETTLEDIFF_DATABASE_PATH": "",
        "SETTLEDIFF_HYPERFUSION_API_KEY": "",
        "SETTLEDIFF_HYPERFUSION_BASE_URL": "",
        "SETTLEDIFF_HYPERFUSION_MODEL": "",
        "SETTLEDIFF_OTLP_ENDPOINT": "",
    }


def test_python_and_package_contract() -> None:
    assert (ROOT / ".python-version").read_text() == "3.12\n"

    config = load_project_config()
    project = object_mapping(config["project"])
    assert project["requires-python"] == ">=3.12,<3.14"
    assert object_mapping(project["scripts"]) == {"settlediff": "settlediff.cli:app"}

    dependencies = string_list(project["dependencies"])
    for dependency in (
        "fastapi",
        "httpx",
        "jinja2",
        "pydantic",
        "pydantic-ai-slim",
        "pydantic-settings",
        "typer",
    ):
        assert any(item.lower().startswith(dependency) for item in dependencies)


def test_development_and_pytest_contract() -> None:
    config = load_project_config()
    groups = object_mapping(config["dependency-groups"])
    development = string_list(groups["dev"])

    for dependency in (
        "dirty-equals",
        "hypothesis",
        "inline-snapshot",
        "pyright",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "ruff",
    ):
        assert any(item.lower().startswith(dependency) for item in development)

    tool_config = object_mapping(config["tool"])
    pytest_section = object_mapping(tool_config["pytest"])
    pytest_config = object_mapping(pytest_section["ini_options"])
    assert pytest_config["addopts"] == "--strict-config --strict-markers"
    markers = "\n".join(string_list(pytest_config["markers"]))
    assert "live_hyperfusion" in markers
    assert "paid" in markers
    assert "offline" in markers


def test_console_entry_point_loads() -> None:
    from pydantic_ai import models
    from typer.testing import CliRunner

    from settlediff.cli import app

    assert app.info.name == "settlediff"
    assert models.ALLOW_MODEL_REQUESTS is False
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "deterministic verification" in result.stdout


def test_ci_is_offline_least_privilege_and_immutable() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow
    assert "secrets." not in workflow
    assert 'pytest -m "not live and not paid"' in workflow
    assert "uv sync --locked --all-groups" in workflow
    assert "uv run ruff format --check ." in workflow
    assert "uv run ruff check ." in workflow
    assert "uv run pyright" in workflow
    assert "uv run python scripts/check_docs.py" in workflow

    action_references = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", workflow)
    assert action_references
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in action_references)


def test_documentation_checker_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_docs.py"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

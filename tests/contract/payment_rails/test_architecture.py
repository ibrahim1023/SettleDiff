from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[3]
DOMAIN = ROOT / "src/settlediff/domain"
APPLICATION = ROOT / "src/settlediff/application"
ADAPTER_MODULES = ("settlediff.perflo", "settlediff.x402")
DOMAIN_TERMS = ("perflo", "x402", "facilitator")
DOMAIN_NAMES = {"perflo_cli_version", "x402_version", "facilitator"}


def parsed_files(directory: Path) -> tuple[tuple[Path, ast.Module], ...]:
    return tuple(
        (path, ast.parse(path.read_text(), filename=str(path)))
        for path in sorted(directory.glob("*.py"))
    )


def imported_modules(tree: ast.Module) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


def test_domain_imports_neither_payment_adapter() -> None:
    for path, tree in parsed_files(DOMAIN):
        imports = imported_modules(tree)
        for adapter in ADAPTER_MODULES:
            assert all(not module.startswith(adapter) for module in imports), path


def test_domain_contains_no_provider_specific_branch_terms() -> None:
    for path, tree in parsed_files(DOMAIN):
        string_constants = {
            node.value.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        identifiers = {
            node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr.casefold() for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not any(term in value for term in DOMAIN_TERMS for value in string_constants), path
        assert identifiers.isdisjoint(DOMAIN_NAMES), path


def test_application_core_depends_on_ports_not_adapter_implementations() -> None:
    for path, tree in parsed_files(APPLICATION):
        imports = imported_modules(tree)
        for adapter in ADAPTER_MODULES:
            assert all(not module.startswith(adapter) for module in imports), path


def test_provider_specific_code_is_confined_to_adapter_packages_and_composition_root() -> None:
    allowed_roots = {
        ROOT / "src/settlediff/perflo",
        ROOT / "src/settlediff/x402",
    }
    provider_importers: list[Path] = []
    for path in sorted((ROOT / "src/settlediff").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if any(module.startswith(ADAPTER_MODULES) for module in imported_modules(tree)):
            provider_importers.append(path)

    unexpected = [
        path
        for path in provider_importers
        if path != ROOT / "src/settlediff/cli.py"
        and not any(path.is_relative_to(root) for root in allowed_roots)
    ]
    assert unexpected == []

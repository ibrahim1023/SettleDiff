"""Command-line entry point shared by future SettleDiff commands."""

import typer

app = typer.Typer(
    name="settlediff",
    help="Investigate agent purchases with deterministic verification.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Investigate agent purchases with deterministic verification."""

"""Explicit offline issuer-key setup, signing, and authenticated verification."""

from pathlib import Path
from typing import Annotated

import typer

from settlediff.application.assurance import (
    MAX_SIGNATURE_BYTES,
    AssuranceError,
    AssuranceResult,
    AuthenticationStatus,
    sign_bundle,
    verify_assurance,
)
from settlediff.application.issuer_files import (
    IssuerFileError,
    load_issuer_key,
    load_public_key,
    read_regular,
    write_exclusive,
    write_issuer_keypair,
)


def issuer_keygen(
    private_key: Annotated[
        Path, typer.Option(help="New encrypted private PEM, outside the repository.")
    ],
    public_key: Annotated[
        Path, typer.Option(help="New public PEM for separate trust distribution.")
    ],
) -> None:
    """Explicitly create a dedicated issuer key; never reuse a payer or seller key."""
    password = typer.prompt("Issuer passphrase", hide_input=True, confirmation_prompt=True)
    try:
        fingerprint = write_issuer_keypair(private_key, public_key, password.encode("utf-8"))
    except (IssuerFileError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from None
    typer.echo(f"Issuer SHA-256 fingerprint: {fingerprint}")
    typer.echo("Distribute the public key through a separately trusted channel.")


def sign_private_bundle(
    bundle: Annotated[Path, typer.Argument()],
    issuer_key: Annotated[Path, typer.Option(help="Owner-only encrypted Ed25519 private PEM.")],
    output: Annotated[Path, typer.Option(help="New detached signature file; never overwritten.")],
) -> None:
    """Sign an existing private bundle without changing it or its financial verdict."""
    password = typer.prompt("Issuer passphrase", hide_input=True)
    try:
        key = load_issuer_key(issuer_key, password.encode("utf-8"))
        signature = sign_bundle(read_regular(bundle, 16 * 1024 * 1024), key)
        write_exclusive(output, signature)
    except (AssuranceError, IssuerFileError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from None
    typer.echo("Detached issuer signature created. Financial findings are unchanged.")


def verify_private_assurance(
    bundle: Annotated[Path, typer.Argument()],
    signature: Annotated[Path, typer.Option(help="Detached assurance-signature/v1 envelope.")],
    issuer_public_key: Annotated[
        Path | None, typer.Option(help="Independently trusted public PEM.")
    ] = None,
    issuer_fingerprint: Annotated[
        str | None, typer.Option(help="Previously trusted SHA-256 fingerprint.")
    ] = None,
    revoked_issuer: Annotated[
        list[str] | None, typer.Option(help="Revoked fingerprint; repeatable.")
    ] = None,
) -> None:
    """Offline issuer authentication, separate from the financial machine verdict."""
    if issuer_public_key is None and issuer_fingerprint is None:
        result = AssuranceResult(status=AuthenticationStatus.UNTRUSTED_ISSUER)
    else:
        try:
            key = load_public_key(issuer_public_key) if issuer_public_key is not None else None
            result = verify_assurance(
                read_regular(bundle, 16 * 1024 * 1024),
                read_regular(signature, MAX_SIGNATURE_BYTES),
                expected_public_key=key,
                expected_fingerprint=issuer_fingerprint,
                revoked_fingerprints=frozenset(revoked_issuer or []),
            )
        except FileNotFoundError:
            result = AssuranceResult(status=AuthenticationStatus.MISSING_ARTIFACT)
        except (IssuerFileError, OSError):
            result = AssuranceResult(status=AuthenticationStatus.UNSUPPORTED_FORMAT)
    typer.echo(result.model_dump_json())
    if result.status != AuthenticationStatus.AUTHENTIC:
        raise typer.Exit(2)

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from settlediff.application.issuer_files import (
    IssuerFileError,
    load_issuer_key,
    read_regular,
    write_exclusive,
    write_issuer_keypair,
)


def test_encrypted_key_round_trip(tmp_path: Path) -> None:
    private, public = tmp_path / "issuer.pem", tmp_path / "issuer.pub"
    fingerprint = write_issuer_keypair(private, public, b"test-only-passphrase")
    key = load_issuer_key(private, b"test-only-passphrase")
    assert len(fingerprint) == 64
    assert b"ENCRYPTED PRIVATE KEY" in private.read_bytes()
    assert private.stat().st_mode & 0o777 == 0o600
    assert key.public_key().public_bytes_raw() not in public.read_bytes()
    with pytest.raises(IssuerFileError):
        load_issuer_key(private, b"wrong")
    with pytest.raises(IssuerFileError):
        write_issuer_keypair(private, public, b"test-only-passphrase")


def test_private_permissions_and_plaintext_are_refused(tmp_path: Path) -> None:
    private, public = tmp_path / "issuer.pem", tmp_path / "issuer.pub"
    write_issuer_keypair(private, public, b"test-only-passphrase")
    private.chmod(0o644)
    with pytest.raises(IssuerFileError):
        load_issuer_key(private, b"test-only-passphrase")
    private.chmod(0o600)
    private.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    with pytest.raises(IssuerFileError):
        load_issuer_key(private, b"test-only-passphrase")


@pytest.mark.parametrize("ancestor", [False, True])
def test_symlinks_refused_for_reads_and_writes(tmp_path: Path, ancestor: bool) -> None:
    target = tmp_path / "real"
    target.mkdir()
    (target / "input").write_bytes(b"contents")
    if ancestor:
        link = tmp_path / "alias"
        link.symlink_to(target, target_is_directory=True)
        source, output = link / "input", link / "output"
    else:
        source, output = tmp_path / "input-link", tmp_path / "output-link"
        source.symlink_to(target / "input")
        output.symlink_to(target / "not-created")
    with pytest.raises(IssuerFileError):
        read_regular(source, 100)
    with pytest.raises(IssuerFileError):
        write_exclusive(output, b"new")
    assert not (target / "not-created").exists()


def test_file_limits_and_no_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    write_exclusive(path, b"original")
    with pytest.raises(IssuerFileError):
        write_exclusive(path, b"replacement")
    with pytest.raises(IssuerFileError):
        read_regular(path, 2)
    with pytest.raises(IssuerFileError):
        read_regular(tmp_path, 100)
    assert path.read_bytes() == b"original"


def test_fifo_and_private_hardlink_are_refused(tmp_path: Path) -> None:
    import os

    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(IssuerFileError):
        read_regular(fifo, 100)
    private, public = tmp_path / "key", tmp_path / "public"
    write_issuer_keypair(private, public, b"synthetic-password")
    os.link(private, tmp_path / "alias")
    with pytest.raises(IssuerFileError):
        load_issuer_key(private, b"synthetic-password")


def test_empty_passphrase_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(IssuerFileError):
        write_issuer_keypair(tmp_path / "key", tmp_path / "public", b"")
    assert list(tmp_path.iterdir()) == []

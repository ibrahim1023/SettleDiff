"""Bounded, no-follow file access and encrypted local issuer keys (ADR 0015)."""

from __future__ import annotations

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from settlediff.application.assurance import issuer_fingerprint


class IssuerFileError(ValueError):
    """Unsuitable file or key; diagnostics must not contain key material."""


@contextmanager
def _parent(path: Path) -> Generator[tuple[int, str]]:
    if ".." in path.parts:
        raise IssuerFileError("parent traversal is not allowed")
    parts = Path(os.path.abspath(path)).parts
    descriptor = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[1:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def read_regular(path: Path, limit: int, *, private: bool = False) -> bytes:
    """Reject symlink components and inspect the opened file before reading any bytes."""
    try:
        with _parent(path) as (parent, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    raise IssuerFileError("regular file within size limit required")
                if private and (
                    info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1
                ):
                    raise IssuerFileError("private key requires sole owner access and one link")
                data = stream.read(limit + 1)
                if len(data) > limit:
                    raise IssuerFileError("file exceeds size limit")
                return data
    except FileNotFoundError:
        raise
    except OSError:
        raise IssuerFileError("file access refused") from None


def write_exclusive(path: Path, data: bytes) -> None:
    """Create a private regular file without following links or replacing an existing file."""
    try:
        with _parent(path) as (parent, name):
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    except OSError:
        raise IssuerFileError(
            "exclusive file creation failed; inspect any partial output"
        ) from None


def write_issuer_keypair(private: Path, public: Path, password: bytes) -> str:
    """Explicit opt-in generation only. Never uses a wallet, seller key, or environment secret."""
    if not password:
        raise IssuerFileError("a nonempty passphrase is required")
    key = Ed25519PrivateKey.generate()
    encrypted = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    write_exclusive(private, encrypted)
    write_exclusive(
        public,
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )
    return issuer_fingerprint(key.public_key())


def load_issuer_key(path: Path, password: bytes) -> Ed25519PrivateKey:
    data = read_regular(path, 16 * 1024, private=True)
    if not password or not data.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise IssuerFileError("encrypted PKCS8 key and passphrase required")
    try:
        key = serialization.load_pem_private_key(data, password)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise IssuerFileError("issuer key could not be unlocked") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise IssuerFileError("Ed25519 issuer key required")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(read_regular(path, 4096))
    except (ValueError, UnsupportedAlgorithm):
        raise IssuerFileError("invalid issuer public key") from None
    if not isinstance(key, Ed25519PublicKey):
        raise IssuerFileError("Ed25519 public key required")
    return key

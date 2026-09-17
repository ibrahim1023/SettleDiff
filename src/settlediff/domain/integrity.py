"""Shared canonical JSON and SHA-256 integrity primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from pydantic import StringConstraints

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_digest(value: object) -> Sha256Digest:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

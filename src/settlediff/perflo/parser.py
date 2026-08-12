"""Bounded parser for Perflo's uniform JSON command envelope."""

from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue

DEFAULT_MAX_OUTPUT_BYTES = 1_048_576


class PerfloProtocolError(ValueError):
    """Perflo output did not satisfy the JSON envelope contract."""

    def __init__(
        self,
        message: str,
        *,
        stdout_bytes: int,
        stderr_bytes: int,
        returncode: int,
    ) -> None:
        super().__init__(message)
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes
        self.returncode = returncode


class PerfloParserModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class PerfloError(PerfloParserModel):
    code: str
    message: str
    recoverable: bool
    details: dict[str, JsonValue] | None
    hint: str | None
    submission_uncertain: bool | None


class PerfloSuccessEnvelope(PerfloParserModel):
    ok: Literal[True]
    payload: dict[str, JsonValue]
    stdout_bytes: int
    stderr_bytes: int
    returncode: int


class PerfloErrorEnvelope(PerfloParserModel):
    ok: Literal[False]
    error: PerfloError
    payload: dict[str, JsonValue]
    stdout_bytes: int
    stderr_bytes: int
    returncode: int


PerfloEnvelope = PerfloSuccessEnvelope | PerfloErrorEnvelope


def parse_perflo_envelope(
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    *,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> PerfloEnvelope:
    """Parse exactly one bounded JSON object without inspecting diagnostic prose."""
    metadata = {
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "returncode": returncode,
    }
    if max_output_bytes < 1 or len(stdout) > max_output_bytes or len(stderr) > max_output_bytes:
        raise PerfloProtocolError("Perflo output exceeded the configured output limit", **metadata)

    try:
        decoded = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PerfloProtocolError("Perflo stdout must be UTF-8", **metadata) from error

    try:
        loaded: object = json.loads(decoded)
    except json.JSONDecodeError as error:
        raise PerfloProtocolError(
            "Perflo stdout must contain one valid JSON value", **metadata
        ) from error

    if not isinstance(loaded, dict):
        raise PerfloProtocolError("Perflo envelope must be a JSON object", **metadata)

    payload = cast(dict[str, JsonValue], loaded)
    ok = payload.get("ok")
    if type(ok) is not bool:
        raise PerfloProtocolError("Perflo envelope requires a boolean 'ok' field", **metadata)

    if ok:
        if returncode != 0:
            raise PerfloProtocolError(
                "Perflo returned a success envelope with a non-zero exit status", **metadata
            )
        return PerfloSuccessEnvelope(ok=True, payload=payload, **metadata)

    raw_error = payload.get("error")
    if not isinstance(raw_error, dict):
        raise PerfloProtocolError("Perflo error must be a JSON object", **metadata)
    error_payload = cast(dict[str, JsonValue], raw_error)

    code = error_payload.get("code")
    if not isinstance(code, str) or not code:
        raise PerfloProtocolError("Perflo error.code must be a non-empty string", **metadata)
    message = error_payload.get("message")
    if not isinstance(message, str) or not message:
        raise PerfloProtocolError("Perflo error.message must be a non-empty string", **metadata)
    recoverable = error_payload.get("recoverable")
    if type(recoverable) is not bool:
        raise PerfloProtocolError("Perflo error.recoverable must be a boolean", **metadata)

    details = error_payload.get("details")
    if details is not None and not isinstance(details, dict):
        raise PerfloProtocolError("Perflo error.details must be a JSON object or null", **metadata)
    hint = error_payload.get("hint")
    if hint is not None and not isinstance(hint, str):
        raise PerfloProtocolError("Perflo error.hint must be a string or null", **metadata)
    submission_uncertain = error_payload.get("submissionUncertain")
    if submission_uncertain is not None and type(submission_uncertain) is not bool:
        raise PerfloProtocolError(
            "Perflo error.submissionUncertain must be a boolean or null", **metadata
        )

    error = PerfloError(
        code=code,
        message=message,
        recoverable=recoverable,
        details=cast(dict[str, JsonValue] | None, details),
        hint=hint,
        submission_uncertain=submission_uncertain,
    )
    return PerfloErrorEnvelope(ok=False, error=error, payload=payload, **metadata)

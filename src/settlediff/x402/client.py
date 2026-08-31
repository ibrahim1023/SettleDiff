"""Shell-free bounded client for an independently owned x402 signer process."""

from __future__ import annotations

import asyncio
import os

from pydantic import ValidationError

from settlediff.application.payment_rails import SubmissionUncertainError
from settlediff.x402.client_contract import (
    ExternalSignerRequest,
    ExternalSignerResult,
    SignerSubmissionState,
)


class X402ClientError(RuntimeError):
    submission_uncertain = False


class X402SubmissionUncertainError(SubmissionUncertainError, X402ClientError):
    submission_uncertain = True


class X402ExternalClient:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        timeout_seconds: float = 30,
        max_input_bytes: int = 1_048_576,
        max_output_bytes: int = 1_048_576,
        environment: dict[str, str] | None = None,
    ) -> None:
        if not command or timeout_seconds <= 0 or min(max_input_bytes, max_output_bytes) < 1:
            raise ValueError("invalid x402 signer process configuration")
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._environment = environment or self._controlled_environment()
        self._launch_lock = asyncio.Lock()
        self._launched = False

    async def execute_once(self, request: ExternalSignerRequest) -> ExternalSignerResult:
        encoded_request = request.model_dump_json().encode()
        if len(encoded_request) > self._max_input_bytes:
            raise X402ClientError("x402 signer input exceeded its configured limit")
        async with self._launch_lock:
            if self._launched:
                raise X402ClientError("x402 signer was already launched")
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment,
            )
            self._launched = True
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=encoded_request),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            await self._terminate(process)
            raise X402SubmissionUncertainError(
                "x402 signer timed out after launch; perform read-only recovery"
            ) from error
        if len(stdout) > self._max_output_bytes or len(stderr) > self._max_output_bytes:
            raise X402SubmissionUncertainError(
                "x402 signer output exceeded its limit after launch; perform read-only recovery"
            )
        try:
            result = ExternalSignerResult.model_validate_json(stdout, strict=True)
        except (ValidationError, ValueError, UnicodeDecodeError) as error:
            raise X402SubmissionUncertainError(
                "x402 signer returned invalid evidence after launch; perform read-only recovery"
            ) from error
        if process.returncode != 0:
            if result.submission_state in {
                SignerSubmissionState.NOT_SUBMITTED,
                SignerSubmissionState.PROVEN_NOT_SUBMITTED,
            }:
                raise X402ClientError("x402 signer refused before submission")
            raise X402SubmissionUncertainError(
                "x402 signer failed after possible submission; perform read-only recovery"
            )
        return result

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=0.25)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _controlled_environment() -> dict[str, str]:
        environment: dict[str, str] = {}
        for name in ("PATH", "SYSTEMROOT", "TMPDIR"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        environment["NO_COLOR"] = "1"
        return environment

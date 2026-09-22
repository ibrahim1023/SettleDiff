"""Shell-free, bounded asynchronous Perflo CLI adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re

from pydantic import BaseModel, ConfigDict, Field

from settlediff.application.auth import ConsumedPaidAuthorization, PaidExecutionRequest
from settlediff.application.payment_rails import SubmissionUncertainError
from settlediff.domain.money import Money
from settlediff.perflo.parser import (
    PerfloEnvelope,
    PerfloError,
    PerfloErrorEnvelope,
    PerfloProtocolError,
    parse_perflo_envelope,
)
from settlediff.subprocess_io import OutputLimitExceeded, communicate_bounded


class PerfloClientError(RuntimeError):
    """Base class for safe Perflo boundary failures."""


class PerfloCommandError(PerfloClientError):
    def __init__(self, error: PerfloError) -> None:
        super().__init__(f"Perflo command failed: {error.code}")
        self.error = error
        self.submission_uncertain = error.submission_uncertain


class PerfloMutationUncertainError(SubmissionUncertainError, PerfloClientError):
    submission_uncertain = True


class PerfloOutputLimitError(PerfloClientError):
    pass


class PerfloVersionError(PerfloClientError):
    pass


class PerfloCliVersion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def contract_family(self) -> str:
        return f"v{self.major}"

    @property
    def is_supported(self) -> bool:
        return self.major == 8


_MINOR_UNIT_EXPONENT = {"USDC": 6, "USDT": 6}
_STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class PerfloClient:
    """Run a narrow Perflo command set with fixed process controls."""

    def __init__(
        self,
        *,
        command: tuple[str, ...] = ("perflo",),
        timeout_seconds: float = 30,
        max_output_bytes: int = 1_048_576,
        environment: dict[str, str] | None = None,
    ) -> None:
        if not command or timeout_seconds <= 0 or max_output_bytes < 1:
            raise ValueError("invalid Perflo process configuration")
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._environment = environment or self._controlled_environment()

    async def probe_version(self) -> PerfloCliVersion:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment,
            )
        except OSError as error:
            raise PerfloVersionError("Perflo executable is unavailable") from error
        try:
            stdout, _stderr = await asyncio.wait_for(
                communicate_bounded(process, self._max_output_bytes),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            await self._terminate(process)
            raise
        except TimeoutError as error:
            await self._terminate(process)
            raise PerfloVersionError("Perflo version probe timed out") from error
        except OutputLimitExceeded as error:
            await self._terminate(process)
            raise PerfloVersionError("Perflo version output exceeded its limit") from error
        if process.returncode != 0:
            raise PerfloVersionError("Perflo version probe failed")
        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise PerfloVersionError("Perflo version output is not UTF-8") from error
        lines = text.splitlines()
        match = _STABLE_VERSION.fullmatch(lines[0]) if len(lines) == 1 else None
        if match is None:
            raise PerfloVersionError("Perflo version output is not a stable semantic version")
        return PerfloCliVersion(
            major=int(match[1]),
            minor=int(match[2]),
            patch=int(match[3]),
        )

    async def inspect_service(self, target: str) -> PerfloEnvelope:
        return await self._run(("check", target, "--json"), mutation=False)

    async def get_schema(self, slug: str) -> PerfloEnvelope:
        return await self._run(("schema", slug, "--json"), mutation=False)

    async def get_activity(self) -> PerfloEnvelope:
        return await self._run(("activity", "--json"), mutation=False)

    async def transaction_status(self, transaction_hash: str) -> PerfloEnvelope:
        return await self._run(("tx", "status", transaction_hash, "--json"), mutation=False)

    async def execute(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> PerfloEnvelope:
        authorization.require_exact_request(request)
        if quoted_price.amount <= 0:
            raise ValueError("quote must be positive")
        if quoted_price.unit != request.budget.unit:
            raise ValueError(
                f"quote unit {quoted_price.unit} does not match authorized budget unit "
                f"{request.budget.unit}"
            )
        if not quoted_price.is_within(request.budget):
            raise ValueError(
                f"quote {quoted_price.amount} {quoted_price.unit} exceeds the authorized budget "
                f"{request.budget.amount} {request.budget.unit}"
            )
        exponent = _MINOR_UNIT_EXPONENT.get(quoted_price.unit)
        if exponent is None:
            raise ValueError(f"Perflo does not support quote unit {quoted_price.unit}")
        price_minor = quoted_price.amount.scaleb(exponent)
        if price_minor != price_minor.to_integral_value():
            raise ValueError("quote has more precision than its settlement asset")
        body = json.dumps(request.body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return await self._run(
            (
                "fetch",
                request.target,
                "-b",
                body,
                "--price",
                format(price_minor, "f"),
                "--asset",
                request.budget.unit,
                "--json",
            ),
            mutation=True,
        )

    async def _run(self, args: tuple[str, ...], *, mutation: bool) -> PerfloEnvelope:
        process = await asyncio.create_subprocess_exec(
            *self._command,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._environment,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                communicate_bounded(process, self._max_output_bytes),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError as error:
            await self._terminate(process)
            if mutation:
                raise PerfloMutationUncertainError(
                    "Perflo mutation was cancelled after launch; "
                    "verify status before any new attempt"
                ) from error
            raise
        except TimeoutError as error:
            await self._terminate(process)
            if mutation:
                raise PerfloMutationUncertainError(
                    "Perflo mutation timed out after launch; verify status before any new attempt"
                ) from error
            raise PerfloClientError("Perflo read timed out") from error
        except OutputLimitExceeded as error:
            await self._terminate(process)
            if mutation:
                raise PerfloMutationUncertainError(
                    "Perflo mutation output exceeded its limit; verify status before retrying"
                ) from error
            raise PerfloOutputLimitError("Perflo output exceeded its configured limit") from error

        try:
            envelope = parse_perflo_envelope(
                stdout,
                stderr,
                process.returncode or 0,
                max_output_bytes=self._max_output_bytes,
            )
        except PerfloProtocolError as error:
            if mutation:
                raise PerfloMutationUncertainError(
                    "Perflo mutation returned an invalid envelope; verify status before retrying"
                ) from error
            raise PerfloClientError("Perflo read returned an invalid envelope") from error

        if isinstance(envelope, PerfloErrorEnvelope):
            if mutation and envelope.error.submission_uncertain is not False:
                raise PerfloMutationUncertainError(
                    "Perflo mutation returned no proof of non-submission; "
                    "verify status before retrying"
                )
            raise PerfloCommandError(envelope.error)
        return envelope

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

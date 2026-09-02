from __future__ import annotations

import asyncio


class OutputLimitExceeded(RuntimeError):
    pass


async def _read_bounded(stream: asyncio.StreamReader | None, max_output_bytes: int) -> bytes:
    if stream is None:
        raise RuntimeError("subprocess output pipe is unavailable")
    output = bytearray()
    while chunk := await stream.read(65_536):
        output.extend(chunk)
        if len(output) > max_output_bytes:
            raise OutputLimitExceeded
    return bytes(output)


async def communicate_bounded(
    process: asyncio.subprocess.Process,
    max_output_bytes: int,
    *,
    input_bytes: bytes | None = None,
) -> tuple[bytes, bytes]:
    if input_bytes is not None:
        if process.stdin is None:
            raise RuntimeError("subprocess input pipe is unavailable")
        try:
            process.stdin.write(input_bytes)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
    tasks = (
        asyncio.create_task(_read_bounded(process.stdout, max_output_bytes)),
        asyncio.create_task(_read_bounded(process.stderr, max_output_bytes)),
    )
    try:
        stdout, stderr = await asyncio.gather(*tasks)
        await process.wait()
        return stdout, stderr
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

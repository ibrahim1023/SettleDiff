from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[2]


def _port() -> int:
    with socket.socket() as value:
        value.bind(("127.0.0.1", 0))
        return int(value.getsockname()[1])


def test_cli_writer_becomes_visible_to_separate_ui_process(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    database.touch()
    port = _port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from settlediff.cli import app; app()",
            "serve",
            "--database",
            str(database),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/runs"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                initial = httpx.get(url, timeout=0.25)
                break
            except httpx.HTTPError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = server.communicate()
                    raise AssertionError(f"UI server failed to start: {stdout} {stderr}") from None
                time.sleep(0.05)
        assert initial.status_code == 200
        assert "syn_x402_clean" not in initial.text

        writer = subprocess.run(
            [
                sys.executable,
                "-c",
                "from settlediff.cli import app; app()",
                "verify-fixture",
                "fixtures/x402-clean-success",
                "--database",
                str(database),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert writer.returncode == 0, writer.stdout + writer.stderr

        observed = httpx.get(url, timeout=1)
        assert observed.status_code == 200
        assert "syn_x402_clean" in observed.text
        assert "fixture" in observed.text
        assert "VERIFIED" in observed.text
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

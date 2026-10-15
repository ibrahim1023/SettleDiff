"""Explicitly opted-in, synthetic loopback TLSNotary research trial."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--upstream", type=Path, required=True)
parser.add_argument("--workdir", type=Path, required=True)
parser.add_argument("--allow-loopback", action="store_true")
options = parser.parse_args()
if not options.allow_loopback:
    parser.error("synthetic network experiment requires --allow-loopback")
source = options.upstream.resolve()
trial = options.workdir.resolve()
if trial.is_relative_to(Path(__file__).resolve().parents[2]):
    parser.error("research outputs must stay outside the SettleDiff checkout")
commit = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, check=True, text=True
).stdout.strip()
if commit != "47aee45b53e06648c1b2ad3689b367b8c923fdec":
    parser.error("TLSNotary checkout must match the pinned alpha.15 commit")
trial.mkdir(mode=0o700, exist_ok=False)
os.umask(0o077)
env = {"PATH": os.environ["PATH"], "NO_COLOR": "1", "TMPDIR": "/private/tmp", "RUST_LOG": "error"}
body = json.dumps(
    json.loads((source / "crates/server-fixture/server/src/data/1kb.json").read_bytes()),
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode()
digest = hashlib.sha256(body).hexdigest()
results: list[dict[str, object]] = []


def run(
    name: str, args: Sequence[str | Path], expect: bool = True
) -> subprocess.CompletedProcess[bytes]:
    started = time.perf_counter()
    result = subprocess.run(
        [str(x) for x in args], cwd=trial, env=env, capture_output=True, timeout=180, check=False
    )
    elapsed = time.perf_counter() - started
    (trial / (name + ".log")).write_bytes(result.stdout + result.stderr)
    record: dict[str, object] = {
        "case": name,
        "exit": result.returncode,
        "seconds": round(elapsed, 6),
    }
    if expect and result.returncode == 0 and args[0] == verify:
        record["output"] = json.loads(result.stdout)
    results.append(record)
    print(json.dumps(record), flush=True)
    if (result.returncode == 0) != expect:
        raise RuntimeError(
            f"{name} unexpected result: {result.stderr[-1200:].decode(errors='replace')}"
        )
    return result


with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
fixture_log = (trial / "fixture.log").open("wb")
server = subprocess.Popen(
    [str(source / "target/release/tlsn-server-fixture")],
    cwd=trial,
    env=env | {"ADDR": "127.0.0.1", "PORT": str(port)},
    stdout=fixture_log,
    stderr=subprocess.STDOUT,
)
prove = source / "target/release/examples/attestation_prove"
present = source / "target/release/examples/attestation_present"
verify = source / "target/release/examples/attestation_verify"
try:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            if server.poll() is not None:
                raise RuntimeError("fixture exited") from None
            time.sleep(0.05)
    env |= {"SERVER_HOST": "127.0.0.1", "SERVER_PORT": str(port)}
    run("fresh-session", [prove])
finally:
    server.terminate()
    server.wait(timeout=10)
    fixture_log.close()
print("HTTPS fixture stopped before presentation verification", flush=True)
run("full-presentation", [present])
full = (trial / "example-json.presentation.tlsn").read_bytes()
args = [
    verify,
    "--presentation",
    "example-json.presentation.tlsn",
    "--notary-key",
    "research-notary-key.bin",
    "--body-sha256",
    digest,
]
run("valid-contract", args)
key = bytearray((trial / "research-notary-key.bin").read_bytes())
key[-1] ^= 1
(trial / "wrong-key.bin").write_bytes(key)
run(
    "wrong-notary",
    [
        verify,
        "--presentation",
        "example-json.presentation.tlsn",
        "--notary-key",
        "wrong-key.bin",
        "--body-sha256",
        digest,
    ],
    False,
)
run("wrong-server", args + ["--server", "other.test"], False)
run("wrong-body", args[:-1] + ["0" * 64], False)
time.sleep(1.1)  # Session time has whole-second resolution.
run("stale-session", args + ["--max-age-seconds", "0"], False)
changed = bytearray(full)
changed[-1] ^= 1
(trial / "example-json.presentation.tlsn").write_bytes(changed)
run("altered-proof", args, False)
(trial / "example-json.presentation.tlsn").write_bytes(full + b"garbage")
run("trailing-bytes", args, False)
for flag in ("omit-identity", "omit-transcript", "redact-body", "redact-status"):
    run("present-" + flag, [present, "--" + flag])
    run("reject-" + flag, args, False)
(trial / "example-json.presentation.tlsn").write_bytes(full)
(trial / "example-json.secrets.tlsn").unlink()
run("valid-replay", args)
summary = {
    "upstream_commit": "47aee45b53e06648c1b2ad3689b367b8c923fdec",
    "synthetic": True,
    "body_bytes": len(body),
    "body_sha256": digest,
    "artifacts": {
        name: (trial / name).stat().st_size
        for name in (
            "example-json.attestation.tlsn",
            "example-json.presentation.tlsn",
            "research-notary-key.bin",
        )
    },
    "results": results,
}
(trial / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
print("Synthetic trial complete; no network services remain running.", flush=True)

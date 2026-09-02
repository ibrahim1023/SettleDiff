from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> None:
    mode = sys.argv[1]
    args = sys.argv[2:]
    if mode == "sleep":
        time.sleep(2)
    elif mode == "count-sleep":
        counter = Path(args.pop(0))
        count = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(count + 1))
        time.sleep(2)
    elif mode == "large":
        sys.stdout.write("x" * 4096)
    elif mode == "malformed":
        sys.stdout.write("not-json")
    elif mode in {"refusal", "uncertain", "unknown-certainty"}:
        error: dict[str, object] = {
            "code": "GUARDRAIL_DENIED" if mode == "refusal" else "UPSTREAM_UNAVAILABLE",
            "message": "synthetic refusal" if mode == "refusal" else "synthetic uncertainty",
            "recoverable": False,
            "details": {"limit": "0.05"},
            "hint": "use a smaller synthetic amount",
        }
        if mode != "unknown-certainty":
            error["submissionUncertain"] = mode == "uncertain"
        sys.stdout.write(json.dumps({"ok": False, "error": error}))
        raise SystemExit(1)
    else:
        sys.stderr.write("synthetic stderr")
        sys.stdout.write(json.dumps({"ok": True, "result": {"argv": args}}))


if __name__ == "__main__":
    main()

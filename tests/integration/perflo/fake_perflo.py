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
    elif mode == "refusal":
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "GUARDRAIL_DENIED",
                        "message": "synthetic refusal",
                        "recoverable": False,
                        "details": {"limit": "0.05"},
                        "hint": "use a smaller synthetic amount",
                        "submissionUncertain": False,
                    },
                }
            )
        )
        raise SystemExit(1)
    else:
        sys.stderr.write("synthetic stderr")
        sys.stdout.write(json.dumps({"ok": True, "result": {"argv": args}}))


if __name__ == "__main__":
    main()

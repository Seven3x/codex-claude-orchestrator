from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    payload = json.load(sys.stdin)
    body = json.dumps({
        "job_id": os.getenv("CCO_JOB_ID", ""),
        "job_dir": os.getenv("CCO_JOB_DIR", ""),
        "worker_result_path": os.getenv("CCO_WORKER_RESULT_PATH", ""),
        "claude_session_id": payload.get("session_id", ""),
        "cwd": payload.get("cwd", ""),
        "reason": payload.get("reason", ""),
        "transcript_path": payload.get("transcript_path", ""),
    }).encode("utf-8")

    hook_url = os.getenv("CCO_HOOK_URL", "http://127.0.0.1:8765/claude-session-end")
    req = urllib.request.Request(
        hook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            _ = resp.read()
    except Exception as exc:
        print(f"cco hook notify failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _job_status_from_fs(job_dir: Path) -> dict[str, Any]:
    meta_path = job_dir / "meta.json"
    worker_result_path = job_dir / "worker_result.json"
    monitor_log_path = job_dir / "cco_monitor.log"
    claude_stdout_path = job_dir / "claude_stdout.log"
    claude_stderr_path = job_dir / "claude_stderr.log"
    claude_output_path = job_dir / "claude_output.log"

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    worker_result = json.loads(worker_result_path.read_text(encoding="utf-8")) if worker_result_path.exists() else None
    monitor_log = monitor_log_path.read_text(encoding="utf-8", errors="replace") if monitor_log_path.exists() else ""
    stdout_tail = claude_stdout_path.read_text(encoding="utf-8", errors="replace") if claude_stdout_path.exists() else ""
    stderr_tail = claude_stderr_path.read_text(encoding="utf-8", errors="replace") if claude_stderr_path.exists() else ""
    output_tail = claude_output_path.read_text(encoding="utf-8", errors="replace") if claude_output_path.exists() else ""

    return {
        "ok": meta is not None,
        "job_dir": str(job_dir),
        "meta": meta,
        "worker_result": worker_result,
        "monitor_log_tail": "\n".join(monitor_log.strip().splitlines()[-20:]) if monitor_log else "",
        "claude_output_tail": "\n".join(output_tail.strip().splitlines()[-50:]) if output_tail else "",
        "claude_stdout_tail": "\n".join(stdout_tail.strip().splitlines()[-20:]) if stdout_tail else "",
        "claude_stderr_tail": "\n".join(stderr_tail.strip().splitlines()[-20:]) if stderr_tail else "",
    }


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("cco")

    @mcp.tool()
    def cco_health(server_url: str = "http://127.0.0.1:8765") -> dict[str, Any]:
        """Check whether the host-side CCO service is up."""
        return _get_json(f"{server_url.rstrip('/')}/health")

    @mcp.tool()
    def cco_dispatch(
        repo_root: str,
        kind: str,
        task: str,
        paths: list[str] | None = None,
        checks: list[str] | None = None,
        codex_thread_id: str = "",
        no_codex_resume: bool = False,
        requires_web: bool = False,
        force: bool = False,
        server_url: str = "http://127.0.0.1:8765",
    ) -> dict[str, Any]:
        """Send a minimal dispatch request to the host-side CCO service."""
        payload = {
            "repo_root": repo_root,
            "kind": kind,
            "task": task,
            "paths": paths or [],
            "checks": checks or [],
            "codex_thread_id": codex_thread_id,
            "no_codex_resume": no_codex_resume,
            "requires_web": requires_web,
            "force": force,
        }
        return _post_json(f"{server_url.rstrip('/')}/dispatch", payload)

    @mcp.tool()
    def cco_job_status(
        job_dir: str,
    ) -> dict[str, Any]:
        """Read job status, worker result, and recent monitor log lines from disk."""
        return _job_status_from_fs(Path(job_dir).resolve())

    @mcp.tool()
    def cco_ping_dispatch(
        server_url: str = "http://127.0.0.1:8765",
        repo_root: str = "",
    ) -> dict[str, Any]:
        """Return health plus the repo root that will be used for future dispatches."""
        result = _get_json(f"{server_url.rstrip('/')}/health")
        if repo_root:
            result["requested_repo_root"] = str(Path(repo_root).resolve())
        return result

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

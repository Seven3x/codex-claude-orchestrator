from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .claude_client import launch_claude
from .config import OrchestratorConfig
from .models import JobRecord
from .prompts import build_claude_worker_prompt
from .registry import Registry
from .router import decide_route
from .util import ensure_dir, trim, split_csv


def new_job_id(kind: str) -> str:
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{kind}"


def dispatch_job(
    *,
    config: OrchestratorConfig,
    kind: str,
    task: str,
    paths_csv: str,
    checks: list[str],
    codex_thread_id: str = "",
    requires_web: bool = False,
    force: bool = False,
    no_codex_resume: bool = False,
) -> dict:
    paths = split_csv(paths_csv)
    decision = decide_route(
        task=task,
        kind=kind,
        requires_web=requires_web,
    )
    if not decision.route_to_claude and not force:
        return {
            "dispatched": False,
            "reason": decision.reason,
            "send_back_to": "codex",
        }

    registry = Registry(config.job_root)
    job_id = new_job_id(kind)
    job_dir = ensure_dir(config.job_root / job_id)
    result_path = job_dir / "worker_result.json"
    claude_stdout_path = job_dir / "claude_stdout.log"
    claude_stderr_path = job_dir / "claude_stderr.log"
    prompt_path = job_dir / "worker_prompt.txt"
    meta_path = job_dir / "meta.json"
    resume_prompt_path = job_dir / "codex_resume_prompt.txt"
    resume_response_path = job_dir / "codex_resume_response.txt"

    repo_name = config.repo_root.name
    session_name = f"ccw/{repo_name}/{job_id}/{kind}"

    prompt = build_claude_worker_prompt(
        job_id=job_id,
        kind=kind,
        task=task,
        paths=paths,
        checks=checks,
        result_path=result_path,
    )
    prompt_path.write_text(prompt, encoding="utf-8")

    record = JobRecord(
        job_id=job_id,
        kind=kind,
        task=trim(task, 220),
        cwd=str(config.repo_root),
        job_dir=str(job_dir),
        claude_session_name=session_name,
        codex_thread_id=codex_thread_id.strip(),
        status="running",
        paths=paths,
        checks=checks,
        prompt_path=str(prompt_path),
        worker_result_path=str(result_path),
        claude_stdout_path=str(claude_stdout_path),
        claude_stderr_path=str(claude_stderr_path),
        codex_resume_prompt_path=str(resume_prompt_path),
        codex_resume_response_path=str(resume_response_path),
        notes={
            "route_reason": decision.reason,
            "no_codex_resume": bool(no_codex_resume),
        },
    )
    registry.upsert(record)
    meta_path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    claude_pid = launch_claude(config=config, record=record, prompt=prompt)
    record.claude_pid = claude_pid
    registry.upsert(record)
    meta_path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "dispatched": True,
        "job_id": job_id,
        "claude_session_name": session_name,
        "claude_pid": claude_pid,
        "claude_started": claude_pid > 0,
        "job_dir": str(job_dir),
        "reason": decision.reason,
        "codex_thread_id": codex_thread_id,
        "no_codex_resume": no_codex_resume,
        "poll_worker_in_this_session": False,
        "wait_for_hook": True,
        "suggested_next_step": (
            "Stop here and wait for the Claude SessionEnd hook."
            if not no_codex_resume
            else "Stop here; do not monitor this worker in the current Codex session."
        ),
    }

from __future__ import annotations

import json
from pathlib import Path

from .util import trim, trim_items


def build_claude_worker_prompt(
    *,
    job_id: str,
    kind: str,
    task: str,
    paths: list[str],
    checks: list[str],
    result_path: Path,
) -> str:
    # Keep this intentionally short.
    # Stable behavior belongs in CLAUDE.md, not here.
    lines: list[str] = [
        "Follow CLAUDE.md.",
        f"TASK: {trim(task, 240)}",
        f"KIND: {kind}",
    ]
    if paths:
        lines.append("PATHS:")
        lines.extend(f"- {p}" for p in trim_items(paths, 8, 120))
    if checks:
        lines.append("CHECK:")
        lines.extend(f"- {c}" for c in trim_items(checks, 2, 140))

    lines.extend(
        [
            "RULES:",
            "- local files and local commands only",
            "- smallest possible diff",
            "- if judgment-heavy, use status NEEDS_CODEX_DECISION",
            f"- write the final JSON result file to {result_path.as_posix()}",
            "JSON KEYS:",
            '- status: "OK" | "NEEDS_CODEX_DECISION" | "FAILED"',
            "- summary: array of short strings",
            "- changed_files: array of relative paths",
            "- check_notes: array of short strings",
            "- open_questions: array of short strings",
            "- needs_codex_review: boolean",
        ]
    )

    if kind == "run":
        lines.append("Focus on the final outcome, not verbose progress.")
    elif kind == "debug":
        lines.append("Only solve a bounded, reproducible local issue.")
    elif kind == "edit":
        lines.append("Do not redesign APIs.")
    else:
        lines.append("No unnecessary edits.")

    return "\n".join(lines)


def build_codex_resume_prompt(
    *,
    job_id: str,
    task: str,
    worker_result: dict | None,
    worker_result_path: Path,
    changed_files: list[str] | None = None,
    checks: list[str] | None = None,
) -> str:
    changed_files = changed_files or []
    checks = checks or []

    if worker_result:
        status = worker_result.get("status", "UNKNOWN")
        summary = worker_result.get("summary", [])
        result_changed = worker_result.get("changed_files", [])
        result_checks = worker_result.get("check_notes", [])
        open_questions = worker_result.get("open_questions", [])
        needs_codex_review = bool(worker_result.get("needs_codex_review", status != "OK"))
    else:
        status = "UNKNOWN"
        summary = ["Worker result file missing; inspect local diff and logs."]
        result_changed = changed_files
        result_checks = checks
        open_questions = []
        needs_codex_review = True

    payload = {
        "job_id": job_id,
        "task": trim(task, 220),
        "worker_status": status,
        "needs_codex_review": needs_codex_review,
        "summary": summary[:5],
        "changed_files": result_changed[:12],
        "checks": result_checks[:8],
        "open_questions": open_questions[:6],
        "worker_result_path": worker_result_path.as_posix(),
    }

    return (
        "Claude worker finished a delegated job.\n"
        "Use the following compact handoff and local files only.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Please:\n"
        "1. review the worker outcome,\n"
        "2. decide whether to accept, patch, or take over,\n"
        "3. keep the response concise."
    )

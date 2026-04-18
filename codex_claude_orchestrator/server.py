from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import datetime as dt
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .codex_client import CodexManager, CodexUnavailableError
from .config import DEFAULT_JOB_ROOT, OrchestratorConfig
from .dispatch import dispatch_job
from .prompts import build_codex_resume_prompt
from .registry import Registry


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _monitor_log_path(record: Any) -> Path:
    if getattr(record, "monitor_log_path", ""):
        return Path(record.monitor_log_path)
    return Path(record.job_dir) / "cco_monitor.log"


def _append_monitor_log(record: Any, message: str) -> None:
    path = _monitor_log_path(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def _persist_record(registry: Registry, record: Any) -> None:
    registry.upsert(record)
    meta_path = Path(record.job_dir) / "meta.json"
    meta_path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _job_root_for_repo(repo_root: Path) -> Path:
    return (repo_root / DEFAULT_JOB_ROOT).resolve()


def _config_for_repo(base: OrchestratorConfig, repo_root: str | Path) -> OrchestratorConfig:
    rr = Path(repo_root).resolve()
    return replace(
        base,
        repo_root=rr,
        job_root=_job_root_for_repo(rr),
    )


def _repo_root_from_job_dir(job_dir: str) -> Path | None:
    if not job_dir:
        return None
    path = Path(job_dir).resolve()
    try:
        return path.parent.parent.parent
    except IndexError:
        return None


def _load_record_from_job_dir(job_dir: str, job_id: str) -> tuple[Registry | None, Any | None]:
    repo_root = _repo_root_from_job_dir(job_dir)
    if repo_root is None:
        return None, None
    registry = Registry(_job_root_for_repo(repo_root))
    record = registry.get(job_id)
    if record is not None:
        return registry, record
    meta_path = Path(job_dir).resolve() / "meta.json"
    data = _load_json(meta_path)
    if not data:
        return registry, None
    try:
        from .models import JobRecord

        return registry, JobRecord.from_dict(data)
    except Exception:
        return registry, None


def _pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _systemd_unit_state(unit_name: str) -> str:
    if not unit_name:
        return ""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit_name, "--property", "ActiveState", "--value"],
            check=True,
            text=True,
            capture_output=True,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _finalize_job(
    *,
    base_config: OrchestratorConfig,
    registry: Registry,
    record: Any,
    payload: dict[str, Any],
    monitor_reason: str = "",
) -> dict[str, Any]:
    worker_result = _load_json(Path(record.worker_result_path))
    no_codex_resume = bool(record.notes.get("no_codex_resume", False))
    worker_needs_codex_review = bool((worker_result or {}).get("needs_codex_review", True))
    should_resume_codex = bool(record.codex_thread_id) and not no_codex_resume and worker_needs_codex_review
    repo_config = _config_for_repo(base_config, record.cwd)

    resume_prompt = build_codex_resume_prompt(
        job_id=record.job_id,
        task=record.task,
        worker_result=worker_result,
        worker_result_path=Path(record.worker_result_path),
        changed_files=(worker_result or {}).get("changed_files", []),
        checks=(worker_result or {}).get("check_notes", []),
    )
    Path(record.codex_resume_prompt_path).write_text(resume_prompt, encoding="utf-8")

    record.status = "worker_finished"
    if payload.get("claude_session_id"):
        record.claude_session_id = payload["claude_session_id"]
    if monitor_reason:
        record.notes["monitor_finalize_reason"] = monitor_reason
    _persist_record(registry, record)
    _append_monitor_log(record, f"finalizing job; worker_result_present={bool(worker_result)} monitor_reason={monitor_reason or 'hook'}")

    codex_response = ""
    codex_resumed = False
    codex_reason = "no codex thread id recorded"

    if no_codex_resume:
        codex_reason = "skipped by job configuration"
    elif not worker_needs_codex_review:
        codex_reason = "worker marked codex review unnecessary"
    elif should_resume_codex:
        try:
            with CodexManager(repo_config) as codex:
                thread = codex.resume_thread(record.codex_thread_id)
                codex_response = codex.run(thread, resume_prompt)
                codex_resumed = True
                codex_reason = "resumed"
        except CodexUnavailableError as exc:
            codex_reason = str(exc)
        except Exception as exc:  # pragma: no cover
            codex_reason = f"codex resume failed: {exc}"

    if codex_response:
        Path(record.codex_resume_response_path).write_text(codex_response, encoding="utf-8")

    record.status = "done" if codex_resumed else "worker_finished"
    _persist_record(registry, record)
    _append_monitor_log(record, f"finalized job with status={record.status} codex_reason={codex_reason}")

    return {
        "ok": True,
        "job_id": record.job_id,
        "repo_root": record.cwd,
        "codex_resumed": codex_resumed,
        "codex_reason": codex_reason,
        "worker_needs_codex_review": worker_needs_codex_review,
        "no_codex_resume": no_codex_resume,
        "worker_result_present": bool(worker_result),
        "worker_result_path": record.worker_result_path,
        "codex_resume_prompt_path": record.codex_resume_prompt_path,
        "codex_resume_response_path": record.codex_resume_response_path,
    }


def _monitor_job_until_done(base_config: OrchestratorConfig, job_dir: str, job_id: str) -> None:
    registry, record = _load_record_from_job_dir(job_dir, job_id)
    if registry is None or record is None:
        return
    _append_monitor_log(record, "monitor started")
    last_snapshot = ""
    idle_rounds = 0
    while True:
        current = registry.get(job_id)
        if current is None:
            _append_monitor_log(record, "monitor stopping: record disappeared from registry")
            return
        record = current
        pid_alive = _pid_is_alive(record.claude_pid)
        unit_name = str(record.notes.get("claude_systemd_unit", "") or "")
        unit_state = _systemd_unit_state(unit_name)
        worker_result_exists = Path(record.worker_result_path).exists()
        snapshot = f"status={record.status} pid_alive={pid_alive} unit_state={unit_state or '-'} worker_result={worker_result_exists}"
        if snapshot != last_snapshot:
            _append_monitor_log(record, snapshot)
            last_snapshot = snapshot

        if record.status in {"worker_finished", "done", "worker_start_failed"}:
            _append_monitor_log(record, f"monitor stopping: terminal status {record.status}")
            return

        if worker_result_exists and not pid_alive and unit_state in {"", "inactive", "failed"}:
            idle_rounds += 1
            if idle_rounds >= 2:
                _append_monitor_log(record, "worker appears finished without relying on hook; running finalize fallback")
                _finalize_job(
                    base_config=base_config,
                    registry=registry,
                    record=record,
                    payload={},
                    monitor_reason="worker exited and result file present",
                )
                return
        else:
            idle_rounds = 0

        time.sleep(1.5)


class HookRequestHandler(BaseHTTPRequestHandler):
    config: OrchestratorConfig
    registry: Registry

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404, "Unknown endpoint")
            return

        body = json.dumps({
            "ok": True,
            "service": "cco",
            "repo_bound": False,
            "default_repo_root": str(self.config.repo_root),
            "default_job_root": str(self.config.job_root),
        }, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON")
            return

        if self.path == "/claude-session-end":
            result = self.handle_session_end(payload)
        elif self.path == "/dispatch":
            result = self.handle_dispatch(payload)
        else:
            self.send_error(404, "Unknown endpoint")
            return
        body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Quiet by default
        return

    def handle_dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = str(payload.get("task", "")).strip()
        kind = str(payload.get("kind", "")).strip()
        if not task:
            return {"ok": False, "dispatched": False, "reason": "missing task"}
        if kind not in {"search", "edit", "debug", "run"}:
            return {"ok": False, "dispatched": False, "reason": "invalid or missing kind"}

        paths_value = payload.get("paths", "")
        if isinstance(paths_value, list):
            paths_csv = ",".join(str(item) for item in paths_value if str(item).strip())
        else:
            paths_csv = str(paths_value or "")

        checks_value = payload.get("checks", [])
        if isinstance(checks_value, list):
            checks = [str(item) for item in checks_value if str(item).strip()]
        elif checks_value:
            checks = [str(checks_value)]
        else:
            checks = []

        repo_root_raw = str(payload.get("repo_root", "") or "").strip()
        config = _config_for_repo(self.config, repo_root_raw or self.config.repo_root)
        result = dispatch_job(
            config=config,
            kind=kind,
            task=task,
            paths_csv=paths_csv,
            checks=checks,
            codex_thread_id=str(payload.get("codex_thread_id", "") or ""),
            requires_web=bool(payload.get("requires_web", False)),
            force=bool(payload.get("force", False)),
            no_codex_resume=bool(payload.get("no_codex_resume", False)),
        )
        result["ok"] = bool(result.get("dispatched", False))
        result["repo_root"] = str(config.repo_root)
        if result.get("dispatched") and result.get("job_dir") and result.get("job_id"):
            thread = threading.Thread(
                target=_monitor_job_until_done,
                args=(self.config, str(result["job_dir"]), str(result["job_id"])),
                daemon=True,
            )
            thread.start()
        return result

    def handle_session_end(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = payload.get("job_id", "").strip()
        if not job_id:
            return {"ok": False, "reason": "missing job_id"}

        registry, record = _load_record_from_job_dir(str(payload.get("job_dir", "") or ""), job_id)
        if record is None:
            record = self.registry.get(job_id)
            registry = self.registry
        if record is None:
            return {"ok": False, "reason": f"unknown job_id {job_id}"}
        if registry is None:
            return {"ok": False, "reason": f"no registry for job_id {job_id}"}
        return _finalize_job(
            base_config=self.config,
            registry=registry,
            record=record,
            payload=payload,
        )


def serve(*, config: OrchestratorConfig, host: str, port: int) -> None:
    config = replace(config, hook_url=f"http://{host}:{port}/claude-session-end")
    registry = Registry(config.job_root)

    class Handler(HookRequestHandler):
        pass

    Handler.config = config
    Handler.registry = registry

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"CCO server listening on http://{host}:{port}/health")
    print(f"CCO server listening on http://{host}:{port}/dispatch")
    print(f"CCO server listening on http://{host}:{port}/claude-session-end")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

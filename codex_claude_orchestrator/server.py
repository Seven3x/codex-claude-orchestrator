from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .codex_client import CodexManager, CodexUnavailableError
from .config import OrchestratorConfig
from .prompts import build_codex_resume_prompt
from .registry import Registry


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


class HookRequestHandler(BaseHTTPRequestHandler):
    config: OrchestratorConfig
    registry: Registry

    def do_POST(self) -> None:
        if self.path != "/claude-session-end":
            self.send_error(404, "Unknown endpoint")
            return

        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_error(400, "Invalid JSON")
            return

        result = self.handle_session_end(payload)
        body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Quiet by default
        return

    def handle_session_end(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = payload.get("job_id", "").strip()
        if not job_id:
            return {"ok": False, "reason": "missing job_id"}

        record = self.registry.get(job_id)
        if record is None:
            return {"ok": False, "reason": f"unknown job_id {job_id}"}

        worker_result = _load_json(Path(record.worker_result_path))
        no_codex_resume = bool(record.notes.get("no_codex_resume", False))
        worker_needs_codex_review = bool((worker_result or {}).get("needs_codex_review", True))
        should_resume_codex = bool(record.codex_thread_id) and not no_codex_resume and worker_needs_codex_review

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
        self.registry.upsert(record)

        codex_response = ""
        codex_resumed = False
        codex_reason = "no codex thread id recorded"

        if no_codex_resume:
            codex_reason = "skipped by job configuration"
        elif not worker_needs_codex_review:
            codex_reason = "worker marked codex review unnecessary"
        elif should_resume_codex:
            try:
                with CodexManager(self.config) as codex:
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
        self.registry.upsert(record)

        return {
            "ok": True,
            "job_id": record.job_id,
            "codex_resumed": codex_resumed,
            "codex_reason": codex_reason,
            "worker_needs_codex_review": worker_needs_codex_review,
            "no_codex_resume": no_codex_resume,
            "worker_result_present": bool(worker_result),
            "worker_result_path": record.worker_result_path,
            "codex_resume_prompt_path": record.codex_resume_prompt_path,
            "codex_resume_response_path": record.codex_resume_response_path,
        }


def serve(*, config: OrchestratorConfig, host: str, port: int) -> None:
    registry = Registry(config.job_root)

    class Handler(HookRequestHandler):
        pass

    Handler.config = config
    Handler.registry = registry

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"CCO server listening on http://{host}:{port}/claude-session-end")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

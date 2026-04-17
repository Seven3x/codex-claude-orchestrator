from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class JobRecord:
    job_id: str
    kind: str
    task: str
    cwd: str
    job_dir: str
    claude_session_name: str
    claude_session_id: str = ""
    claude_pid: int | None = None
    codex_thread_id: str = ""
    status: str = "queued"
    paths: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    prompt_path: str = ""
    worker_result_path: str = ""
    claude_stdout_path: str = ""
    claude_stderr_path: str = ""
    codex_resume_prompt_path: str = ""
    codex_resume_response_path: str = ""
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        return cls(**data)

    @property
    def job_dir_path(self) -> Path:
        return Path(self.job_dir)

    @property
    def worker_result_file(self) -> Path:
        return Path(self.worker_result_path)

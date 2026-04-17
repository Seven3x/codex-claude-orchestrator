from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_JOB_ROOT = Path(".cco/jobs")
DEFAULT_HOOK_URL = "http://127.0.0.1:8765/claude-session-end"


@dataclass(slots=True)
class OrchestratorConfig:
    repo_root: Path
    job_root: Path = DEFAULT_JOB_ROOT
    hook_url: str = DEFAULT_HOOK_URL
    claude_bin: str = "claude"
    claude_config_dir: Path | None = None
    codex_bin: str | None = None
    codex_model: str = "gpt-5.4"

    @classmethod
    def from_env(cls, repo_root: str | Path | None = None) -> "OrchestratorConfig":
        rr = Path(repo_root or os.getenv("CCO_REPO_ROOT", ".")).resolve()
        job_root = Path(os.getenv("CCO_JOB_ROOT", str(rr / DEFAULT_JOB_ROOT))).resolve()
        hook_url = os.getenv("CCO_HOOK_URL", DEFAULT_HOOK_URL)
        claude_bin = os.getenv("CCO_CLAUDE_BIN", "claude")
        claude_config_dir_raw = os.getenv("CCO_CLAUDE_CONFIG_DIR") or os.getenv("CLAUDE_CONFIG_DIR")
        codex_bin = os.getenv("CCO_CODEX_BIN") or None
        codex_model = os.getenv("CCO_CODEX_MODEL", "gpt-5.4")
        return cls(
            repo_root=rr,
            job_root=job_root,
            hook_url=hook_url,
            claude_bin=claude_bin,
            claude_config_dir=Path(claude_config_dir_raw).resolve() if claude_config_dir_raw else None,
            codex_bin=codex_bin,
            codex_model=codex_model,
        )

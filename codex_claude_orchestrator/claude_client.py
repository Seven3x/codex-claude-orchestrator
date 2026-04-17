from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import OrchestratorConfig
from .models import JobRecord


def permission_mode_for(kind: str) -> str:
    if kind in {"search", "run"}:
        return "plan"
    if kind in {"edit", "debug"}:
        return "acceptEdits"
    return "plan"


def _user_claude_config_dir(config: OrchestratorConfig) -> Path:
    if config.claude_config_dir is not None:
        return config.claude_config_dir
    return Path.home() / ".claude"


def _load_claude_settings_env(config_dir: Path) -> dict[str, str]:
    settings_path = config_dir / "settings.json"
    if not settings_path.exists():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    env_values = data.get("env", {})
    if not isinstance(env_values, dict):
        return {}
    return {str(key): str(value) for key, value in env_values.items()}


def _prepare_runtime_claude_config(
    *,
    config: OrchestratorConfig,
    record: JobRecord,
    env: dict[str, str],
) -> Path:
    source_dir = _user_claude_config_dir(config)
    runtime_dir = record.job_dir_path / "claude_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # Keep runtime state writable inside the job folder, but seed it with the
    # user's Claude settings so API base URL / key and similar env still apply.
    for name in ("settings.json", "config.json"):
        src = source_dir / name
        dst = runtime_dir / name
        if src.exists():
            shutil.copy2(src, dst)

    env.update(_load_claude_settings_env(source_dir))
    env["CLAUDE_CONFIG_DIR"] = str(runtime_dir)
    return runtime_dir


def launch_claude(
    *,
    config: OrchestratorConfig,
    record: JobRecord,
    prompt: str,
) -> int:
    env = os.environ.copy()
    env["CCO_JOB_ID"] = record.job_id
    env["CCO_JOB_DIR"] = record.job_dir
    env["CCO_HOOK_URL"] = config.hook_url
    env["CCO_REPO_ROOT"] = str(config.repo_root)
    env["CCO_WORKER_RESULT_PATH"] = record.worker_result_path
    _prepare_runtime_claude_config(config=config, record=record, env=env)

    cmd = [
        config.claude_bin,
        "-n", record.claude_session_name,
        "--permission-mode", permission_mode_for(record.kind),
        "-p", prompt,
    ]
    stdout_path = Path(record.claude_stdout_path)
    stderr_path = Path(record.claude_stderr_path)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.repo_root),
            env=env,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        return proc.pid

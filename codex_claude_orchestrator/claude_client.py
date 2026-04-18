from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse
from pathlib import Path

from .config import OrchestratorConfig
from .models import JobRecord


class ClaudeLaunchError(RuntimeError):
    pass


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
    runtime_dir = Path(record.claude_runtime_dir or (config.runtime_root / record.job_id))
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # Keep runtime state writable under a stable temp root, but seed it with the
    # user's Claude settings so API base URL / key and similar env still apply.
    for name in ("settings.json", "config.json"):
        src = source_dir / name
        dst = runtime_dir / name
        if src.exists():
            shutil.copy2(src, dst)

    env.update(_load_claude_settings_env(source_dir))
    env["CLAUDE_CONFIG_DIR"] = str(runtime_dir)
    return runtime_dir


def _check_proxy_connectivity(env: dict[str, str]) -> None:
    base_url = env.get("ANTHROPIC_BASE_URL", "").strip()
    if not base_url:
        return

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    try:
        with socket.create_connection((parsed.hostname, port), timeout=1.5):
            return
    except OSError as exc:
        raise ClaudeLaunchError(
            f"Claude worker cannot reach configured API base {base_url}: {exc}"
        ) from exc


def _read_log_excerpt(path: Path, *, max_chars: int = 1200) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


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
    _check_proxy_connectivity(env)

    cmd = [
        config.claude_bin,
        "-n", record.claude_session_name,
        "--permission-mode", permission_mode_for(record.kind),
    ]
    if config.claude_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["-p", prompt])
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
        time.sleep(0.75)
        returncode = proc.poll()
        if returncode is not None:
            stderr_excerpt = _read_log_excerpt(stderr_path)
            stdout_excerpt = _read_log_excerpt(stdout_path)
            details = stderr_excerpt or stdout_excerpt or "no worker output captured"
            raise ClaudeLaunchError(
                f"Claude worker exited immediately with code {returncode}: {details}"
            )
        return proc.pid

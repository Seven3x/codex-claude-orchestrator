from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from io import TextIOBase
from urllib.parse import urlparse
from pathlib import Path

from .config import OrchestratorConfig
from .models import JobRecord


class ClaudeLaunchError(RuntimeError):
    pass


@dataclass(slots=True)
class ClaudeLaunchResult:
    started: bool
    pid: int | None = None
    launcher: str = "subprocess"
    systemd_unit: str = ""


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
    try:
        data = _load_json_file(config_dir / "settings.json")
    except Exception:
        return {}
    if not data:
        return {}

    env_values = data.get("env", {})
    if not isinstance(env_values, dict):
        return {}
    return {str(key): str(value) for key, value in env_values.items()}


def _load_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_json(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_json(merged[key], value)
        else:
            merged[key] = value
    return merged


def _default_hook_settings() -> dict[str, object]:
    return {
        "hooks": {
            "SessionEnd": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "cco-claude-session-end-hook",
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
    }


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
    user_settings = _load_json_file(source_dir / "settings.json")
    repo_settings = _load_json_file(config.repo_root / ".claude" / "settings.json")
    merged_settings = _merge_json(_default_hook_settings(), user_settings)
    merged_settings = _merge_json(merged_settings, repo_settings)
    if merged_settings:
        (runtime_dir / "settings.json").write_text(
            json.dumps(merged_settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    config_src = source_dir / "config.json"
    if config_src.exists():
        shutil.copy2(config_src, runtime_dir / "config.json")

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


def _build_claude_cmd(config: OrchestratorConfig, record: JobRecord, prompt: str) -> list[str]:
    cmd = [
        config.claude_bin,
        "-n", record.claude_session_name,
        "--permission-mode", permission_mode_for(record.kind),
    ]
    if config.claude_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["-p", prompt])
    return cmd


def _stream_to_logs(stream: TextIOBase, sinks: list[TextIOBase]) -> None:
    try:
        for chunk in iter(stream.readline, ""):
            if not chunk:
                break
            for sink in sinks:
                sink.write(chunk)
                sink.flush()
    finally:
        stream.close()


def _sanitize_unit_name(job_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", job_id).strip("-")
    if not safe:
        safe = "worker"
    return f"cco-{safe}"


def _systemd_env_items(env: dict[str, str]) -> list[tuple[str, str]]:
    keys = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "CCO_HOOK_URL",
        "CCO_JOB_DIR",
        "CCO_JOB_ID",
        "CCO_REPO_ROOT",
        "CCO_WORKER_RESULT_PATH",
        "HOME",
        "LANG",
        "LOGNAME",
        "PATH",
        "SHELL",
        "USER",
    }
    items: list[tuple[str, str]] = []
    for key in sorted(keys):
        value = env.get(key)
        if value:
            items.append((key, value))
    return items


def _launch_via_subprocess(
    *,
    config: OrchestratorConfig,
    record: JobRecord,
    env: dict[str, str],
    prompt: str,
) -> ClaudeLaunchResult:
    _check_proxy_connectivity(env)
    cmd = _build_claude_cmd(config, record, prompt)
    stdout_path = Path(record.claude_stdout_path)
    stderr_path = Path(record.claude_stderr_path)
    output_path = Path(record.claude_output_path)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("a", encoding="utf-8") as stdout_file, \
        stderr_path.open("a", encoding="utf-8") as stderr_file, \
        output_path.open("a", encoding="utf-8") as output_file:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.repo_root),
            env=env,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            start_new_session=True,
        )
        stdout_thread = threading.Thread(
            target=_stream_to_logs,
            args=(proc.stdout, [stdout_file, output_file]),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_stream_to_logs,
            args=(proc.stderr, [stderr_file, output_file]),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        time.sleep(0.75)
        returncode = proc.poll()
        if returncode is not None:
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            stderr_excerpt = _read_log_excerpt(stderr_path)
            stdout_excerpt = _read_log_excerpt(output_path)
            details = stderr_excerpt or stdout_excerpt or "no worker output captured"
            raise ClaudeLaunchError(
                f"Claude worker exited immediately with code {returncode}: {details}"
            )
        return ClaudeLaunchResult(
            started=True,
            pid=proc.pid,
            launcher="subprocess",
        )


def _systemctl_show(user_unit: str, prop: str) -> str:
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            user_unit,
            "--property",
            prop,
            "--value",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _launch_via_systemd(
    *,
    config: OrchestratorConfig,
    record: JobRecord,
    env: dict[str, str],
    prompt: str,
) -> ClaudeLaunchResult:
    stdout_path = Path(record.claude_stdout_path)
    stderr_path = Path(record.claude_stderr_path)
    output_path = Path(record.claude_output_path)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    unit_name = _sanitize_unit_name(record.job_id)
    claude_cmd = _build_claude_cmd(config, record, prompt)
    shell_cmd = (
        f"exec {shlex.join(claude_cmd)}"
        f" > >(tee -a {shlex.quote(str(stdout_path))} {shlex.quote(str(output_path))})"
        f" 2> >(tee -a {shlex.quote(str(stderr_path))} {shlex.quote(str(output_path))} >&2)"
    )
    systemd_cmd = [
        "systemd-run",
        "--user",
        "--unit", unit_name,
        "--collect",
        "--no-block",
        "--service-type=exec",
        "--working-directory", str(config.repo_root),
    ]
    for key, value in _systemd_env_items(env):
        systemd_cmd.extend(["--setenv", f"{key}={value}"])
    systemd_cmd.extend(["/bin/bash", "-lc", shell_cmd])

    try:
        subprocess.run(
            systemd_cmd,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if "Failed to create bus connection" in stderr or "Operation not permitted" in stderr:
            raise ClaudeLaunchError(
                "Claude host launcher needs an unsandboxed dispatch command. "
                "Re-run `cco dispatch` with escalated execution so it can use `systemd-run --user`."
            ) from exc
        raise ClaudeLaunchError(
            f"Claude host launcher failed: {stderr or exc.stdout.strip() or exc}"
        ) from exc

    time.sleep(0.75)
    active_state = ""
    sub_state = ""
    pid = 0
    try:
        active_state = _systemctl_show(f"{unit_name}.service", "ActiveState")
        sub_state = _systemctl_show(f"{unit_name}.service", "SubState")
        pid_value = _systemctl_show(f"{unit_name}.service", "MainPID")
        pid = int(pid_value or "0")
    except (subprocess.CalledProcessError, ValueError):
        pass

    if active_state in {"failed", "inactive"} and pid <= 0:
        stderr_excerpt = _read_log_excerpt(stderr_path)
        stdout_excerpt = _read_log_excerpt(stdout_path)
        details = stderr_excerpt or stdout_excerpt or f"unit state {active_state or 'unknown'}/{sub_state or 'unknown'}"
        raise ClaudeLaunchError(
            f"Claude host worker exited immediately: {details}"
        )

    return ClaudeLaunchResult(
        started=True,
        pid=pid or None,
        launcher="systemd",
        systemd_unit=f"{unit_name}.service",
    )


def launch_claude(
    *,
    config: OrchestratorConfig,
    record: JobRecord,
    prompt: str,
) -> ClaudeLaunchResult:
    env = os.environ.copy()
    env["CCO_JOB_ID"] = record.job_id
    env["CCO_JOB_DIR"] = record.job_dir
    env["CCO_HOOK_URL"] = config.hook_url
    env["CCO_REPO_ROOT"] = str(config.repo_root)
    env["CCO_WORKER_RESULT_PATH"] = record.worker_result_path
    _prepare_runtime_claude_config(config=config, record=record, env=env)
    launcher = config.claude_launcher
    if launcher not in {"auto", "subprocess", "systemd"}:
        raise ClaudeLaunchError(f"Unsupported CCO_CLAUDE_LAUNCHER value: {launcher}")
    if launcher == "systemd":
        return _launch_via_systemd(config=config, record=record, env=env, prompt=prompt)
    if launcher == "subprocess":
        return _launch_via_subprocess(config=config, record=record, env=env, prompt=prompt)

    if shutil.which("systemd-run"):
        return _launch_via_systemd(config=config, record=record, env=env, prompt=prompt)
    return _launch_via_subprocess(config=config, record=record, env=env, prompt=prompt)

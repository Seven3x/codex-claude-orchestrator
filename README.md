# Codex + Claude Code Orchestrator (Python SDK version)

This scaffold is optimized for one goal: **save Codex usage by letting Claude Code handle bounded local work and long waits, then resume Codex only when needed.**

It uses:

- **Codex Python SDK** (`codex_app_server`) to start or resume Codex threads and run short follow-up prompts. OpenAI documents this Python SDK as **experimental**, requiring Python 3.10+ and a local checkout of the open-source Codex repo. citeturn909572view0turn643494search1
- **Claude Code CLI** for the worker session. Claude supports `--resume` / `--continue`, custom names via `-n`, and `SessionEnd` hooks that can call shell commands or HTTP endpoints. citeturn884392search0turn909572view1

## Why this version

This package is for **scripted orchestration**, not for the Codex TUI itself.

Use it when you want:

- a Python service to remember `job_id -> codex_thread_id -> claude_session_name`
- Claude to run long local tasks without Codex waiting
- Claude `SessionEnd` to notify a local server
- the server to resume the Codex thread with a short summary only

This follows OpenAI's split between:
- **Codex SDK** for automation / CI / custom workflows, and
- **app-server** for deeper product integrations. citeturn643494search1turn909572view0

## What it does

1. You run the local server:
   ```bash
   cco server
   ```
2. A dispatcher launches Claude for a bounded task:
   ```bash
   cco dispatch \
     --kind debug \
     --task "Fix the failing import path in the CLI tests" \
     --paths "src,tests" \
     --check "pytest tests/test_cli.py -q" \
     --codex-thread-id THR_ID
   ```
3. `cco dispatch` returns as soon as the Claude worker process has started.
4. Claude works locally and writes a short JSON result into `.cco/jobs/<job_id>/worker_result.json`.
5. Claude exits.
6. Claude's `SessionEnd` hook posts to the local orchestrator.
7. The orchestrator resumes the Codex thread with a **short** prompt only if the job still needs Codex review.
8. The orchestrator also writes active worker state transitions to `.cco/jobs/<job_id>/cco_monitor.log`.

## Host-service mode

The cleanest deployment is to keep `cco server` running on the host as a small local service and let Codex send only minimal dispatch payloads to it.

In that mode:
- Codex sends `task`, `kind`, `paths`, `checks`, and `codex_thread_id`
- the host-side `cco` service launches Claude using the host environment
- the service owns job state, logs, runtime directories, and hook callbacks
- only a short worker summary needs to come back to Codex

The built-in server now exposes:
- `GET /health`
- `POST /dispatch`
- `POST /claude-session-end`

Each job directory now includes:
- `meta.json` for the latest persisted job state
- `worker_result.json` for the worker's final JSON output
- `cco_monitor.log` for service-side worker lifecycle monitoring

## Install

### 1) Install this scaffold
```bash
pip install -e .
```

If `cco` lives in your conda `base` environment and you do not want to hardcode the Miniconda path every time, use the wrapper script in this repo:

```bash
./scripts/cco-base --help
```

It will:
- use `cco` directly if it is already on `PATH`
- otherwise try common `conda` / `miniconda` locations and run `cco` from `base`

### 2) Install the Codex Python SDK

OpenAI's official docs say the Python SDK is experimental and should be installed from a local Codex repo checkout:

```bash
git clone https://github.com/openai/codex.git
cd codex/sdk/python
python -m pip install -e .
```

You can also point the SDK at a specific local `codex` binary with `AppServerConfig(codex_bin=...)`. citeturn909572view0

### 3) Install / configure Claude Code CLI
Make sure `claude` is on PATH and authenticated.

By default this scaffold launches Claude workers with `--dangerously-skip-permissions` so bounded local worker tasks do not stall on Claude's own interactive file-write prompts. Set `CCO_CLAUDE_SKIP_PERMISSIONS=0` if you want to restore Claude's normal per-action confirmation behavior.
By default this scaffold also prefers launching Claude through host `systemd-run --user` (`CCO_CLAUDE_LAUNCHER=auto`) so the worker does not inherit a restricted Codex sandbox as a child process. Set `CCO_CLAUDE_LAUNCHER=subprocess` if you explicitly want the old direct-child behavior.

### 4) Add the Claude hook config
Copy:

- `examples/.claude/settings.json`
- `examples/CLAUDE.md`
- `scripts/cco-base`

into your target repository:

```bash
mkdir -p .claude
mkdir -p scripts
cp examples/.claude/settings.json .claude/settings.json
cp examples/CLAUDE.md CLAUDE.md
cp scripts/cco-base scripts/cco-base
```

### 5) (Optional) Add Codex routing instructions
Copy `examples/AGENTS.md` into your repo root if you want Codex to delegate by rule.

The important detail is that the target repository should also contain `scripts/cco-base`, otherwise `./scripts/cco-base ...` will fail when Codex runs from that repository instead of from the orchestrator repo.

## Quickstart

### Start the local callback server
```bash
./scripts/cco-base server --host 127.0.0.1 --port 8765
```

### Dispatch through the host service
```bash
curl -s -X POST http://127.0.0.1:8765/dispatch \
  -H 'Content-Type: application/json' \
  -d '{
    "kind": "search",
    "task": "List README filenames in this repository and write the required result JSON.",
    "paths": ["."],
    "checks": ["rg --files -g '\''README*'\''"],
    "codex_thread_id": "",
    "no_codex_resume": true
  }'
```

The `/dispatch` API intentionally accepts only the small worker-planning fields instead of a full repo summary:
- `kind`
- `task`
- `paths`
- `checks`
- `codex_thread_id`
- optional `no_codex_resume`, `requires_web`, `force`

## MCP tool wrapper

This package also ships a small stdio MCP server:

```bash
cco-mcp
```

It exposes thin tools that forward to the host-side `cco` service:
- `cco_health`
- `cco_dispatch`
- `cco_job_status`

`cco_dispatch` accepts the same minimal planning fields:
- `repo_root`
- `kind`
- `task`
- `paths`
- `checks`
- `codex_thread_id`
- optional `no_codex_resume`, `requires_web`, `force`, `server_url`

`cco_job_status` reads a job directory directly and returns:
- `meta`
- `worker_result`
- recent `cco_monitor.log` lines
- recent Claude stdout/stderr tails

### Start a Codex thread (SDK)
```bash
./scripts/cco-base codex-start \
  --model gpt-5.4 \
  --input "You are supervising low-risk worker delegation. Keep decisions short."
```

This prints JSON including the thread id.

### Delegate work to Claude
```bash
./scripts/cco-base dispatch \
  --kind debug \
  --task "Fix the failing import path in the CLI tests" \
  --paths "src,tests" \
  --check "pytest tests/test_cli.py -q" \
  --codex-thread-id YOUR_THREAD_ID
```

If you are issuing `cco dispatch` from a Codex sandboxed session, run that dispatch command with escalated execution so the scaffold can actually talk to host `systemd --user` and launch Claude outside the sandbox.

For pure execution tasks where Codex does not need to review the result, add:

```bash
--no-codex-resume
```

### What happens next
- `cco dispatch` returns immediately once the worker starts, with either a real `claude_pid` or a host `claude_systemd_unit`.
- By default the worker is launched through host `systemd-run --user`, so it does not inherit the current Codex sandbox's blocked localhost / network namespace.
- If Claude cannot reach its configured API base, cannot talk to host `systemd --user`, or exits immediately during startup, `cco dispatch` now fails fast instead of leaving a fake `running` job behind.
- Once `cco dispatch` reports `claude_started: true`, Codex should stop and should not poll job files, `/tmp`, logs, or process state in the same session unless the user explicitly asked for monitoring.
- Claude runs the worker task.
- At session end, the hook calls `http://127.0.0.1:8765/claude-session-end`.
- The server loads `.cco/jobs/<job_id>/worker_result.json`.
- The server resumes the Codex thread only when needed and writes:
  - `.cco/jobs/<job_id>/codex_resume_prompt.txt`
  - `.cco/jobs/<job_id>/codex_resume_response.txt`

## Task routing rules

This scaffold is intentionally conservative.

### Route to Claude
- code search / grep
- test scaffolding or test running
- docs / README / changelog
- small mechanical edits
- bounded local debugging
- long local validation / simulation / experiment runs

### Keep in Codex
- architecture / redesign
- unclear root cause
- methodology / paper-claim / fairness decisions
- anything requiring web search or latest docs
- large refactors or high-risk changes

The reason is simple: if Codex first spends many tokens analyzing and then writes a long prompt for Claude, the savings disappear. Long sessions and long-running tasks also accumulate more working context. OpenAI explicitly notes that Codex sessions are working threads whose context management matters, and Claude hooks are a deterministic way to automate lifecycle behavior. citeturn850380search11turn909572view1

## Session strategy

Use **one Claude session per work item**:
- resume that session for follow-up on the same job
- do **not** keep one endless Claude session for unrelated tasks

Claude supports `--resume <session-id-or-name>` and custom names via `-n`. Sessions from `claude -p` do not appear in the picker, but they can still be resumed by ID or custom name. citeturn884392search0

## Prompt budget rules

For Claude worker prompts:
- one sentence task
- at most 8 path hints
- at most 2 check commands
- no long logs
- no repo-wide explanation
- stable rules live in `CLAUDE.md`, not in every prompt

This is the most important token-saving design choice in the scaffold.

## Limitations

- The **Codex Python SDK is experimental**. This scaffold tries a few likely resume method names, but official Python docs currently show `thread_start(...)` explicitly and do not spell out every convenience method on the page. If your local SDK version differs, update `codex_client.py`. citeturn909572view0
- The Claude hook only fires when the worker session exits normally enough to emit `SessionEnd`.
- If Claude never writes `worker_result.json`, the orchestrator falls back to a minimal summary.
- System-level local runs such as ROS / Gazebo may require escalated execution outside the Codex sandbox even when the worker routing itself succeeds.

## Files

- `codex_claude_orchestrator/cli.py` — entrypoint
- `codex_claude_orchestrator/server.py` — local HTTP callback server
- `codex_claude_orchestrator/dispatch.py` — Claude launcher
- `codex_claude_orchestrator/codex_client.py` — Codex SDK wrapper
- `codex_claude_orchestrator/prompts.py` — short prompt templates
- `codex_claude_orchestrator/router.py` — conservative routing rules
- `codex_claude_orchestrator/hook.py` — command used by Claude `SessionEnd`
- `examples/AGENTS.md` — Codex routing rules
- `examples/CLAUDE.md` — Claude worker role rules
- `examples/.claude/settings.json` — Claude hook config

## Recommended use pattern

**Codex should appear at most twice:**
1. before delegation
2. after Claude finishes, only if the worker or job configuration requires Codex review

Everything in the middle should stay out of Codex unless the worker escalates or the user explicitly asks for live monitoring.

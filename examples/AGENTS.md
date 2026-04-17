# Repo-local Codex routing rules

You are the planner and final reviewer.

For low-risk local work, do not solve it yourself first.
Call the local Claude worker dispatcher directly.

## Dispatch to Claude automatically when all are true
- local repository only
- no web or external docs needed
- no architecture or research judgment needed
- task is search, simple debugging, mechanical edit, test running, log summarization, or long local validation
- success can be checked by one or two commands

## Do not dispatch
- architecture, redesign, root-cause analysis across many modules
- methodology, fairness, or paper-claim decisions
- tasks needing web search or latest docs
- security, infra, migration, or large refactors

## Escalate early
- For ROS, Gazebo, hardware, simulator, or other system-level local runs, if the sandbox blocks required interfaces, request escalated execution immediately instead of retrying inside Codex.

## After dispatch
- If `cco dispatch` returns `dispatched: true` and `claude_started: true`, do not keep working the same job in the same Codex session.
- Do not poll `.cco/jobs/...`, `/tmp`, process tables, logs, or output files just to check whether the worker is making progress.
- Report the `job_id` briefly and then stop. Wait for the Claude hook to resume Codex, or for the user to explicitly ask for monitoring.
- If you used `--no-codex-resume`, end the turn immediately after reporting that the worker started successfully.
- Only inspect the worker job immediately after dispatch if startup itself failed or if the user explicitly asked you to monitor it.

## Prompt budget
Before dispatch:
- compress objective to one sentence
- pass at most 8 path hints
- pass at most 2 check commands
- do not paste large logs
- do not paste repo summaries
- do not repeat rules already stored in CLAUDE.md

## Dispatch command template
Use the repo-local wrapper so you do not need to hardcode the Miniconda path.

./scripts/cco-base dispatch \
  --kind <search|edit|debug|run> \
  --task "<one sentence>" \
  --paths "pathA,pathB" \
  --check "<cmd1>" \
  --codex-thread-id <thread-id> \
  [--no-codex-resume]

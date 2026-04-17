from __future__ import annotations

import argparse
import json
from pathlib import Path

from .codex_client import CodexManager
from .config import OrchestratorConfig
from .dispatch import dispatch_job
from .server import serve


def cmd_server(args) -> int:
    config = OrchestratorConfig.from_env(args.repo_root)
    serve(config=config, host=args.host, port=args.port)
    return 0


def cmd_dispatch(args) -> int:
    config = OrchestratorConfig.from_env(args.repo_root)
    result = dispatch_job(
        config=config,
        kind=args.kind,
        task=args.task,
        paths_csv=args.paths,
        checks=args.check or [],
        codex_thread_id=args.codex_thread_id or "",
        requires_web=args.requires_web,
        force=args.force,
        no_codex_resume=args.no_codex_resume,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_codex_start(args) -> int:
    config = OrchestratorConfig.from_env(args.repo_root)
    model = args.model or config.codex_model
    with CodexManager(config) as codex:
        thread = codex.start_thread(model=model, cwd=config.repo_root)
        thread_id = codex.thread_id_of(thread)
        response = ""
        if args.input:
            response = codex.run(thread, args.input)
        print(json.dumps({
            "thread_id": thread_id,
            "model": model,
            "response": response,
        }, ensure_ascii=False, indent=2))
    return 0


def cmd_codex_resume(args) -> int:
    config = OrchestratorConfig.from_env(args.repo_root)
    with CodexManager(config) as codex:
        thread = codex.resume_thread(args.thread_id)
        response = codex.run(thread, args.input)
        print(json.dumps({
            "thread_id": args.thread_id,
            "response": response,
        }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cco", description="Codex + Claude Code orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("server", help="Start the local SessionEnd callback server")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8765)
    ps.add_argument("--repo-root", default=".")
    ps.set_defaults(func=cmd_server)

    pd = sub.add_parser("dispatch", help="Dispatch a bounded task to Claude Code")
    pd.add_argument("--repo-root", default=".")
    pd.add_argument("--kind", required=True, choices=["search", "edit", "debug", "run"])
    pd.add_argument("--task", required=True)
    pd.add_argument("--paths", default="")
    pd.add_argument("--check", action="append", default=[])
    pd.add_argument("--codex-thread-id", default="")
    pd.add_argument("--requires-web", action="store_true")
    pd.add_argument("--force", action="store_true", help="Bypass the conservative router")
    pd.add_argument("--no-codex-resume", action="store_true", help="Do not resume Codex when the worker finishes")
    pd.set_defaults(func=cmd_dispatch)

    pcs = sub.add_parser("codex-start", help="Create a Codex thread and optionally run one prompt")
    pcs.add_argument("--repo-root", default=".")
    pcs.add_argument("--model", default="")
    pcs.add_argument("--input", default="")
    pcs.set_defaults(func=cmd_codex_start)

    pcr = sub.add_parser("codex-resume", help="Resume a Codex thread and run one prompt")
    pcr.add_argument("--repo-root", default=".")
    pcr.add_argument("--thread-id", required=True)
    pcr.add_argument("--input", required=True)
    pcr.set_defaults(func=cmd_codex_resume)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

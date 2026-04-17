from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .config import OrchestratorConfig


class CodexUnavailableError(RuntimeError):
    pass


class CodexManager(AbstractContextManager):
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self._codex = None

    def __enter__(self) -> "CodexManager":
        try:
            from codex_app_server import Codex  # type: ignore
        except Exception as exc:  # pragma: no cover - import error path
            raise CodexUnavailableError(
                "Could not import codex_app_server. "
                "Install the experimental Python SDK from a local Codex repo: "
                "`cd sdk/python && python -m pip install -e .`"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self.config.codex_bin:
            try:
                from codex_app_server import AppServerConfig  # type: ignore
                kwargs["config"] = AppServerConfig(codex_bin=self.config.codex_bin)
            except Exception:
                # Fallback: older SDK versions may not expose AppServerConfig.
                pass

        self._codex = Codex(**kwargs)
        if hasattr(self._codex, "__enter__"):
            self._codex.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._codex is not None and hasattr(self._codex, "__exit__"):
            self._codex.__exit__(exc_type, exc, tb)

    @property
    def codex(self):
        if self._codex is None:
            raise CodexUnavailableError("CodexManager not entered.")
        return self._codex

    def start_thread(self, *, model: str, cwd: Path | None = None):
        kwargs: dict[str, Any] = {"model": model}
        if cwd is not None:
            kwargs["cwd"] = str(cwd)
        try:
            thread = self.codex.thread_start(**kwargs)
        except TypeError:
            thread = self.codex.thread_start(model=model)
        return thread

    def resume_thread(self, thread_id: str):
        # The official Python page is currently terse. Try likely method names.
        for method_name in ("thread_resume", "resume_thread", "resumeThread"):
            method = getattr(self.codex, method_name, None)
            if callable(method):
                return method(thread_id)
        raise CodexUnavailableError(
            "Could not find a resume method on codex_app_server.Codex. "
            "Update codex_client.py for your local SDK version."
        )

    @staticmethod
    def thread_id_of(thread: Any) -> str:
        for attr in ("id", "thread_id", "threadId"):
            value = getattr(thread, attr, None)
            if value:
                return str(value)
        # Fall back to repr only as a last resort
        return ""

    @staticmethod
    def final_response_of(result: Any) -> str:
        for attr in ("final_response", "output_text", "text"):
            value = getattr(result, attr, None)
            if value:
                return str(value)
        return str(result)

    def run(self, thread: Any, prompt: str) -> str:
        result = thread.run(prompt)
        return self.final_response_of(result)

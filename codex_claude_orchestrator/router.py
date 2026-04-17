from __future__ import annotations

from dataclasses import dataclass


LOW_RISK_KEYWORDS = {
    "rename", "replace", "update import", "fix lint", "format",
    "add test", "run test", "grep", "find call", "list files",
    "summarize log", "summarize diff", "readme", "changelog",
    "mechanical", "batch edit", "docstring", "type hint",
    "path", "flag", "cli", "import", "failing test", "repro",
    "simulation", "validation", "experiment run",
}

HIGH_RISK_KEYWORDS = {
    "architecture", "redesign", "refactor strategy", "tradeoff",
    "root cause", "why does", "investigate", "paper claim",
    "methodology", "security", "migration", "fairness",
    "performance strategy", "research direction",
    "latest docs", "web", "browse", "internet",
}


@dataclass(slots=True)
class RouteDecision:
    route_to_claude: bool
    reason: str


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def decide_route(
    *,
    task: str,
    kind: str,
    requires_web: bool = False,
    expected_changed_files: int | None = None,
) -> RouteDecision:
    text = _norm(task)

    if requires_web:
        return RouteDecision(False, "requires web or external knowledge")

    if any(keyword in text for keyword in HIGH_RISK_KEYWORDS):
        return RouteDecision(False, "looks design-heavy or judgment-heavy")

    if kind not in {"search", "edit", "debug", "run"}:
        return RouteDecision(False, "unsupported kind")

    if kind in {"search", "run"}:
        return RouteDecision(True, "bounded local worker task")

    if kind == "debug":
        return RouteDecision(True, "bounded local debugging task")

    if expected_changed_files is not None and expected_changed_files > 6:
        return RouteDecision(False, "too many files for a cheap worker")

    if any(keyword in text for keyword in LOW_RISK_KEYWORDS):
        return RouteDecision(True, "mechanical low-risk task")

    return RouteDecision(False, "edit is not clearly mechanical")

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def trim(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def split_csv(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def trim_items(items: Iterable[str], max_items: int, max_chars: int) -> list[str]:
    out: list[str] = []
    for item in list(items)[:max_items]:
        item = trim(item, max_chars)
        if item:
            out.append(item)
    return out

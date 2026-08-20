"""Collect Bedrock usage for the current chat request (one row per API call)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

_sink: ContextVar[list["UsageRecord"] | None] = ContextVar("usage_sink", default=None)


@dataclass
class UsageRecord:
    model_id: str
    call_kind: str  # embed | answer | related
    prompt_tokens: int
    completion_tokens: int


def start_collection() -> list[UsageRecord]:
    records: list[UsageRecord] = []
    bind_collection(records)
    return records


def bind_collection(records: list[UsageRecord]) -> None:
    """Re-attach the same list after each SSE yield.

    Starlette runs each generator ``next()`` in a copied Context, so a ContextVar
    set before ``yield`` is invisible on the next chunk. Re-bind after every yield.
    """
    _sink.set(records)


def end_collection() -> None:
    try:
        _sink.set(None)
    except Exception:
        pass


def record_usage(
    *,
    model_id: str,
    call_kind: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    sink = _sink.get()
    if sink is None:
        return
    sink.append(
        UsageRecord(
            model_id=model_id,
            call_kind=call_kind,
            prompt_tokens=max(0, int(prompt_tokens or 0)),
            completion_tokens=max(0, int(completion_tokens or 0)),
        )
    )

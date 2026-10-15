from __future__ import annotations

from uuid import uuid4


_PREFIXES = {
    "project": "prj",
    "session": "ses",
    "task": "tsk",
    "event": "evt",
    "artifact": "art",
    "invocation": "inv",
    "lease": "lease",
    "checkpoint": "chk",
    "turn": "turn",
}


def new_id(kind: str) -> str:
    try:
        prefix = _PREFIXES[kind]
    except KeyError as error:
        raise ValueError(f"unsupported identity kind: {kind}") from error
    return f"{prefix}_{uuid4().hex}"

from __future__ import annotations

import re
from typing import Any


_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:token|api[_-]?key|secret|password)\s*[=:]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(\"(?:token|api[_-]?key|secret|password)\"\s*:\s*\")([^\"]+)(\")"),
)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        redacted = value
        for pattern in _PATTERNS:
            redacted = pattern.sub(lambda match: _replacement(match), redacted)
        return redacted
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def _replacement(match: re.Match[str]) -> str:
    groups = match.groups()
    if len(groups) == 3:
        return f"{groups[0]}[REDACTED]{groups[2]}"
    return f"{groups[0]}[REDACTED]"

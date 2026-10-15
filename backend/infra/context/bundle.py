from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    source_ref: str
    project_id: str
    content: str
    token_count: int
    priority: int
    occurred_at: datetime
    role_scopes: tuple[str, ...] = ("shared",)
    sensitivity: str = "project"


@dataclass(frozen=True, slots=True)
class ContextManifest:
    project_id: str
    profile_id: str
    role_id: str
    token_budget: int
    included: tuple[str, ...]
    excluded: dict[str, str]


@dataclass(frozen=True, slots=True)
class ContextBundle:
    bundle_id: str
    items: tuple[ContextCandidate, ...]
    total_tokens: int
    manifest: ContextManifest


class ContextAssembler:
    def build(
        self,
        project_id: str,
        profile_id: str,
        role_id: str,
        token_budget: int,
        candidates: Iterable[ContextCandidate],
    ) -> ContextBundle:
        if token_budget < 0:
            raise ValueError("token budget must be non-negative")
        ordered = sorted(
            candidates,
            key=lambda item: (-item.priority, item.occurred_at, item.source_ref),
        )
        included: list[ContextCandidate] = []
        excluded: dict[str, str] = {}
        content_keys: set[str] = set()
        total_tokens = 0
        for candidate in ordered:
            if candidate.token_count < 0:
                raise ValueError(f"negative token count for {candidate.source_ref}")
            if candidate.project_id != project_id:
                excluded[candidate.source_ref] = "project_denied"
                continue
            if "shared" not in candidate.role_scopes and role_id not in candidate.role_scopes:
                excluded[candidate.source_ref] = "role_denied"
                continue
            content_key = self._content_key(candidate.content)
            if content_key in content_keys:
                excluded[candidate.source_ref] = "duplicate_content"
                continue
            if total_tokens + candidate.token_count > token_budget:
                excluded[candidate.source_ref] = "budget_exceeded"
                continue
            included.append(candidate)
            content_keys.add(content_key)
            total_tokens += candidate.token_count

        manifest = ContextManifest(
            project_id, profile_id, role_id, token_budget,
            tuple(item.source_ref for item in included), excluded,
        )
        bundle_id = self._bundle_id(included, manifest)
        return ContextBundle(bundle_id, tuple(included), total_tokens, manifest)

    @staticmethod
    def _content_key(content: str) -> str:
        normalized = re.sub(r"\s+", " ", content).strip().casefold()
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def _bundle_id(items: list[ContextCandidate], manifest: ContextManifest) -> str:
        payload = {
            "items": [
                {**asdict(item), "occurred_at": item.occurred_at.isoformat()}
                for item in items
            ],
            "manifest": asdict(manifest),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"ctx_{digest}"

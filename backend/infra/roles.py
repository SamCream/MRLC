from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "role.definition.v1"
ROLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
MAX_TEXT = 2000
DEFAULT_PROFILE = (Path(__file__).resolve().parents[1] / "plugins/dev/profile/conversation.json")

# Fields a role definition may carry. `module`/`responsibility`/`keywords` are the live
# conversation-routing fields; the rest are reserved so that a future "a role with authority
# creates a new role" flow extends the same carrier instead of introducing a second schema.
KNOWN_FIELDS = frozenset({
    "role_id", "module", "responsibility", "keywords", "coordinator",
    "instructions", "capabilities", "model_binding", "delegation", "metadata",
})
REQUIRED_FIELDS = frozenset({"role_id", "module", "responsibility", "keywords"})


class RoleDefinitionError(ValueError):
    pass


def _text(value: Any, name: str, *, required: bool = False, limit: int = MAX_TEXT) -> str:
    if value is None:
        if required:
            raise RoleDefinitionError(f"{name} required")
        return ""
    if not isinstance(value, str):
        raise RoleDefinitionError(f"{name} must be a string")
    text = value.strip()
    if required and not text:
        raise RoleDefinitionError(f"{name} required")
    if len(text) > limit:
        raise RoleDefinitionError(f"{name} exceeds {limit} characters")
    return text


def _keywords(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RoleDefinitionError("keywords must be a non-empty list")
    out: list[str] = []
    for item in value:
        text = _text(item, "keyword", required=True, limit=64)
        if text.casefold() in {existing.casefold() for existing in out}:
            raise RoleDefinitionError(f"duplicate keyword: {text}")
        out.append(text)
    return out


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """Extensible project-role definition carried through one versioned schema.

    Stored as JSON so new optional fields (`instructions`, `capabilities`, `model_binding`,
    `delegation`, `metadata`) can be adopted without a migration; unknown fields are rejected
    today so a typo cannot silently create a variant of the schema.
    """

    role_id: str
    module: str
    responsibility: str
    keywords: tuple[str, ...]
    coordinator: bool = False
    instructions: str = ""
    capabilities: tuple[str, ...] = ()
    model_binding: str = ""
    delegation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "module": self.module,
            "responsibility": self.responsibility,
            "keywords": list(self.keywords),
            "coordinator": self.coordinator,
            "instructions": self.instructions,
            "capabilities": list(self.capabilities),
            "model_binding": self.model_binding,
            "delegation": dict(self.delegation),
            "metadata": dict(self.metadata),
        }

    @property
    def definition_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, ensure_ascii=False)

    def as_member(self) -> dict[str, Any]:
        """The subset the conversation router needs, in its existing member shape."""
        return {"role_id": self.role_id, "module": self.module,
                "responsibility": self.responsibility, "keywords": list(self.keywords)}


def parse_role_definition(payload: Any, *, default_schema: str = SCHEMA_VERSION) -> RoleDefinition:
    if not isinstance(payload, dict):
        raise RoleDefinitionError("role definition must be an object")
    unknown = set(payload) - KNOWN_FIELDS - {"schema_version"}
    if unknown:
        raise RoleDefinitionError(f"unknown role fields: {', '.join(sorted(unknown))}")
    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        raise RoleDefinitionError(f"missing role fields: {', '.join(sorted(missing))}")
    role_id = _text(payload["role_id"], "role_id", required=True, limit=64)
    if not ROLE_ID_PATTERN.match(role_id):
        raise RoleDefinitionError("role_id must be lowercase dotted/dashed identifier")
    capabilities = payload.get("capabilities") or []
    if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
        raise RoleDefinitionError("capabilities must be a list of strings")
    delegation = payload.get("delegation") or {}
    metadata = payload.get("metadata") or {}
    if not isinstance(delegation, dict) or not isinstance(metadata, dict):
        raise RoleDefinitionError("delegation and metadata must be objects")
    schema_version = _text(payload.get("schema_version") or default_schema, "schema_version", required=True, limit=64)
    if schema_version != SCHEMA_VERSION:
        raise RoleDefinitionError(f"unsupported role schema_version: {schema_version}")
    return RoleDefinition(
        role_id=role_id,
        module=_text(payload["module"], "module", required=True, limit=120),
        responsibility=_text(payload["responsibility"], "responsibility", required=True),
        keywords=tuple(_keywords(payload["keywords"])),
        coordinator=bool(payload.get("coordinator", False)),
        instructions=_text(payload.get("instructions"), "instructions"),
        capabilities=tuple(_text(item, "capability", required=True, limit=64) for item in capabilities),
        model_binding=_text(payload.get("model_binding"), "model_binding", limit=120),
        delegation=dict(delegation),
        metadata=dict(metadata),
        schema_version=schema_version,
    )


class RoleLifecycle:
    """Extensible lifecycle hooks for project roles.

    The catalogue today is Profile baseline + project overrides. A future "a role with
    `role.grant` creates another role" flow must not need a new schema or table, so the
    lifecycle exposes the two facts such a flow needs:

    - `AUTHORITIES`: the console authorities this module owns (`role.configure`, `role.grant`);
    - `delegable(kind)`: whether `kind` may be composed by a role held by a non-owner actor.

    Composition itself still goes through the single `configure_role` command, so permissions,
    audit, idempotency, and CAS stay on the one Infra write path.
    """

    AUTHORITIES = frozenset({"role.configure", "role.grant"})
    DELEGABLE_KINDS = frozenset({"create", "update", "disable"})

    @classmethod
    def delegable(cls, kind: str) -> bool:
        return kind in cls.DELEGABLE_KINDS


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    """Load the Dev conversation profile that defines the baseline project roles."""
    resolved = Path(path or os.environ.get("MRLC_CONVERSATION_PROFILE") or DEFAULT_PROFILE)
    profile = json.loads(resolved.read_text())
    roles = profile.get("roles")
    if not isinstance(roles, list) or not roles:
        raise RoleDefinitionError("conversation profile requires at least one role")
    if len({role["role_id"] for role in roles}) != len(roles):
        raise RoleDefinitionError("conversation profile requires unique role ids")
    if sum(role["role_id"] == profile.get("coordinator") for role in roles) != 1:
        raise RoleDefinitionError("conversation profile requires exactly one coordinator")
    return profile


def baseline_roles(profile: dict[str, Any]) -> list[RoleDefinition]:
    return [RoleDefinition(role_id=role["role_id"], module=role["module"],
                           responsibility=role["responsibility"], keywords=tuple(role["keywords"]),
                           coordinator=role["role_id"] == profile["coordinator"])
            for role in profile["roles"]]


def role_catalogue(profile: dict[str, Any], overrides: list, grants: list | None = None) -> list[dict[str, Any]]:
    """Merge the profile baseline with a project's stored overrides into a display catalogue.

    Kept next to the definition schema so the projection and the router agree on how a
    project role resolves; the profile stays the single baseline source.
    """
    baseline = {role.role_id: role for role in baseline_roles(profile)}
    by_id = {row["role_id"]: row for row in overrides}
    catalogue: list[dict[str, Any]] = []
    for role_id in sorted(set(baseline) | set(by_id)):
        row = by_id.get(role_id)
        if row is None:
            entry = baseline[role_id].to_payload()
            entry.update({"origin": "inherited", "status": "ACTIVE",
                          "base_profile_version": profile.get("version"), "updated_by": None,
                          "updated_at": None})
        else:
            entry = parse_role_definition(json.loads(row["definition_json"])).to_payload()
            entry.update({"origin": row["origin"], "status": row["status"],
                          "base_profile_version": row["base_profile_version"],
                          "updated_by": row["updated_by"], "updated_at": row["updated_at"]})
        entry["authorities"] = sorted({row["authority"] for row in (grants or [])
                                       if row["role_id"] == role_id})
        catalogue.append(entry)
    return catalogue

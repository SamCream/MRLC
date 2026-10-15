from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
INFRA_API_VERSION = "v1"
INFRA_CONTRACT_VERSION = "1.0.0"
INFRA_SCHEMA_VERSION = "infra.events.v1"
INFRA_PROFILE_VERSION = "infra.profile.v1"


@dataclass(frozen=True, slots=True)
class ContractComponent:
    name: str
    owner: str
    contract_version: str
    schema_version: str
    profile_version: str
    compatible_with: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContractManifest:
    schema_version: str
    components: dict[str, ContractComponent]


def load_contract_manifest(path: str | Path) -> ContractManifest:
    raw = json.loads(Path(path).read_text())
    if raw.get("schema_version") != "mrlc.contracts.v1":
        raise ValueError("unsupported contract manifest schema")
    entries = raw.get("components")
    if not isinstance(entries, dict):
        raise ValueError("contract manifest components must be an object")
    components: dict[str, ContractComponent] = {}
    for name, value in entries.items():
        if not isinstance(value, dict):
            raise ValueError(f"contract entry must be an object: {name}")
        component = ContractComponent(
            name=name,
            owner=_required(value, "owner", name),
            contract_version=_required(value, "contract_version", name),
            schema_version=_required(value, "schema_version", name),
            profile_version=_required(value, "profile_version", name),
            compatible_with=tuple(value.get("compatible_with", ())),
        )
        if not _VERSION.fullmatch(component.contract_version):
            raise ValueError(f"invalid contract version: {name}")
        if not component.compatible_with:
            raise ValueError(f"missing compatibility range: {name}")
        components[name] = component
    return ContractManifest(str(raw["schema_version"]), components)


def _required(value: dict[str, Any], key: str, component: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"missing {key}: {component}")
    return result


def validate_health_payload(manifest: ContractManifest, payload: dict[str, Any]) -> bool:
    service = payload.get("service")
    if not isinstance(service, str) or service not in manifest.components:
        raise ValueError("health payload has unknown service")
    component = manifest.components[service]
    expected = {
        "api_version": "v1" if service == "infra" else payload.get("api_version"),
        "contract_version": component.contract_version,
        "schema_version": component.schema_version,
        "profile_version": component.profile_version,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"health payload {key} does not match manifest")
    if payload.get("status") not in {"ok", "running"}:
        raise ValueError("health payload is not healthy")
    return True

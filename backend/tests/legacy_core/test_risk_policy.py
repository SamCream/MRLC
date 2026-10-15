from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from infra.audit.policy import ActionRequest, AuditStore, RiskLevel, RiskPolicy


def test_r3_and_r4_require_explicit_user_authorization(tmp_path: Path) -> None:
    audit = AuditStore(tmp_path / "audit.sqlite")
    policy = RiskPolicy(audit)
    external = ActionRequest(
        actor_id="engineer", profile_id="dev", project_id="prj-1",
        action="publish", target="public-release", environment="production",
        authority=("code.write",), budget_cents=100,
    )

    denied = policy.authorize(external)
    approved = policy.authorize(external, user_authorization="approval-123")

    assert denied.risk == RiskLevel.R4
    assert denied.allowed is False
    assert denied.reason == "explicit user authorization required"
    assert approved.allowed is True
    assert approved.authorization_ref == "approval-123"
    assert [entry.result for entry in audit.list("prj-1")] == ["DENIED", "ALLOWED"]


def test_r2_requires_role_authority_and_audit_is_append_only(tmp_path: Path) -> None:
    audit = AuditStore(tmp_path / "audit.sqlite")
    policy = RiskPolicy(audit)
    request = ActionRequest(
        actor_id="engineer", profile_id="dev", project_id="prj-1",
        action="cancel_child", target="child-1", environment="local",
        authority=(), budget_cents=0,
    )

    denied = policy.authorize(request)
    allowed = policy.authorize(replace(request, authority=("task.cancel",)))

    assert denied.risk == RiskLevel.R2
    assert denied.allowed is False
    assert allowed.allowed is True
    with pytest.raises(RuntimeError, match="append-only"):
        audit.delete(denied.decision_id)

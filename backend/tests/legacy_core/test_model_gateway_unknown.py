import pytest

from infra.model_gateway import ModelGateway, ModelRequest


def test_unknown_reconciliation_preserves_invocation_context_after_restart(tmp_path) -> None:
    database = tmp_path / "gateway.sqlite3"
    request = ModelRequest("fast", "CONSULT", "prj-one", "inv-1")
    gateway = ModelGateway({"fast": ["local-fast"]}, database_path=database)
    started = gateway.invoke(request)

    unknown = gateway.mark_unknown("inv-1", provider_request_id="provider-1", reason="stream lost")
    assert unknown.status == "UNKNOWN"
    assert unknown.project_id == "prj-one"
    assert unknown.model == "local-fast"

    restarted = ModelGateway({"fast": ["local-fast"]}, database_path=database)
    with pytest.raises(RuntimeError, match="reconciliation"):
        restarted.invoke(request)

    reconciled = restarted.reconcile(
        "inv-1", status="COMPLETED", usage_input=12, usage_output=4,
        provider_request_id="provider-1",
    )
    assert reconciled.project_id == "prj-one"
    assert reconciled.model == "local-fast"
    assert reconciled.usage_input == 12
    assert restarted.invoke(request) == reconciled

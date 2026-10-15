from __future__ import annotations

import os

os.environ.setdefault("MRLC_ADMIN_PASSWORD","test-password-very-long")
os.environ.setdefault("MRLC_ADMIN_TOKEN","test-admin-token-very-long-and-independent-12345")
os.environ.setdefault("MRLC_SESSION_SECRET","test-signing-key-longer-than-thirty-two-chars-12345")

from fastapi.testclient import TestClient
from app.main import create_app

PASSWORD="test-password-very-long"


def _client(tmp_path):
    app=create_app(
        db_path=str(tmp_path/"infra-console.sqlite3"),
        artifact_dir=str(tmp_path/"artifacts"),
        password=PASSWORD,
        secret="s"*40,
        admin_token="a"*32,
    )
    return app,TestClient(app)


def _login_and_project(client):
    assert client.post("/api/v1/auth/login",json={"password":PASSWORD}).status_code==200
    result=client.post("/api/v1/projects",json={"name":"Infra UI","description":"console tests"})
    assert result.status_code==201,result.text
    return result.json()["id"]


def test_infra_console_read_models_are_project_scoped_and_stable(tmp_path):
    app,client=_client(tmp_path)
    with client:
        project_id=_login_and_project(client)
        paths=[
            "overview","scheduler","context","bank","provenance",
            "invocations","consistency","operations",
        ]
        for suffix in paths:
            response=client.get(f"/api/v1/projects/{project_id}/infra/{suffix}")
            assert response.status_code==200,(suffix,response.text)

        events=client.get(f"/api/v1/projects/{project_id}/infra/events")
        replay=client.get(f"/api/v1/projects/{project_id}/infra/replay")
        assert events.status_code==200,events.text
        assert replay.status_code==200,replay.text
        assert any(item["type"]=="PROJECT_CREATED" for item in events.json()["items"])
        assert replay.json()["projects"][project_id]=="Infra UI"

        overview=client.get(f"/api/v1/projects/{project_id}/infra/overview").json()
        assert overview["events"]["sequenceGapCount"]==0
        assert overview["replay"]["healthy"] is True
        assert overview["consistency"]["deadLetters"]==0


def test_infra_console_does_not_duplicate_product_view_payloads(tmp_path):
    app,client=_client(tmp_path)
    with client:
        project_id=_login_and_project(client)
        scheduler=client.get(f"/api/v1/projects/{project_id}/infra/scheduler").json()
        context=client.get(f"/api/v1/projects/{project_id}/infra/context").json()
        operations=client.get(f"/api/v1/projects/{project_id}/infra/operations").json()

        serialized=str(scheduler)
        assert "taskTitle" not in serialized
        assert "gpu" not in serialized.lower()
        assert "decodeTokensPerSecond" not in serialized
        assert set(scheduler)=={
            "capacity","active","queued","parked","sessions","queue","priorityAdjustments"
        }

        # History is an index only. The Infra UI deliberately does not mirror Workspace chat text.
        for item in context["historySessions"]:
            assert "content" not in item
        assert "providerHealth" not in str(operations)
        assert operations["backup"]["tracked"] is False

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
        assert operations["backup"]["tracked"] is True


class CapturingExecutor:
    available=True
    model="context-test-model"
    capabilities={"chatCompletions":True}
    def __init__(self):
        self.calls=[]
    async def complete_result(self,system_prompt,messages):
        self.calls.append({"system":system_prompt,"messages":messages})
        return {
            "content":"context-aware completion",
            "model":self.model,
            "inputTokens":13,
            "outputTokens":5,
            "costUsd":None,
            "usageReported":True,
            "providerRequestId":"context-provider-request",
        }


def test_runtime_consumes_evidence_backed_memory_and_persists_assembly_trace(tmp_path):
    import time
    executor=CapturingExecutor()
    app=create_app(
        db_path=str(tmp_path/"memory-runtime.sqlite3"),
        artifact_dir=str(tmp_path/"artifacts"),
        password=PASSWORD,
        secret="s"*40,
        admin_token="a"*32,
        executor=executor,
    )
    with TestClient(app) as client:
        project_id=_login_and_project(client)
        role=client.post("/api/v1/roles",json={
            "name":"Engineer","purpose":"prove bounds","lifecycle":"active",
            "capabilities":[],"allowedPermissions":["tasks.execute","messages.reply"],
        })
        assert role.status_code==201,role.text
        role_id=role.json()["id"]
        agent=client.post(f"/api/v1/projects/{project_id}/agents",json={
            "roleTemplateId":role_id,"name":"Engineer"
        })
        assert agent.status_code==201,agent.text

        events=client.get(f"/api/v1/projects/{project_id}/infra/events").json()["items"]
        source_event=next(item["eventId"] for item in events if item["type"]=="PROJECT_CREATED")
        memory=client.post(f"/api/v1/projects/{project_id}/infra/memory",json={
            "roleScope":"shared","kind":"fact",
            "content":"Use the Gronwall estimate when proving this bound.",
            "sourceEventRefs":[source_event],"confidence":0.98,
            "evidence":[{"eventId":source_event,"stance":"supports",
                         "sourceRef":"event:"+source_event,"confidence":1.0}],
        })
        assert memory.status_code==201,memory.text

        task=client.post(f"/api/v1/projects/{project_id}/tasks",json={
            "projectId":project_id,"title":"Gronwall bound",
            "description":"Prove the estimate using Gronwall.",
            "assigneeRoleId":role_id,"dependencyIds":[],
            "acceptanceCriteria":[{"description":"proof"}],
        })
        assert task.status_code==201,task.text
        dispatched=client.post(f"/api/v1/tasks/{task.json()['id']}/dispatch")
        assert dispatched.status_code==202,dispatched.text
        job_id=dispatched.json()["jobId"]
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            jobs=client.get("/api/v1/runtime/jobs").json()["items"]
            row=next(item for item in jobs if item["id"]==job_id)
            if row["status"] in {"completed","failed","cancelled"}:
                break
            time.sleep(.05)
        assert row["status"]=="completed",row
        assert executor.calls
        serialized=str(executor.calls[-1]["messages"])
        assert "Use the Gronwall estimate when proving this bound." in serialized

        context=client.get(f"/api/v1/projects/{project_id}/infra/context")
        assert context.status_code==200,context.text
        body=context.json()
        assert body["assemblyRuns"]
        trace=body["assemblyRuns"][0]
        memory_ref="memory:"+memory.json()["memory_id"]
        assert memory_ref in trace["included"]
        assert trace["total_tokens"]>0
        snapshot=next(item for item in body["snapshots"] if item["taskId"]==task.json()["id"])
        assert snapshot["memoryBundleId"]
        assert memory_ref in snapshot["memorySourceRefs"]
        domain=[item["type"] for item in client.get(f"/api/v1/projects/{project_id}/infra/events").json()["items"]]
        assert "CONTEXT_ASSEMBLED" in domain


def test_point_in_time_replay_and_state_diff(tmp_path):
    app,client=_client(tmp_path)
    with client:
        project_id=_login_and_project(client)
        before=client.get(f"/api/v1/projects/{project_id}/infra/events").json()["items"][-1]["sequence"]
        task=client.post(f"/api/v1/projects/{project_id}/tasks",json={
            "projectId":project_id,"title":"Replay task","description":"",
            "dependencyIds":[],"acceptanceCriteria":[],
        })
        assert task.status_code==201,task.text
        after=client.get(f"/api/v1/projects/{project_id}/infra/events").json()["items"][-1]["sequence"]

        old=client.get(f"/api/v1/projects/{project_id}/infra/replay?sequence={before}")
        new=client.get(f"/api/v1/projects/{project_id}/infra/replay?sequence={after}")
        diff=client.get(
            f"/api/v1/projects/{project_id}/infra/replay/compare"
            f"?fromSequence={before}&toSequence={after}"
        )
        assert old.status_code==200 and new.status_code==200 and diff.status_code==200
        assert task.json()["id"] not in old.json()["tasks"]
        assert new.json()["tasks"][task.json()["id"]]=="CREATED"
        assert any(item["id"]==task.json()["id"] for item in diff.json()["changes"]["tasks"])


def test_managed_backup_and_restore_drill_are_recorded_without_path_leakage(tmp_path,monkeypatch):
    monkeypatch.setenv("MRLC_BACKUP_DIR",str(tmp_path/"managed-backups"))
    app,client=_client(tmp_path)
    with client:
        _login_and_project(client)
        created=client.post("/api/v1/infra/backups",json={})
        assert created.status_code==201,created.text
        backup_id=created.json()["backupId"]
        assert created.json()["checksumStatus"]=="VERIFIED"

        listing=client.get("/api/v1/infra/backups")
        assert listing.status_code==200,listing.text
        first=listing.json()["items"][0]
        assert first["backup_id"]==backup_id
        assert "destination" not in first
        assert "manifest_path" not in first
        assert first["storage_ref"]==backup_id

        verified=client.post(f"/api/v1/infra/backups/{backup_id}/verify",json={})
        assert verified.status_code==200,verified.text
        assert verified.json()["checksumStatus"]=="VERIFIED"
        runs=client.get("/api/v1/infra/backups").json()["items"]
        assert {row["kind"] for row in runs}>={"BACKUP","VERIFY"}
        assert all("destination" not in row for row in runs)

from __future__ import annotations

import os
import sqlite3
import time

os.environ.setdefault("MRLC_ADMIN_PASSWORD","test-password-very-long")
os.environ.setdefault("MRLC_ADMIN_TOKEN","test-admin-token-very-long-and-independent-12345")
os.environ.setdefault("MRLC_SESSION_SECRET","test-signing-key-longer-than-thirty-two-chars-12345")

from fastapi.testclient import TestClient
from app.main import create_app

PASSWORD="test-password-very-long"


class MigratedExecutor:
    available=True
    model="migration-test-model"
    capabilities={"chatCompletions":True}

    async def complete_result(self,system_prompt,messages):
        return {
            "content":"infra migration execution",
            "model":self.model,
            "inputTokens":11,
            "outputTokens":7,
            "costUsd":None,
            "usageReported":True,
            "providerRequestId":"provider-migration-1",
        }


def _login(client):
    result=client.post("/api/v1/auth/login",json={"password":PASSWORD})
    assert result.status_code==200,result.text


def _project(client,name="Infra"):
    result=client.post("/api/v1/projects",json={"name":name,"description":"migration"})
    assert result.status_code==201,result.text
    return result.json()["id"]


def test_phase8_events_table_is_migrated_without_losing_sse_history(tmp_path):
    db=tmp_path/"legacy.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            occurred_at TEXT NOT NULL, type TEXT NOT NULL,
            project_id TEXT, payload TEXT NOT NULL
        );
        INSERT INTO events(id,occurred_at,type,project_id,payload)
        VALUES('evt-old','2026-01-01T00:00:00Z','legacy.event','project-old','{}');
        """)
    app=create_app(db_path=str(db),artifact_dir=str(tmp_path/"files"),password=PASSWORD,
                   secret="s"*40,admin_token="a"*32)
    try:
        cols={row["name"] for row in app.state.store.execute("PRAGMA table_info(events)").fetchall()}
        assert {"event_id","event_type","idempotency_key","sequence"}.issubset(cols)
        old=app.state.store.execute("SELECT id,type FROM ui_events WHERE id='evt-old'").fetchone()
        assert old["type"]=="legacy.event"
    finally:
        app.state.store.close()


def test_fastapi_projection_populates_authoritative_infra_and_replay(tmp_path):
    app=create_app(db_path=str(tmp_path/"infra.sqlite3"),artifact_dir=str(tmp_path/"files"),
                   password=PASSWORD,secret="s"*40,admin_token="a"*32)
    with TestClient(app) as client:
        _login(client)
        pid=_project(client)
        task=client.post(f"/api/v1/projects/{pid}/tasks",json={
            "projectId":pid,"title":"Core task","description":"bridge",
            "dependencyIds":[],"acceptanceCriteria":[]
        })
        assert task.status_code==201,task.text
        tid=task.json()["id"]

        status=client.get("/api/v1/infra/status")
        assert status.status_code==200,status.text
        body=status.json()
        assert body["mode"]=="migrated-infra-core"
        assert body["counts"]["projects"]==1
        assert body["counts"]["tasks"]==1

        events=client.get(f"/api/v1/projects/{pid}/infra/events").json()["items"]
        kinds=[event["type"] for event in events]
        assert "PROJECT_CREATED" in kinds
        assert "TASK_CREATED" in kinds

        replay=client.get(f"/api/v1/projects/{pid}/infra/replay")
        assert replay.status_code==200,replay.text
        assert replay.json()["projects"][pid]=="Infra"
        assert replay.json()["tasks"][tid]=="CREATED"


def test_dispatch_uses_migrated_scheduler_and_gateway_ledger(tmp_path):
    app=create_app(db_path=str(tmp_path/"runtime.sqlite3"),artifact_dir=str(tmp_path/"files"),
                   password=PASSWORD,secret="s"*40,admin_token="a"*32,executor=MigratedExecutor())
    with TestClient(app) as client:
        _login(client)
        pid=_project(client,"Runtime")
        role=client.post("/api/v1/roles",json={
            "name":"Engineer","purpose":"execute","lifecycle":"active",
            "capabilities":[],"allowedPermissions":["tasks.execute","messages.reply"]
        })
        assert role.status_code==201,role.text
        rid=role.json()["id"]
        agent=client.post(f"/api/v1/projects/{pid}/agents",json={"roleTemplateId":rid,"name":"E"})
        assert agent.status_code==201,agent.text
        task=client.post(f"/api/v1/projects/{pid}/tasks",json={
            "projectId":pid,"title":"Execute","assigneeRoleId":rid,
            "dependencyIds":[],"acceptanceCriteria":[]
        })
        assert task.status_code==201,task.text

        dispatched=client.post(f"/api/v1/tasks/{task.json()['id']}/dispatch")
        assert dispatched.status_code==202,dispatched.text
        job_id=dispatched.json()["jobId"]
        deadline=time.monotonic()+5
        final=None
        while time.monotonic()<deadline:
            jobs=client.get("/api/v1/runtime/jobs").json()["items"]
            final=next(x for x in jobs if x["id"]==job_id)
            if final["status"] in {"completed","failed","cancelled"}:
                break
            time.sleep(.05)
        assert final and final["status"]=="completed",final

        ledger=app.state.store.execute(
            "SELECT model,status,provider_request_id,usage_input,usage_output "
            "FROM gateway_invocations WHERE idempotency_key=?",
            ("job:"+job_id,)
        ).fetchone()
        assert dict(ledger)=={
            "model":"migration-test-model","status":"COMPLETED",
            "provider_request_id":"provider-migration-1","usage_input":11,"usage_output":7
        }
        scheduler=app.state.store.execute("SELECT state_json FROM scheduler_state WHERE id=1").fetchone()
        assert scheduler is not None
        domain=[e.event_type for e in app.state.infra.events.read(pid)]
        assert "LEASE_ACQUIRED" in domain
        assert "LEASE_EXPIRED" in domain
        assert "MODEL_INVOCATION_COMPLETED" in domain


def test_scheduler_enqueue_is_idempotent_for_same_job(tmp_path):
    app=create_app(db_path=str(tmp_path/"idempotent.sqlite3"),artifact_dir=str(tmp_path/"files"),
                   password=PASSWORD,secret="s"*40,admin_token="a"*32)
    try:
        store=app.state.store
        with store.tx():
            project={"id":"project-idempotent","name":"Idempotent","description":"","status":"idle","progress":0}
            store.change("test","project",project,project["id"])
            role={"id":"role-idempotent","name":"Engineer","version":"v1","purpose":"",
                  "capabilities":[],"allowedPermissions":["tasks.execute"],"preferredModelId":None,"lifecycle":"active"}
            store.change("test","role",role)
            session_id="session-idempotent"
            agent={"id":"agent-idempotent","projectId":project["id"],"roleTemplateId":role["id"],
                   "name":"E","status":"idle","taskId":None,"sessionId":session_id}
            store.change("test","agent",agent,project["id"])
            app.state.infra.enqueue_job(agent,project["id"],50,job_id="job-stable")
            app.state.infra.enqueue_job(agent,project["id"],50,job_id="job-stable")
        queued=[event for event in app.state.infra.events.read(project["id"])
                if event.event_type=="SESSION_QUEUED" and event.payload.get("job_id")=="job-stable"]
        assert len(queued)==1
    finally:
        app.state.store.close()


def test_workspace_artifact_and_approval_promote_into_infra_truth(tmp_path):
    app=create_app(db_path=str(tmp_path/"semantic.sqlite3"),artifact_dir=str(tmp_path/"files"),
                   password=PASSWORD,secret="s"*40,admin_token="a"*32)
    with TestClient(app) as client:
        _login(client)
        pid=_project(client,"Semantic")

        channel=client.post(f"/api/v1/projects/{pid}/channels",json={
            "name":"general","purpose":"history","contextPolicy":"relevant"
        })
        assert channel.status_code==201,channel.text
        message=client.post(f"/api/v1/channels/{channel.json()['id']}/messages",json={
            "content":"preserve this message","responseMode":"single"
        })
        assert message.status_code==201,message.text
        history=app.state.store.execute(
            "SELECT actor,content FROM history WHERE project_id=? AND session_id=?",
            (pid,"channel:"+channel.json()["id"])
        ).fetchall()
        assert [(row["actor"],row["content"]) for row in history]==[("You","preserve this message")]

        artifact=client.post(
            f"/api/v1/projects/{pid}/artifacts",
            files={"file":("proof.txt",b"verified bytes","text/plain")},
        )
        assert artifact.status_code==201,artifact.text
        art=artifact.json()
        replay=client.get(f"/api/v1/projects/{pid}/infra/replay").json()
        assert replay["artifacts"][art["id"]]==art["checksum"]

        role=client.post("/api/v1/roles",json={
            "name":"Reviewer","purpose":"request approval","lifecycle":"active",
            "capabilities":[],"allowedPermissions":[]
        })
        assert role.status_code==201,role.text
        agent=client.post(f"/api/v1/projects/{pid}/agents",json={
            "roleTemplateId":role.json()["id"],"name":"Reviewer"
        })
        assert agent.status_code==201,agent.text
        approval=client.post("/api/v1/approvals",json={
            "projectId":pid,"requestedByAgentId":agent.json()["id"],
            "title":"Adopt backend migration","type":"decision","risk":"low",
            "evidenceArtifactIds":[art["id"]]
        })
        assert approval.status_code==201,approval.text
        decided=client.post(f"/api/v1/approvals/{approval.json()['id']}/decisions",json={
            "decision":"approve","reason":"migration evidence verified","expectedRevision":1
        })
        assert decided.status_code==200,decided.text
        bank=app.state.store.execute(
            "SELECT title,rationale,status FROM bank_decisions WHERE project_id=?",(pid,)
        ).fetchall()
        assert len(bank)==1
        assert bank[0]["title"]=="Adopt backend migration"
        assert bank[0]["rationale"]=="migration evidence verified"
        assert bank[0]["status"]=="active"
        domain=[event.event_type for event in app.state.infra.events.read(pid)]
        assert "MESSAGE_RECORDED" in domain
        assert "ARTIFACT_RECORDED" in domain
        assert "DECISION_RECORDED" in domain


def test_memory_and_bank_write_apis_are_evented_and_project_scoped(tmp_path):
    app=create_app(db_path=str(tmp_path/"knowledge.sqlite3"),artifact_dir=str(tmp_path/"files"),
                   password=PASSWORD,secret="s"*40,admin_token="a"*32)
    with TestClient(app) as client:
        _login(client)
        pid=_project(client,"Knowledge")
        events=client.get(f"/api/v1/projects/{pid}/infra/events").json()["items"]
        source_event=next(item["eventId"] for item in events if item["type"]=="PROJECT_CREATED")

        first=client.post(f"/api/v1/projects/{pid}/infra/memory",json={
            "roleScope":"shared","kind":"fact","content":"Infra owns durable state",
            "sourceEventRefs":[source_event],"confidence":0.9,
            "evidence":[{"eventId":source_event,"stance":"supports",
                         "sourceRef":"event:"+source_event,"confidence":1.0}]
        })
        assert first.status_code==201,first.text
        second=client.post(f"/api/v1/projects/{pid}/infra/memory",json={
            "roleScope":"shared","kind":"fact","content":"Infra owns durable state and replay",
            "sourceEventRefs":[source_event],"confidence":0.95,
            "evidence":[{"eventId":source_event,"stance":"supports",
                         "sourceRef":"event:"+source_event,"confidence":1.0}]
        })
        assert second.status_code==201,second.text
        superseded=client.post(
            f"/api/v1/projects/{pid}/infra/memory/{first.json()['memory_id']}/supersede",
            json={"replacementMemoryId":second.json()["memory_id"]},
        )
        assert superseded.status_code==200,superseded.text
        assert app.state.infra.memory.get(pid,first.json()["memory_id"]).status=="superseded"

        issue=client.post(f"/api/v1/projects/{pid}/infra/bank/issues",json={
            "title":"Choose migration cutover","blocking":"soft",
            "decisionOwner":"owner","autoDecidable":False,"reversible":True
        })
        assert issue.status_code==201,issue.text
        decision=client.post(f"/api/v1/projects/{pid}/infra/bank/decisions",json={
            "title":"Keep FastAPI compatibility layer","authority":"owner",
            "risk":"low","rationale":"UI contract remains stable"
        })
        assert decision.status_code==201,decision.text
        resolved=client.post(
            f"/api/v1/projects/{pid}/infra/bank/issues/{issue.json()['issue_id']}/resolve",
            json={"decisionId":decision.json()["decision_id"]},
        )
        assert resolved.status_code==200,resolved.text
        assert resolved.json()["status"]=="RESOLVED"
        lesson=client.post(f"/api/v1/projects/{pid}/infra/bank/lessons",json={
            "title":"Do not fork the source of truth","sourceRefs":[decision.json()["decision_id"]]
        })
        assert lesson.status_code==201,lesson.text
        progress=client.post(f"/api/v1/projects/{pid}/infra/bank/progress",json={
            "milestone":"Backend core migrated","status":"green","artifactRefs":[]
        })
        assert progress.status_code==201,progress.text

        replay=client.get(f"/api/v1/projects/{pid}/infra/replay")
        assert replay.status_code==200,replay.text
        state=replay.json()
        assert state["memories"][first.json()["memory_id"]]=="SUPERSEDED"
        assert state["memories"][second.json()["memory_id"]]=="ACTIVE"
        assert state["issues"][issue.json()["issue_id"]]=="RESOLVED"
        assert decision.json()["decision_id"] in state["decisions"]

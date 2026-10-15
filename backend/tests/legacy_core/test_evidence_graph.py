from __future__ import annotations

import json
import sqlite3

from infra.evidence_graph import evidence_chains


def test_evidence_graph_uses_only_persisted_backend_records(tmp_path):
    database=tmp_path/"infra.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.row_factory=sqlite3.Row
        conn.executescript("""
        CREATE TABLE tasks(task_id TEXT PRIMARY KEY,title TEXT,state TEXT);
        CREATE TABLE runtime_delegations(
            relation_id TEXT PRIMARY KEY,state TEXT,return_contract TEXT
        );
        CREATE TABLE frontier_delegate_runs(
            request_id TEXT PRIMARY KEY, project_id TEXT, relation_id TEXT,
            parent_task_id TEXT, child_task_id TEXT, objective TEXT,
            state TEXT, return_package_json TEXT, commit_sha TEXT,
            branch TEXT, merge_sha TEXT, created_at REAL
        );
        CREATE TABLE events(
            project_id TEXT, sequence INTEGER, payload TEXT, actor_id TEXT,
            occurred_at TEXT, event_type TEXT
        );
        """)
        conn.execute("INSERT INTO tasks VALUES('parent','Parent requirement','RUNNING')")
        conn.execute("INSERT INTO tasks VALUES('child','Child task','RUNNABLE')")
        conn.execute("INSERT INTO runtime_delegations VALUES('rel-1','OPEN','artifact,tests')")
        conn.execute(
            "INSERT INTO frontier_delegate_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("req-1","prj-1","rel-1","parent","child","implement backend",
             "QUEUED",None,None,"frontier/req-1",None,1.0),
        )
        graph=evidence_chains(conn,"prj-1")

    assert graph["stages"]==[
        "requirement","task","delegation","invocation","artifact","tests","review","commit"
    ]
    assert len(graph["chains"])==1
    chain=graph["chains"][0]
    assert chain["chain_state"]=="IN_PROGRESS"
    nodes={node["stage"]:node for node in chain["nodes"]}
    assert [nodes[name]["status"] for name in ("requirement","task","delegation")]==["PRESENT"]*3
    assert {nodes[name]["status"] for name in ("invocation","artifact","tests","review","commit")}=={"PENDING"}


def test_evidence_graph_marks_missing_authoritative_link_broken(tmp_path):
    database=tmp_path/"infra.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.row_factory=sqlite3.Row
        conn.executescript("""
        CREATE TABLE tasks(task_id TEXT PRIMARY KEY,title TEXT,state TEXT);
        CREATE TABLE runtime_delegations(
            relation_id TEXT PRIMARY KEY,state TEXT,return_contract TEXT
        );
        CREATE TABLE frontier_delegate_runs(
            request_id TEXT PRIMARY KEY, project_id TEXT, relation_id TEXT,
            parent_task_id TEXT, child_task_id TEXT, objective TEXT,
            state TEXT, return_package_json TEXT, commit_sha TEXT,
            branch TEXT, merge_sha TEXT, created_at REAL
        );
        CREATE TABLE events(
            project_id TEXT, sequence INTEGER, payload TEXT, actor_id TEXT,
            occurred_at TEXT, event_type TEXT
        );
        """)
        conn.execute("INSERT INTO tasks VALUES('parent','Parent','RUNNING')")
        conn.execute("INSERT INTO tasks VALUES('child','Child','RUNNABLE')")
        conn.execute(
            "INSERT INTO frontier_delegate_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("req-1","prj-1","missing-rel","parent","child","implement",
             "WAIT_OWNER_REVIEW",json.dumps({"verification":[{"command":"pytest","passed":True}]}),
             "commit-1","frontier/req-1",None,1.0),
        )
        graph=evidence_chains(conn,"prj-1")

    chain=graph["chains"][0]
    nodes={node["stage"]:node for node in chain["nodes"]}
    assert nodes["delegation"]["status"]=="MISSING"
    assert chain["chain_state"]=="BROKEN"

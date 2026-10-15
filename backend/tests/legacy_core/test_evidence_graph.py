from __future__ import annotations

import threading

from infra.console_projection import ConsoleModel
from runtime.frontier_delegate import FrontierDelegateRunner
from tests.contract.infra.test_console_api import request
from tests.contract.infra.test_frontier_delegate_console import console, delegate, review, worker  # noqa: F401
from ui.console.server import create_server


def chain(model, project):
    graph = model.evidence_graph((project,), project)
    assert graph["stages"] == ["requirement", "task", "delegation", "invocation", "artifact", "tests", "review", "commit"]
    assert len(graph["chains"]) == 1
    item = graph["chains"][0]
    return item, {node["stage"]: node for node in item["nodes"]}


def test_queued_delegate_reports_pending_links_not_inferred_ones(console):
    model, project, commands, identity, _ = console
    delegate(model, project, commands, identity)
    item, nodes = chain(model, project)
    assert item["chain_state"] == "IN_PROGRESS"
    assert [nodes[s]["status"] for s in ("requirement", "task", "delegation")] == ["PRESENT"] * 3
    assert {nodes[s]["status"] for s in ("invocation", "artifact", "tests", "review", "commit")} == {"PENDING"}


def test_accepted_delegate_closes_every_link_with_its_own_record(console):
    model, project, commands, identity, tmp_path = console
    result = delegate(model, project, commands, identity)["result"]
    runner = FrontierDelegateRunner(model, worker(tmp_path))
    runner.process_one()
    item, nodes = chain(model, project)
    assert item["chain_state"] == "IN_PROGRESS"
    assert nodes["tests"]["status"] == "PRESENT" and "PASS" in nodes["tests"]["summary"]
    # This Worker performs no gateway call, so the meter has nothing to report (DEC-0161).
    assert nodes["invocation"]["summary"].startswith("UNKNOWN · usage UNKNOWN")
    assert nodes["review"]["status"] == "PENDING" and nodes["commit"]["status"] == "PENDING"
    commands.delegate = runner
    merged = review(model, project, commands, identity, result["relation_id"], "ACCEPT")["result"]["frontier"]
    item, nodes = chain(model, project)
    assert item["chain_state"] == "COMPLETE"
    assert {node["status"] for node in item["nodes"]} == {"PRESENT"}
    assert nodes["commit"]["ref"] == merged["merge_sha"]
    assert nodes["review"]["summary"].startswith("ACCEPTED by owner: reviewed diff and tests")
    assert nodes["artifact"]["summary"].endswith("hello.py")


def test_failed_verification_is_shown_as_failed_and_later_links_not_reached(console):
    model, project, commands, identity, tmp_path = console
    delegate(model, project, commands, identity,
             commands_list=["python3 -c 'import hello; assert hello.greet(\"A\") == \"nope\"'"])
    FrontierDelegateRunner(model, worker(tmp_path)).process_one()
    item, nodes = chain(model, project)
    assert item["chain_state"] == "FAILED"
    assert nodes["tests"]["status"] == "FAILED"
    assert nodes["review"]["status"] == "NOT_REACHED" and nodes["commit"]["status"] == "NOT_REACHED"


def test_rework_leaves_commit_not_applicable(console):
    model, project, commands, identity, tmp_path = console
    result = delegate(model, project, commands, identity)["result"]
    runner = FrontierDelegateRunner(model, worker(tmp_path))
    runner.process_one()
    commands.delegate = runner
    review(model, project, commands, identity, result["relation_id"], "REWORK")
    item, nodes = chain(model, project)
    assert item["chain_state"] == "REWORK"
    assert nodes["commit"]["status"] == "NOT_APPLICABLE"


def test_missing_record_breaks_the_chain(console):
    model, project, commands, identity, _ = console
    result = delegate(model, project, commands, identity)["result"]
    with model.connect() as conn:
        conn.execute("DELETE FROM runtime_delegations WHERE relation_id=?", (result["relation_id"],))
    item, nodes = chain(model, project)
    assert nodes["delegation"]["status"] == "MISSING" and item["chain_state"] == "BROKEN"


def test_evidence_endpoint_is_project_scoped(tmp_path):
    model = ConsoleModel(tmp_path / "infra.sqlite3")
    project = model.infra.create_project("Scoped", actor_id="owner").project_id
    server = create_server("127.0.0.1", 0, tmp_path / "infra.sqlite3", {
        "owner": {"actor_id": "owner", "projects": [project], "authority": []},
        "stranger": {"actor_id": "stranger", "projects": [], "authority": []}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        status, body, _ = request(base, f"/api/evidence?project_id={project}", key="owner")
        assert status == 200 and body["chains"] == []
        assert request(base, f"/api/evidence?project_id={project}", key="stranger")[0] == 403
        assert request(base, "/api/evidence", key="owner")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_tui_lists_one_row_per_stage(console):
    from ui.console.tui import SECTIONS, TerminalConsole
    model, project, commands, identity, _ = console
    delegate(model, project, commands, identity)
    tui = TerminalConsole(client=None)
    tui.snapshot = {"capabilities": {}}
    tui.section = next(index for index, (key, _) in enumerate(SECTIONS) if key == "evidence")
    tui.evidence = model.evidence_graph((project,), project)
    rows = tui.rows()
    assert [row["title"].split(" ")[1] for row in rows] == tui.evidence["stages"]
    assert rows[3]["status"] == "PENDING" and rows[0]["chain_state"] == "IN_PROGRESS"

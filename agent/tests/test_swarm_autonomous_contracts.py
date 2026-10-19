"""Trusted preset loading and existing REST/SSE boundaries for autonomous runs."""

import threading
import time
from io import StringIO

import pytest
import yaml
from fastapi.testclient import TestClient
from rich.console import Console

import src.interfaces.api_server as api_server
import src.interfaces.api.swarm_routes as routes
import src.runtime.swarm.runtime as runtime_module
from src.runtime.swarm import presets
from src.runtime.swarm.blackboard import Blackboard
from src.runtime.swarm.models import SwarmAgentSpec, SwarmRun, SwarmTask, WorkerResult
from src.runtime.swarm.store import SwarmStore


def test_bundled_autonomous_preset_has_eligible_peers_and_no_order_tools():
    run = presets.build_run_from_preset("autonomous_research_team", {"topic": "Research an investment hypothesis"})
    assert run.execution_mode == "autonomous"
    assert all(not t.agent_id and t.required_capabilities for t in run.tasks)
    assert all(a.capabilities for a in run.agents)
    assert all(not name.startswith("trading_") and name != "bash" for a in run.agents for name in a.tools)
    report = presets.inspect_preset("autonomous_research_team")
    assert report["valid"], report["errors"]
    assert report["execution_mode"] == "autonomous"
    assert report["tasks"][0]["required_capabilities"]
    entry = next(p for p in presets.list_presets() if p["name"] == "autonomous_research_team")
    assert entry["execution_mode"] == "autonomous"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"autonomy": {"lease_seconds": 0}}, "lease_seconds"),
        ({"execution_mode": "invalid"}, "execution_mode"),
        ({"final_task_id": "missing"}, "final_task_id"),
        ({"tasks": [{"id": "report", "prompt_template": "x", "required_capabilities": ["unknown"]}]}, "eligible"),
    ],
)
def test_autonomous_preset_rejects_invalid_settings_before_run_creation(tmp_path, monkeypatch, change, message):
    data = {
        "name": "test-peer",
        "execution_mode": "autonomous",
        "final_task_id": "report",
        "agents": [{"id": "analyst", "role": "Research", "system_prompt": "x", "capabilities": ["research"]}],
        "tasks": [{"id": "report", "prompt_template": "Research {topic}", "required_capabilities": ["research"]}],
        **change,
    }
    (tmp_path / "test-peer.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr(presets, "USER_PRESETS_DIR", tmp_path)
    with pytest.raises(ValueError, match=message):
        presets.build_run_from_preset("test-peer", {"topic": "test"})


def test_legacy_presets_keep_dag_execution_by_default():
    run = presets.build_run_from_preset("investment_committee", {"market": "US", "target": "test"})
    assert run.execution_mode == "dag"
    assert all(t.agent_id for t in run.tasks)


@pytest.fixture
def api(tmp_path, monkeypatch):
    store = SwarmStore(tmp_path)
    runtime = runtime_module.SwarmRuntime(store)
    monkeypatch.setattr(routes, "_swarm_runtime", runtime)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(
        runtime_module, "run_worker", lambda **kwargs: WorkerResult(status="completed", summary="API result")
    )
    return TestClient(api_server.app, client=("127.0.0.1", 50000)), store, runtime


def seed(store, *, mode="autonomous"):
    run = SwarmRun(
        id="api-peer",
        preset_name="custom",
        execution_mode=mode,
        created_at="2026-10-19T00:00:00+00:00",
        final_task_id="report",
        agents=[SwarmAgentSpec(id="analyst", role="Research", system_prompt="x", capabilities=["research"])],
        tasks=[SwarmTask(id="report", prompt_template="x", required_capabilities=["research"])],
    )
    store.create_run(run)
    if mode == "autonomous":
        Blackboard(store.run_dir(run.id)).initialize(run, max_workers=1)
    return run


def test_recover_route_runs_existing_work_and_exposes_public_claim_metadata(api):
    client, store, runtime = api
    run = seed(store)
    response = client.post(f"/swarm/runs/{run.id}/recover")
    assert response.status_code == 200
    assert response.json()["id"] == run.id
    deadline = time.monotonic() + 3
    while store.load_run(run.id).status == "running" and time.monotonic() < deadline:
        threading.Event().wait(0.02)
    response = client.get(f"/swarm/runs/{run.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["execution_mode"] == "autonomous"
    assert data["tasks"][0]["attempt"] == 1
    assert data["tasks"][0]["required_capabilities"] == ["research"]
    assert data["tasks"][0]["lease_expires_at"] is None
    assert "prompt_template" not in data["tasks"][0]


def test_recover_route_rejects_missing_dag_terminal_and_duplicate_owners(api):
    client, store, runtime = api
    assert client.post("/swarm/runs/missing/recover").status_code == 404
    run = seed(store, mode="dag")
    assert client.post(f"/swarm/runs/{run.id}/recover").status_code == 409


def test_recover_route_is_authenticated(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(api_server, "_API_KEY", "a-test-secret")
    response = client.post("/swarm/runs/missing/recover")
    assert response.status_code in {401, 403}


def test_autonomous_sse_cursor_reads_durable_events_without_duplicate_replay(api):
    client, store, _ = api
    run = seed(store)
    board = Blackboard(store.run_dir(run.id))
    board.complete(board.claim("analyst"), WorkerResult(status="completed", summary="done"))
    response = client.get(f"/swarm/runs/{run.id}/events", headers={"Last-Event-ID": "2"})
    assert response.status_code == 200
    assert "event: run_started" not in response.text
    assert "event: task_started" not in response.text
    assert "id: 3\nevent: task_completed" in response.text
    assert 'event: done\ndata: {"status": "completed"}' in response.text


def test_cli_inspection_shows_capability_requirements_instead_of_blank_owners(monkeypatch):
    from src.interfaces.cli import _legacy

    output = StringIO()
    monkeypatch.setattr(_legacy, "console", Console(file=output, width=160, color_system=None))
    assert _legacy.cmd_swarm_inspect("autonomous_research_team") == 0
    rendered = output.getvalue()
    assert "Autonomous Task Dependencies" in rendered
    assert "requires: research" in rendered
    assert "requires: synthesis" in rendered


def test_sse_drains_committed_terminal_events_before_done(api, monkeypatch):
    client, store, _ = api
    run = seed(store)
    board = Blackboard(store.run_dir(run.id))
    lease = board.claim("analyst")
    read_events = store.read_events
    completed = False

    def finish_between_event_and_status_reads(run_id, after_index=0):
        nonlocal completed
        captured = read_events(run_id, after_index)
        if not completed:
            completed = True
            board.complete(lease, WorkerResult(status="completed", summary="done"))
        return captured

    monkeypatch.setattr(store, "read_events", finish_between_event_and_status_reads)
    response = client.get(f"/swarm/runs/{run.id}/events")
    assert response.status_code == 200
    assert "id: 3\nevent: task_completed" in response.text
    assert "id: 4\nevent: run_completed" in response.text
    assert response.text.index("event: run_completed") < response.text.index("event: done")

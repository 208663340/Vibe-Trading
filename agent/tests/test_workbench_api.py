"""External clients and report readers share one agent-free workbench."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_workbench_experiments import PROGRAM


def app_factory():
    try:
        return importlib.import_module("src.interfaces.workbench_server").create_app
    except ModuleNotFoundError as exc:
        pytest.fail(f"Standalone workbench API is missing: {exc}")


def test_api_executes_program_and_reads_snapshot_report(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "strategy.py").write_text(PROGRAM)
    app = app_factory()(root=tmp_path / "state", workspace=workspace)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/experiments", json={"source_dir": str(workspace), "name": "API experiment", "files": ["strategy.py"], "config": {"mode": "program", "entrypoint": "strategy.py"}})
        assert response.status_code == 202, response.text
        item = response.json()
        run_id = item["run_id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = client.get(f"/api/experiments/{run_id}").json()
            if status["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert status["status"] == "success", status
        report = client.get(f"/runs/{run_id}?chart_payload=summary")
        assert report.status_code == 200, report.text
        assert report.json()["metrics"]["final_value"] == 100
        assert report.json()["experiment"]["name"] == "API experiment"
        assert client.get(f"/runs/{run_id}/code").json()["strategy.py"] == PROGRAM
        assert client.get("/runs").json()[0]["run_id"] == run_id
        assert client.get("/api/experiments").json()[0]["run_id"] == run_id
        assert client.get("/sessions").status_code == 404


def test_api_rejects_foreign_browser_and_workspace_escape(tmp_path):
    app = app_factory()(root=tmp_path / "state", workspace=tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/experiments", json={}, headers={"origin": "https://evil.example"})
        assert response.status_code == 403
        assert client.get("/api/workbench", headers={"host": "evil.example"}).status_code == 403
        response = client.post("/api/experiments", json={"source_dir": "/", "files": ["strategy.py"], "config": {"mode": "program", "entrypoint": "strategy.py"}})
        assert response.status_code == 400


def test_workbench_imports_and_startup_do_not_load_agent_modules(tmp_path):
    script = '''import importlib.abc,sys
class DenyAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("src.runtime", "src.interfaces.cli", "langchain", "langgraph", "pi_agent")):
            raise AssertionError("Internal Agent imported: " + fullname)
sys.meta_path.insert(0,DenyAgent())
from pathlib import Path
from fastapi.testclient import TestClient
from src.interfaces.workbench_server import create_app
from src.interfaces.workbench_cli import main
from src.interfaces.workbench_mcp import build_server
app=create_app(root=Path(sys.argv[1])/"state",workspace=Path(sys.argv[1]))
with TestClient(app,base_url="http://localhost") as client:
    assert client.get("/api/workbench").status_code==200
assert build_server() is not None
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(__import__('pathlib').Path(__file__).resolve().parents[1])}, timeout=30)
    assert result.returncode == 0, result.stderr


def test_builtin_path_validation_does_not_load_agent_modules(tmp_path):
    script = '''import importlib.abc,sys,os
class DenyAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("src.runtime"):
            raise AssertionError("Internal Agent imported: " + fullname)
sys.meta_path.insert(0,DenyAgent())
from pathlib import Path
from src.backtest.runner import main
run=Path(sys.argv[1])
os.environ["VIBE_TRADING_ALLOWED_RUN_ROOTS"]=str(run)
try:
    main(run)
except SystemExit as exc:
    assert exc.code==1
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, env={**os.environ,"PYTHONPATH":str(__import__('pathlib').Path(__file__).resolve().parents[1])}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "config.json not found" in result.stdout


def test_workbench_serves_ui_boot_assets_and_legacy_deep_link(tmp_path, monkeypatch):
    from src.interfaces import workbench_server
    dist=tmp_path / 'dist'
    (dist/'fonts').mkdir(parents=True)
    (dist/'assets').mkdir()
    (dist/'index.html').write_text('<html>Workbench</html>')
    (dist/'theme-boot.js').write_text('/* theme boot */')
    (dist/'favicon.svg').write_text('<svg/>')
    (dist/'fonts'/'test.woff2').write_bytes(b'font')
    monkeypatch.setattr(workbench_server,'_FRONTEND_DIST',dist)
    with TestClient(workbench_server.create_app(root=tmp_path/'state',workspace=tmp_path),base_url='http://localhost') as client:
        assert client.get('/theme-boot.js').status_code==200
        assert client.get('/fonts/test.woff2').content==b'font'
        assert client.get('/favicon.svg').status_code==200
        assert client.get('/agent',headers={'accept':'text/html'}).status_code==200
        assert client.get('/sessions').status_code==404

"""Real CLI and MCP clients use one HTTP owner and one offline report."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from fastmcp import Client
from src.interfaces.workbench_mcp import build_server

ROOT = Path(__file__).resolve().parents[2]


def test_cli_and_mcp_share_persisted_offline_experiment(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port=sock.getsockname()[1]
    url=f"http://127.0.0.1:{port}"
    env={**os.environ,"PYTHONPATH":str(ROOT / "agent")}
    command=[sys.executable,"-m","src.interfaces.workbench_cli"]
    log=tmp_path / "service.log"
    with log.open("w") as stream:
        server=subprocess.Popen([*command,"serve","--workspace",str(ROOT),"--root",str(tmp_path / "state"),"--port",str(port)],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                try:
                    if httpx.get(url+"/api/workbench",trust_env=False).status_code==200: break
                except httpx.HTTPError: pass
                assert server.poll() is None, log.read_text()
                time.sleep(0.1)
            else: raise AssertionError(log.read_text())
            def cli(*args):
                output=subprocess.run([*command,"--url",url,*args],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
                assert output.returncode==0,output.stdout+output.stderr
                return json.loads(output.stdout)
            item=cli("submit","examples/workbench/experiment.json")
            run_id=item["run_id"]
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                current=cli("status",run_id)
                if current["status"] not in {"queued","running"}:break
                time.sleep(0.05)
            assert current["status"]=="success",current
            assert cli("result",run_id)["metrics"]["final_value"]==1025
            assert "Synthetic offline demo complete" in cli("logs",run_id)["text"]
            assert cli("experiments","--project","offline-demo")[0]["run_id"]==run_id
            assert cli("cancel",run_id)["status"]=="success"
            async def via_mcp():
                async with Client(build_server(url)) as client:
                    names={tool.name for tool in await client.list_tools()}
                    assert names=={"workbench_info","submit_experiment","list_experiments","get_experiment","get_experiment_result","get_experiment_logs","cancel_experiment"}
                    result=await client.call_tool("get_experiment",{"run_id":run_id})
                    assert result.data["run_id"]==run_id
                    assert result.data["status"]=="success"
                    report=await client.call_tool("get_experiment_result",{"run_id":run_id})
                    assert report.data["metrics"]["final_value"]==1025
            asyncio.run(via_mcp())
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill();server.wait()

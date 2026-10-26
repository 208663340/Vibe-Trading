"""MCP adapter for the separately running, persistent local workbench."""
from __future__ import annotations

from urllib.parse import quote

from fastmcp import FastMCP
from src.interfaces.workbench_client import WorkbenchClient


def build_server(url: str | None = None) -> FastMCP:
    server = FastMCP("Quant Research Workbench")
    client = WorkbenchClient(url)

    @server.tool
    def workbench_info() -> dict:
        """Read the configured workspace, storage root and execution capabilities."""
        return client.call("GET", "/api/workbench")

    @server.tool
    def submit_experiment(source_dir: str, files: list[str], config: dict, name: str, project: str = "default", parent_run_id: str | None = None, change_note: str = "", timeout_seconds: int = 900) -> dict:
        """Snapshot selected local files and queue a run. Return its stable ID.

        source_dir must be inside the operator-selected workspace. Include the
        entrypoint, imported project modules and data in files. Program config
        names mode='program', entrypoint and optional string args. The Python
        program writes its report CSVs into RUN_DIR/artifacts. The workbench
        owns execution even after this MCP connection closes.
        """
        return client.call("POST", "/api/experiments", {"source_dir": source_dir, "files": files, "config": config, "name": name, "project": project, "parent_run_id": parent_run_id, "change_note": change_note, "timeout_seconds": timeout_seconds})

    @server.tool
    def list_experiments(project: str | None = None) -> list[dict]:
        """List persisted attempts, including failed and interrupted experiments."""
        return client.call("GET", "/api/experiments" + ("?project=" + quote(project, safe="") if project else ""))

    @server.tool
    def get_experiment(run_id: str) -> dict:
        """Read execution state, captured inputs, provenance and result metrics."""
        return client.call("GET", "/api/experiments/" + quote(run_id, safe=""))

    @server.tool
    def get_experiment_result(run_id: str) -> dict:
        """Read the same saved report and evidence as the Web workbench."""
        return client.call("GET", "/runs/" + quote(run_id, safe="") + "?chart_payload=summary")

    @server.tool
    def get_experiment_logs(run_id: str) -> dict:
        """Read a bounded log tail. A truncated response explicitly says so."""
        return client.call("GET", "/api/experiments/" + quote(run_id, safe="") + "/logs")

    @server.tool
    def cancel_experiment(run_id: str) -> dict:
        """Request cancellation; poll until the owner records a terminal state."""
        return client.call("POST", "/api/experiments/" + quote(run_id, safe="") + "/cancel")

    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="External-agent research tools (stdio)")
    parser.add_argument("--url", default="http://127.0.0.1:8899")
    args = parser.parse_args()
    build_server(args.url).run(transport="stdio")


if __name__ == "__main__":
    main()

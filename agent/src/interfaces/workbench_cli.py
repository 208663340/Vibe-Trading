"""Deterministic research commands used by people and external Code Agents."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

from src.interfaces.workbench_client import WorkbenchClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quant-workbench", description="Local quant research workbench for external Code Agents")
    parser.add_argument("--url", default="http://127.0.0.1:8899", help="Local workbench API address")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local research API and report UI")
    serve.add_argument("--workspace", type=Path, default=Path.cwd())
    serve.add_argument("--root", type=Path)
    serve.add_argument("--port", type=int, default=8899)
    serve.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    serve.add_argument("--workers", type=int, choices=range(1, 9), default=2)
    submit = commands.add_parser("submit", help="Snapshot inputs and queue an experiment")
    submit.add_argument("manifest", type=Path)
    listing = commands.add_parser("experiments", help="List persisted experiments")
    listing.add_argument("--project")
    for name in ("status", "result", "logs", "cancel"):
        commands.add_parser(name).add_argument("run_id")
    commands.add_parser("info")
    commands.add_parser("mcp", help="Expose workbench tools over stdio")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            import uvicorn
            from src.interfaces.workbench_server import create_app
            uvicorn.run(create_app(root=args.root, workspace=args.workspace, workers=args.workers), host=args.host, port=args.port, log_level="info")
            return 0
        if args.command == "mcp":
            from src.interfaces.workbench_mcp import build_server
            build_server(args.url).run(transport="stdio")
            return 0
        client = WorkbenchClient(args.url)
        if args.command == "submit":
            manifest_path = args.manifest.expanduser().resolve()
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest must be a JSON object")
            source = Path(payload.get("source_dir", "."))
            payload["source_dir"] = str((manifest_path.parent / source).resolve())
            result = client.call("POST", "/api/experiments", payload)
        elif args.command == "experiments":
            suffix = "?project=" + quote(args.project, safe="") if args.project else ""
            result = client.call("GET", "/api/experiments" + suffix)
        elif args.command == "info":
            result = client.call("GET", "/api/workbench")
        else:
            run_id = quote(args.run_id, safe="")
            path = f"/api/experiments/{run_id}"
            method = "GET"
            if args.command == "cancel":
                method, path = "POST", path + "/cancel"
            elif args.command == "logs":
                path += "/logs"
            elif args.command == "result":
                path = f"/runs/{run_id}?chart_payload=summary"
            result = client.call(method, path)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

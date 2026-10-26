"""Standalone local research API. No conversation or model runtime is started."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from src.interfaces.api import models
from src.interfaces.api.attribution_routes import register_attribution_routes
from src.interfaces.api.helpers import _FRONTEND_DIST, _spa_html_deep_link_fallback
from src.interfaces.api.runs_routes import register_runs_routes
from src.interfaces.api.security import (
    _CORS_ORIGINS, _apply_security_headers, _reject_untrusted_loopback_host,
    require_auth,
)
from src.platform.config.paths import get_runtime_root
from src.research.experiments.service import ExperimentService


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_dir: str = "."
    files: list[str] = Field(min_length=1, max_length=512)
    config: dict
    name: str = Field(default="Untitled experiment", min_length=1, max_length=256)
    project: str = Field(default="default", min_length=1, max_length=256)
    parent_run_id: str | None = None
    change_note: str = Field(default="", max_length=4000)
    timeout_seconds: float = Field(default=900, ge=1, le=86400, allow_inf_nan=False)


def create_app(*, root: Path | None = None, workspace: Path | None = None, workers: int = 2) -> FastAPI:
    service = ExperimentService(root or get_runtime_root(), workspace or Path.cwd(), workers=workers)

    @asynccontextmanager
    async def lifespan(app):
        service.start()
        try:
            yield
        finally:
            await asyncio.to_thread(service.close)

    app = FastAPI(title="Quant Research Workbench", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.experiments = service
    app.add_middleware(CORSMiddleware, allow_origins=[*_CORS_ORIGINS, "http://localhost:5899", "http://127.0.0.1:5899"], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    app.middleware("http")(_reject_untrusted_loopback_host)
    app.middleware("http")(_spa_html_deep_link_fallback)
    app.middleware("http")(_apply_security_headers)

    @app.exception_handler(FileNotFoundError)
    async def missing(_request, _exc):
        return JSONResponse(status_code=404, content={"detail": "Experiment or input file was not found"})

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def conflict(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def validate(value: str, _kind: str):
        try:
            service.path(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    host = SimpleNamespace(RUNS_DIR=service.runs, _validate_path_param=validate, **{name: getattr(models, name) for name in ("RunResponse", "RunInfo")})
    register_runs_routes(app, require_auth, host=host)
    register_attribution_routes(app, require_auth, host=host)

    @app.get("/api/workbench", dependencies=[Depends(require_auth)])
    async def info():
        try:
            app_version = version("vibe-trading-ai")
        except PackageNotFoundError:
            app_version = "development"
        return {"version": app_version, "workspace": str(service.workspace), "runs_dir": str(service.runs), "workers": service.workers, "execution_modes": ["program", "builtin"], "snapshot_limit_bytes": 100 * 1024 * 1024, "model_required": False}

    @app.get("/api/experiments", dependencies=[Depends(require_auth)])
    async def experiments(project: str | None = None):
        return await run_in_threadpool(service.list, project)

    @app.post("/api/experiments", status_code=202, dependencies=[Depends(require_auth)])
    async def submit(request: Submission):
        return await run_in_threadpool(service.submit, request.model_dump())

    @app.get("/api/experiments/{run_id}", dependencies=[Depends(require_auth)])
    async def experiment(run_id: str):
        return await run_in_threadpool(service.get, run_id)

    @app.post("/api/experiments/{run_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel(run_id: str):
        return service.cancel(run_id)

    @app.get("/api/experiments/{run_id}/logs", dependencies=[Depends(require_auth)])
    async def logs(run_id: str):
        service.get(run_id)
        path = service.path(run_id) / "logs" / "worker.log"
        if not path.exists():
            return {"text": "", "truncated": False}
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - 64000))
            text = stream.read(64000).decode("utf-8", errors="replace")
        return {"text": text, "truncated": size > 64000}

    # Unknown API paths must stay 404, rather than returning the SPA HTML.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE"])
    async def unknown_api(path: str):
        raise HTTPException(status_code=404, detail="Unknown workbench endpoint")

    if _FRONTEND_DIST.is_dir():
        for route in ("/", "/reports", "/compare", "/settings", "/about", "/agent"):
            from fastapi.responses import FileResponse
            async def page():
                return FileResponse(_FRONTEND_DIST / "index.html")
            app.add_api_route(route, page, include_in_schema=False)
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse
        for directory in ("assets", "fonts"):
            if (_FRONTEND_DIST / directory).is_dir():
                app.mount(f"/{directory}", StaticFiles(directory=_FRONTEND_DIST / directory), name=directory)
        for filename in ("favicon.svg", "logo.svg", "theme-boot.js"):
            if (_FRONTEND_DIST / filename).is_file():
                def static_response(path=_FRONTEND_DIST / filename):
                    async def asset():
                        return FileResponse(path)
                    return asset
                app.add_api_route(f"/{filename}", static_response(), include_in_schema=False)
    return app

"""Own local experiment processes and their immutable submitted inputs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE = frozenset({"queued", "running", "cancelling"})
_ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_MAX_INPUT_BYTES = 100 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


class ExperimentService:
    """One process owner per local storage root; API clients never own jobs.

    Selected source/data files are copied before admission. A retry is a new run,
    never an overwrite. Restarted owners mark unfinished attempts interrupted:
    this does not assert that an orphaned external program has stopped.
    """

    def __init__(self, root: Path, workspace: Path, *, workers: int = 2):
        self.root = Path(root).resolve()
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if not 1 <= workers <= 8:
            raise ValueError("workers must be between 1 and 8")
        self.runs = self.root / "runs"
        self.workers = workers
        self._guard = threading.RLock()
        self._events: dict[str, threading.Event] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._lock_file = None
        self._closing = False

    def start(self):
        with self._guard:
            if self._pool is not None:
                return self
            self.runs.mkdir(parents=True, exist_ok=True)
            lock = (self.root / "workbench.lock").open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    lock.write(b"0")
                    lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                lock.close()
                raise RuntimeError("a workbench already owns this storage root") from exc
            self._lock_file = lock
            try:
                for item in self.list():
                    if item.get("status") in ACTIVE:
                        self._update(item["run_id"], status="interrupted", reason="Service restarted; prior process outcome is unknown. Inspect artifacts before retrying.", finished_at=utc_now())
                self._closing = False
                self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="experiment")
            except BaseException:
                lock.close()
                self._lock_file = None
                raise
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._guard:
            self._closing = True
            pool = self._pool
            for event in self._events.values():
                event.set()
        if pool is not None:
            pool.shutdown(wait=True)
        with self._guard:
            self._pool = None
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None

    def path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        path = self.runs / run_id
        if path.is_symlink() or not path.resolve().is_relative_to(self.runs):
            raise ValueError("run directory escapes storage root")
        return path

    def get(self, run_id: str) -> dict:
        with self._guard:
            return read_json(self.path(run_id) / "experiment.json")

    def list(self, project: str | None = None) -> list[dict]:
        with self._guard:
            results = []
            for path in self.runs.glob("*/experiment.json"):
                if path.parent.is_symlink() or path.parent.name.startswith("."):
                    continue
                try:
                    item = self.get(path.parent.name)
                    if item.get("run_id") != path.parent.name:
                        continue
                    if project is None or item.get("project") == project:
                        results.append(item)
                except (OSError, ValueError):
                    continue
            return sorted(results, key=lambda x: x.get("created_at", ""), reverse=True)

    def _update(self, run_id: str, **changes) -> dict:
        with self._guard:
            item = self.get(run_id)
            item.update(changes, updated_at=utc_now())
            run = self.path(run_id)
            write_json(run / "state.json", {"status": item["status"], "reason": item.get("reason", "")})
            write_json(run / "experiment.json", item)
            return item

    def submit(self, request: dict[str, Any]) -> dict:
        config = request.get("config")
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        config = json.loads(json.dumps(config, allow_nan=False))
        mode = config.get("mode", "program")
        if mode not in {"program", "builtin"}:
            raise ValueError("mode must be program or builtin")
        source = Path(request.get("source_dir") or self.workspace).expanduser()
        if not source.is_absolute():
            source = self.workspace / source
        source = source.resolve()
        if not source.is_dir() or not source.is_relative_to(self.workspace):
            raise ValueError("source_dir must be inside the configured workspace")
        files = request.get("files")
        if not isinstance(files, list) or not files or len(files) > 512 or any(not isinstance(f, str) for f in files):
            raise ValueError("files must explicitly list 1–512 source and input files")
        entry = config.get("entrypoint", "strategy.py") if mode == "program" else "code/signal_engine.py"
        if not isinstance(entry, str) or entry not in files or not entry.endswith(".py"):
            raise ValueError("files must include the Python entrypoint")
        args = config.get("args", [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ValueError("args must contain strings")
        timeout = request.get("timeout_seconds", 900)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 86400:
            raise ValueError("timeout_seconds must be between 1 and 86400")
        name = request.get("name", "Untitled experiment")
        project = request.get("project", "default")
        if any(not isinstance(x, str) or not x.strip() or len(x) > 256 for x in (name, project)):
            raise ValueError("name and project must be nonempty strings up to 256 characters")
        parent = request.get("parent_run_id")
        if parent is not None:
            self.get(parent)
        with self._guard:
            if self._pool is None or self._closing:
                raise RuntimeError("workbench is not accepting experiments")
            if len(self._events) >= 64:
                raise RuntimeError("experiment queue is full")
            run_id = "run_" + uuid.uuid4().hex[:24]
            run = self.path(run_id)
            staging = self.runs / (".pending_" + run_id)
            staging.mkdir()
            try:
                records = []
                total = 0
                for filename in dict.fromkeys(files):
                    rel = Path(filename)
                    if rel.is_absolute() or ".." in rel.parts or any(p.startswith(".") or p.lower() in {"node_modules", "__pycache__"} for p in rel.parts):
                        raise ValueError(f"unsupported snapshot path: {filename}")
                    original = source / rel
                    if not original.resolve().is_relative_to(source) or any((source / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
                        raise ValueError(f"snapshot symlinks are not supported: {filename}")
                    if not original.is_file():
                        raise ValueError(f"snapshot input is not a file: {filename}")
                    if original.stat().st_size + total > _MAX_INPUT_BYTES:
                        raise ValueError("snapshot exceeds 100 MiB; select a bounded dataset")
                    content = original.read_bytes()
                    total += len(content)
                    if total > _MAX_INPUT_BYTES:
                        raise ValueError("snapshot exceeds 100 MiB")
                    target = staging / "source" / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    records.append({"path": rel.as_posix(), "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)})
                for folder in ("artifacts", "logs", "code"):
                    (staging / folder).mkdir(exist_ok=True)
                config["mode"] = mode
                if mode == "program":
                    config.update(program_root=str(run / "source"), entrypoint=entry)
                else:
                    shutil.copy2(staging / "source" / entry, staging / "code" / "signal_engine.py")
                write_json(staging / "config.json", config)
                write_json(staging / "req.json", {"prompt": name, "context": {"project": project, "source": "external_agent"}})
                now = utc_now()
                item = {"schema_version": 1, "run_id": run_id, "name": name.strip(), "project": project.strip(), "parent_run_id": parent, "change_note": str(request.get("change_note", ""))[:4000], "status": "queued", "created_at": now, "updated_at": now, "timeout_seconds": timeout, "execution_source": mode, "files": records, "config": config, "environment": {"python": sys.version.split()[0]}, "report_url": f"/runs/{run_id}?view=dashboard", "snapshot_scope": "explicit_files"}
                write_json(staging / "experiment.json", item)
                write_json(staging / "state.json", {"status": "queued"})
                os.replace(staging, run)
            except BaseException:
                shutil.rmtree(staging)
                raise
            event = threading.Event()
            self._events[run_id] = event
            self._pool.submit(self._execute, run_id, event)
            return item

    def cancel(self, run_id: str) -> dict:
        with self._guard:
            item = self.get(run_id)
            if item["status"] not in ACTIVE:
                return item
            event = self._events.get(run_id)
            if event is None:
                return self._update(run_id, status="interrupted", reason="No active owner; process outcome is unknown")
            event.set()
            return self._update(run_id, status="cancelling")

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                # Do not reap the leader yet: its exit says nothing about
                # descendants, and keeping its PID reserved prevents reuse.
                time.sleep(0.25)
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin reports EPERM for an empty process group after its
                # leader exits. We have still attempted group-wide SIGKILL,
                # including when a descendant outlives the leader. Never
                # swallow a denial for a live leader or on other platforms.
                if sys.platform != "darwin" or process.poll() is None:
                    raise
        process.wait()

    def _execute(self, run_id: str, event: threading.Event):
        run = self.path(run_id)
        try:
            with self._guard:
                if event.is_set():
                    self._update(run_id, status="interrupted" if self._closing else "cancelled", finished_at=utc_now())
                    return
                self._update(run_id, status="running", started_at=utc_now())
            from src.platform.core.runner import Runner
            env = Runner()._build_runtime_env(run, pythonpath_extra=Path(__file__).resolve().parents[3])
            with (run / "logs" / "worker.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "src.research.experiments.worker", str(run)], cwd=run, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
                deadline = time.monotonic() + self.get(run_id)["timeout_seconds"]
                while process.poll() is None:
                    if event.wait(0.05) or time.monotonic() >= deadline:
                        timed_out = not event.is_set()
                        self._stop(process)
                        state = "failed" if timed_out else ("interrupted" if self._closing else "cancelled")
                        self._update(run_id, status=state, reason="Experiment timed out" if timed_out else "Execution stopped", finished_at=utc_now())
                        return
            result = read_json(run / "execution.json") if (run / "execution.json").exists() else {"status": "failed", "reason": f"Worker exited with code {process.returncode}; inspect worker.log"}
            with self._guard:
                if event.is_set():
                    result = {"status": "interrupted" if self._closing else "cancelled", "reason": "Execution stopped"}
                elif process.returncode != 0:
                    result["status"] = "failed"
                self._update(run_id, **result, finished_at=utc_now())
        except Exception as exc:
            self._update(run_id, status="failed", reason=f"{type(exc).__name__}: {exc}", finished_at=utc_now())
        finally:
            with self._guard:
                self._events.pop(run_id, None)

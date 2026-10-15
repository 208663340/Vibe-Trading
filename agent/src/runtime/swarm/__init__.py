"""Swarm multi-agent system — package entry point."""

from __future__ import annotations

from src.runtime.swarm.models import (
    RunStatus,
    SwarmAgentSpec,
    SwarmEvent,
    SwarmRun,
    SwarmTask,
    TaskStatus,
    WorkerResult,
)
from src.runtime.swarm.presets import build_run_from_preset, inspect_preset, list_presets, load_preset
from src.runtime.swarm.runtime import SwarmRuntime
from src.runtime.swarm.store import SwarmStore
from src.runtime.swarm.worker import run_worker

__all__ = [
    "RunStatus",
    "SwarmAgentSpec",
    "SwarmEvent",
    "SwarmRun",
    "SwarmRuntime",
    "SwarmStore",
    "SwarmTask",
    "TaskStatus",
    "WorkerResult",
    "build_run_from_preset",
    "inspect_preset",
    "list_presets",
    "load_preset",
    "run_worker",
]

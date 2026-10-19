"""Peer admission rules, independent of provider execution and storage."""

from dataclasses import dataclass

from src.runtime.swarm.models import SwarmAgentSpec, SwarmRun, SwarmTask
from src.runtime.swarm.task_store import validate_dag


@dataclass(frozen=True)
class TaskLease:
    task: SwarmTask
    agent_id: str
    epoch: int


def eligible(agent: SwarmAgentSpec, task: SwarmTask) -> bool:
    return (not task.agent_id or task.agent_id == agent.id) and set(task.required_capabilities).issubset(
        agent.capabilities
    )


def validate_autonomous_run(run: SwarmRun) -> None:
    if not 1 <= len(run.agents) <= 32:
        raise ValueError("Autonomous runs require 1 to 32 agents")
    if not 1 <= len(run.tasks) <= 256:
        raise ValueError("Autonomous runs require 1 to 256 tasks")
    for label, ids in (("agent", [a.id for a in run.agents]), ("task", [t.id for t in run.tasks])):
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate {label} id")
        if any(not name.strip() or name in {".", ".."} or "/" in name or "\\" in name for name in ids):
            raise ValueError(f"Each {label} id must be one safe path segment")
    validate_dag(run.tasks)
    if run.final_task_id not in {t.id for t in run.tasks}:
        raise ValueError("Autonomous final_task_id must identify an existing task")
    for task in run.tasks:
        if not any(eligible(agent, task) for agent in run.agents):
            raise ValueError(f"Task {task.id!r} has no eligible agent")
        if not set(task.input_from.values()).issubset(task.depends_on):
            raise ValueError(f"Task {task.id!r}: input_from must reference declared dependencies")

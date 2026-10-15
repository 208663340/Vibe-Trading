import {
  applySwarmEvent,
  buildSwarmStatusFromStarted,
  buildSwarmStatusFromToolResultPreview,
} from "../swarmStatus";

describe("swarmStatus server timestamps", () => {
  it("leaves the run start unset until a server timestamp arrives", () => {
    const nowSpy = vi.spyOn(Date, "now").mockImplementation(() => {
      throw new Error("client clock must not seed swarm status");
    });

    const initial = buildSwarmStatusFromStarted({
      run_id: "run-1",
      status: "pending",
      tasks: [],
    })!;
    expect(initial.startedAt).toBe(0);

    const started = applySwarmEvent(initial, {
      type: "run_started",
      timestamp: "2026-07-29T12:00:00Z",
    });
    expect(started.startedAt).toBe(Date.parse("2026-07-29T12:00:00Z"));
    expect(nowSpy).not.toHaveBeenCalled();
  });

  it("derives agent elapsed time only from paired server event timestamps", () => {
    const initial = buildSwarmStatusFromStarted({
      run_id: "run-2",
      status: "running",
      agents: [{ id: "analyst" }],
      tasks: [{ id: "task-1", agent_id: "analyst", status: "pending" }],
    })!;
    const started = applySwarmEvent(initial, {
      type: "task_started",
      agent_id: "analyst",
      task_id: "task-1",
      timestamp: "2026-07-29T12:00:00Z",
    });
    const completed = applySwarmEvent(started, {
      type: "task_completed",
      agent_id: "analyst",
      task_id: "task-1",
      timestamp: "2026-07-29T12:00:05Z",
    });
    const missingCompletionTimestamp = applySwarmEvent(started, {
      type: "task_completed",
      agent_id: "analyst",
      task_id: "task-1",
    });

    expect(completed.agents[0].elapsed_s).toBe(5);
    expect(missingCompletionTimestamp.agents[0].elapsed_s).toBeUndefined();
  });

  it("seeds parsed and truncated previews only from serialized server times", () => {
    const parsed = buildSwarmStatusFromToolResultPreview(JSON.stringify({
      run_id: "run-3",
      preset: "demo",
      status: "completed",
      tasks: [{
        started_at: "2026-07-29T12:00:01Z",
        completed_at: "2026-07-29T12:00:08Z",
      }],
    }))!;
    const truncated = buildSwarmStatusFromToolResultPreview(
      '{"run_id":"run-4","preset":"demo","status":"completed","started_at":"2026-07-29T12:00:02Z","completed_at":"2026-07-29T12:00:09Z"',
    )!;

    expect(parsed.startedAt).toBe(Date.parse("2026-07-29T12:00:01Z"));
    expect(parsed.completedAt).toBe(Date.parse("2026-07-29T12:00:08Z"));
    expect(truncated.startedAt).toBe(Date.parse("2026-07-29T12:00:02Z"));
    expect(truncated.completedAt).toBe(Date.parse("2026-07-29T12:00:09Z"));
  });
});

describe("swarmStatus cancellation events", () => {
  it("marks a mid-flight worker cancelled, not failed, on task_cancelled", () => {
    const initial = buildSwarmStatusFromStarted({
      run_id: "run-cancel",
      status: "running",
      agents: [{ id: "analyst" }],
      tasks: [{ id: "task-1", agent_id: "analyst", status: "pending" }],
    })!;
    const started = applySwarmEvent(initial, {
      type: "task_started",
      agent_id: "analyst",
      task_id: "task-1",
      timestamp: "2026-08-22T09:00:00Z",
    });
    const cancelled = applySwarmEvent(started, {
      type: "task_cancelled",
      task_id: "task-1",
      timestamp: "2026-08-22T09:00:07Z",
      data: { iterations: 2 },
    });

    expect(cancelled.agents[0].status).toBe("cancelled");
    expect(cancelled.agents[0].iterations).toBe(2);
    expect(cancelled.agents[0].elapsed_s).toBe(7);
    expect(cancelled.agents[0].error).toBeUndefined();
  });

  it("maps the worker-side worker_cancelled event the same way", () => {
    const initial = buildSwarmStatusFromStarted({
      run_id: "run-cancel-2",
      status: "running",
      agents: [{ id: "analyst" }],
      tasks: [{ id: "task-1", agent_id: "analyst", status: "in_progress" }],
    })!;
    const cancelled = applySwarmEvent(initial, {
      type: "worker_cancelled",
      agent_id: "analyst",
      task_id: "task-1",
      data: { iterations: 1 },
    });
    expect(cancelled.agents[0].status).toBe("cancelled");
  });
});

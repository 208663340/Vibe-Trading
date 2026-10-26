import { request } from "@/shared/http/client";
export type ExperimentStatus = "queued" | "running" | "cancelling" | "cancelled" | "success" | "failed" | "interrupted";
export interface Experiment {
  run_id: string; name: string; project: string; status: ExperimentStatus; created_at: string;
  parent_run_id: string | null; change_note: string; execution_source: string;
  config: Record<string, unknown>; files: { path: string; sha256: string; size_bytes: number }[];
  reason?: string; metrics?: Record<string, number>; checks?: Record<string, string>;
}
export interface WorkbenchInfo { workspace: string; runs_dir: string; workers: number; model_required: boolean; version: string }
export const activeExperiment = (item: Experiment) => ["queued", "running", "cancelling"].includes(item.status);
export const workbenchApi = {
  list: (signal?: AbortSignal) => request<Experiment[]>("/api/experiments", { signal }),
  info: () => request<WorkbenchInfo>("/api/workbench"),
  cancel: (id: string) => request<Experiment>(`/api/experiments/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  logs: (id: string) => request<{ text: string; truncated: boolean }>(`/api/experiments/${encodeURIComponent(id)}/logs`),
};

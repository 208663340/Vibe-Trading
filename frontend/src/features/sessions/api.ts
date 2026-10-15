import { METADATA_TIMEOUT_MS, request } from "@/shared/http/client";

export interface SessionItem {
  session_id: string;
  title?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  last_attempt_id?: string;
}

export function listSessions() {
  return request<SessionItem[]>("/sessions", undefined, METADATA_TIMEOUT_MS);
}

export function deleteSession(sid: string) {
  return request<{ status: string }>(`/sessions/${sid}`, { method: "DELETE" });
}

export function renameSession(sid: string, title: string) {
  return request<{ status: string }>(`/sessions/${sid}`, { method: "PATCH", body: JSON.stringify({ title }) });
}

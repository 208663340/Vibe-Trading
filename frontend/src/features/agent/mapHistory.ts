import type { MessageItem } from '@/lib/api';
import type { StoredAgentMessage, AgentMessageMeta } from '@/stores/agent';
import { buildToolTimelineMessages } from '@/features/agent/timeline';
interface RuntimeIdentity {sessionId?:string; provider?:string;model?:string;reasoningEffort?:string;}
interface PendingRun {runId:string;messageId:string;attemptId?:string;timestamp:number;}
const answerId=(sid:string,m:MessageItem)=>m.linked_attempt_id ? `answer:${sid}:${m.linked_attempt_id}` : m.message_id;
export function mapHistory(sid:string,msgs:MessageItem[],toDisplayPrompt:(content:string)=>{content:string;meta?:AgentMessageMeta}) {
      const agentMsgs: StoredAgentMessage[] = [];
      const pendingRuns: PendingRun[] = [];
      let latestRuntimeIdentity: RuntimeIdentity | null = null;
      for (const m of msgs) {
        const meta = m.metadata as Record<string, unknown> | undefined;
        const runId = meta?.run_id as string | undefined;
        const metrics = meta?.metrics as Record<string, number> | undefined;
        const elapsedMs = typeof meta?.elapsed_ms === "number" ? meta.elapsed_ms : undefined;
        if (
          m.role === "assistant"
          && (
            typeof meta?.provider === "string"
            || typeof meta?.model === "string"
            || typeof meta?.reasoning_effort === "string"
          )
        ) {
          latestRuntimeIdentity = {
            sessionId: sid,
            provider: typeof meta?.provider === "string" ? meta.provider : undefined,
            model: typeof meta?.model === "string" ? meta.model : undefined,
            reasoningEffort: typeof meta?.reasoning_effort === "string"
              ? meta.reasoning_effort
              : undefined,
          };
        }
        const ts = new Date(m.created_at).getTime();
        // The reply is committed when the attempt ends, so created_at is the
        // end. The start comes from the persisted attempt start when present,
        // else is backed out of the attempt's elapsed time; only legacy rows
        // without either fall back to the first tool call.
        const startedAtSec = typeof meta?.started_at === "number" ? meta.started_at : NaN;
        const attemptStartedAt = Number.isFinite(startedAtSec) && startedAtSec > 0
          ? startedAtSec * 1000
          : elapsedMs != null
            ? ts - elapsedMs
            : undefined;
        const toolTimeline = m.role === "assistant" && meta?.status !== "waiting_user"
          ? buildToolTimelineMessages(m.tool_trail ?? [], {
              fallbackTimestamp: ts,
              idPrefix: `${m.message_id}_`,
              attemptId: m.linked_attempt_id || `${m.message_id}_attempt`,
              state: meta?.status === "failed"
                ? "failed"
                : meta?.status === "cancelled"
                  ? "stopped"
                  : "done",
              startedAt: attemptStartedAt,
              endedAt: ts,
            })
          : [];
        agentMsgs.push(...toolTimeline);
        const lastToolTimestamp = toolTimeline.length > 0
          ? toolTimeline[toolTimeline.length - 1].timestamp
          : ts - 1;
        const assistantTs = Math.max(ts, lastToolTimestamp + 1);
        if (m.role === "user") {
          const displayPrompt = toDisplayPrompt(m.content);
          agentMsgs.push({
            id: m.message_id,
            type: "user",
            content: displayPrompt.content,
            meta: displayPrompt.meta,
            timestamp: ts,
          });
        } else if (runId) {
          // Show text answer first (if non-empty), then chart card
          if (m.content && m.content !== "Strategy execution completed.") {
            agentMsgs.push({ id: answerId(sid,m), meta: {entityKey:answerId(sid,m), attemptId:m.linked_attempt_id}, type: "answer", content: m.content, elapsed_ms: elapsedMs, timestamp: assistantTs });
          }
          if (metrics && Object.keys(metrics).length > 0) {
            agentMsgs.push({ id: `report:${sid}:${m.linked_attempt_id||m.message_id}:${runId}`, type: "run_complete", content: "", runId, metrics, timestamp: assistantTs + 1 });
          } else {
            pendingRuns.push({runId, messageId:m.message_id, attemptId:m.linked_attempt_id, timestamp:assistantTs+1});
          }
        } else {
          agentMsgs.push({ id: answerId(sid,m), meta: {entityKey:answerId(sid,m), attemptId:m.linked_attempt_id}, type: "answer", content: m.content, elapsed_ms: elapsedMs, timestamp: assistantTs });
        }
      }

return {messages:agentMsgs, runtimeIdentity:latestRuntimeIdentity, pendingRuns};
}

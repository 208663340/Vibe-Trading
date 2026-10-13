import type { StoredAgentMessage } from '@/stores/agent';
export function messageKey(message: StoredAgentMessage): string {
  if (message.meta?.entityKey) return message.meta.entityKey;
  if (message.meta?.activity) return `activity:${message.meta.activity.attemptId}`;
  return message.id;
}
/** Durable identity, never text equality, joins snapshots and observed entities. */
export function reconcileHistory(snapshot: StoredAgentMessage[], current: StoredAgentMessage[], startedRevision: number): StoredAgentMessage[] {
  const rows=new Map(snapshot.map(message=>[messageKey(message),message]));
  for (const message of current) {
    const partial=message.meta?.partialAttemptId;
    if(partial && snapshot.some(row=>row.type==='answer' && row.meta?.attemptId===partial && row.content.trim())) continue;
    if ((message.meta?.revision ?? 0) > startedRevision || message.id.startsWith('local:') || message.type==='swarm_status' || partial) rows.set(messageKey(message), message);
  }
  return [...rows.values()].sort((a,b)=>a.timestamp-b.timestamp);
}
/** Unique run IDs, bounded workers; cancellation prevents queued reads starting. */
export async function enrichRuns(ids: string[], signal: AbortSignal, load: (id: string) => Promise<void>, concurrency=3) {
  const queue=[...new Set(ids)]; let next=0;
  await Promise.all(Array.from({length:Math.min(concurrency,queue.length)},async()=> {
    while (!signal.aborted && next<queue.length) { const id=queue[next++]; await load(id); }
  }));
}

export function confirmUserMessage(messages: StoredAgentMessage[], operationId: string, messageId: string, revision: number): StoredAgentMessage[] {
  const optimistic=messages.find(message=>message.type==='user' && message.meta?.operationId===operationId);
  if(!optimistic) return messages;
  return messages.filter(message=>message.id!==messageId || message===optimistic).map(message=>message===optimistic ? {...message,id:messageId,meta:{...message.meta,revision}} : message);
}

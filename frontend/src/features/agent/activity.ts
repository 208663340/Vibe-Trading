import type { ActivityState, AgentActivity, AgentState, StoredAgentMessage } from '@/stores/agent';
import { buildToolTimelineMessages } from '@/pages/agentToolTimeline';

export function activityRow(activity: AgentActivity, revision?: number): StoredAgentMessage {
  const [row]=buildToolTimelineMessages(activity.steps,{activity});
  return {...row,id:`activity:${activity.attemptId}`,meta:{activity,revision}};
}
export function finishActivity(state: AgentState, terminal: ActivityState, partialText: string, now=Date.now()): Partial<AgentState> {
  if(!state.activity) return {};
  const activity={...state.activity,state:terminal,endedAt:now};
  const revision=state.transcriptRevision+1;
  const messages=state.messages.filter(message=>message.meta?.activity?.attemptId!==activity.attemptId && message.meta?.partialAttemptId!==activity.attemptId);
  messages.push(activityRow(activity,revision));
  if(partialText.trim()) messages.push({id:`partial_${activity.attemptId}`,type:'answer',content:partialText,meta:{partialAttemptId:activity.attemptId,entityKey:`answer:${state.sessionId}:${activity.attemptId}`,revision},timestamp:now});
  return {messages,transcriptRevision:revision,activity:null,toolCalls:[],streamingText:'',reasoningTail:'',status:'idle',cancelPendingAttemptId:null,streamingSessionId:state.streamingSessionId===state.sessionId ? null : state.streamingSessionId};
}

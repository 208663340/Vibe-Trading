import { useTranslation } from 'react-i18next';
import { ShieldCheck } from 'lucide-react';
import { useAgentStore } from '@/stores/agent';
import { AgentAvatar } from '@/components/chat/AgentAvatar';
import { ActivityLine } from '@/components/chat/ActivityLine';
import { MarkdownContent } from '@/components/chat/MessageBubble';

/** High-frequency text, reasoning and tool progress terminate at this leaf. */
export function AgentStream({groundingRevision}:{groundingRevision:{attemptId:string;round:number}|null}) {
  const {t}=useTranslation();
  const text=useAgentStore(s=>s.streamingText);
  const reasoning=useAgentStore(s=>s.reasoningTail);
  const activity=useAgentStore(s=>s.activity);
  const cancelling=useAgentStore(s=>s.cancelPendingAttemptId);
  const status=useAgentStore(s=>s.status);
  if(status!=="streaming") return null;
  const round=activity?.attemptId===groundingRevision?.attemptId ? groundingRevision?.round : null;
  return <div className="flex gap-3 msg-enter">
    <AgentAvatar/>
    <div className="flex-1 min-w-0 space-y-2">
      {cancelling && <p role="status" className="px-3 text-xs text-muted-foreground">{t("agent.cancelRequestSent")}</p>}
      {activity && <ActivityLine activity={activity} reasoningTail={reasoning}/>}
      {round != null && <p role="status" aria-live="polite" className="flex items-center gap-2 px-3 text-xs text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-muted-foreground/80" aria-hidden="true"/>
        <span>{t("agent.activity.checkingFigures",{round})}</span>
      </p>}
      {text && <div aria-live="polite" aria-atomic="false"><MarkdownContent content={text} streaming showCursor/></div>}
    </div>
  </div>;
}

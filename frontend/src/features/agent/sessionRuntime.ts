import { useAgentStore, type AgentState } from '@/stores/agent';

export type OperationKind = 'send' | 'cancel' | 'history' | 'goal' | 'report' | 'recovery';
export interface OperationToken {
  sessionId: string | null;
  viewEpoch: number;
  operationId: string;
  kind: OperationKind;
  attemptId?: string;
}

export function owns(state: AgentState, token: OperationToken): boolean {
  const sameView = state.sessionId === token.sessionId && state.viewEpoch === token.viewEpoch;
  const sameOperation = state.operations[token.kind] === token.operationId;
  const sameAttempt = !token.attemptId || state.activity?.attemptId === token.attemptId;
  return sameView && sameOperation && sameAttempt;
}

/** View resources can be reopened after StrictMode's setup/cleanup/setup. */
export class SessionRuntime {
  private reads = new Map<OperationKind, AbortController>();
  private pending = new Map<string, { token: OperationToken; cancel: boolean }>();

  begin(kind: OperationKind, attemptId?: string): OperationToken {
    return useAgentStore.getState().beginOperation(kind, attemptId);
  }

  current(token: OperationToken) {
    return owns(useAgentStore.getState(), token);
  }

  commit(token: OperationToken, update: (state: AgentState) => Partial<AgentState>) {
    return useAgentStore.getState().commitOperation(token, update);
  }

  read(kind: OperationKind) {
    this.reads.get(kind)?.abort();
    const controller = new AbortController();
    this.reads.set(kind, controller);
    return controller.signal;
  }

  /** Revoke one superseded read category, preserving independent mutations. */
  fenceRead(kind: OperationKind) {
    this.begin(kind);
    this.reads.get(kind)?.abort();
    this.reads.delete(kind);
  }

  close() {
    for (const read of this.reads.values()) read.abort();
    this.reads.clear();
    useAgentStore.getState().invalidateView();
  }

  sending(token: OperationToken) {
    this.pending.set(token.operationId, { token, cancel: false });
  }

  requestPendingCancel() {
    const pending = [...this.pending.values()].find(item => this.current(item.token));
    if (!pending) return false;
    pending.cancel = true;
    return true;
  }

  acknowledged(token: OperationToken) {
    const pending = this.pending.get(token.operationId);
    if (!pending) return false;
    this.pending.delete(token.operationId);
    return pending.cancel;
  }

  rejected(token: OperationToken) {
    this.pending.delete(token.operationId);
  }
}

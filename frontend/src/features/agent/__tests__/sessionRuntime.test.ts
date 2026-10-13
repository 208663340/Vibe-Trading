import { SessionRuntime } from '../sessionRuntime';
import { useAgentStore } from '@/stores/agent';

it('fences only history ownership without revoking send, goal or exact-attempt cancel', () => {
  const store = useAgentStore.getState();
  store.reset();
  store.switchSession('A', []);
  store.startActivity('attempt');
  const runtime = new SessionRuntime();
  const history = runtime.begin('history');
  const historySignal = runtime.read('history');
  const send = runtime.begin('send');
  const goal = runtime.begin('goal');
  const cancel = runtime.begin('cancel', 'attempt');
  const viewEpoch = useAgentStore.getState().viewEpoch;
  runtime.fenceRead('history');
  expect(historySignal.aborted).toBe(true);
  expect(runtime.commit(history, () => ({ messages: [] }))).toBe(false);
  expect(useAgentStore.getState().viewEpoch).toBe(viewEpoch);
  expect(runtime.commit(send, () => ({ streamingText: 'send remains owned' }))).toBe(true);
  expect(runtime.commit(goal, () => ({ reasoningTail: 'goal remains owned' }))).toBe(true);
  expect(runtime.commit(cancel, () => ({ cancelPendingAttemptId: 'attempt' }))).toBe(true);
  expect(useAgentStore.getState().activity?.attemptId).toBe('attempt');
  expect(useAgentStore.getState().streamingText).toBe('send remains owned');
});

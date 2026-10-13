import { mergeEnrichment, reconcileHistory, stampMessages } from '../history';
import type { StoredAgentMessage } from '@/stores/agent';

const fallback: StoredAgentMessage = {
  id: 'report:A:attempt:run',
  type: 'run_complete',
  content: '',
  runId: 'run',
  timestamp: 1,
  meta: { revision: 1 },
};

it('improves a bare fallback when enrichment starts after that fallback', () => {
  const enriched = { ...fallback, metrics: { total_return: 0.3 }, equityCurve: [{ time: 'day', equity: 130 }] };
  const result = mergeEnrichment([fallback], [enriched], 1, 2);
  expect(result).toHaveLength(1);
  expect(result[0].metrics).toEqual(enriched.metrics);
  expect(result[0].equityCurve).toEqual(enriched.equityCurve);
  expect(result[0].meta?.revision).toBe(2);
});

it.each([undefined, { total_return: 0.1 }])('retains a report observed after enrichment began', metrics => {
  const current = { ...fallback, metrics: { total_return: 0.5 }, shadowId: 'shadow_fresh', meta: { revision: 3 } };
  expect(mergeEnrichment([current], [{ ...fallback, metrics }], 1, 4)).toEqual([current]);
});

it('preserves stamped authoritative recovery rows through an older snapshot merge', () => {
  const recovered = stampMessages([
    { id: 'answer:A:attempt', type: 'answer', content: 'authoritative answer', timestamp: 2 },
    { id: 'activity', type: 'thinking', content: '', timestamp: 1, meta: { activity: { attemptId: 'attempt', state: 'done', verb: 'working', steps: [], startedAt: 1, endedAt: 2 } } },
  ], 2);
  expect(reconcileHistory([], recovered, 1)).toEqual([recovered[1], recovered[0]]);
});

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import * as AgentModule from '../Agent';
import { StrictMode } from 'react';
import { useAgentStore } from '@/stores/agent';
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; }
const empty = (url: string) => url==='/settings/llm' ? json({provider:'fixture',providers:[],sse_timeout_seconds:90}) : url.endsWith('/goal') ? new Response('{}', {status:404}) : url.endsWith('/messages') ? json([]) : new Response('{}',{status:404});
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
class Events {
 static instances: Events[] = [];
 listeners = new Map<string, EventListener>(); onopen = null; onerror = null;
 private lastEventId = '';
 constructor(public url: string) { Events.instances.push(this); }
 addEventListener(name: string, handler: EventListener) { this.listeners.set(name, handler); }
 close() { this.listeners.clear(); }
 emit(name: string, data: unknown, id?: string) {
  if (id !== undefined) this.lastEventId = id;
  this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data), lastEventId: this.lastEventId }));
 }
}
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
 localStorage.clear(); useAgentStore.getState().reset(); useAgentStore.getState().cacheSession('A',[]); useAgentStore.getState().cacheSession('B',[]); Events.instances=[];
 vi.stubGlobal('EventSource', Events);
 Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() });
 fetcher=vi.fn(async (url: string) => empty(url));
 vi.stubGlobal('fetch', fetcher);
});
afterEach(() => vi.unstubAllGlobals());
async function page() { const router=createMemoryRouter([{path:'/', element:<AgentModule.Agent/>}], {initialEntries:['/?session=A']}); const view=render(<RouterProvider router={router}/>); await waitFor(() => expect(Events.instances).toHaveLength(1)); return {router,...view}; }
function send() { fireEvent.change(screen.getByRole('textbox'), {target:{value:'original prompt'}}); fireEvent.click(screen.getByRole('button',{name:'Send message'})); }
it('keeps terminal answer and optimistic prompt when older history arrives', async () => {
 const history=deferred<Response>(); fetcher.mockImplementation(async (url: string) => url==='/sessions/A/messages' ? history.promise : empty(url));
 await page();
 act(() => { useAgentStore.getState().addMessage({type:'user',content:'new optimistic',timestamp:Date.now()}); Events.instances[0].emit('attempt.completed',{attempt_id:'one', summary:'new terminal'}); });
 await act(async () => history.resolve(json([])));
 expect(useAgentStore.getState().messages.filter(m=>m.type==='answer').map(m=>m.content)).toEqual(['new terminal']);
 expect(useAgentStore.getState().messages.some(m=>m.content==='new optimistic')).toBe(true);
});
it('ignores old send rejection after A to B to A', async () => {
 const pending=deferred<Response>(); fetcher.mockImplementation(async (url: string, init?: RequestInit) => url==='/sessions/A/messages' && init?.method==='POST' ? pending.promise : empty(url));
 const {router}=await page(); send();
 await waitFor(()=>expect(fetcher.mock.calls.some(([url,init])=>url==='/sessions/A/messages' && init?.method==='POST')).toBe(true));
 await act(async () => { await router.navigate('/?session=B'); });
 expect(useAgentStore.getState().sessionId).toBe('B');
 await act(async () => { await router.navigate('/?session=A'); });
 const before=useAgentStore.getState().messages;
 await act(async () => pending.reject(new TypeError('offline')));
 expect(useAgentStore.getState().messages).toEqual(before); expect(useAgentStore.getState().status).toBe('idle');
});
it('does not cancel before send ACK and cancels the acknowledged identity', async () => {
 const pending=deferred<Response>(); fetcher.mockImplementation(async (url: string, init?: RequestInit) => url==='/sessions/A/messages' && init?.method==='POST' ? pending.promise : url.endsWith('/cancel') ? json({status:'cancelled'}) : empty(url));
 await page(); send(); fireEvent.click(screen.getByTitle('Stop generation'));
 expect(fetcher.mock.calls.filter(([url])=>String(url).endsWith('/cancel'))).toHaveLength(0);
 await act(async () => pending.resolve(json({message_id:'durable-user',attempt_id:'exact-attempt'})));
 await waitFor(() => expect(fetcher.mock.calls.filter(([url])=>String(url).endsWith('/cancel'))).toHaveLength(1));
 const call=fetcher.mock.calls.find(([url])=>String(url).endsWith('/cancel'))!;
 expect(JSON.parse(call[1].body)).toEqual({expected_attempt_id:'exact-attempt'});
 expect(useAgentStore.getState().messages.some(m=>m.id==='durable-user')).toBe(true);
});

it('keeps a terminal event authoritative when it precedes the send ACK', async()=> {
 const pending=deferred<Response>(); fetcher.mockImplementation(async(url:string,init?:RequestInit)=>url==='/sessions/A/messages' && init?.method==='POST' ? pending.promise : empty(url));
 await page(); send();
 await waitFor(()=>expect(fetcher.mock.calls.some(([url,init])=>url==='/sessions/A/messages' && init?.method==='POST')).toBe(true));
 act(()=>Events.instances[0].emit('attempt.completed',{attempt_id:'fast',summary:'finished'}));
 await act(async()=>pending.resolve(json({message_id:'ack-user',attempt_id:'fast'})));
 expect(useAgentStore.getState().status).toBe('idle'); expect(useAgentStore.getState().activity).toBeNull();
 expect(useAgentStore.getState().messages.filter(m=>m.type==='answer').map(m=>m.content)).toEqual(['finished']);
 expect(useAgentStore.getState().messages.filter(m=>m.type==='user').map(m=>m.id)).toEqual(['ack-user']);
});
it('ignores a report after navigating away and returning to A',async()=> {
 const report=deferred<Response>(); fetcher.mockImplementation(async(url:string)=>url==='/runs/run-one' ? report.promise : empty(url));
 const {router}=await page(); act(()=>Events.instances[0].emit('attempt.completed',{attempt_id:'one',summary:'terminal',run_dir:'run-one'}));
 await waitFor(()=>expect(fetcher.mock.calls.some(([url])=>url==='/runs/run-one')).toBe(true));
 await act(async()=>{await router.navigate('/?session=B');}); await act(async()=>{await router.navigate('/?session=A');});
 const before=useAgentStore.getState().messages;
 await act(async()=>report.resolve(json({metrics:{total_return:1},equity_curve:[{time:'2026-01-01',equity:1}]})));
 expect(useAgentStore.getState().messages).toEqual(before);
});
it('ignores an old cancellation ACK after a later attempt begins',async()=> {
 const cancel=deferred<Response>(); fetcher.mockImplementation(async(url:string)=>url.endsWith('/cancel') ? cancel.promise : empty(url));
 await page(); act(()=>Events.instances[0].emit('attempt.started',{attempt_id:'first'})); fireEvent.click(screen.getByTitle('Stop generation'));
 act(()=> { Events.instances[0].emit('attempt.completed',{attempt_id:'first',summary:'first done'}); Events.instances[0].emit('attempt.started',{attempt_id:'second'}); });
 await act(async()=>cancel.resolve(json({status:'cancelled'})));
 expect(useAgentStore.getState().status).toBe('streaming'); expect(useAgentStore.getState().activity?.attemptId).toBe('second');
});
it('renders base history before bounded deduplicated enrichment',async()=> {
 const reports=deferred<Response>(); const rows=Array.from({length:5},(_,i)=>({message_id:`m${i}`,role:'assistant',session_id:'A',content:`answer ${i}`,linked_attempt_id:`a${i}`,created_at:'2026-10-13T00:00:00Z',metadata:{run_id:`r${i%4}`}}));
 fetcher.mockImplementation(async(url:string)=>url==='/sessions/A/messages' ? json(rows) : url.startsWith('/runs/') ? reports.promise : empty(url));
 const {unmount}=await page(); await screen.findByText('answer 4');
 await waitFor(()=>expect(fetcher.mock.calls.filter(([url])=>String(url).startsWith('/runs/'))).toHaveLength(3));
 unmount(); await act(async()=>reports.resolve(json({}))); expect(fetcher.mock.calls.filter(([url])=>String(url).startsWith('/runs/'))).toHaveLength(3);
});
it('hydrates and sends through StrictMode, then fences callbacks after unmount',async()=> {
 const pending=deferred<Response>(); fetcher.mockImplementation(async(url:string,init?:RequestInit)=>url==='/sessions/A/messages' && init?.method==='POST' ? pending.promise : empty(url));
 const router=createMemoryRouter([{path:'/',element:<StrictMode><AgentModule.Agent/></StrictMode>}],{initialEntries:['/?session=A']});
 const view=render(<RouterProvider router={router}/>); await waitFor(()=>expect(useAgentStore.getState().sessionLoading).toBe(false)); send();
 await waitFor(()=>expect(fetcher.mock.calls.filter(([url,init])=>url==='/sessions/A/messages' && init?.method==='POST')).toHaveLength(1));
 view.unmount();const before=useAgentStore.getState().messages;
 await act(async()=>pending.reject(new TypeError('offline'))); expect(useAgentStore.getState().messages).toBe(before);
});
it('keeps the Agent page render count stable for twelve text/reasoning updates',async()=> {
 const renderSpy=vi.spyOn(AgentModule,'Agent');
 await page();act(()=>Events.instances[0].emit('attempt.started',{attempt_id:'stream'}));
 const before=renderSpy.mock.calls.length;
 for(let i=0;i<12;i++) act(()=>{useAgentStore.getState().appendDelta('word ');useAgentStore.getState().setReasoningTail(`reason ${i}`);});
 expect(renderSpy.mock.calls.length-before).toBe(0); expect(screen.getByText(/word word word/)).toBeInTheDocument(); expect(screen.getByText('reason 11')).toBeInTheDocument(); expect(useAgentStore.getState().streamingText).toBe('word '.repeat(12));
 renderSpy.mockRestore();
});
it('recovers a gap with authoritative reads and reconnects at the replacement cursor',async()=> {
 const hydrate=deferred<Response>(); let recovering=false;
 fetcher.mockImplementation(async(url:string)=> {
   if(url==='/sessions/A/messages') return recovering ? hydrate.promise : json([]);
   if(url==='/sessions/A') return json({session_id:'A',last_attempt_id:'gap-attempt'});
   if(url==='/sessions/A/attempts/gap-attempt') return json({attempt_id:'gap-attempt',status:'cancelled',started_at:1,terminal_metadata:{}});
   return empty(url);
 });
 await page(); recovering=true;act(()=> {
  Events.instances[0].emit('attempt.started',{attempt_id:'gap-attempt'},'cursor-1');
  Events.instances[0].emit('session.resync',{reason:'queue_overflow',latest_event_id:'cursor-9'});
 });
 expect(Events.instances).toHaveLength(1);
 await act(async()=>hydrate.resolve(json([])));
 await waitFor(()=>expect(Events.instances).toHaveLength(2));
 expect(Events.instances[1].url).toContain('Last-Event-ID=cursor-9'); expect(useAgentStore.getState().status).toBe('idle');
 expect(fetcher.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(0);
});
it('fences a late session creation from replacing a navigated view',async()=> {
 const create=deferred<Response>();fetcher.mockImplementation(async(url:string,init?:RequestInit)=>url==='/sessions' && init?.method==='POST' ? create.promise : empty(url));
 const router=createMemoryRouter([{path:'/',element:<AgentModule.Agent/>}],{initialEntries:['/']});render(<RouterProvider router={router}/>);send();
 await waitFor(()=>expect(fetcher.mock.calls.some(([url])=>url==='/sessions')).toBe(true));
 await act(async()=>{await router.navigate('/?session=B');});
 await act(async()=>create.resolve(json({session_id:'late-created'})));
 expect(useAgentStore.getState().sessionId).toBe('B');expect(router.state.location.search).toBe('?session=B');
 expect(fetcher.mock.calls.filter(([url,init])=>String(url).endsWith('/messages')&&init?.method==='POST')).toHaveLength(0);
});
it('keeps resync failure recoverable and retries reads without sending a user POST',async()=> {
 let reads=0; fetcher.mockImplementation(async(url:string)=> {
  if(url==='/sessions/A') {reads++;return reads===1 ? new Response('{}',{status:503}) : json({session_id:'A',last_attempt_id:'unknown'});}
  if(url==='/sessions/A/attempts/unknown') return json({attempt_id:'unknown',status:'failed',started_at:null,terminal_metadata:{}});
  return empty(url);
 });
 await page();act(()=>Events.instances[0].emit('session.resync',{reason:'cursor_expired',latest_event_id:'fenced'}));
 await screen.findByRole('alert');expect(useAgentStore.getState().status).toBe('error');expect(Events.instances).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'Retry connection'}));await waitFor(()=>expect(Events.instances).toHaveLength(2));
 expect(Events.instances[1].url).toContain('Last-Event-ID=fenced');expect(fetcher.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(0);
});
it('fences a late create-goal response after navigation',async()=> {
 const goal=deferred<Response>();fetcher.mockImplementation(async(url:string,init?:RequestInit)=>url==='/sessions/A/goal' && init?.method==='POST' ? goal.promise : empty(url));
 const {router}=await page();fireEvent.click(screen.getByTitle('More options'));fireEvent.click(screen.getByRole('menuitem',{name:'Research Goal'}));send();
 await waitFor(()=>expect(fetcher.mock.calls.some(([url,init])=>url==='/sessions/A/goal' && init?.method==='POST')).toBe(true));
 await act(async()=>{await router.navigate('/?session=B');});
 await act(async()=>goal.resolve(json({goal:{goal_id:'late-goal',objective:'old objective',status:'active'},criteria:[],evidence:[]})));
 expect(screen.queryByText('old objective')).not.toBeInTheDocument();expect(useAgentStore.getState().sessionId).toBe('B');
 expect(fetcher.mock.calls.filter(([url,init])=>String(url).endsWith('/messages') && init?.method==='POST')).toHaveLength(0);
});
it('joins terminal REST and SSE by attempt identity while retaining identical prompts',async()=> {
 const history=deferred<Response>();fetcher.mockImplementation(async(url:string)=>url==='/sessions/A/messages' ? history.promise : empty(url));
 await page();act(()=>Events.instances[0].emit('attempt.completed',{attempt_id:'join',summary:'same terminal'}));
 await act(async()=>history.resolve(json([
  {message_id:'u1',role:'user',content:'identical',created_at:'2026-10-13T00:00:00Z'},
  {message_id:'u2',role:'user',content:'identical',created_at:'2026-10-13T00:00:01Z'},
  {message_id:'reply',role:'assistant',linked_attempt_id:'join',content:'same terminal',created_at:'2026-10-13T00:00:02Z'},
 ])));
 expect(useAgentStore.getState().messages.filter(m=>m.type==='answer')).toHaveLength(1);
 expect(useAgentStore.getState().messages.filter(m=>m.meta?.activity?.attemptId==='join')).toHaveLength(1);
 expect(useAgentStore.getState().messages.filter(m=>m.type==='user')).toHaveLength(2);
});
it('keeps cancellation ACK busy until the exact backend terminal event',async()=> {
 let sends=0;fetcher.mockImplementation(async(url:string,init?:RequestInit)=> {
  if(url.endsWith('/messages')&&init?.method==='POST') {sends++;return json({message_id:`user-${sends}`,attempt_id:`attempt-${sends}`});}
  if(url.endsWith('/cancel')) return json({status:'cancelled'});
  return empty(url);
 });
 await page();send();await waitFor(()=>expect(useAgentStore.getState().activity?.attemptId).toBe('attempt-1'));
 act(()=>Events.instances[0].emit('text_delta',{attempt_id:'attempt-1',delta:'partial'}));
 fireEvent.click(screen.getByTitle('Stop generation'));
 await waitFor(()=>expect(useAgentStore.getState().cancelPendingAttemptId).toBe('attempt-1'));
 expect(useAgentStore.getState().status).toBe('streaming');expect(useAgentStore.getState().activity?.endedAt).toBeUndefined();
 expect(screen.queryByRole('button',{name:'Send message'})).not.toBeInTheDocument();expect(sends).toBe(1);
 fireEvent.click(screen.getByTitle('Stop generation'));expect(fetcher.mock.calls.filter(([url])=>String(url).endsWith('/cancel'))).toHaveLength(1);
 act(()=>Events.instances[0].emit('attempt.cancelled',{attempt_id:'attempt-1'}));
 expect(useAgentStore.getState().cancelPendingAttemptId).toBeNull();expect(useAgentStore.getState().status).toBe('idle');
 expect(useAgentStore.getState().messages.filter(m=>m.meta?.activity?.attemptId==='attempt-1')).toHaveLength(1);
 expect(useAgentStore.getState().messages.filter(m=>m.meta?.partialAttemptId==='attempt-1').map(m=>m.content)).toEqual(['partial']);
 send();await waitFor(()=>expect(sends).toBe(2));
});
it('joins a durable received user to its ACK and retains separate identical user IDs',async()=> {
 const pending=deferred<Response>();fetcher.mockImplementation(async(url:string,init?:RequestInit)=>url.endsWith('/messages')&&init?.method==='POST' ? pending.promise : empty(url));
 await page();send();await waitFor(()=>expect(fetcher.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 act(()=>Events.instances[0].emit('message.received',{role:'user',message_id:'accepted-user',content:'original prompt'}));
 await act(async()=>pending.resolve(json({message_id:'accepted-user',attempt_id:'one'})));
 expect(useAgentStore.getState().messages.filter(m=>m.type==='user')).toHaveLength(1);
 act(()=>Events.instances[0].emit('message.received',{role:'user',message_id:'other-identical-user',content:'original prompt'}));
 expect(useAgentStore.getState().messages.filter(m=>m.type==='user').map(m=>m.id)).toEqual(['accepted-user','other-identical-user']);
});
it('uses the attempt snapshot on no-active cancel and preserves waiting-user interaction',async()=> {
 fetcher.mockImplementation(async(url:string)=> {
  if(url.endsWith('/cancel')) return json({status:'no_active_loop'});
  if(url==='/sessions/A') return json({session_id:'A',last_attempt_id:'paused'});
  if(url==='/sessions/A/attempts/paused') return json({attempt_id:'paused',status:'waiting_user',started_at:1,terminal_metadata:{}});
  return empty(url);
 });
 await page();act(()=>Events.instances[0].emit('attempt.started',{attempt_id:'paused'}));fireEvent.click(screen.getByTitle('Stop generation'));
 await waitFor(()=>expect(Events.instances).toHaveLength(2));
 expect(useAgentStore.getState().status).toBe('idle');expect(useAgentStore.getState().cancelPendingAttemptId).toBeNull();
 expect(useAgentStore.getState().messages.filter(m=>m.meta?.activity?.state==='done')).toHaveLength(0);
 expect(screen.getByRole('button',{name:'Send message'})).toBeInTheDocument();
});
it('confirms a background ACK in the cache without mutating B and joins its later history',async()=> {
 const pending=deferred<Response>();let confirmed=false;
 fetcher.mockImplementation(async(url:string,init?:RequestInit)=> {
  if(url==='/sessions/A/messages' && init?.method==='POST') return pending.promise;
  if(url==='/sessions/A/messages' && confirmed) return json([{message_id:'background-user',role:'user',content:'original prompt',created_at:'2026-10-13T00:00:00Z'}]);
  return empty(url);
 });
 const {router}=await page();send();await waitFor(()=>expect(fetcher.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 await act(async()=>{await router.navigate('/?session=B');});const before=useAgentStore.getState().messages;
 confirmed=true;await act(async()=>pending.resolve(json({message_id:'background-user',attempt_id:'background'})));
 expect(useAgentStore.getState().messages).toBe(before);
 await act(async()=>{await router.navigate('/?session=A');});
 await waitFor(()=>expect(useAgentStore.getState().messages.filter(m=>m.type==='user').map(m=>m.id)).toEqual(['background-user']));
});
it('ignores a delayed goal snapshot after A to B to A',async()=> {
 const goal=deferred<Response>();let reads=0;fetcher.mockImplementation(async(url:string)=>url==='/sessions/A/goal' && reads++===0 ? goal.promise : empty(url));
 const {router}=await page();await act(async()=>{await router.navigate('/?session=B');});await act(async()=>{await router.navigate('/?session=A');});
 await act(async()=>goal.resolve(json({goal:{goal_id:'obsolete',objective:'obsolete goal',status:'active'},criteria:[],evidence:[]})));
 expect(screen.queryByText('obsolete goal')).not.toBeInTheDocument();expect(useAgentStore.getState().sessionId).toBe('A');
});
it('still sends a goal kickoff when its created SSE snapshot precedes the create ACK',async()=> {
 const goal=deferred<Response>();const snapshot={goal:{goal_id:'goal',objective:'original prompt',status:'active'},criteria:[],evidence:[]};
 fetcher.mockImplementation(async(url:string,init?:RequestInit)=> {
  if(url==='/sessions/A/goal' && init?.method==='POST') return goal.promise;
  if(url==='/sessions/A/goal' && useAgentStore.getState().status==='streaming') return json(snapshot);
  if(url==='/sessions/A/messages' && init?.method==='POST') return json({message_id:'goal-user',attempt_id:'goal-attempt'});
  return empty(url);
 });
 await page();fireEvent.click(screen.getByTitle('More options'));fireEvent.click(screen.getByRole('menuitem',{name:'Research Goal'}));send();
 await waitFor(()=>expect(fetcher.mock.calls.some(([url,init])=>url==='/sessions/A/goal' && init?.method==='POST')).toBe(true));
 act(()=>Events.instances[0].emit('goal.created',{}));await act(async()=>goal.resolve(json(snapshot)));
 await waitFor(()=>expect(fetcher.mock.calls.filter(([url,init])=>url==='/sessions/A/messages' && init?.method==='POST')).toHaveLength(1));
 expect(useAgentStore.getState().activity?.attemptId).toBe('goal-attempt');
});

it('retains authoritative resync rows when an older initial history returns', async () => {
  const initialHistory = deferred<Response>();
  let messageReads = 0;
  let initialSignal: AbortSignal | undefined;
  const recoveredReply = {
    message_id: 'recovered-reply',
    role: 'assistant',
    linked_attempt_id: 'recovered-attempt',
    content: 'authoritative recovered answer',
    created_at: '2026-10-13T00:00:00Z',
    metadata: { status: 'completed' },
  };
  fetcher.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/sessions/A/messages') {
      messageReads += 1;
      if (messageReads === 1) {
        initialSignal = init?.signal as AbortSignal;
        return initialHistory.promise;
      }
      return json([recoveredReply]);
    }
    if (url === '/sessions/A') return json({ session_id: 'A', last_attempt_id: 'recovered-attempt' });
    if (url === '/sessions/A/attempts/recovered-attempt') {
      return json({ attempt_id: 'recovered-attempt', status: 'completed', started_at: 1, terminal_metadata: {} });
    }
    return empty(url);
  });
  const view = await page();
  try {
    act(() => Events.instances[0].emit('session.resync', { reason: 'queue_overflow', latest_event_id: 'recovered-cursor' }));
    await waitFor(() => expect(Events.instances).toHaveLength(2));
    expect(useAgentStore.getState().messages.some(message => message.content === recoveredReply.content)).toBe(true);
    await act(async () => initialHistory.resolve(json([])));
    expect(useAgentStore.getState().messages.filter(message => message.type === 'answer').map(message => message.content)).toEqual([recoveredReply.content]);
    expect(useAgentStore.getState().messages.filter(message => message.meta?.activity?.attemptId === 'recovered-attempt')).toHaveLength(1);
    expect(useAgentStore.getState().status).toBe('idle');
    expect(initialSignal?.aborted).toBe(true);
  } finally {
    initialHistory.resolve(json([]));
    view.unmount();
  }
});

it.each(['stale', 'failure'])('preserves a newer report when old history enrichment returns %s', async (outcome) => {
  const oldReport = deferred<Response>();
  let runReads = 0;
  fetcher.mockImplementation(async (url: string) => {
    if (url === '/sessions/A/messages') {
      // Legacy durable messages without linked_attempt_id remain supported.
      return json([{ message_id: 'report-attempt', role: 'assistant', content: 'legacy answer', created_at: '2026-10-13T00:00:00Z', metadata: { run_id: 'run-overlap' } }]);
    }
    if (url === '/runs/run-overlap') {
      runReads += 1;
      return runReads === 1 ? oldReport.promise : json({ metrics: { total_return: 0.5 }, equity_curve: [{ time: '2026-10-13', equity: 150 }] });
    }
    return empty(url);
  });
  const view = await page();
  try {
    await waitFor(() => expect(runReads).toBe(1));
    act(() => {
      const events = Events.instances[0];
      events.emit('tool_call', { attempt_id: 'report-attempt', call_id: 'shadow-call', tool: 'render_shadow_report' });
      events.emit('tool_result', { call_id: 'shadow-call', tool: 'render_shadow_report', status: 'ok', preview: '{"shadow_id":"shadow_fresh"}' });
      events.emit('attempt.completed', { attempt_id: 'report-attempt', summary: 'fresh answer', run_dir: 'run-overlap' });
    });
    await waitFor(() => expect(useAgentStore.getState().messages.find(message => message.type === 'run_complete')?.metrics).toEqual({ total_return: 0.5 }));
    const freshReport = useAgentStore.getState().messages.find(message => message.type === 'run_complete');
    expect(freshReport?.equityCurve).toEqual([{ time: '2026-10-13', equity: 150 }]);
    expect(freshReport?.shadowId).toBe('shadow_fresh');
    await act(async () => {
      if (outcome === 'failure') oldReport.reject(new TypeError('old report unavailable'));
      else oldReport.resolve(json({ metrics: { total_return: 0.1 }, equity_curve: [{ time: '2026-10-13', equity: 110 }] }));
    });
    expect(useAgentStore.getState().messages.filter(message => message.type === 'run_complete')).toEqual([freshReport]);
  } finally {
    oldReport.resolve(json({}));
    view.unmount();
  }
});

it('still applies fresh initial history report enrichment', async () => {
  const report = deferred<Response>();
  fetcher.mockImplementation(async (url: string) => {
    if (url === '/sessions/A/messages') return json([{ message_id: 'fresh-reply', role: 'assistant', linked_attempt_id: 'fresh-attempt', content: 'base answer', created_at: '2026-10-13T00:00:00Z', metadata: { run_id: 'fresh-run' } }]);
    if (url === '/runs/fresh-run') return report.promise;
    return empty(url);
  });
  const view = await page();
  try {
    await screen.findByText('base answer');
    expect(useAgentStore.getState().messages.filter(message => message.type === 'run_complete')).toHaveLength(0);
    await act(async () => report.resolve(json({ metrics: { total_return: 0.3 }, equity_curve: [{ time: '2026-10-13', equity: 130 }] })));
    expect(useAgentStore.getState().messages.filter(message => message.type === 'run_complete').map(message => message.metrics)).toEqual([{ total_return: 0.3 }]);
  } finally {
    report.resolve(json({}));
    view.unmount();
  }
});

it.each(['stale', 'failure'])('preserves newer history enrichment when an older SSE report read returns %s', async (outcome) => {
  const history = deferred<Response>();
  const oldReport = deferred<Response>();
  let runReads = 0;
  fetcher.mockImplementation(async (url: string) => {
    if (url === '/sessions/A/messages') return history.promise;
    if (url === '/runs/run-overlap') {
      runReads += 1;
      return runReads === 1 ? oldReport.promise : json({ metrics: { total_return: 0.5 }, equity_curve: [{ time: '2026-10-13', equity: 150 }] });
    }
    return empty(url);
  });
  const view = await page();
  try {
    act(() => Events.instances[0].emit('attempt.completed', { attempt_id: 'report-attempt', summary: 'terminal answer', run_dir: 'run-overlap' }));
    await waitFor(() => expect(runReads).toBe(1));
    await act(async () => history.resolve(json([{
      message_id: 'reply', role: 'assistant', linked_attempt_id: 'report-attempt', content: 'terminal answer',
      created_at: '2026-10-13T00:00:00Z', metadata: { run_id: 'run-overlap' },
    }])));
    await waitFor(() => expect(useAgentStore.getState().messages.find(message => message.type === 'run_complete')?.metrics).toEqual({ total_return: 0.5 }));
    expect(runReads).toBe(2);
    // Optional report fields can be committed after that enrichment through the store boundary.
    // The obsolete GET must preserve the entire newer entity, including these fields.
    act(() => useAgentStore.setState(state => ({
      transcriptRevision: state.transcriptRevision + 1,
      messages: state.messages.map(message => message.type === 'run_complete' ? {
        ...message, shadowId: 'shadow_fresh', meta: { ...message.meta, revision: state.transcriptRevision + 1 },
      } : message),
    })));
    const freshReport = useAgentStore.getState().messages.find(message => message.type === 'run_complete');
    expect(freshReport?.id).toBe('report:A:report-attempt:run-overlap');
    expect(freshReport?.equityCurve).toEqual([{ time: '2026-10-13', equity: 150 }]);
    expect(freshReport?.shadowId).toBe('shadow_fresh');
    await act(async () => {
      if (outcome === 'failure') oldReport.reject(new TypeError('old SSE report unavailable'));
      else oldReport.resolve(json({ metrics: { total_return: 0.1 }, equity_curve: [{ time: '2026-10-13', equity: 110 }] }));
    });
    expect(useAgentStore.getState().messages.filter(message => message.type === 'run_complete')).toEqual([freshReport]);
    expect(useAgentStore.getState().messages.filter(message => message.type === 'answer').map(message => message.content)).toEqual(['terminal answer']);
  } finally {
    history.resolve(json([]));
    oldReport.resolve(json({}));
    view.unmount();
  }
});

it.each(['success', 'failure'])('still commits a fresh SSE report read on %s', async (outcome) => {
  const report = deferred<Response>();
  fetcher.mockImplementation(async (url: string) => url === '/runs/fresh-sse-run' ? report.promise : empty(url));
  const view = await page();
  try {
    await waitFor(() => expect(useAgentStore.getState().sessionLoading).toBe(false));
    act(() => Events.instances[0].emit('attempt.completed', { attempt_id: 'fresh-sse-attempt', summary: 'fresh SSE answer', run_dir: 'fresh-sse-run' }));
    await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url === '/runs/fresh-sse-run')).toBe(true));
    expect(useAgentStore.getState().messages.filter(message => message.type === 'run_complete')).toHaveLength(0);
    await act(async () => {
      if (outcome === 'failure') report.reject(new TypeError('report unavailable'));
      else report.resolve(json({ metrics: { total_return: 0.3 }, equity_curve: [{ time: '2026-10-13', equity: 130 }] }));
    });
    const reports = useAgentStore.getState().messages.filter(message => message.type === 'run_complete');
    expect(reports).toHaveLength(1);
    expect(reports[0].id).toBe('report:A:fresh-sse-attempt:fresh-sse-run');
    expect(reports[0].runId).toBe('fresh-sse-run');
    expect(reports[0].metrics).toEqual(outcome === 'success' ? { total_return: 0.3 } : undefined);
    expect(reports[0].equityCurve).toEqual(outcome === 'success' ? [{ time: '2026-10-13', equity: 130 }] : undefined);
    expect(useAgentStore.getState().status).toBe('idle');
  } finally {
    report.resolve(json({}));
    view.unmount();
  }
});

it('clears the real session after an ID-less clear inherits an indexed event ID', async () => {
  const { router, unmount } = await page();
  try {
    act(() => {
      Events.instances[0].emit('attempt.completed', { attempt_id: 'cleared-attempt', summary: 'removed answer' }, 'event-1');
      Events.instances[0].emit('session_cleared', { session_id: 'A' });
    });
    await waitFor(() => expect(useAgentStore.getState().sessionId).toBeNull());
    expect(useAgentStore.getState().messages).toEqual([]);
    expect(useAgentStore.getState().getCachedSession('A')).toBeUndefined();
    expect(router.state.location.search).toBe('');
  } finally {
    unmount();
  }
});

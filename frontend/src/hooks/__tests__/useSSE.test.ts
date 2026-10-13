import { renderHook, act } from "@testing-library/react";
import { useSSE } from "../useSSE";

// ── Mock EventSource ──────────────────────────────────────

type ESHandler = (e: MessageEvent) => void;

class MockEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, ESHandler[]>();
  private lastEventId = "";

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: ESHandler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type)!.push(handler);
  }

  /** Test helper: simulate an event from the server */
  emit(type: string, data: unknown, lastEventId?: string) {
    if (lastEventId !== undefined) this.lastEventId = lastEventId;
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    Object.defineProperty(event, "lastEventId", { value: this.lastEventId });
    const handlers = this.listeners.get(type) || [];
    handlers.forEach((h) => h(event));
  }

  close() {
    this.listeners.clear();
  }

  // ── Static helpers ──
  static instances: MockEventSource[] = [];

  static reset() {
    this.instances = [];
  }

  static get latest(): MockEventSource {
    return this.instances[this.instances.length - 1];
  }
}

beforeEach(() => {
  MockEventSource.reset();
  vi.stubGlobal("EventSource", MockEventSource);
  vi.useFakeTimers();
});

describe("useSSE — inherited IDs on recovery controls", () => {
  it("delivers resync after indexed data, retains terminal, and waits for hydration before replacement", async () => {
    let hydrate!: () => void;
    const hydration = new Promise<void>(resolve => { hydrate = resolve; });
    const delta = vi.fn();
    const terminal = vi.fn();
    const { result, unmount } = renderHook(() => useSSE());
    const resync = vi.fn(async (data: Record<string, unknown>) => {
      await hydration;
      result.current.connect('/events', { text_delta: delta }, String(data.latest_event_id));
    });
    try {
      act(() => result.current.connect('/events', { text_delta: delta, 'session.resync': resync, 'attempt.completed': terminal }));
      const source = MockEventSource.latest;
      act(() => {
        source.emit('text_delta', { delta: 'accepted' }, 'event-1');
        source.emit('text_delta', { delta: 'replay' }, 'event-1');
        source.emit('session.resync', { reason: 'queue_overflow', latest_event_id: 'event-9' });
        source.emit('text_delta', { delta: 'damaged' }, 'event-10');
        source.emit('attempt.completed', { attempt_id: 'one' }, 'event-11');
        source.onerror?.();
        vi.advanceTimersByTime(30000);
      });
      expect(delta).toHaveBeenCalledExactlyOnceWith({ delta: 'accepted' });
      expect(resync).toHaveBeenCalledExactlyOnceWith({ reason: 'queue_overflow', latest_event_id: 'event-9' });
      expect(terminal).toHaveBeenCalledExactlyOnceWith({ attempt_id: 'one' });
      expect(result.current.getStatus()).toBe('reconnecting');
      expect(MockEventSource.instances).toHaveLength(1);
      expect(vi.getTimerCount()).toBe(0);
      await act(async () => { hydrate(); await hydration; });
      expect(MockEventSource.instances).toHaveLength(2);
      expect(MockEventSource.latest.url).toBe('/events?Last-Event-ID=event-9');
      act(() => {
        MockEventSource.latest.emit('text_delta', { delta: 'recovered' }, 'event-10');
        MockEventSource.latest.emit('text_delta', { delta: 'recovered replay' }, 'event-10');
        MockEventSource.latest.onerror?.();
        vi.advanceTimersByTime(1000);
      });
      expect(delta.mock.calls.map(([data]) => data.delta)).toEqual(['accepted', 'recovered']);
      expect(MockEventSource.latest.url).toBe('/events?Last-Event-ID=event-10');
    } finally {
      await act(async () => { hydrate(); await hydration; });
      unmount();
    }
  });

  it("delivers session clear carrying the preceding indexed ID", () => {
    const clear = vi.fn();
    const { result } = renderHook(() => useSSE());
    act(() => result.current.connect('/events', { text_delta: () => {}, session_cleared: clear }));
    act(() => {
      MockEventSource.latest.emit('text_delta', { delta: 'accepted' }, 'event-1');
      MockEventSource.latest.emit('session_cleared', { session_id: 'A' });
    });
    expect(clear).toHaveBeenCalledExactlyOnceWith({ session_id: 'A' });
  });

  it("does not treat an ID-less clear as an ordinary dedup or resume observation", () => {
    const delta = vi.fn();
    const clear = vi.fn();
    const { result } = renderHook(() => useSSE());
    act(() => result.current.connect('/events', { text_delta: delta, session_cleared: clear }));
    act(() => {
      MockEventSource.latest.emit('text_delta', { delta: 'first data' }, 'event-1');
      // EventSource also updates its wire buffer for event types without a listener.
      MockEventSource.latest.emit('unsubscribed-event', {}, 'event-9');
      MockEventSource.latest.emit('session_cleared', { session_id: 'A' });
      MockEventSource.latest.onerror?.();
      vi.advanceTimersByTime(1000);
      MockEventSource.latest.emit('text_delta', { delta: 'later data' }, 'event-9');
      MockEventSource.latest.emit('text_delta', { delta: 'replay' }, 'event-9');
    });
    expect(clear).toHaveBeenCalledOnce();
    expect(MockEventSource.latest.url).toBe('/events?Last-Event-ID=event-1');
    expect(delta.mock.calls.map(([data]) => data.delta)).toEqual(['first data', 'later data']);
  });

  it("fences queued control callbacks from a replaced source generation", () => {
    const resync = vi.fn();
    const clear = vi.fn();
    const { result } = renderHook(() => useSSE());
    act(() => result.current.connect('/events', { 'session.resync': resync, session_cleared: clear }));
    const source = MockEventSource.latest;
    const callbacks = [...(source as unknown as { listeners: Map<string, ESHandler[]> }).listeners.entries()]
      .filter(([type]) => type === 'session.resync' || type === 'session_cleared');
    act(() => result.current.connect('/replacement', { 'session.resync': resync, session_cleared: clear }));
    act(() => callbacks.forEach(([type, handlers]) => handlers.forEach(handler => handler(new MessageEvent(type, {
      data: JSON.stringify({ latest_event_id: 'stale' }), lastEventId: 'event-1',
    })))));
    expect(resync).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    act(() => {
      MockEventSource.latest.onerror?.();
      vi.advanceTimersByTime(1000);
    });
    expect(MockEventSource.latest.url).toBe('/replacement');
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ── Tests ─────────────────────────────────────────────────

describe("useSSE — connect/disconnect", () => {
  it("creates an EventSource on connect", () => {
    const { result } = renderHook(() => useSSE());
    act(() => result.current.connect("http://test/events", {}));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.latest.url).toBe("http://test/events");
  });

  it("closes previous EventSource on reconnect", () => {
    const { result } = renderHook(() => useSSE());
    const closeSpy = vi.fn();

    act(() => result.current.connect("http://test/events", {}));
    MockEventSource.latest.close = closeSpy;

    act(() => result.current.connect("http://test/events2", {}));
    expect(closeSpy).toHaveBeenCalled();
  });

  it("sets status to connected on open", () => {
    const statuses: string[] = [];
    const { result } = renderHook(() => useSSE());

    act(() => {
      result.current.onStatusChange((s) => statuses.push(s));
      result.current.connect("http://test/events", {});
    });

    act(() => MockEventSource.latest.onopen?.());
    expect(statuses).toContain("connected");
    expect(result.current.getStatus()).toBe("connected");
  });

  it("disconnect closes source and sets disconnected status", () => {
    const statuses: string[] = [];
    const { result } = renderHook(() => useSSE());

    act(() => {
      result.current.onStatusChange((s) => statuses.push(s));
      result.current.connect("http://test/events", {});
    });

    act(() => result.current.disconnect());
    expect(statuses).toContain("disconnected");
    expect(result.current.getStatus()).toBe("disconnected");
  });
});

describe("useSSE — event handling", () => {
  it("dispatches typed events to handlers", () => {
    const textDeltas: unknown[] = [];
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        text_delta: (data) => textDeltas.push(data),
      }),
    );

    act(() => MockEventSource.latest.emit("text_delta", { content: "hello" }));
    expect(textDeltas).toHaveLength(1);
    expect(textDeltas[0]).toEqual({ content: "hello" });
  });

  it("dispatches reasoning progress events", () => {
    const reasoningEvents: unknown[] = [];
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        reasoning_delta: (data) => reasoningEvents.push(data),
      }),
    );

    act(() => MockEventSource.latest.emit("reasoning_delta", { chars: 8 }, "evt-reasoning"));
    expect(reasoningEvents).toEqual([{ chars: 8 }]);
  });

  it("dispatches stream reset events", () => {
    const resetEvents: unknown[] = [];
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        stream_reset: (data) => resetEvents.push(data),
      }),
    );

    act(() =>
      MockEventSource.latest.emit(
        "stream_reset",
        { reason: "provider_stream_retry" },
        "evt-reset",
      ),
    );
    expect(resetEvents).toEqual([{ reason: "provider_stream_retry" }]);
  });

  it("dispatches grounding status events", () => {
    const groundingEvents: unknown[] = [];
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        grounding_status: (data) => groundingEvents.push(data),
      }),
    );

    act(() =>
      MockEventSource.latest.emit(
        "grounding_status",
        { stage: "revising", round: 1, issues: 2 },
        "evt-grounding",
      ),
    );
    expect(groundingEvents).toEqual([{ stage: "revising", round: 1, issues: 2 }]);
  });

  it("falls back to message handler for known event types without specific handler", () => {
    const messages: unknown[] = [];
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        message: (data) => messages.push(data),
      }),
    );

    // "text_delta" is a known type the hook subscribes to,
    // but no specific handler → falls back to "message"
    act(() => MockEventSource.latest.emit("text_delta", { foo: "bar" }, "evt-1"));
    expect(messages).toHaveLength(1);
  });

  it("handles malformed JSON gracefully", () => {
    const messages: unknown[] = [];
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        text_delta: (data) => messages.push(data),
      }),
    );

    // Emit with raw non-JSON data
    const event = new MessageEvent("text_delta", { data: "not json" });
    Object.defineProperty(event, "lastEventId", { value: "evt-x" });
    const handlers = (MockEventSource.latest as any).listeners.get("text_delta");
    act(() => handlers?.forEach((h: ESHandler) => h(event)));

    expect(messages).toHaveLength(1);
    expect(messages[0]).toEqual({ raw: "not json" });
  });
});

describe("useSSE — deduplication", () => {
  it("deduplicates events by lastEventId", () => {
    const messages: unknown[] = [];
    const { result } = renderHook(() => useSSE({ dedupeCapacity: 10 }));

    act(() =>
      result.current.connect("http://test/events", {
        text_delta: (data) => messages.push(data),
      }),
    );

    act(() => MockEventSource.latest.emit("text_delta", { n: 1 }, "dup-1"));
    act(() => MockEventSource.latest.emit("text_delta", { n: 2 }, "dup-1")); // duplicate
    act(() => MockEventSource.latest.emit("text_delta", { n: 3 }, "dup-2"));

    expect(messages).toHaveLength(2);
    expect(messages[0]).toEqual({ n: 1 });
    expect(messages[1]).toEqual({ n: 3 });
  });

  it("evicts oldest events when dedup capacity exceeded", () => {
    const messages: unknown[] = [];
    const { result } = renderHook(() => useSSE({ dedupeCapacity: 3 }));

    act(() =>
      result.current.connect("http://test/events", {
        text_delta: (data) => messages.push(data),
      }),
    );

    // Fill dedup set to capacity (3)
    act(() => MockEventSource.latest.emit("text_delta", { n: 1 }, "a"));
    act(() => MockEventSource.latest.emit("text_delta", { n: 2 }, "b"));
    act(() => MockEventSource.latest.emit("text_delta", { n: 3 }, "c"));

    // Duplicate: should be suppressed
    act(() => MockEventSource.latest.emit("text_delta", { n: 10 }, "a"));
    expect(messages).toHaveLength(3);

    // Adding 4th event → "a" gets evicted → re-emit "a" should pass through
    act(() => MockEventSource.latest.emit("text_delta", { n: 4 }, "d"));
    expect(messages).toHaveLength(4);
    act(() => MockEventSource.latest.emit("text_delta", { n: 5 }, "a"));
    expect(messages).toHaveLength(5);
  });
});

describe("useSSE — exponential backoff", () => {
  it("schedules reconnect with exponential delay on error", () => {
    const reconnects: unknown[] = [];
    const { result } = renderHook(() =>
      useSSE({ initialRetryMs: 100, maxRetryMs: 5000, backoffFactor: 2 }),
    );

    act(() =>
      result.current.connect("http://test/events", {
        reconnect: (data) => reconnects.push(data),
      }),
    );

    // Trigger error → should schedule reconnect
    act(() => MockEventSource.latest.onerror?.());

    expect(reconnects).toHaveLength(1);
    expect(reconnects[0]).toEqual({ attempt: 1, delayMs: 100 });
    expect(result.current.getStatus()).toBe("reconnecting");

    // Advance timer past the delay → should create a new EventSource
    act(() => vi.advanceTimersByTime(150));
    expect(MockEventSource.instances.length).toBeGreaterThan(1);
  });

  it("caps retry delay at maxRetryMs", () => {
    const reconnects: unknown[] = [];
    const { result } = renderHook(() =>
      useSSE({ initialRetryMs: 100, maxRetryMs: 200, backoffFactor: 10 }),
    );

    act(() =>
      result.current.connect("http://test/events", {
        reconnect: (data) => reconnects.push(data),
      }),
    );

    act(() => MockEventSource.latest.onerror?.());
    expect((reconnects[0] as any).delayMs).toBe(100);

    // Trigger another error on the new source
    act(() => vi.advanceTimersByTime(200));
    act(() => MockEventSource.latest.onerror?.());
    // backoff: 100 * 10^1 = 1000, but capped at 200
    expect((reconnects[1] as any).delayMs).toBe(200);
  });
});

describe("useSSE — Last-Event-ID resume", () => {
  it("appends Last-Event-ID to reconnect URL", () => {
    const { result } = renderHook(() => useSSE());

    act(() =>
      result.current.connect("http://test/events", {
        text_delta: () => {},
      }),
    );

    // Emit an event with lastEventId
    act(() => MockEventSource.latest.emit("text_delta", { n: 1 }, "resume-42"));

    // Trigger reconnect
    act(() => MockEventSource.latest.onerror?.());
    act(() => vi.advanceTimersByTime(2000));

    // The new EventSource should have Last-Event-ID in the URL
    const newUrl = MockEventSource.latest.url;
    expect(newUrl).toContain("Last-Event-ID=resume-42");
  });
});

describe("useSSE — SSE ticket auth (VT-003)", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("stays synchronous and mints no ticket in dev mode (no stored key)", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    const { result } = renderHook(() => useSSE());
    act(() => result.current.connect("http://test/events", {}));

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.latest.url).toBe("http://test/events");
  });

  it("mints a single-use ticket and opens the stream with ?ticket= when a key is stored", async () => {
    localStorage.setItem("vibe_trading_api_auth_key", "remote-key");
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ticket: "SSE-TICKET" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const { result } = renderHook(() => useSSE());
    await act(async () => {
      result.current.connect("http://test/events", {});
      // Flush the ticket-fetch promise chain before the EventSource is opened.
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/auth/sse-ticket",
      expect.objectContaining({ method: "POST" }),
    );
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.latest.url).toContain("ticket=SSE-TICKET");
    // The long-lived key must never appear in the stream URL.
    expect(MockEventSource.latest.url).not.toContain("remote-key");
  });

  it("ignores a ticket that resolves after a newer connection", async () => {
    localStorage.setItem("vibe_trading_api_auth_key", "remote-key");
    const ticketResolvers: Array<(response: Response) => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            ticketResolvers.push(resolve);
          }),
      ),
    );

    const { result } = renderHook(() => useSSE());
    act(() => {
      result.current.connect("http://test/session-a/events", {});
      result.current.connect("http://test/session-b/events", {});
    });

    await act(async () => {
      ticketResolvers[1](
        new Response(JSON.stringify({ ticket: "TICKET-B" }), { status: 200 }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      ticketResolvers[0](
        new Response(JSON.stringify({ ticket: "TICKET-A" }), { status: 200 }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.latest.url).toContain("session-b/events");
    expect(MockEventSource.latest.url).toContain("ticket=TICKET-B");
  });
});

it('aborts owned authentication on disconnect and unmount without reconnecting', async()=> {
 localStorage.setItem('vibe_trading_api_auth_key','fixture'); let signal:AbortSignal|undefined;
 vi.stubGlobal('fetch',vi.fn((_url,init)=>{signal=init.signal;return new Promise(()=>{});}));
 const {result,unmount}=renderHook(()=>useSSE());act(()=>result.current.connect('/events',{}));
 expect(signal?.aborted).toBe(false);act(()=>result.current.disconnect());expect(signal?.aborted).toBe(true);
 await act(async()=>vi.advanceTimersByTimeAsync(16000));expect(MockEventSource.instances).toHaveLength(0);expect(vi.getTimerCount()).toBe(0);unmount();
 localStorage.clear();
});
it('fences ordinary events after resync and replaces at the explicit cursor',()=> {
 const delta=vi.fn();const resync=vi.fn();const {result}=renderHook(()=>useSSE());
 act(()=>result.current.connect('/events',{text_delta:delta,'session.resync':resync}));
 act(()=> {MockEventSource.latest.emit('session.resync',{reason:'queue_overflow',latest_event_id:'nine'});MockEventSource.latest.emit('text_delta',{delta:'damaged'},'ten');});
 expect(delta).not.toHaveBeenCalled();expect(resync).toHaveBeenCalledOnce();
 act(()=>result.current.connect('/events',{text_delta:delta},'nine'));expect(MockEventSource.latest.url).toContain('Last-Event-ID=nine');
});

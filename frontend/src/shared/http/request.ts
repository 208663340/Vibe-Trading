/** One deadline covers headers, error decoding and the complete body read. */
export class RequestTimeout extends Error {
  constructor() { super('Request timed out'); this.name = 'RequestTimeout'; }
}
export async function boundedRequest<T>(url: string, init: RequestInit, read: (response: Response) => Promise<T>, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  let rejectBoundary!: (reason: unknown) => void;
  const boundary = new Promise<never>((_, reject) => { rejectBoundary = reject; });
  const abort = () => { controller.abort(init.signal?.reason); rejectBoundary(init.signal?.reason ?? new DOMException('Aborted', 'AbortError')); };
  const timer = setTimeout(() => { controller.abort(); rejectBoundary(new RequestTimeout()); }, timeoutMs);
  if (init.signal?.aborted) abort();
  else init.signal?.addEventListener('abort', abort, { once: true });
  try {
    return await Promise.race([boundary, (async () => {
      controller.signal.throwIfAborted();
      const response = await fetch(url, { ...init, signal: controller.signal });
      controller.signal.throwIfAborted();
      return read(response);
    })()]);
  } finally { clearTimeout(timer); init.signal?.removeEventListener('abort', abort); }
}

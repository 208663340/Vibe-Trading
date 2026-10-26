import i18n from "@/i18n";
import { authHeaders } from "@/lib/apiAuth";
import { boundedRequest, RequestTimeout } from "@/shared/http/request";

const BASE = "";
const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS);
const REQUEST_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout > 0
  ? configuredTimeout : 120_000;
export const METADATA_TIMEOUT_MS = Math.min(REQUEST_TIMEOUT_MS, 15_000);

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const AUTH_REQUIRED_MESSAGE_KEY = "connection.authRequired";

function getAuthRequiredMessage(): string {
  return i18n.t(AUTH_REQUIRED_MESSAGE_KEY as never);
}

// Keep the existing string export compatible with consumers while updating its
// live ES-module binding whenever the active locale changes.
export let AUTH_REQUIRED_MESSAGE = getAuthRequiredMessage();
i18n.on("languageChanged", () => {
  AUTH_REQUIRED_MESSAGE = getAuthRequiredMessage();
});

export function isAuthRequiredError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`;
  let code: string | undefined;
  try {
    const body = await res.json();
    // Options endpoints report errors under an `error` key
    // ({status:"error", error} / {ok:false, error}) rather than detail/message.
    const raw = body.detail ?? body.message ?? body.error;
    if (typeof raw === "string" && raw) {
      detail = raw;
    } else if (raw && typeof raw === "object") {
      const structured = raw as { code?: unknown; message?: unknown };
      if (typeof structured.code === "string" && structured.code) code = structured.code;
      if (typeof structured.message === "string" && structured.message) detail = structured.message;
      else if (code) detail = code;
    }
  } catch {
    if (res.status >= 500) detail = i18n.t("connection.requestFailed");
  }
  if (res.status === 401 || res.status === 403) {
    detail = getAuthRequiredMessage();
  }
  return new ApiError(detail, res.status, code);
}

/** Bound both the connection and body read; never automatically replay writes. */
export async function performRequest<T>(
  path: string,
  options: RequestInit,
  read: (response: Response) => Promise<T>,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  try {
    return await boundedRequest(`${BASE}${path}`, options, async (response) => {
      if (!response.ok) throw await errorFromResponse(response);
      return read(response);
    }, timeoutMs);
  } catch (error) {
    if (error instanceof RequestTimeout) {
      const mutation = options.method && !["GET", "HEAD"].includes(options.method.toUpperCase());
      throw new ApiError(i18n.t(mutation ? "connection.requestTimeoutMutation" : "connection.requestTimeout"), 0, "request_timeout");
    }
    if (options.signal?.aborted || error instanceof ApiError) throw error;
    if (error instanceof TypeError) throw new ApiError(i18n.t("connection.requestFailed"), 0, "network_error");
    throw error;
  }
}

export async function request<T>(path: string, options?: RequestInit, timeoutMs?: number): Promise<T> {
  const { headers, ...rest } = options ?? {};
  const mergedHeaders: Record<string, string> = { "Content-Type": "application/json", ...authHeaders() };
  if (headers) {
    new Headers(headers).forEach((value, key) => {
      mergedHeaders[key] = value;
    });
  }
  return performRequest(path, {
    ...rest,
    headers: mergedHeaders,
  }, async (res) => {
    const text = await res.text();
    if (!text) return {} as T;

    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      const preview = text.slice(0, 80).replace(/\s+/g, " ");
      throw new ApiError(
        `Expected JSON from ${path}, got ${contentType || "unknown content type"}: ${preview}`,
        res.status,
      );
    }

    return JSON.parse(text) as T;
  }, timeoutMs);
}

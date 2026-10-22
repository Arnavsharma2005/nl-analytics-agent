import type { Problem } from "./types";

/** An error from the API, carrying the stable code from its problem object. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryAfter?: number;

  constructor(problem: Problem) {
    super(problem.detail ? `${problem.title}. ${problem.detail}` : problem.title);
    this.code = problem.code;
    this.status = problem.status;
    this.retryAfter = problem.retry_after;
  }
}

export async function problemFrom(r: Response): Promise<ApiError> {
  const body = await r.json().catch(() => null);
  if (body && typeof body.code === "string") return new ApiError(body as Problem);
  return new ApiError({ code: "http_error", title: `The server answered ${r.status}`, status: r.status });
}

export const offline = () =>
  new ApiError({ code: "network", title: "Can't reach the server", detail: "Check your connection and try again.", status: 0 });

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } }).catch(() => {
    throw offline();
  });
  if (!r.ok) throw await problemFrom(r);
  return r.json();
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T | null> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).catch(() => {
    throw offline();
  });
  if (!r.ok) throw await problemFrom(r);
  return r.status === 204 ? null : r.json();
}

export const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(signal.reason);
    });
  });

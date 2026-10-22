/**
 * Cloudflare Pages Functions that forward /v1/* and /healthz to the API, so the browser talks to one origin:
 * the session cookie stays first-party and no CORS is needed. Streams (server-sent events) pass straight through.
 *
 * Settings on the Pages project: API_ORIGIN (e.g. https://nl-analytics-agent.onrender.com) and PROXY_SECRET
 * (the same value as the API's PROXY_SECRET), so the API can trust the visitor IP this proxy passes on.
 */

export interface Env {
  API_ORIGIN: string;
  PROXY_SECRET?: string;
}

interface Context {
  request: Request;
  env: Env;
}

// Hop-by-hop and client-supplied headers that must not reach the API as if this proxy had vouched for them.
const DROP = ["host", "connection", "x-client-ip", "x-proxy-secret", "x-forwarded-for", "true-client-ip"];

export async function forward({ request, env }: Context): Promise<Response> {
  if (!env.API_ORIGIN) return new Response("API_ORIGIN is not set", { status: 500 });
  const url = new URL(request.url);
  const target = new URL(url.pathname + url.search, env.API_ORIGIN);
  const headers = new Headers(request.headers);
  for (const name of DROP) headers.delete(name);
  const ip = request.headers.get("cf-connecting-ip");
  if (ip) headers.set("x-client-ip", ip);
  if (env.PROXY_SECRET) headers.set("x-proxy-secret", env.PROXY_SECRET);
  headers.set("x-forwarded-proto", "https");
  const hasBody = !["GET", "HEAD"].includes(request.method);
  return fetch(target, { method: request.method, headers, body: hasBody ? request.body : undefined, redirect: "manual" });
}

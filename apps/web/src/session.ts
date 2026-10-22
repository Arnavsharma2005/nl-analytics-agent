/** Visitor sessions: the API asks for one before answering, after a Cloudflare Turnstile bot check when it is on. */

declare global {
  interface Window {
    turnstile?: {
      render(el: HTMLElement, options: Record<string, unknown>): string;
      remove(id: string): void;
    };
  }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

/** An error from the API, with the stable code from its problem details (see docs/errors.md). */
export class ApiError extends Error {
  constructor(message: string, readonly code: string | null, readonly status: number) {
    super(message);
  }
}

export async function apiError(r: Response): Promise<ApiError> {
  const problem = await r.json().catch(() => null);
  return new ApiError(problem?.detail ?? problem?.title ?? `Request failed (${r.status})`, problem?.code ?? null, r.status);
}

let session: Promise<void> | null = null;

/** Make sure the browser holds a session cookie. `renew` starts a new one, e.g. after the old one expired. */
export function ensureSession(renew = false): Promise<void> {
  if (!session || renew) {
    session = start().catch((err) => {
      session = null;
      throw err;
    });
  }
  return session;
}

async function start(): Promise<void> {
  const config: { turnstile_site_key?: string | null } = await fetch("/api/config")
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  const token = config.turnstile_site_key ? await turnstileToken(config.turnstile_site_key) : null;
  const r = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turnstile_token: token }),
  });
  if (!r.ok) throw await apiError(r);
}

let script: Promise<void> | null = null;

function loadScript(): Promise<void> {
  script ??= new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = SCRIPT;
    el.async = true;
    el.onload = () => resolve();
    el.onerror = () => {
      script = null;
      reject(new Error("Could not load the bot check. Check your connection and reload the page."));
    };
    document.head.appendChild(el);
  });
  return script;
}

/** Run the Turnstile check. It is usually invisible; Cloudflare shows a checkbox only when it is unsure. */
async function turnstileToken(siteKey: string): Promise<string> {
  await loadScript();
  const box = document.createElement("div");
  box.className = "bot-check";
  document.body.appendChild(box);
  try {
    return await new Promise<string>((resolve, reject) => {
      const id = window.turnstile!.render(box, {
        sitekey: siteKey,
        appearance: "interaction-only",
        callback: (token: string) => resolve(token),
        "error-callback": () => reject(new Error("The bot check failed. Please reload the page and try again.")),
      });
      box.dataset.widget = id;
    });
  } finally {
    if (box.dataset.widget) window.turnstile!.remove(box.dataset.widget);
    box.remove();
  }
}

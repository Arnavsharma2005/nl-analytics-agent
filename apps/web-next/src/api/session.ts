/** Visitor sessions: the API asks for one before answering, after a Cloudflare Turnstile bot check when it is on. */
import { ApiError, getJSON, postJSON } from "./http";
import type { Config } from "./types";

declare global {
  interface Window {
    turnstile?: {
      render(el: HTMLElement, options: Record<string, unknown>): string;
      remove(id: string): void;
    };
  }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export const getConfig = () => getJSON<Config>("/v1/config");

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
  const config = await getConfig();
  const token = config.turnstile_site_key ? await turnstileToken(config.turnstile_site_key) : null;
  await postJSON("/v1/session", { turnstile_token: token });
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
      reject(botCheckError("The bot check could not load. Check your connection and reload the page."));
    };
    document.head.appendChild(el);
  });
  return script;
}

const botCheckError = (detail: string) => new ApiError({ code: "bot_check_failed", title: "Bot check failed", detail, status: 403 });

/** Run the Turnstile check. It is usually invisible; Cloudflare shows a checkbox only when it is unsure. */
async function turnstileToken(siteKey: string): Promise<string> {
  await loadScript();
  const box = document.createElement("div");
  box.className = "fixed right-4 bottom-24 z-20";
  document.body.appendChild(box);
  try {
    return await new Promise<string>((resolve, reject) => {
      const id = window.turnstile!.render(box, {
        sitekey: siteKey,
        appearance: "interaction-only",
        callback: (token: string) => resolve(token),
        "error-callback": () => reject(botCheckError("Reload the page and try again.")),
      });
      box.dataset.widget = id;
    });
  } finally {
    if (box.dataset.widget) window.turnstile!.remove(box.dataset.widget);
    box.remove();
  }
}

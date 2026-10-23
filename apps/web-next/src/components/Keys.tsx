/**
 * API keys at /keys: make a key to ask from a script, an SDK or the widget, see the keys that still work, and revoke
 * one. A new key is shown once, right after it is made.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ApiError } from "../api/http";
import { createKey, listKeys, revokeKey } from "../api/keys";
import type { NewApiKey } from "../api/types";
import Brand from "./Brand";
import ErrorNotice from "./ErrorNotice";

const when = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });

export default function Keys() {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [origins, setOrigins] = useState("");
  const [made, setMade] = useState<NewApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const keys = useQuery({ queryKey: ["keys"], queryFn: listKeys, retry: false });
  const create = useMutation({
    mutationFn: () => createKey(name.trim(), origins.split(/[\s,]+/).filter(Boolean)),
    onSuccess: (key) => {
      setMade(key);
      setCopied(false);
      setName("");
      setOrigins("");
      client.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const revoke = useMutation({
    mutationFn: revokeKey,
    onSuccess: (_, id) => {
      if (made?.id === id) setMade(null);
      client.invalidateQueries({ queryKey: ["keys"] });
    },
  });

  useEffect(() => {
    document.title = "API keys · Sales analyst";
  }, []);

  return (
    <div className="mx-auto min-h-dvh max-w-[1240px] px-4 sm:px-6">
      <header className="py-4"><Brand href="/" /></header>
      <main className="mx-auto max-w-[760px] pb-16 pt-10">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">API keys</h1>
        <p className="mt-3 max-w-[60ch] text-muted">
          A key lets a script, the Python or TypeScript SDK, or the chat widget ask questions as you, within your own
          question limits. Send it as <code className="font-mono text-sm">Authorization: Bearer &lt;key&gt;</code>.
        </p>

        <form
          className="mt-8 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <div>
            <label htmlFor="key-name" className="block text-sm font-medium text-ink">What it is for</label>
            <input id="key-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="Weekly sales script"
              className="mt-1 w-full rounded-md border border-rule bg-surface px-3 py-2 focus:border-ink focus:outline-none" />
          </div>
          <div>
            <label htmlFor="key-origins" className="block text-sm font-medium text-ink">Sites that may use it in a browser</label>
            <input id="key-origins" value={origins} onChange={(e) => setOrigins(e.target.value)} placeholder="https://example.com"
              aria-describedby="key-origins-help"
              className="mt-1 w-full rounded-md border border-rule bg-surface px-3 py-2 focus:border-ink focus:outline-none" />
            <p id="key-origins-help" className="mt-1 text-sm text-muted">
              For the widget, list the sites it is embedded on, separated by spaces. Leave it empty for a script.
            </p>
          </div>
          <button type="submit" disabled={create.isPending || !name.trim()}
            className="rounded-md bg-ink px-4 py-2 font-medium text-surface hover:bg-ink-soft disabled:opacity-50">
            {create.isPending ? "Making the key…" : "Make a key"}
          </button>
          {create.isError && <ErrorNotice error={create.error as ApiError} />}
        </form>

        {made && (
          <section className="mt-6 rounded-md border border-ink p-4" aria-live="polite" aria-labelledby="new-key">
            <h2 id="new-key" className="font-semibold text-ink">Your new key for {made.name}</h2>
            <p className="mt-1 text-sm text-muted">Copy it now. It is not shown again.</p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <code className="min-w-0 flex-1 break-all rounded-md bg-surface px-3 py-2 font-mono text-sm">{made.key}</code>
              <button type="button" className="rounded-md border border-rule px-3 py-1.5 text-sm hover:border-ink"
                onClick={() => navigator.clipboard?.writeText(made.key).then(() => setCopied(true))}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </section>
        )}

        <section className="mt-12" aria-labelledby="your-keys">
          <h2 id="your-keys" className="font-semibold text-ink">Your keys</h2>
          {keys.isError && <div className="mt-3"><ErrorNotice error={keys.error as ApiError} /></div>}
          {keys.data?.length === 0 && <p className="mt-2 text-muted">None yet.</p>}
          {revoke.isError && <div className="mt-3"><ErrorNotice error={revoke.error as ApiError} /></div>}
          {!!keys.data?.length && (
            <ul className="mt-3 divide-y divide-rule border-y border-rule">
              {keys.data.map((k) => (
                <li key={k.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-3">
                  <div className="min-w-0">
                    <p className="text-ink">{k.name} <span className="font-mono text-sm text-muted">{k.hint}…</span></p>
                    <p className="text-sm text-muted">
                      Made {when.format(new Date(k.created_at))}
                      {k.last_used_at ? `, last used ${when.format(new Date(k.last_used_at))}` : ", never used"}
                      {k.origins.length > 0 && `, for ${k.origins.join(", ")}`}
                    </p>
                  </div>
                  <button type="button" disabled={revoke.isPending} onClick={() => revoke.mutate(k.id)}
                    className="rounded-md border border-rule px-3 py-1.5 text-sm hover:border-danger hover:text-danger">
                    Revoke<span className="sr-only"> {k.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}

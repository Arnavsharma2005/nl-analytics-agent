import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getConfig } from "./api/session";
import Account, { SignInNotice } from "./components/Account";
import AnswerView from "./components/AnswerView";
import Composer from "./components/Composer";
import Inspector from "./components/Inspector";
import ServerStatus from "./components/ServerStatus";
import { useConversation, type Turn } from "./conversation";
import { useMedia } from "./lib/hooks";

function Starters({ questions, onAsk }: { questions: string[]; onAsk: (q: string) => void }) {
  return (
    <section className="pt-10 pb-6" aria-labelledby="start">
      <h2 id="start" className="max-w-[24ch] text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        What do you want to know about the store's sales?
      </h2>
      <p className="mt-3 max-w-[60ch] text-muted">
        Ask in plain English. You get the answer, a chart, and the SQL behind it. These are good places to start:
      </p>
      <ul className="mt-6 divide-y divide-rule border-y border-rule">
        {questions.map((q) => (
          <li key={q}>
            <button type="button" onClick={() => onAsk(q)} className="w-full py-3 text-left hover:text-ink">
              {q}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function InspectorPanel({ turn, onClose, docked }: { turn: Turn; onClose: () => void; docked: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!docked) dialog.current?.showModal();
  }, [docked]);

  const body = (
    <>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 id="inspector-title" className="font-semibold">How this was answered</h2>
          <p className="mt-0.5 text-sm text-muted">{turn.question}</p>
        </div>
        <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-sm text-muted hover:bg-rule/60" aria-label="Close details">
          Close
        </button>
      </div>
      <Inspector turn={turn} />
    </>
  );

  if (docked) {
    return (
      <aside aria-labelledby="inspector-title" className="sticky top-6 max-h-[calc(100dvh-3rem)] overflow-y-auto rounded-lg border border-rule bg-surface p-5">
        {body}
      </aside>
    );
  }
  return (
    <dialog
      ref={dialog}
      aria-labelledby="inspector-title"
      onClose={onClose}
      className="mt-auto mb-0 max-h-[85dvh] w-full max-w-none rounded-t-2xl bg-surface p-5 text-text backdrop:bg-black/40"
    >
      {body}
    </dialog>
  );
}

export default function App() {
  const { turns, conversationId, ask, retry, reset, open, busy } = useConversation();
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig, staleTime: Infinity });
  const [inspected, setInspected] = useState<string | null>(null);
  const wide = useMedia("(min-width: 1100px)");
  const last = useRef<HTMLDivElement>(null);
  const inspectedTurn = turns.find((t) => t.key === inspected);

  useEffect(() => {
    if (turns.length) last.current?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [turns.length]);

  return (
    <div className="mx-auto min-h-dvh max-w-[1240px] px-4 sm:px-6">
      <header className="flex items-center justify-between gap-4 py-4">
        <h1 className="flex items-baseline gap-2 text-base font-semibold text-ink">
          <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4 self-center"><rect x="1" y="9" width="3" height="6" rx="1" fill="var(--ink)" /><rect x="6.5" y="5" width="3" height="10" rx="1" fill="var(--ink)" /><rect x="12" y="1" width="3" height="14" rx="1" fill="var(--turmeric)" /></svg>
          Sales analyst
        </h1>
        <div className="flex items-center gap-2">
          {config.data?.features.sign_in && (
            <Account
              current={conversationId}
              onOpen={(id, messages) => {
                setInspected(null);
                open(id, messages);
              }}
            />
          )}
          {turns.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setInspected(null);
                reset();
              }}
              className="rounded-md border border-rule px-3 py-1.5 text-sm hover:border-ink"
            >
              New conversation
            </button>
          )}
        </div>
      </header>

      <div className={wide && inspectedTurn ? "grid grid-cols-[minmax(0,1fr)_400px] gap-10" : ""}>
        <main className="mx-auto flex min-h-[calc(100dvh-4.5rem)] max-w-[760px] flex-col">
          <ServerStatus />
          <SignInNotice />
          <div className="flex-1">
            {turns.length === 0 ? (
              <Starters questions={config.data?.starters ?? []} onAsk={ask} />
            ) : (
              turns.map((t, i) => (
                <div key={t.key} ref={i === turns.length - 1 ? last : undefined} className="scroll-mt-4">
                  <AnswerView
                    turn={t}
                    onAsk={ask}
                    onRetry={retry}
                    inspecting={inspected === t.key}
                    onInspect={(turn) => setInspected(inspected === turn.key ? null : turn.key)}
                  />
                </div>
              ))
            )}
          </div>
          <div className="sticky bottom-0 bg-gradient-to-t from-paper from-70% to-transparent pt-6 pb-3">
            <Composer onAsk={ask} busy={busy} voice={config.data?.features.voice ?? false} />
          </div>
        </main>
        {inspectedTurn && <InspectorPanel key={inspectedTurn.key + wide} turn={inspectedTurn} docked={wide} onClose={() => setInspected(null)} />}
      </div>
    </div>
  );
}

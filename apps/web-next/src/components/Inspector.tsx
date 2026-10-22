/** How an answer was worked out: the steps and their timings, the models used, the SQL, and the data. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { explain } from "../api/actions";
import { ApiError } from "../api/http";
import type { Cell } from "../api/types";
import type { Turn } from "../conversation";
import DataTable from "./DataTable";
import ErrorNotice from "./ErrorNotice";

const STEP_NAMES: Record<string, string> = {
  router: "Understand the question",
  context: "Look up similar questions",
  verified: "Use a verified query",
  sql_agent: "Write SQL",
  validate: "Check the SQL",
  execute: "Run the query",
  chart_agent: "Choose a chart",
  summary_agent: "Write the summary",
  schema_answer: "Describe the data",
};

interface LLMCall {
  step?: string;
  provider: string;
  ok: boolean;
  error?: string;
  seconds?: number;
}

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <section className="border-t border-rule py-4 first:border-0 first:pt-0">
    <h3 className="mb-2 text-sm font-semibold">{title}</h3>
    {children}
  </section>
);

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="text-xs font-medium text-ink underline underline-offset-4"
      onClick={() => navigator.clipboard.writeText(text).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })}
    >
      {copied ? "Copied" : "Copy SQL"}
    </button>
  );
}

function Explanation({ messageId }: { messageId: string }) {
  const [open, setOpen] = useState(false);
  const steps = useQuery({ queryKey: ["explain", messageId], queryFn: () => explain(messageId), enabled: open, staleTime: Infinity, retry: false });
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="rounded-md border border-rule px-3 py-1.5 text-sm font-medium hover:border-ink">
        Explain this query in plain English
      </button>
    );
  }
  if (steps.isPending) return <p className="text-sm text-muted" role="status">Working out the steps…</p>;
  if (steps.isError) return <ErrorNotice
        error={steps.error instanceof ApiError ? steps.error : { title: "The query couldn't be explained", canRetry: true }}
        onRetry={() => steps.refetch()}
      />;
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm">
      {steps.data.map((s, i) => <li key={i}>{s}</li>)}
    </ol>
  );
}

export default function Inspector({ turn }: { turn: Turn }) {
  const answer = turn.message?.answer;
  if (!answer) return <p className="text-sm text-muted">The details appear once the answer is finished.</p>;
  const calls = (answer.llm_calls ?? []) as unknown as LLMCall[];
  const timings = Object.entries(answer.timings ?? {}) as [string, number][];
  const total = timings.reduce((sum, [, s]) => sum + s, 0);

  return (
    <div className="text-sm">
      <Section title="Steps">
        {answer.cached ? <p className="mb-2 text-muted">Answered from a recent identical question.</p> : null}
        <table className="w-full">
          <tbody>
            {timings.map(([node, seconds]) => (
              <tr key={node}>
                <td className="py-0.5">{STEP_NAMES[node] ?? node}</td>
                <td className="py-0.5 text-right text-muted">{seconds.toFixed(2)}s</td>
              </tr>
            ))}
            <tr className="border-t border-rule font-medium">
              <td className="pt-1">Total</td>
              <td className="pt-1 text-right">{total.toFixed(2)}s</td>
            </tr>
          </tbody>
        </table>
      </Section>

      {calls.length > 0 && (
        <Section title="Models">
          <ul className="space-y-1">
            {calls.map((c, i) => (
              <li key={i} className="flex justify-between gap-3">
                <span>
                  {c.step && <span className="text-muted">{STEP_NAMES[c.step] ?? c.step}: </span>}
                  {c.provider}
                </span>
                <span className={c.ok ? "text-muted" : "text-danger"}>{c.ok ? `${(c.seconds ?? 0).toFixed(1)}s` : c.error ?? "failed"}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {answer.failures.length > 0 && (
        <Section title={`Fixed itself after ${answer.failures.length} failed ${answer.failures.length === 1 ? "try" : "tries"}`}>
          <ul className="space-y-3">
            {answer.failures.map((f, i) => (
              <li key={i}>
                <p className="text-danger">{f.error}</p>
                <pre tabIndex={0} className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-paper p-2 font-mono text-xs">{f.sql}</pre>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {answer.sql && (
        <Section title="SQL">
          {answer.verified_question && <p className="mb-2 text-teal">Reused the verified query for “{answer.verified_question}”.</p>}
          {answer.sql_reasoning && <p className="mb-2 text-muted">{answer.sql_reasoning}</p>}
          <pre tabIndex={0} className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-paper p-3 font-mono text-xs leading-relaxed">{answer.sql}</pre>
          <div className="mt-2 flex items-center gap-4">
            <CopyButton text={answer.sql} />
          </div>
          {!answer.error && turn.message && <div className="mt-3"><Explanation messageId={turn.message.id} /></div>}
        </Section>
      )}

      {answer.intent === "data_query" && !answer.error && (
        <Section title={`Data (${answer.rows.length}${answer.truncated ? "+" : ""} ${answer.rows.length === 1 ? "row" : "rows"})`}>
          <DataTable columns={answer.columns} rows={answer.rows as Cell[][]} truncated={answer.truncated} caption={`Data for: ${turn.question}`} />
        </Section>
      )}
    </div>
  );
}

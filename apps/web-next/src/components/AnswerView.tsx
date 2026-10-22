/**
 * One question and its answer: the summary (figures marked), the chart or figures, and what to do next.
 * The answer fills in as it streams: steps first, then the table, the chart, and the summary word by word.
 */
import type { ECharts } from "echarts/core";
import { lazy, Suspense, useCallback, useRef, useState } from "react";
import type { Cell } from "../api/types";
import type { Turn } from "../conversation";
import { drawable } from "../lib/chartOption";
import { fileName, save, toCSV } from "../lib/download";
import { splitFigures } from "../lib/figures";
import { formatValue, humanize } from "../lib/format";
import DataTable from "./DataTable";
import ErrorNotice from "./ErrorNotice";
import Feedback from "./Feedback";

const Chart = lazy(() => import("./Chart"));

function Summary({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <p className={`max-w-[68ch] text-lg leading-relaxed ${streaming ? "caret" : ""}`} aria-busy={streaming}>
      {splitFigures(text).map((part, i) => (part.figure ? <span key={i} className="figure">{part.text}</span> : part.text))}
    </p>
  );
}

function StepTrail({ steps }: { steps: Turn["steps"] }) {
  const current = steps.at(-1);
  return (
    <ol className="space-y-1 text-sm" aria-label="Progress">
      {steps.slice(0, -1).map((s, i) => (
        <li key={i} className="text-muted">
          <span aria-hidden="true" className="mr-2 text-teal">✓</span>{s.label}
        </li>
      ))}
      <li className="text-text" aria-current="step">
        <span aria-hidden="true" className="mr-2 inline-block size-2 animate-pulse rounded-full bg-turmeric align-middle" />
        {current ? current.label : "Sending the question"}…
      </li>
    </ol>
  );
}

function Figures({ columns, row, measures }: { columns: string[]; row: Cell[]; measures: string[] }) {
  return (
    <dl className="flex flex-wrap gap-x-10 gap-y-4">
      {measures.filter((m) => columns.includes(m)).map((m) => (
        <div key={m}>
          <dt className="text-sm text-muted">{humanize(m)}</dt>
          <dd className="mt-1 text-3xl font-semibold tracking-tight text-ink">{formatValue(row[columns.indexOf(m)], m, true)}</dd>
        </div>
      ))}
    </dl>
  );
}

const action = "rounded-md px-2 py-1.5 text-sm text-muted hover:bg-rule/60 hover:text-text";

function ReadAloud({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false);
  if (!("speechSynthesis" in window)) return null;
  const toggle = () => {
    speechSynthesis.cancel();
    if (speaking) return setSpeaking(false);
    const u = new SpeechSynthesisUtterance(text.replace(/₹/g, "rupees ").replace(/\bCr\b/g, "crore").replace(/\bL\b/g, "lakh"));
    u.lang = "en-IN";
    u.onend = u.onerror = () => setSpeaking(false);
    setSpeaking(true);
    speechSynthesis.speak(u);
  };
  return <button type="button" className={action} onClick={toggle} aria-pressed={speaking}>{speaking ? "Stop reading" : "Read aloud"}</button>;
}

interface Props {
  turn: Turn;
  onAsk: (question: string) => void;
  onRetry: (turn: Turn) => void;
  onInspect: (turn: Turn) => void;
  inspecting: boolean;
}

export default function AnswerView({ turn, onAsk, onRetry, onInspect, inspecting }: Props) {
  const chart = useRef<ECharts | null>(null);
  const onReady = useCallback((c: ECharts | null) => { chart.current = c; }, []);
  const streaming = turn.status === "streaming";
  const message = turn.message;
  const answer = message?.answer;
  const table = turn.table;
  const spec = turn.chart;
  const rows = table?.rows ?? [];

  const savePNG = () => {
    const url = chart.current?.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: getComputedStyle(document.documentElement).getPropertyValue("--surface") });
    if (url) save(fileName(turn.question, "png"), url);
  };

  return (
    <article className="border-t border-rule py-8 first:border-0 first:pt-2" aria-labelledby={`q-${turn.key}`}>
      <h2 id={`q-${turn.key}`} className="mb-4 text-xl font-semibold text-ink">{turn.question}</h2>
      {answer?.standalone_question && (
        <p className="-mt-3 mb-4 text-sm text-muted">Understood as: {answer.standalone_question}</p>
      )}

      {streaming && !turn.summary && <StepTrail steps={turn.steps} />}

      {turn.status === "failed" && turn.error && <ErrorNotice error={turn.error} onRetry={() => onRetry(turn)} />}

      {answer && answer.intent !== "data_query" && <p className="max-w-[68ch] text-lg leading-relaxed">{answer.answer}</p>}

      {answer?.error && (
        <ErrorNotice
          error={{ title: "This question couldn't be answered", detail: answer.error, canRetry: true }}
          onRetry={() => onRetry(turn)}
        />
      )}

      {turn.summary && <Summary text={turn.summary} streaming={streaming} />}
      {message && answer?.summary && <p className="sr-only" role="status">Answer ready.</p>}

      {(answer?.verified_question || (answer?.assumptions?.length ?? 0) > 0 || answer?.summary_verified === false) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          {answer?.verified_question && (
            <span className="rounded-full bg-teal/10 px-2.5 py-0.5 font-medium text-teal">From a verified query</span>
          )}
          {answer?.assumptions?.map((a) => (
            <span key={a} className="rounded-full border border-rule px-2.5 py-0.5 text-muted">Read as: {a}</span>
          ))}
          {answer?.summary_verified === false && (
            <span className="text-danger">Some figures in this summary don't match the data. Check the table.</span>
          )}
        </div>
      )}

      {table && spec?.type === "kpi" && rows.length > 0 && (
        <div className="mt-6"><Figures columns={table.columns} row={rows[0]} measures={spec.y} /></div>
      )}

      {table && drawable(spec, rows) && (
        <figure className="mt-6">
          <figcaption className="mb-2 text-sm font-medium text-muted">{spec!.title}</figcaption>
          <Suspense fallback={<div className="h-80 animate-pulse rounded-md bg-rule/40" />}>
            <Chart spec={spec!} columns={table.columns} rows={rows} onReady={onReady} />
          </Suspense>
        </figure>
      )}

      {table && !streaming && spec?.type !== "kpi" && !drawable(spec, rows) && rows.length > 0 && (
        <div className="mt-6"><DataTable columns={table.columns} rows={rows} truncated={table.truncated} caption={`Data for: ${turn.question}`} /></div>
      )}

      {table && !streaming && rows.length === 0 && <p className="mt-4 text-muted">The query ran and found no matching rows.</p>}

      {message && (
        <div className="mt-6 flex flex-wrap items-center gap-x-2 gap-y-2">
          <Feedback messageId={message.id} />
          <span className="mx-1 h-5 w-px bg-rule" aria-hidden="true" />
          <button type="button" className={action} aria-expanded={inspecting} onClick={() => onInspect(turn)}>How this was answered</button>
          {table && rows.length > 0 && (
            <button type="button" className={action} onClick={() => save(fileName(turn.question, "csv"), new Blob([toCSV(table.columns, rows)], { type: "text/csv" }))}>
              Download CSV
            </button>
          )}
          {table && drawable(spec, rows) && <button type="button" className={action} onClick={savePNG}>Save chart</button>}
          {turn.summary && <ReadAloud text={turn.summary} />}
        </div>
      )}

      {message && message.suggestions.length > 0 && (
        <nav className="mt-6" aria-label="Follow-up questions">
          <h3 className="mb-2 text-sm text-muted">Ask next</h3>
          <ul className="flex flex-col items-start gap-1">
            {message.suggestions.map((s) => (
              <li key={s}>
                <button type="button" onClick={() => onAsk(s)} className="text-left text-ink underline decoration-rule decoration-2 underline-offset-4 hover:decoration-ink">
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </article>
  );
}

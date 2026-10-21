import { useState } from "react";
import type { AskResult, Step } from "../types";
import ChartView from "./ChartView";
import DataTable from "./DataTable";

export interface Entry {
  id: number;
  question: string;
  steps: Step[];
  result?: AskResult;
  error?: string;
}

const PIPELINE = ["router", "sql_agent", "validate", "execute", "chart_agent", "summary_agent"];
const LABELS: Record<string, string> = {
  router: "Understand",
  sql_agent: "Write SQL",
  validate: "Validate",
  execute: "Run query",
  chart_agent: "Pick chart",
  summary_agent: "Summarise",
};

function Progress({ steps }: { steps: Step[] }) {
  const done = new Set(steps.map((s) => s.node));
  const retries = steps.filter((s) => s.node === "sql_agent").length - 1;
  const current = PIPELINE.find((n) => !done.has(n));
  return (
    <ol className="progress" aria-label="Progress">
      {PIPELINE.map((n) => (
        <li key={n} className={done.has(n) ? "done" : n === current ? "active" : ""}>
          {LABELS[n]}
          {n === "sql_agent" && retries > 0 ? ` (retry ${retries})` : ""}
        </li>
      ))}
    </ol>
  );
}

export default function ResultCard({ entry }: { entry: Entry }) {
  const [tab, setTab] = useState<"none" | "sql" | "data">("none");
  const r = entry.result;
  const pending = !r && !entry.error;

  return (
    <article className="card">
      <h2 className="question">{entry.question}</h2>
      {pending && <Progress steps={entry.steps} />}
      {entry.error && <p className="error">Something went wrong: {entry.error}</p>}
      {r && r.intent !== "data_query" && <p className="answer">{r.answer}</p>}
      {r && r.error && <p className="error">{r.error}</p>}
      {r && r.intent === "data_query" && !r.error && (
        <>
          {r.summary && <p className="summary">{r.summary}</p>}
          {r.chart && <ChartView spec={r.chart} columns={r.columns} rows={r.rows} />}
          <div className="meta">
            <button className={tab === "sql" ? "on" : ""} onClick={() => setTab(tab === "sql" ? "none" : "sql")}>SQL</button>
            <button className={tab === "data" ? "on" : ""} onClick={() => setTab(tab === "data" ? "none" : "data")}>
              Data ({r.rows.length}{r.truncated ? "+" : ""} {r.rows.length === 1 ? "row" : "rows"})
            </button>
            <span className="muted">
              {r.attempts > 1 ? `Fixed its own SQL after ${r.attempts - 1} failed attempt${r.attempts > 2 ? "s" : ""} · ` : ""}
              {Object.values(r.timings).reduce((a, b) => a + b, 0).toFixed(1)}s
            </span>
          </div>
          {tab === "sql" && (
            <div className="sql">
              {r.sql_reasoning && <p className="muted">{r.sql_reasoning}</p>}
              <pre><code>{r.sql}</code></pre>
            </div>
          )}
          {tab === "data" && <DataTable columns={r.columns} rows={r.rows} />}
        </>
      )}
    </article>
  );
}

/**
 * Deep Analysis at /reports: ask a broad question and get a report built from several answers, each section citing
 * the steps (questions, queries and results) its figures come from. A report at /reports/{id} shows its progress
 * while it runs, and prints as a PDF once it is done.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import type { ApiError } from "../api/http";
import { getReport, listReports, startReport } from "../api/reports";
import type { Cell, ReportStatus, ReportStep } from "../api/types";
import { drawable } from "../lib/chartOption";
import Brand from "./Brand";
import DataTable from "./DataTable";
import ErrorNotice from "./ErrorNotice";

const Chart = lazy(() => import("./Chart"));

const EXAMPLES = ["How did 2025 go, and where should we focus next year?", "Which customers and products drive our profit?"];
const when = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

function progress(r: ReportStatus): string {
  if (r.status === "failed") return "Failed";
  if (r.status === "done") return "Done";
  if (r.stage === "planning") return "Planning the steps…";
  if (r.stage === "writing") return "Writing the report…";
  return `Answering step ${Math.min(r.steps_done + 1, r.steps_total ?? 1)} of ${r.steps_total}…`;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto min-h-dvh max-w-[1240px] px-4 sm:px-6">
      <header className="flex items-center justify-between gap-4 py-4 print:hidden">
        <Brand href="/" />
        <a href="/reports" className="rounded-md px-2 py-1.5 text-sm hover:bg-rule/60">Reports</a>
      </header>
      <main className="mx-auto max-w-[760px] pb-16">{children}</main>
    </div>
  );
}

export function ReportList() {
  const client = useQueryClient();
  const [question, setQuestion] = useState("");
  const reports = useQuery({ queryKey: ["reports"], queryFn: listReports, retry: false });
  const start = useMutation({
    mutationFn: startReport,
    onSuccess: (r) => {
      client.invalidateQueries({ queryKey: ["reports"] });
      location.assign(`/reports/${r.id}`);
    },
  });

  useEffect(() => {
    document.title = "Deep Analysis · Sales analyst";
  }, []);

  return (
    <Shell>
      <section className="pt-10" aria-labelledby="deep">
        <h1 id="deep" className="text-3xl font-semibold tracking-tight text-ink sm:text-4xl">Deep Analysis</h1>
        <p className="mt-3 max-w-[60ch] text-muted">
          Ask something broad. It is broken into up to eight specific questions, each answered from the data, and
          written up as a report where every figure links to the query it came from. It takes a minute or two.
        </p>
        <form
          className="mt-6 flex flex-col gap-3 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim().length >= 3) start.mutate(question.trim());
          }}
        >
          <label htmlFor="broad" className="sr-only">Your question</label>
          <input
            id="broad"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
            placeholder={EXAMPLES[0]}
            className="min-w-0 flex-1 rounded-md border border-rule bg-surface px-3 py-2 focus:border-ink focus:outline-none"
          />
          <button
            type="submit"
            disabled={start.isPending || question.trim().length < 3}
            className="rounded-md bg-ink px-4 py-2 font-medium text-surface hover:bg-ink-soft disabled:opacity-50"
          >
            {start.isPending ? "Starting…" : "Write the report"}
          </button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          {EXAMPLES.map((q) => (
            <button key={q} type="button" onClick={() => setQuestion(q)} className="rounded-full border border-rule px-3 py-1 text-muted hover:text-ink">
              {q}
            </button>
          ))}
        </div>
        {start.isError && <div className="mt-4"><ErrorNotice error={start.error as ApiError} /></div>}
      </section>

      <section className="mt-12" aria-labelledby="yours">
        <h2 id="yours" className="font-semibold text-ink">Your reports</h2>
        {reports.isError && <div className="mt-3"><ErrorNotice error={reports.error as ApiError} /></div>}
        {reports.data?.length === 0 && <p className="mt-2 text-muted">None yet. Reports are kept for 30 days.</p>}
        {!!reports.data?.length && (
          <ul className="mt-3 divide-y divide-rule border-y border-rule">
            {reports.data.map((r) => (
              <li key={r.id}>
                <a href={`/reports/${r.id}`} className="flex items-baseline justify-between gap-4 py-3 hover:text-ink">
                  <span>{r.question}</span>
                  <span className="shrink-0 text-sm text-muted">{r.status === "done" ? when.format(new Date(r.created_at)) : progress(r)}</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Shell>
  );
}

function StepCard({ n, step }: { n: number; step: ReportStep }) {
  const a = step.answer;
  const rows = a.rows as Cell[][];
  return (
    <section id={`step-${n}`} className="scroll-mt-4 border-t border-rule pt-6" aria-labelledby={`step-${n}-q`}>
      <p className="text-sm text-muted">Step {n} · {step.purpose}</p>
      <h3 id={`step-${n}-q`} className="mt-1 font-semibold text-ink">{step.question}</h3>
      {a.error ? (
        <p className="mt-2 text-danger">This step could not be answered, so the report does not rely on it.</p>
      ) : (
        <>
          {a.summary && <p className="mt-2">{a.summary}</p>}
          {drawable(a.chart, rows) ? (
            <figure className="mt-4 break-inside-avoid">
              <figcaption className="mb-2 text-sm font-medium text-muted">{a.chart!.title}</figcaption>
              <Suspense fallback={<div className="h-80 animate-pulse rounded-md bg-rule/40" />}>
                <Chart spec={a.chart!} columns={a.columns} rows={rows} />
              </Suspense>
            </figure>
          ) : (
            rows.length > 0 && <div className="mt-4"><DataTable columns={a.columns} rows={rows} truncated={a.truncated} caption={`Data for step ${n}`} /></div>
          )}
          {a.sql && (
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-muted hover:text-ink">The SQL behind step {n}</summary>
              <pre className="mt-2 overflow-x-auto rounded-md bg-surface p-3 font-mono text-xs leading-relaxed">{a.sql}</pre>
            </details>
          )}
        </>
      )}
    </section>
  );
}

export function ReportView({ id }: { id: string }) {
  const report = useQuery({
    queryKey: ["report", id],
    queryFn: () => getReport(id),
    retry: false,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  const data = report.data;
  const body = data?.report;

  useEffect(() => {
    document.title = `${body?.title ?? data?.question ?? "Report"} · Sales analyst`;
  }, [body, data]);

  return (
    <Shell>
      {report.isPending && <p className="pt-10 text-muted">Opening the report…</p>}
      {report.isError && <div className="pt-10"><ErrorNotice error={report.error as ApiError} /></div>}
      {data && !body && (
        <section className="pt-10" aria-live="polite">
          <p className="text-sm text-muted">Deep Analysis</p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">{data.question}</h1>
          {data.status === "failed" ? (
            <p className="mt-4 text-danger">{data.error}</p>
          ) : (
            <p className="mt-4 text-muted">{progress(data)}</p>
          )}
        </section>
      )}
      {body && (
        <article className="pt-8">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm text-muted">{data!.question}</p>
              <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink">{body.title}</h1>
            </div>
            <button type="button" onClick={() => print()} className="shrink-0 rounded-md border border-rule px-3 py-1.5 text-sm hover:border-ink print:hidden">
              Save as PDF
            </button>
          </div>
          {body.summary && <p className="mt-4 text-lg">{body.summary}</p>}
          {body.sections.map((s) => (
            <section key={s.heading} className="mt-8 break-inside-avoid">
              <h2 className="font-semibold text-ink">{s.heading}</h2>
              <p className="mt-2">{s.text}</p>
              <p className="mt-2 text-sm text-muted">
                {s.steps.length ? "From " : "No step cited"}
                {s.steps.map((n, i) => (
                  <span key={n}>
                    {i > 0 && ", "}
                    <a href={`#step-${n}`} className="underline decoration-rule underline-offset-2 hover:text-ink">step {n}</a>
                  </span>
                ))}
              </p>
              {s.unsupported.length > 0 && (
                <p className="mt-1 text-sm text-danger">
                  These figures could not be found in the cited results, so check them: {s.unsupported.join(", ")}.
                </p>
              )}
            </section>
          ))}
          <h2 className="mt-12 mb-2 font-semibold text-ink">How each figure was found</h2>
          <div className="space-y-8">
            {body.steps.map((step, i) => <StepCard key={i} n={i + 1} step={step} />)}
          </div>
        </article>
      )}
    </Shell>
  );
}

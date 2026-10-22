/** The public eval page at /eval: how often the analyst gets the right answer, on questions it was never tuned on.
 * Its data is public/eval-report.json, built from the committed runs in eval/results/ by eval/publish.py, so every
 * figure here can be checked against a file in the repository. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getEvalReport, type EvalReportData, type Latest, type Question, type Run, type Score } from "../api/evalReport";
import Brand from "./Brand";

const REPO = "https://github.com/Arnavsharma2005/nl-analytics-agent";
const day = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "long", year: "numeric" });
const DIFFICULTY_ORDER = ["easy", "medium", "hard", "first turn", "follow-up"];

function Commit({ run }: { run: Run }) {
  return (
    <a href={`${REPO}/commit/${run.commit}`} className="font-mono text-sm text-ink underline underline-offset-4">
      {run.commit}
    </a>
  );
}

function Bars({ title, groups, order }: { title: string; groups: Record<string, Score>; order?: string[] }) {
  const rank = (name: string) => (order?.includes(name) ? order.indexOf(name) : Infinity);
  const names = Object.keys(groups).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return (
    <section aria-label={title}>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <dl className="mt-2 space-y-2">
        {names.map((name) => {
          const s = groups[name];
          return (
            <div key={name} className="grid grid-cols-[7rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm">
              <dt className="capitalize">{name}</dt>
              <dd className="h-2 overflow-hidden rounded-full bg-rule" aria-hidden="true">
                <div className="h-full rounded-full bg-ink" style={{ width: `${s.accuracy_pct ?? 0}%` }} />
              </dd>
              <dd className="text-right tabular-nums text-muted">{s.correct} of {s.questions}</dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

function Headline({ latest, label }: { latest: Latest; label: string }) {
  const writers = Object.entries(latest.sql_written_by).sort((a, b) => b[1] - a[1]).map(([name]) => name);
  return (
    <div>
      <p className="text-5xl font-semibold tracking-tight text-ink tabular-nums sm:text-6xl">
        {latest.held_out.accuracy_pct}%
      </p>
      <p className="mt-2 max-w-[60ch] text-lg">
        {latest.held_out.correct} of {latest.held_out.questions} {label} answered correctly.
      </p>
      <p className="mt-2 text-sm text-muted">
        Run on {day.format(new Date(latest.run_at))} at commit <Commit run={latest} />
        {writers.length > 0 && <>, with SQL written by {writers.join(", ")}</>}
        {latest.features.length > 0 && <>, with {latest.features.join(", ").replaceAll("_", " ")} on</>}.
        {latest.repaired_by_retry > 0 && <> {latest.repaired_by_retry} were right only after the agent fixed its own failed query.</>}
      </p>
    </div>
  );
}

function QuestionList({ questions }: { questions: Question[] }) {
  const [missesOnly, setMissesOnly] = useState(false);
  const misses = questions.filter((q) => !q.correct).length;
  const shown = missesOnly ? questions.filter((q) => !q.correct) : questions;
  return (
    <section aria-labelledby="every-question" className="mt-12">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 id="every-question" className="text-xl font-semibold text-ink">Every held-out question</h3>
        {misses > 0 && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={missesOnly} onChange={(e) => setMissesOnly(e.target.checked)} className="accent-[var(--ink)]" />
            Only the {misses} it got wrong
          </label>
        )}
      </div>
      <ul className="mt-3 divide-y divide-rule border-y border-rule">
        {shown.map((q) => (
          <li key={q.id}>
            <details className="group py-3">
              <summary className="flex cursor-pointer list-none items-baseline gap-3">
                <span className={q.correct ? "w-14 shrink-0 text-sm text-teal" : "w-14 shrink-0 text-sm font-medium text-danger"}>
                  {q.correct ? "Right" : "Wrong"}
                </span>
                <span className="min-w-0 flex-1 break-words">{q.question}</span>
                <span className="hidden shrink-0 text-sm text-muted sm:inline">{q.difficulty}</span>
              </summary>
              <div className="mt-3 pl-17">
                <p className="text-sm text-muted">
                  #{q.id}, {q.category}, {q.difficulty}
                  {q.attempts > 1 && <>, {q.attempts} attempts</>}
                </p>
                {q.error && <p className="mt-2 text-sm text-danger">{q.error}</p>}
                {q.sql && (
                  <pre className="mt-2 overflow-x-auto rounded-md bg-surface p-3 font-mono text-xs leading-relaxed" aria-label="SQL it ran">{q.sql}</pre>
                )}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Runs({ runs, heldOut }: { runs: Run[]; heldOut: EvalReportData["held_out_questions"] }) {
  return (
    <section aria-labelledby="runs" className="mt-12">
      <h3 id="runs" className="text-xl font-semibold text-ink">Every published run</h3>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-muted">
            <tr className="border-b border-rule">
              <th scope="col" className="py-2 pr-4 font-normal">Date</th>
              <th scope="col" className="py-2 pr-4 font-normal">Commit</th>
              <th scope="col" className="py-2 pr-4 font-normal">Set</th>
              <th scope="col" className="py-2 pr-4 font-normal">Held-out score</th>
              <th scope="col" className="py-2 font-normal">All questions run</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {runs.map((run) => (
              <tr key={run.file} className="border-b border-rule">
                <td className="py-2 pr-4 whitespace-nowrap">{day.format(new Date(run.run_at))}</td>
                <td className="py-2 pr-4"><Commit run={run} /></td>
                <td className="py-2 pr-4">{run.set === "questions" ? "Single questions" : "Conversations"}</td>
                <td className="py-2 pr-4">
                  {run.held_out
                    ? `${run.held_out.correct} of ${run.held_out.questions} (${run.held_out.accuracy_pct}%)`
                    : <span className="text-muted">Not all {heldOut[run.set]} run</span>}
                </td>
                <td className="py-2">{run.all.correct} of {run.all.questions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Method({ heldOut }: { heldOut: EvalReportData["held_out_questions"] }) {
  return (
    <section aria-labelledby="method" className="mt-12 max-w-[65ch]">
      <h3 id="method" className="text-xl font-semibold text-ink">How this is measured</h3>
      <ul className="mt-3 list-disc space-y-2 pl-5">
        <li>
          There are 120 questions about the demo&rsquo;s sales data, each with a query someone checked by hand. 50 are
          used to tune the prompts and examples; the other {heldOut.questions} are held out and only ever scored.
        </li>
        <li>
          An answer counts as right when the rows it returns match the checked query&rsquo;s, whatever SQL produced
          them. A wrong number, a missing row or no answer at all counts as wrong.
        </li>
        <li>
          Follow-up questions are measured separately: {heldOut.conversations} held-out turns of conversations, each
          asked with the turns before it.
        </li>
        <li>
          Each run&rsquo;s full results, including the SQL for every question, are in{" "}
          <a href={`${REPO}/tree/main/eval/results`} className="text-ink underline underline-offset-4">eval/results</a>.
          To reproduce one: <code className="font-mono text-sm">python eval/run_eval.py --split test</code>.
        </li>
      </ul>
    </section>
  );
}

export default function EvalReport() {
  const report = useQuery({ queryKey: ["eval-report"], queryFn: getEvalReport, retry: 1 });

  useEffect(() => {
    document.title = "Accuracy · Sales analyst";
  }, []);

  const data = report.data;
  const questions = data?.latest.questions;
  const conversations = data?.latest.conversations;

  return (
    <div className="mx-auto min-h-dvh max-w-[1240px] px-4 sm:px-6">
      <header className="flex items-center justify-between gap-4 py-4">
        <Brand href="/" />
        <a href="/" className="rounded-md px-2 py-1.5 text-sm hover:bg-rule/60">Ask a question</a>
      </header>
      <main className="mx-auto max-w-[760px] pb-16">
        <h2 className="pt-10 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">How often it gets it right</h2>

        {report.isPending && <p className="mt-6 text-muted" role="status">Loading the results…</p>}
        {report.isError && <p className="mt-6 text-danger" role="alert">{(report.error as Error).message}</p>}

        {data && (
          <>
            <div className="mt-8">
              {questions ? (
                <Headline latest={questions} label="questions it was never tuned on" />
              ) : (
                <p className="max-w-[60ch] text-lg">
                  No run over all {data.held_out_questions.questions} held-out questions has been published yet.
                  The first one will appear here once its results are committed.
                </p>
              )}
            </div>

            {questions && (
              <div className="mt-10 grid gap-8 sm:grid-cols-2">
                <Bars title="By difficulty" groups={questions.by_difficulty} order={DIFFICULTY_ORDER} />
                <Bars title="By kind of question" groups={questions.by_category} />
              </div>
            )}

            {conversations && (
              <section aria-labelledby="follow-ups" className="mt-12">
                <h3 id="follow-ups" className="mb-4 text-xl font-semibold text-ink">Follow-up questions</h3>
                <Headline latest={conversations} label="conversation turns answered in context" />
                <div className="mt-6">
                  <Bars title="By turn" groups={conversations.by_difficulty} order={DIFFICULTY_ORDER} />
                </div>
              </section>
            )}

            {questions && <QuestionList questions={questions.questions} />}
            {data.runs.length > 0 && <Runs runs={data.runs} heldOut={data.held_out_questions} />}
            <Method heldOut={data.held_out_questions} />
          </>
        )}
      </main>
    </div>
  );
}

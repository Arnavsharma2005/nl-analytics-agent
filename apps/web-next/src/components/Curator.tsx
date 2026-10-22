/** The Curator at /curator: answers people rated thumbs up, to approve into the verified library or reject. Only
 * curators (CURATORS on the server) can use it. Approved answers reach the analyst only once the pull request that
 * adds them to semantic/queries.yml is merged, after CI has checked them. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { exportAsPullRequest, exportAsYaml, getCurator, review } from "../api/curator";
import { ApiError } from "../api/http";
import { signInUrl } from "../api/account";
import type { Candidate, CuratorOverview } from "../api/types";
import Brand from "./Brand";
import ErrorNotice from "./ErrorNotice";

const when = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

const quiet = "rounded-md border border-rule px-3 py-1.5 text-sm hover:border-ink disabled:opacity-60";
const strong = "rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-surface hover:bg-ink-soft disabled:opacity-60";

function CandidateCard({ candidate }: { candidate: Candidate }) {
  const id = useId();
  const [question, setQuestion] = useState(candidate.question);
  const queryClient = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      review(candidate.message_id, decision === "approve" && question.trim() !== candidate.question
        ? { decision, question: question.trim() }
        : { decision }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["curator"] }),
  });
  const tooShort = question.trim().length < 3;

  return (
    <li className="py-6">
      <article aria-labelledby={`${id}-q`}>
        <h3 id={`${id}-q`} className="text-lg font-semibold text-ink">{candidate.question}</h3>
        <p className="mt-1 text-sm text-muted">
          {candidate.asked !== candidate.question ? <>Asked as &ldquo;{candidate.asked}&rdquo;, rated </> : "Rated "}
          {when.format(new Date(candidate.rated_at))}
        </p>
        {candidate.comment && (
          <blockquote className="mt-3 border-l-2 border-turmeric pl-3 text-sm">{candidate.comment}</blockquote>
        )}
        {candidate.summary && <p className="mt-3 max-w-[65ch]">{candidate.summary}</p>}
        <pre className="mt-3 overflow-x-auto rounded-md bg-surface p-3 font-mono text-xs leading-relaxed" aria-label="SQL">
          {candidate.sql}
        </pre>
        <div className="mt-4">
          <label htmlFor={`${id}-as`} className="text-sm text-muted">Approve as the question</label>
          <input
            id={`${id}-as`}
            value={question}
            maxLength={300}
            onChange={(e) => setQuestion(e.target.value)}
            className="mt-1 block w-full rounded-md border border-rule bg-surface px-3 py-1.5 text-sm text-text focus-visible:border-ink"
          />
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={strong} disabled={decide.isPending || tooShort} onClick={() => decide.mutate("approve")}>
            {decide.isPending && decide.variables === "approve" ? "Approving…" : "Approve"}
          </button>
          <button type="button" className={quiet} disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
            {decide.isPending && decide.variables === "reject" ? "Rejecting…" : "Reject"}
          </button>
        </div>
        {decide.isError && (
          <div className="mt-3">
            <ErrorNotice error={decide.error as ApiError} />
          </div>
        )}
      </article>
    </li>
  );
}

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false; // no clipboard permission: the YAML is shown to copy by hand
  }
}

function Approved({ overview }: { overview: CuratorOverview }) {
  const queryClient = useQueryClient();
  const [pr, setPr] = useState<string | null>(null);
  const open = useMutation({
    mutationFn: exportAsPullRequest,
    onSuccess: (done) => {
      setPr(done.url);
      void queryClient.invalidateQueries({ queryKey: ["curator"] });
    },
  });
  const yaml = useMutation({
    mutationFn: async () => {
      const text = await exportAsYaml();
      return { text, copied: await copy(text) };
    },
  });
  const { approved } = overview;

  return (
    <section aria-labelledby="approved" className="mt-12">
      <h2 id="approved" className="text-xl font-semibold text-ink">Approved, not in the library yet</h2>
      <p role="status" className="mt-1 max-w-[65ch] text-muted">
        {pr ? (
          <>
            The pull request is open:{" "}
            <a href={pr} target="_blank" rel="noreferrer" className="text-ink underline underline-offset-4">{pr.replace("https://github.com/", "")}</a>.
            Merge it once CI passes to add them.
          </>
        ) : approved.length ? (
          "CI checks each one runs and gives away no held-out eval question before you merge."
        ) : (
          "Nothing waiting. Answers you approve gather here until you export them."
        )}
      </p>
      {approved.length > 0 && (
        <>
          <ul className="mt-4 divide-y divide-rule border-y border-rule">
            {approved.map((a) => (
              <li key={a.message_id} className="flex flex-wrap items-baseline justify-between gap-x-4 py-3">
                <span className="min-w-0 break-words">{a.question}</span>
                <span className="text-sm text-muted">approved by {a.reviewer}</span>
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap gap-2">
            {overview.can_open_pr && (
              <button type="button" className={strong} disabled={open.isPending} onClick={() => open.mutate()}>
                {open.isPending ? "Opening the pull request…" : "Open a pull request"}
              </button>
            )}
            <button type="button" className={overview.can_open_pr ? quiet : strong} disabled={yaml.isPending} onClick={() => yaml.mutate()}>
              Copy as YAML
            </button>
          </div>
        </>
      )}
      {open.isError && (
        <div className="mt-3">
          <ErrorNotice error={open.error as ApiError} />
        </div>
      )}
      {yaml.isError && (
        <div className="mt-3">
          <ErrorNotice error={yaml.error as ApiError} />
        </div>
      )}
      {yaml.data && (
        <div className="mt-4">
          <p role="status" className="text-sm text-muted">
            {yaml.data.copied ? "Copied. Paste it at the end of apps/api/semantic/queries.yml." : "Copy this to the end of apps/api/semantic/queries.yml:"}
          </p>
          <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-surface p-3 font-mono text-xs leading-relaxed">{yaml.data.text}</pre>
        </div>
      )}
    </section>
  );
}

export default function Curator() {
  const overview = useQuery({ queryKey: ["curator"], queryFn: getCurator, retry: false });
  const refused = overview.error instanceof ApiError && overview.error.code === "not_a_curator";

  useEffect(() => {
    document.title = "Curator · Sales analyst";
  }, []);

  return (
    <div className="mx-auto min-h-dvh max-w-[1240px] px-4 sm:px-6">
      <header className="flex items-center justify-between gap-4 py-4">
        <Brand href="/" />
        <a href="/" className="rounded-md px-2 py-1.5 text-sm hover:bg-rule/60">Back to asking</a>
      </header>
      <main className="mx-auto max-w-[760px] pb-16">
        <h2 className="pt-10 text-3xl font-semibold tracking-tight text-ink sm:text-4xl">Answers to review</h2>
        <p className="mt-3 max-w-[60ch] text-muted">
          People rated these answers thumbs up. Approve the ones whose SQL is right, and they become verified queries the
          analyst can reuse once their pull request is merged.
        </p>
        {overview.isPending && <p className="pt-8 text-muted">Loading the queue…</p>}
        {refused && (
          <div className="pt-8">
            <ErrorNotice error={{ title: "Only curators can see this", detail: "Sign in with a GitHub account listed in CURATORS on the server.", canRetry: false }} />
            <a href={signInUrl("/curator")} className={`mt-4 inline-block ${quiet}`}>Sign in with GitHub</a>
          </div>
        )}
        {overview.isError && !refused && (
          <div className="pt-8">
            <ErrorNotice error={overview.error as ApiError} onRetry={() => void overview.refetch()} />
          </div>
        )}
        {overview.data && (
          <>
            <section aria-labelledby="queue" className="mt-10">
              <h2 id="queue" className="text-xl font-semibold text-ink">
                Waiting for review <span className="font-normal text-muted">({overview.data.queue.length})</span>
              </h2>
              {overview.data.queue.length === 0 ? (
                <p className="mt-1 text-muted">Nothing to review. Answers rated thumbs up about the sales data show up here.</p>
              ) : (
                <ul className="divide-y divide-rule border-b border-rule">
                  {overview.data.queue.map((c) => <CandidateCard key={c.message_id} candidate={c} />)}
                </ul>
              )}
            </section>
            <Approved overview={overview.data} />
          </>
        )}
      </main>
    </div>
  );
}

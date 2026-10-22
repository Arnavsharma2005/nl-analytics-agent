/** A shared answer at /s/{id}: read-only, exactly as it was when it was shared. Nothing is asked again. */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { getShare } from "../api/actions";
import type { ApiError } from "../api/http";
import { fromMessage } from "../conversation";
import AnswerView from "./AnswerView";
import Brand from "./Brand";
import ErrorNotice from "./ErrorNotice";

const day = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "long", year: "numeric" });

export default function SharedAnswer({ id }: { id: string }) {
  const share = useQuery({ queryKey: ["share", id], queryFn: () => getShare(id), staleTime: Infinity, retry: 1 });
  const data = share.data;

  useEffect(() => {
    if (data) document.title = `${data.question} · Sales analyst`;
  }, [data]);

  return (
    <div className="mx-auto min-h-dvh max-w-[1240px] px-4 sm:px-6">
      <header className="flex items-center justify-between gap-4 py-4">
        <Brand href="/" />
        <a href="/" className="rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-surface hover:bg-ink-soft">
          Ask your own question
        </a>
      </header>
      <main className="mx-auto max-w-[760px] pb-16">
        {share.isPending && <p className="pt-10 text-muted">Opening the shared answer…</p>}
        {share.isError && (
          <div className="pt-10">
            <ErrorNotice error={share.error as ApiError} />
          </div>
        )}
        {data && (
          <>
            <p className="pt-6 pb-2 text-sm text-muted">
              {`A shared answer about ${data.about ?? "the store's sales"}, asked on ${day.format(new Date(data.asked_at))}`}
            </p>
            <AnswerView
              readOnly
              turn={fromMessage({
                id: data.id, conversation_id: data.id, dataset_id: null, question: data.question, answer: data.answer,
                suggestions: [], created_at: data.asked_at,
              })}
            />
            {data.answer.sql && (
              <details className="border-t border-rule pt-4 text-sm">
                <summary className="cursor-pointer text-muted hover:text-ink">The SQL behind this answer</summary>
                <pre className="mt-2 overflow-x-auto rounded-md bg-surface p-3 font-mono text-xs leading-relaxed">{data.answer.sql}</pre>
              </details>
            )}
          </>
        )}
      </main>
    </div>
  );
}

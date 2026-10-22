/** Optional sign-in with GitHub, in the header, and the earlier conversations it keeps. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getConversation, getMe, listConversations, signInUrl, signOut } from "../api/account";
import { getDataset } from "../api/datasets";
import { ApiError } from "../api/http";
import type { ConversationSummary, Message, User } from "../api/types";
import type { Subject } from "../conversation";
import ErrorNotice from "./ErrorNotice";

const when = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

function ago(iso: string): string {
  const days = Math.round((Date.parse(iso) - Date.now()) / 86_400_000);
  if (days === 0) return "Today";
  return when.format(days, "day").replace(/^./, (c) => c.toUpperCase());
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38v-1.33c-2.23.48-2.7-1.07-2.7-1.07-.36-.92-.89-1.17-.89-1.17-.73-.5.06-.49.06-.49.8.06 1.23.83 1.23.83.72 1.22 1.87.87 2.33.66.07-.52.28-.87.5-1.07-1.78-.2-3.65-.89-3.65-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.2c0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

/** The uploaded file a conversation was about, if any: "gone" once it has been deleted. */
async function about(datasetId: string | null): Promise<Subject> {
  if (!datasetId) return null;
  try {
    return await getDataset(datasetId);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return "gone";
    throw e;
  }
}

interface HistoryProps {
  user: User;
  current: string;
  onOpen: (id: string, messages: Message[], about: Subject) => void;
  onClose: () => void;
}

function History({ user, current, onOpen, onClose }: HistoryProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const queryClient = useQueryClient();
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: listConversations, staleTime: 0 });
  const opening = useMutation({
    mutationFn: (c: ConversationSummary) => Promise.all([getConversation(c.id), about(c.dataset_id)]),
    onSuccess: ([messages, subject], c) => {
      onOpen(c.id, messages, subject);
      onClose();
    },
  });
  const leave = useMutation({
    mutationFn: signOut,
    onSuccess: () => {
      queryClient.setQueryData(["me"], { user: null });
      queryClient.removeQueries({ queryKey: ["conversations"] });
      onClose();
    },
  });

  useEffect(() => dialog.current?.showModal(), []);

  return (
    <dialog
      ref={dialog}
      aria-labelledby="history-title"
      onClose={onClose}
      className="m-auto max-h-[80dvh] w-[min(34rem,calc(100vw-2rem))] rounded-xl bg-surface p-5 text-text backdrop:bg-black/40"
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 id="history-title" className="font-semibold">Your conversations</h2>
          <p className="mt-0.5 text-sm text-muted">Signed in as {user.login}. These follow you to every device you sign in on.</p>
        </div>
        <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-sm text-muted hover:bg-rule/60">
          Close
        </button>
      </div>

      {conversations.isPending && <p className="py-6 text-sm text-muted">Loading your conversations…</p>}
      {conversations.isError && <ErrorNotice error={conversations.error as ApiError} onRetry={() => void conversations.refetch()} />}
      {conversations.data?.length === 0 && (
        <p className="py-6 text-sm text-muted">Nothing yet. Questions you ask while signed in are kept here.</p>
      )}
      {opening.isError && <ErrorNotice error={opening.error as ApiError} />}
      {!!conversations.data?.length && (
        <ul className="divide-y divide-rule border-y border-rule">
          {conversations.data.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                disabled={opening.isPending}
                aria-current={c.id === current ? "true" : undefined}
                onClick={() => opening.mutate(c)}
                className="flex w-full items-baseline justify-between gap-4 py-3 text-left hover:text-ink disabled:opacity-60"
              >
                <span className="min-w-0 truncate" title={c.title}>{c.title}</span>
                <span className="shrink-0 text-sm text-muted">
                  {opening.isPending && opening.variables?.id === c.id
                    ? "Opening…"
                    : `${ago(c.updated_at)}, ${c.messages} ${c.messages === 1 ? "question" : "questions"}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => leave.mutate()}
          disabled={leave.isPending}
          className="rounded-md border border-rule px-3 py-1.5 text-sm hover:border-ink"
        >
          Sign out
        </button>
      </div>
    </dialog>
  );
}

/** Says so when a sign-in came back without signing anyone in, and tidies the address bar. */
export function SignInNotice() {
  const [failed, setFailed] = useState(() => new URLSearchParams(location.search).get("signin") === "failed");
  useEffect(() => {
    if (failed) history.replaceState(null, "", location.pathname);
  }, [failed]);
  if (!failed) return null;
  return (
    <div className="mb-4 flex items-start justify-between gap-4 rounded-md border-l-4 border-danger bg-surface px-4 py-3" role="alert">
      <div>
        <p className="font-medium">Signing in with GitHub didn't finish</p>
        <p className="mt-1 text-sm text-muted">You can keep asking without signing in, or try again from the top of the page.</p>
      </div>
      <button type="button" onClick={() => setFailed(false)} className="rounded-md px-2 py-1 text-sm text-muted hover:bg-rule/60">
        Dismiss
      </button>
    </div>
  );
}

interface Props {
  current: string;
  onOpen: (id: string, messages: Message[], about: Subject) => void;
}

export default function Account({ current, onOpen }: Props) {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, staleTime: 5 * 60_000 });
  const [open, setOpen] = useState(false);
  if (!me.data) return null;
  const { user } = me.data;

  if (!user) {
    return (
      <a href={signInUrl(location.pathname)} className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm hover:bg-rule/60">
        <GitHubMark />
        <span>Sign in<span className="sr-only sm:not-sr-only"> with GitHub</span></span>
      </a>
    );
  }
  return (
    <>
      {user.curator && (
        <a href="/curator" className="rounded-md px-2 py-1.5 text-sm hover:bg-rule/60">Review answers</a>
      )}
      <button type="button" onClick={() => setOpen(true)} className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-rule/60">
        {user.avatar_url && <img src={user.avatar_url} alt="" className="size-6 rounded-full" />}
        History
      </button>
      {open && <History user={user} current={current} onOpen={onOpen} onClose={() => setOpen(false)} />}
    </>
  );
}

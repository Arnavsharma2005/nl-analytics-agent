/** Asking about your own CSV file: uploading it, what was found in it, and the files you uploaded earlier. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { deleteDataset, listDatasets, uploadDataset } from "../api/datasets";
import { ApiError } from "../api/http";
import type { Dataset, DatasetColumn } from "../api/types";
import { cn } from "../lib/cn";
import ErrorNotice from "./ErrorNotice";

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

/** "today at 4:10 PM" or "tomorrow at 4:10 PM": uploads are kept for a day. */
function until(iso: string): string {
  const at = new Date(iso);
  const today = new Date().toDateString() === at.toDateString();
  return `${today ? "today" : "tomorrow"} at ${time.format(at)}`;
}

/** Pick a CSV file and upload it. The file input is the control itself, so it works by keyboard and screen reader. */
export function UploadButton({ maxMb, onUploaded }: { maxMb: number; onUploaded: (d: Dataset) => void }) {
  const id = useId();
  const queryClient = useQueryClient();
  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > maxMb * 1024 * 1024) {
        throw new ApiError({ code: "file_too_large", title: "This file is too large", detail: `Files can be up to ${maxMb} MB.`, status: 413 });
      }
      return uploadDataset(file);
    },
    onSuccess: (dataset) => {
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      onUploaded(dataset);
    },
  });

  return (
    <div>
      <input
        id={id}
        type="file"
        accept=".csv,.tsv,.txt,text/csv"
        className="peer sr-only"
        disabled={upload.isPending}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ""; // so choosing the same file again uploads it again
          if (file) upload.mutate(file);
        }}
      />
      <label
        htmlFor={id}
        className={cn(
          "inline-flex cursor-pointer items-center gap-2 rounded-md border border-ink px-3 py-1.5 text-sm font-medium text-ink",
          "hover:bg-ink hover:text-surface peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink",
          upload.isPending && "cursor-wait opacity-60",
        )}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M8 10.5V2.5M4.75 5.5 8 2.25l3.25 3.25M2.5 10v2.25c0 .7.55 1.25 1.25 1.25h8.5c.7 0 1.25-.55 1.25-1.25V10" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Upload a CSV file
      </label>
      <p className="mt-2 text-sm text-muted" role="status">
        {upload.isPending ? `Reading ${upload.variables?.name}…` : `Up to ${maxMb} MB. It is deleted after a day, and personal data such as email addresses is never shown in answers.`}
      </p>
      {upload.isError && (
        <div className="mt-3">
          <ErrorNotice error={upload.error as ApiError} />
        </div>
      )}
    </div>
  );
}

function values(c: DatasetColumn): string {
  if (c.personal) return `Hidden from answers: ${c.personal}`;
  if (c.low != null && c.high != null) return c.low === c.high ? String(c.low) : `${c.low} to ${c.high}`;
  if (c.examples.length) return c.examples.slice(0, 3).join(", ") + (c.distinct > 3 ? ", …" : "");
  return "";
}

/** Which uploaded file the questions are about, its columns, and the way back to the demo data. */
export function DatasetBar({ dataset, onLeave }: { dataset: Dataset | "gone"; onLeave: () => void }) {
  if (dataset === "gone") {
    return (
      <section aria-label="Uploaded file" className="mb-4 rounded-md border-l-4 border-turmeric bg-surface px-4 py-3">
        <p className="font-medium">The file this conversation was about has been deleted</p>
        <p className="mt-1 text-sm text-muted">Uploads are kept for a day. Upload it again to keep asking about it.</p>
        <button type="button" onClick={onLeave} className="mt-2 text-sm font-medium text-ink underline underline-offset-4">
          Ask about the sales demo instead
        </button>
      </section>
    );
  }
  const hidden = dataset.columns.filter((c) => c.personal).length;
  return (
    <section aria-label="Uploaded file" className="mb-4 border-b border-rule pb-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="min-w-0">
          Asking about <strong className="font-semibold break-all text-ink">{dataset.name}</strong>
          <span className="text-muted">
            {`: ${count(dataset.rows, "row")} and ${count(dataset.columns.length, "column")}`}
            {hidden > 0 && `, ${hidden} kept private`}
          </span>
        </p>
        <button type="button" onClick={onLeave} className="shrink-0 text-sm text-muted underline underline-offset-4 hover:text-ink">
          Back to the sales demo
        </button>
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted hover:text-ink">See the columns (deleted {until(dataset.expires_at)})</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-muted">
              <tr>
                <th scope="col" className="py-1 pr-4 font-medium">Column</th>
                <th scope="col" className="py-1 pr-4 font-medium">Kind</th>
                <th scope="col" className="py-1 font-medium">Values</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {dataset.columns.map((c) => (
                <tr key={c.name}>
                  <th scope="row" className="py-1 pr-4 font-normal">
                    <code className="font-mono">{c.name}</code>
                    {c.header !== c.name && <span className="block text-xs text-muted">{c.header}</span>}
                  </th>
                  <td className="py-1 pr-4 text-muted">{c.kind}</td>
                  <td className={cn("py-1", c.personal ? "font-medium text-text" : "text-muted")}>{values(c)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

/** Files uploaded earlier that haven't expired, to ask about again or delete. */
export function YourUploads({ current, onPick }: { current: string | null; onPick: (d: Dataset | null) => void }) {
  const queryClient = useQueryClient();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: listDatasets, staleTime: 30_000 });
  const remove = useMutation({
    mutationFn: (d: Dataset) => deleteDataset(d.id),
    onSuccess: (_, d) => {
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      if (d.id === current) onPick(null);
    },
  });
  if (!datasets.data?.length) return null;

  return (
    <div className="mt-6">
      <h3 className="text-sm font-medium text-muted">Your uploads</h3>
      {remove.isError && <ErrorNotice error={remove.error as ApiError} />}
      <ul className="mt-2 divide-y divide-rule border-y border-rule">
        {datasets.data.map((d) => (
          <li key={d.id} className="flex items-baseline justify-between gap-4">
            <button type="button" onClick={() => onPick(d)} className="min-w-0 flex-1 truncate py-3 text-left hover:text-ink" title={d.name}>
              {d.name}
              <span className="ml-2 text-sm text-muted">{count(d.rows, "row")}</span>
            </button>
            <button
              type="button"
              onClick={() => remove.mutate(d)}
              disabled={remove.isPending}
              aria-label={`Delete ${d.name}`}
              className="shrink-0 rounded-md px-2 py-1 text-sm text-muted hover:bg-rule/60 hover:text-danger"
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

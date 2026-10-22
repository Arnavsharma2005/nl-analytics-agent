/** Rate an answer. A thumbs down asks what was wrong, so the rating can be acted on. */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { sendFeedback } from "../api/actions";
import type { Feedback as Rating } from "../api/types";
import { cn } from "../lib/cn";

const REASONS: { value: NonNullable<Rating["category"]>; label: string }[] = [
  { value: "wrong_data", label: "The numbers are wrong" },
  { value: "misunderstood", label: "It misread the question" },
  { value: "wrong_chart", label: "The chart doesn't fit" },
  { value: "lost_context", label: "It forgot what I asked before" },
  { value: "other", label: "Something else" },
];

const Thumb = ({ down }: { down?: boolean }) => (
  <svg viewBox="0 0 20 20" aria-hidden="true" className={cn("size-4", down && "rotate-180")} fill="currentColor">
    <path d="M7 8.5V17H4.5A1.5 1.5 0 0 1 3 15.5v-5.5A1.5 1.5 0 0 1 4.5 8.5H7Zm1.5 8.5h6.2a2 2 0 0 0 2-1.6l1-5A2 2 0 0 0 15.7 8H12V4.5A1.5 1.5 0 0 0 10.5 3h-.3a1 1 0 0 0-.9.6L8.5 8v9Z" />
  </svg>
);

export default function Feedback({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState<Rating["rating"] | null>(null);
  const [asking, setAsking] = useState(false);
  const [category, setCategory] = useState<Rating["category"]>(null);
  const [comment, setComment] = useState("");
  const send = useMutation({ mutationFn: (f: Rating) => sendFeedback(messageId, f) });

  const rate = (value: "up" | "down") => {
    setRating(value);
    if (value === "up") {
      setAsking(false);
      send.mutate({ rating: "up" });
    } else {
      setAsking(true);
    }
  };

  const button = (value: "up" | "down", label: string) => (
    <button
      type="button"
      aria-pressed={rating === value}
      aria-label={label}
      title={label}
      onClick={() => rate(value)}
      className={cn("rounded-md p-1.5 text-muted hover:bg-rule/60 hover:text-text", rating === value && "bg-rule/60 text-ink")}
    >
      <Thumb down={value === "down"} />
    </button>
  );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1">
        {button("up", "Good answer")}
        {button("down", "Bad answer")}
        {send.isSuccess && !asking && <span className="ml-1 text-xs text-muted" role="status">Thanks, noted.</span>}
        {send.isError && <span className="ml-1 text-xs text-danger" role="status">Couldn't send that. Try again.</span>}
      </div>
      {asking && (
        <form
          className="flex max-w-md flex-col gap-2 rounded-md border border-rule bg-surface p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate({ rating: "down", category, comment: comment.trim() || null }, { onSuccess: () => setAsking(false) });
          }}
        >
          <fieldset>
            <legend className="mb-2 text-sm font-medium">What was wrong?</legend>
            <div className="flex flex-wrap gap-1.5">
              {REASONS.map((r) => (
                <label key={r.value} className={cn(
                  "cursor-pointer rounded-full border border-rule px-2.5 py-1 text-xs has-focus-visible:outline-2 has-focus-visible:outline-ink",
                  category === r.value && "border-ink bg-ink text-surface",
                )}>
                  <input type="radio" name="reason" value={r.value} className="sr-only" checked={category === r.value} onChange={() => setCategory(r.value)} />
                  {r.label}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="text-sm">
            <span className="sr-only">Anything else (optional)</span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={500}
              rows={2}
              placeholder="Anything else? (optional)"
              className="w-full resize-none rounded-md border border-rule bg-paper px-2 py-1.5 text-sm"
            />
          </label>
          <div className="flex gap-2">
            <button type="submit" disabled={send.isPending} className="rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-surface disabled:opacity-60">
              Send feedback
            </button>
            <button type="button" onClick={() => setAsking(false)} className="px-2 text-sm text-muted">Cancel</button>
          </div>
        </form>
      )}
    </div>
  );
}

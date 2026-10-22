/** The question box. Enter sends, Shift+Enter starts a new line. */
import { useEffect, useRef, useState } from "react";
import MicButton, { canRecord } from "./MicButton";

interface Props {
  onAsk: (question: string) => void;
  busy: boolean;
  voice: boolean;
  /** What questions are about, for the placeholder; null when nothing can be asked (the uploaded file is gone). */
  about: string | null;
}

const MIN = 3;
const MAX = 500;

export default function Composer({ onAsk, busy, voice, about }: Props) {
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const question = text.trim();
  const ready = question.length >= MIN && !busy && about !== null;

  useEffect(() => {
    const el = box.current!;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  const submit = () => {
    if (!ready) return;
    onAsk(question);
    setText("");
    setNote(null);
  };

  return (
    <form
      className="flex flex-col gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="flex items-end gap-1 rounded-2xl border border-rule bg-surface p-1.5 shadow-[0_1px_0_var(--rule)] focus-within:border-ink">
        <label htmlFor="question" className="sr-only">Your question</label>
        <textarea
          id="question"
          ref={box}
          rows={1}
          value={text}
          maxLength={MAX}
          disabled={about === null}
          placeholder={about === null ? "Start a new conversation to keep asking" : `Ask a question about ${about}`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          className="min-h-10 flex-1 resize-none bg-transparent px-3 py-2 text-base outline-none placeholder:text-muted"
        />
        {voice && canRecord() && (
          <MicButton
            onText={(t) => {
              setText(t);
              setNote("Check the question, then press Enter to ask it.");
              box.current?.focus();
            }}
            onError={setNote}
          />
        )}
        <button
          type="submit"
          disabled={!ready}
          aria-label="Ask"
          className="grid size-10 shrink-0 place-items-center rounded-full bg-ink text-surface disabled:bg-rule disabled:text-muted"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="size-5">
            <path d="M10 3.5a.75.75 0 0 1 .53.22l5 5a.75.75 0 1 1-1.06 1.06l-3.72-3.72V16a.75.75 0 0 1-1.5 0V6.06L5.53 9.78a.75.75 0 0 1-1.06-1.06l5-5A.75.75 0 0 1 10 3.5Z" />
          </svg>
        </button>
      </div>
      <p className="min-h-5 px-3 text-xs text-muted" role="status">
        {note ?? (busy ? "Working on the answer…" : "")}
      </p>
    </form>
  );
}

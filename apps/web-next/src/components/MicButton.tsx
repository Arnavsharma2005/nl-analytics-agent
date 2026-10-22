/** Ask by voice: record a short question, turn it into text, and put it in the box to check before sending. */
import { useEffect, useRef, useState } from "react";
import { transcribe } from "../api/actions";
import { ApiError } from "../api/http";
import { cn } from "../lib/cn";

const TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
const MAX_SECONDS = 55;

export const canRecord = () => typeof window.MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

interface Props {
  onText: (text: string) => void;
  onError: (message: string) => void;
}

export default function MicButton({ onText, onError }: Props) {
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => {
    clearTimeout(timer.current);
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
  }, []);

  const start = async () => {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return onError("Allow the microphone to ask by voice.");
    }
    const mimeType = TYPES.find((t) => MediaRecorder.isTypeSupported(t));
    const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      clearTimeout(timer.current);
      setState("transcribing");
      try {
        const text = await transcribe(new Blob(chunks, { type: rec.mimeType.split(";")[0] || "audio/webm" }));
        if (text) onText(text);
        else onError("No speech was heard. Try again a little closer to the microphone.");
      } catch (e) {
        onError(e instanceof ApiError ? e.message : "The recording could not be turned into text.");
      } finally {
        setState("idle");
      }
    };
    recorder.current = rec;
    rec.start();
    setState("recording");
    timer.current = setTimeout(() => rec.state === "recording" && rec.stop(), MAX_SECONDS * 1000);
  };

  const stop = () => recorder.current?.state === "recording" && recorder.current.stop();

  const label = state === "recording" ? "Stop recording" : state === "transcribing" ? "Turning speech into text" : "Ask by voice";
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={state === "recording"}
      disabled={state === "transcribing"}
      onClick={state === "recording" ? stop : start}
      className={cn(
        "grid size-10 shrink-0 place-items-center rounded-full text-muted hover:bg-rule/60 hover:text-text disabled:opacity-60",
        state === "recording" && "bg-danger text-surface hover:bg-danger hover:text-surface",
      )}
    >
      <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className={cn("size-5", state === "transcribing" && "animate-pulse")}>
        {state === "recording"
          ? <rect x="5" y="5" width="10" height="10" rx="1.5" />
          : <path d="M10 2a3 3 0 0 0-3 3v5a3 3 0 1 0 6 0V5a3 3 0 0 0-3-3Zm-5 7.5a.75.75 0 0 0-1.5 0 6.5 6.5 0 0 0 5.75 6.46V18h-2a.75.75 0 0 0 0 1.5h5.5a.75.75 0 0 0 0-1.5h-2v-2.04A6.5 6.5 0 0 0 16.5 9.5a.75.75 0 0 0-1.5 0 5 5 0 0 1-10 0Z" />}
      </svg>
    </button>
  );
}

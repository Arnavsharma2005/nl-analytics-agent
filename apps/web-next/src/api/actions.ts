import { ApiError, postJSON, problemFrom } from "./http";
import { ensureSession } from "./session";
import type { Explanation, Feedback } from "./types";

export const sendFeedback = (messageId: string, feedback: Feedback) =>
  postJSON(`/v1/messages/${messageId}/feedback`, feedback);

export const explain = async (messageId: string) => (await postJSON<Explanation>(`/v1/messages/${messageId}/explain`))!.steps;

export async function transcribe(audio: Blob): Promise<string> {
  await ensureSession();
  const form = new FormData();
  form.append("audio", audio, "question");
  const r = await fetch("/v1/transcribe", { method: "POST", body: form }).catch(() => null);
  if (!r) throw new ApiError({ code: "network", title: "Can't reach the server", status: 0 });
  if (!r.ok) throw await problemFrom(r);
  return (await r.json()).text;
}

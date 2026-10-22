import { ApiError } from "../api/http";

function wait(seconds: number): string {
  if (seconds < 90) return "in a minute";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 90) return `in ${minutes} minutes`;
  return `in about ${Math.round(minutes / 60)} hours`;
}

/** What went wrong and what to do about it, in words a visitor can act on. */
export function describe(error: ApiError): Notice {
  switch (error.code) {
    case "quota_exceeded":
      return { title: "You've reached the question limit for now", detail: `You can ask again ${wait(error.retryAfter ?? 3600)}.`, canRetry: false };
    case "daily_limit_reached":
      return {
        title: "The demo has used today's model budget",
        detail: `Questions answered before still work. New ones can be asked again ${wait(error.retryAfter ?? 3600)}.`,
        canRetry: false,
      };
    case "bot_check_failed":
    case "bot_check_required":
      return { title: "The bot check didn't pass", detail: "Reload the page and try again.", canRetry: false };
    case "all_providers_failed":
      return { title: "Every model provider is busy right now", detail: "Try again in a moment.", canRetry: true };
    case "network":
      return { title: "Can't reach the server", detail: "Check your connection and try again.", canRetry: true };
    default:
      return { title: error.message || "Something went wrong", canRetry: error.status === 0 || error.status >= 500 };
  }
}

type Notice = { title: string; detail?: string; canRetry: boolean };

export default function ErrorNotice({ error, onRetry }: { error: ApiError | Notice; onRetry?: () => void }) {
  const { title, detail, canRetry } = error instanceof ApiError ? describe(error) : error;
  return (
    <div role="alert" className="rounded-md border-l-4 border-danger bg-surface px-4 py-3">
      <p className="font-medium">{title}</p>
      {detail && <p className="mt-1 text-sm text-muted">{detail}</p>}
      {canRetry && onRetry && (
        <button type="button" onClick={onRetry} className="mt-2 text-sm font-medium text-ink underline underline-offset-4">
          Ask again
        </button>
      )}
    </div>
  );
}

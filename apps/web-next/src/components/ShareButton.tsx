/** Share an answer as a read-only link, copied to the clipboard, with a way to stop sharing it. */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { shareMessage, shareUrl, stopSharing } from "../api/actions";
import type { ApiError } from "../api/http";
import type { Share } from "../api/types";
import ErrorNotice from "./ErrorNotice";

const action = "rounded-md px-2 py-1.5 text-sm text-muted hover:bg-rule/60 hover:text-text";

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false; // no clipboard permission (or an old browser): the link is shown to copy by hand
  }
}

export default function ShareButton({ messageId }: { messageId: string }) {
  const [shared, setShared] = useState<{ share: Share; copied: boolean } | null>(null);
  const share = useMutation({
    mutationFn: async () => {
      const made = await shareMessage(messageId);
      return { share: made, copied: await copy(shareUrl(made.id)) };
    },
    onSuccess: setShared,
  });
  const stop = useMutation({
    mutationFn: () => stopSharing(shared!.share.id),
    onSuccess: () => setShared(null),
  });

  if (!shared) {
    return (
      <>
        <button type="button" className={action} onClick={() => share.mutate()} disabled={share.isPending}>
          {share.isPending ? "Making a link…" : "Share"}
        </button>
        {share.isError && (
          <div className="basis-full">
            <ErrorNotice error={share.error as ApiError} />
          </div>
        )}
      </>
    );
  }

  const url = shareUrl(shared.share.id);
  return (
    <div className="flex basis-full flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-rule bg-surface px-3 py-2 text-sm">
      <p role="status" className="text-text">
        {shared.copied ? "Link copied. Anyone with it can see this answer, without signing in." : "Anyone with this link can see this answer:"}
      </p>
      <label className="sr-only" htmlFor={`share-${shared.share.id}`}>Share link</label>
      <input
        id={`share-${shared.share.id}`}
        readOnly
        value={url}
        onFocus={(e) => e.currentTarget.select()}
        className="min-w-0 flex-1 rounded border border-rule bg-paper px-2 py-1 font-mono text-xs text-text"
      />
      <button type="button" className={action} onClick={() => stop.mutate()} disabled={stop.isPending}>
        Stop sharing
      </button>
      {stop.isError && <ErrorNotice error={stop.error as ApiError} />}
    </div>
  );
}

/**
 * The API runs on a free host that sleeps when nobody uses it, and takes up to a minute to wake. Ping it as the
 * page opens, and say so if it is slow to answer, so a first question that takes a while doesn't look broken.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

const SLOW_MS = 2500;

async function ping(): Promise<true> {
  const r = await fetch("/healthz", { cache: "no-store" });
  if (!r.ok) throw new Error(`healthz ${r.status}`);
  return true;
}

export default function ServerStatus() {
  const health = useQuery({ queryKey: ["healthz"], queryFn: ping, retry: 12, retryDelay: 5000, staleTime: Infinity });
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setSlow(true), SLOW_MS);
    return () => clearTimeout(t);
  }, []);

  if (health.isSuccess || !slow) return null;
  return (
    <p role="status" className="rounded-md bg-mark px-4 py-2 text-sm">
      {health.isError
        ? "The server isn't answering. Try reloading the page in a minute."
        : "The server is waking up. It sleeps when idle, so the first answer can take up to a minute."}
    </p>
  );
}

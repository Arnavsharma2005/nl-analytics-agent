import { useQuery } from "@tanstack/react-query";
import { getInsights } from "../api/insights";

/** What moved unusually last week, each a question to ask; nothing at all until the nightly job has run. */
export default function Insights({ onAsk }: { onAsk: (q: string) => void }) {
  const feed = useQuery({ queryKey: ["insights"], queryFn: getInsights, staleTime: 10 * 60_000, retry: false });
  const data = feed.data;
  if (!data?.week_start || data.insights.length === 0) return null;
  const week = new Date(`${data.week_start}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <section className="mt-10" aria-labelledby="insights">
      <h3 id="insights" className="font-semibold text-ink">
        What changed in the week of {week}
      </h3>
      <p className="mt-1 mb-3 max-w-[60ch] text-muted">Unusual moves found overnight. Pick one to ask why.</p>
      <ul className="divide-y divide-rule border-y border-rule">
        {data.insights.map((i) => (
          <li key={`${i.metric}|${i.dimension}|${i.group}`}>
            <button type="button" onClick={() => onAsk(i.question)} className="w-full py-3 text-left hover:text-ink">
              <span className="block">{i.headline}</span>
              <span className="block text-sm text-muted">{i.question}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

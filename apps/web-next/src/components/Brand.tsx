/** The app's name and mark, at the top left of every page. */
export default function Brand({ href }: { href?: string }) {
  const mark = (
    <>
      <svg viewBox="0 0 16 16" aria-hidden="true" className="size-4 self-center"><rect x="1" y="9" width="3" height="6" rx="1" fill="var(--ink)" /><rect x="6.5" y="5" width="3" height="10" rx="1" fill="var(--ink)" /><rect x="12" y="1" width="3" height="14" rx="1" fill="var(--turmeric)" /></svg>
      Sales analyst
    </>
  );
  return (
    <h1 className="flex items-baseline gap-2 text-base font-semibold text-ink">
      {href ? <a href={href} className="flex items-baseline gap-2">{mark}</a> : mark}
    </h1>
  );
}

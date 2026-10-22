const MONEY = /(revenue|profit|cost|price|aov|value|sales|amount|spend)/i;
const PERCENT = /(pct|percent|rate|share|margin|growth)/i;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const isMoney = (col: string) => MONEY.test(col) && !PERCENT.test(col);
export const isPercent = (col: string) => PERCENT.test(col);

export function humanize(col: string): string {
  const s = col.replace(/_/g, " ").replace(/\bpct\b/, "%").replace(/\baov\b/i, "AOV");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** ₹ in lakh / crore, the way Indian business users read money. */
export function compactINR(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e7) return `₹${(n / 1e7).toFixed(abs >= 1e8 ? 1 : 2)} Cr`;
  if (abs >= 1e5) return `₹${(n / 1e5).toFixed(1)} L`;
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function formatValue(v: unknown, col: string, compact = false): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") {
    if (isPercent(col)) return `${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}%`;
    if (isMoney(col)) return compact ? compactINR(v) : `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return compact && Math.abs(v) >= 1e5 ? compactINR(v).slice(1) : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  }
  if (typeof v === "string") return formatDate(v, col);
  return String(v);
}

export function formatDate(v: string, col = ""): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (!m) return v;
  const [, y, mo, d] = m;
  if (/quarter/i.test(col)) return `Q${Math.floor((Number(mo) - 1) / 3) + 1} ${y}`;
  if (/month/i.test(col) || (d === "01" && !/(week|day|date)/i.test(col))) return `${MONTHS[Number(mo) - 1]} ${y}`;
  return `${Number(d)} ${MONTHS[Number(mo) - 1]} ${y}`;
}

import type { Cell } from "../api/types";

export function toCSV(columns: string[], rows: Cell[][]): string {
  const quote = (v: Cell) => {
    if (v === null) return "";
    const s = String(v);
    // Leading =, +, - or @ would run as a formula in a spreadsheet.
    const safe = /^[=+\-@]/.test(s) && typeof v === "string" ? `'${s}` : s;
    return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  return [columns, ...rows].map((r) => r.map(quote).join(",")).join("\r\n") + "\r\n";
}

export function save(name: string, data: Blob | string) {
  const url = typeof data === "string" ? data : URL.createObjectURL(data);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  if (typeof data !== "string") setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const fileName = (question: string, ext: string) =>
  `${question.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "answer"}.${ext}`;

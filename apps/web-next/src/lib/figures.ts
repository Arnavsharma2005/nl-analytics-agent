/**
 * Split an answer into plain text and figures (₹ amounts, percentages, counts), so the figures can be marked.
 * Years, quarters and small ordinals are left as text: they name things rather than measure them.
 */
const FIGURE = /₹\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:Cr|L|K|crore|lakh))?|\d[\d,]*(?:\.\d+)?\s?%|\b\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\b|\b\d+\.\d+\b|\b\d{2,}\b/g;
const NOT_A_FIGURE = /^(19|20)\d\d$/;

export type Part = { text: string; figure: boolean };

export function splitFigures(text: string): Part[] {
  const parts: Part[] = [];
  let last = 0;
  for (const m of text.matchAll(FIGURE)) {
    const value = m[0];
    if (NOT_A_FIGURE.test(value)) continue;
    if (m.index > last) parts.push({ text: text.slice(last, m.index), figure: false });
    parts.push({ text: value, figure: true });
    last = m.index + value.length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), figure: false });
  return parts;
}

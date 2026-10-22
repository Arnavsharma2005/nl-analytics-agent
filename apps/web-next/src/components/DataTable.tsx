import type { Cell } from "../api/types";
import { formatValue, humanize } from "../lib/format";

interface Props {
  columns: string[];
  rows: Cell[][];
  truncated?: boolean;
  caption: string;
}

export default function DataTable({ columns, rows, truncated, caption }: Props) {
  const numeric = columns.map((_, i) => rows.some((r) => typeof r[i] === "number"));
  return (
    <div className="max-h-96 overflow-auto rounded-md border border-rule bg-surface" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-surface">
          <tr>
            {columns.map((c, i) => (
              <th key={c} scope="col" className={`border-b border-rule px-3 py-2 font-medium text-muted ${numeric[i] ? "text-right" : "text-left"}`}>
                {humanize(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className="border-b border-rule last:border-0">
              {r.map((v, i) => (
                <td key={i} className={`px-3 py-1.5 whitespace-nowrap ${numeric[i] ? "text-right" : ""}`}>{formatValue(v, columns[i])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {truncated && <p className="px-3 py-2 text-xs text-muted">Showing the first {rows.length} rows.</p>}
    </div>
  );
}

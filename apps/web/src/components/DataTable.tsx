import { formatValue, humanize } from "../format";
import type { Cell } from "../types";

export default function DataTable({ columns, rows }: { columns: string[]; rows: Cell[][] }) {
  if (!rows.length) return <p className="muted">No rows matched this question.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c}>{humanize(c)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={typeof v === "number" ? "num" : undefined}>{formatValue(v, columns[j])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

import { useEffect, useState } from "react";
import { getSchema } from "../api";
import type { TableInfo } from "../types";

export default function SchemaPanel() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  useEffect(() => {
    getSchema().then(setTables).catch(() => setTables([]));
  }, []);
  if (!tables.length) return null;
  return (
    <details className="schema">
      <summary>What data is here?</summary>
      <p className="muted">A sample e-commerce business, January 2024 to December 2025. Money is in rupees.</p>
      <ul>
        {tables.map((t) => (
          <li key={t.table}>
            <strong>{t.table}</strong>
            {t.description ? ` – ${t.description}` : ""}
            <div className="cols">{t.columns.map((c) => c.name).join(", ")}</div>
          </li>
        ))}
      </ul>
    </details>
  );
}

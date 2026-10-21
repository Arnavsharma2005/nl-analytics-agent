export type ChartType = "bar" | "line" | "pie" | "scatter" | "kpi" | "table";

export interface ChartSpec {
  type: ChartType;
  x: string | null;
  y: string[];
  series: string | null;
  title: string;
}

export type Cell = string | number | boolean | null;

export interface AskResult {
  question: string;
  intent: "data_query" | "schema_question" | "out_of_scope" | null;
  answer: string | null;
  sql: string | null;
  sql_reasoning: string | null;
  columns: string[];
  rows: Cell[][];
  truncated: boolean;
  chart: ChartSpec | null;
  chart_source: "llm" | "fallback" | null;
  summary: string | null;
  error: string | null;
  attempts: number;
  timings: Record<string, number>;
  llm_calls: LLMCall[];
}

export interface LLMCall {
  step: string;
  provider: string;
  ok: boolean;
  error?: string;
  seconds: number;
}

export interface Step {
  node: string;
  label: string;
}

export interface TableInfo {
  table: string;
  description: string | null;
  columns: { name: string; type: string; description: string | null }[];
}

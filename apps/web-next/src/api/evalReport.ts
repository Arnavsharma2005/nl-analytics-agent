/** The public eval results (eval/publish.py writes public/eval-report.json from the committed runs in eval/results/). */

export type Score = { correct: number; questions: number; accuracy_pct: number | null };

export type Run = {
  file: string;
  run_at: string;
  commit: string;
  set: "questions" | "conversations";
  models: string[];
  sql_written_by: Record<string, number>;
  features: string[];
  all: Score;
  held_out: Score | null;
  median_latency_s: number | null;
};

export type Question = {
  id: number | string;
  question: string;
  category: string;
  difficulty: string;
  correct: boolean;
  attempts: number;
  sql: string | null;
  error: string | null;
};

export type Latest = Run & {
  held_out: Score;
  by_difficulty: Record<string, Score>;
  by_category: Record<string, Score>;
  repaired_by_retry: number;
  questions: Question[];
};

export type EvalReportData = {
  held_out_questions: Record<"questions" | "conversations", number>;
  runs: Run[];
  latest: Partial<Record<"questions" | "conversations", Latest>>;
};

export async function getEvalReport(): Promise<EvalReportData> {
  const response = await fetch("/eval-report.json", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`The results could not be loaded (${response.status}).`);
  return response.json();
}

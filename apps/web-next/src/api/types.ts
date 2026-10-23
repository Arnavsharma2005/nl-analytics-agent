/** Types generated from the API's OpenAPI spec (src/api/schema.d.ts; `npm run gen:api` refreshes them). */
import type { components } from "./schema";

type Schemas = components["schemas"];
export type Answer = Schemas["Answer"];
export type ChartSpec = Schemas["ChartSpec"];
export type Config = Schemas["ConfigV1"];
export type Message = Schemas["MessageV1"];
export type Feedback = Schemas["FeedbackV1"];
export type Explanation = Schemas["ExplanationV1"];
export type Me = Schemas["MeV1"];
export type User = Schemas["UserV1"];
export type ConversationSummary = Schemas["ConversationV1"];
export type Dataset = Schemas["DatasetV1"];
export type Share = Schemas["ShareV1"];
export type DatasetColumn = Schemas["ColumnV1"];
export type CuratorOverview = Schemas["CuratorV1"];
export type Candidate = Schemas["CandidateV1"];
export type Curated = Schemas["CuratedV1"];
export type Review = Schemas["ReviewV1"];
export type Cell = string | number | boolean | null;

/** The data of each server-sent event on POST /v1/conversations/{id}/messages. */
export interface StreamEvents {
  message: { id: string; conversation_id: string };
  step: { node: string; label: string };
  table: { columns: string[]; rows: Cell[][]; truncated: boolean };
  chart: ChartSpec;
  token: { text: string; replace: boolean };
  done: Message;
  error: Problem;
}

/** An RFC 9457 problem object, as every API error is (docs/errors.md). */
export interface Problem {
  code: string;
  title: string;
  status: number;
  detail?: string;
  retry_after?: number;
}
export type Insights = Schemas["InsightsV1"];
export type Insight = Schemas["InsightV1"];
export type ReportStatus = Schemas["ReportStatusV1"];
export type ApiKey = Schemas["KeyV1"];
export type NewApiKey = Schemas["NewKeyV1"];
export type Report = Schemas["ReportV1"];
export type ReportStep = Schemas["StepV1"];

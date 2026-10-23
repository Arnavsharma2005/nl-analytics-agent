/** Deep Analysis reports: start one on a broad question, then poll it until it is done. Signed-in users only. */
import { getJSON, postJSON } from "./http";
import type { ReportStatus } from "./types";

export const startReport = async (question: string) => (await postJSON<ReportStatus>("/v1/reports", { question }))!;

export const getReport = (id: string) => getJSON<ReportStatus>(`/v1/reports/${id}`);

export const listReports = () => getJSON<ReportStatus[]>("/v1/reports");

/** Asking about your own data instead of the demo's: an uploaded CSV file or an imported Google Sheet, kept for a day. */
import { ApiError, deleteJSON, getJSON, postForm, postJSON } from "./http";
import { ensureSession } from "./session";
import type { Dataset } from "./types";

/** Send a request that needs a session, starting another and sending it again if the session has expired. */
async function withSession<T>(send: () => Promise<T>): Promise<T> {
  await ensureSession();
  try {
    return await send();
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 403) throw e;
    await ensureSession(true);
    return send();
  }
}

export function uploadDataset(file: File): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return withSession(() => postForm<Dataset>("/v1/datasets", form));
}

/** Import a Google Sheet shared by link (its tab named by the link's #gid, or else the first). */
export const importSheet = (url: string) =>
  withSession(async () => (await postJSON<Dataset>("/v1/datasets/sheets", { url }))!);

/** Import an imported sheet again, to ask about what it holds now. */
export const refreshDataset = (id: string) =>
  withSession(async () => (await postJSON<Dataset>(`/v1/datasets/${id}/refresh`))!);

export const listDatasets = () => getJSON<Dataset[]>("/v1/datasets");

export const getDataset = (id: string) => getJSON<Dataset>(`/v1/datasets/${id}`);

export const deleteDataset = (id: string) => deleteJSON(`/v1/datasets/${id}`);

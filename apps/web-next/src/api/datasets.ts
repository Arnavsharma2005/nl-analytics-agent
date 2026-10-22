/** Uploading a CSV file to ask questions about it instead of the demo data. Uploads are kept for a day. */
import { ApiError, deleteJSON, getJSON, postForm } from "./http";
import { ensureSession } from "./session";
import type { Dataset } from "./types";

export async function uploadDataset(file: File): Promise<Dataset> {
  await ensureSession();
  const form = new FormData();
  form.append("file", file);
  try {
    return await postForm<Dataset>("/v1/datasets", form);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 403) throw e;
    await ensureSession(true); // the session expired: start another and send the file again
    return postForm<Dataset>("/v1/datasets", form);
  }
}

export const listDatasets = () => getJSON<Dataset[]>("/v1/datasets");

export const getDataset = (id: string) => getJSON<Dataset>(`/v1/datasets/${id}`);

export const deleteDataset = (id: string) => deleteJSON(`/v1/datasets/${id}`);

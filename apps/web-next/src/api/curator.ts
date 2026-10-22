/** The Curator: review answers rated thumbs up, and export the approved ones to the verified library. */
import { getJSON, offline, postJSON, problemFrom } from "./http";
import type { Curated, CuratorOverview, Review } from "./types";

export const getCurator = () => getJSON<CuratorOverview>("/v1/curator");

export const review = async (messageId: string, body: Review) => (await postJSON<Curated>(`/v1/curator/${messageId}`, body))!;

/** Open a draft pull request adding the approved answers to semantic/queries.yml. */
export const exportAsPullRequest = async () => (await postJSON<{ url: string; count: number }>("/v1/curator/export"))!;

/** The approved answers as YAML, to paste at the end of semantic/queries.yml. */
export async function exportAsYaml(): Promise<string> {
  const r = await fetch("/v1/curator/export.yml").catch(() => {
    throw offline();
  });
  if (!r.ok) throw await problemFrom(r);
  return r.text();
}

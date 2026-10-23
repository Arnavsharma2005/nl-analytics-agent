/** API keys: a signed-in user makes keys to call the API from scripts, the SDKs and the widget, and revokes them. */
import { deleteJSON, getJSON, postJSON } from "./http";
import type { ApiKey, NewApiKey } from "./types";

export const listKeys = () => getJSON<ApiKey[]>("/v1/keys");

export const createKey = async (name: string, origins: string[]) => (await postJSON<NewApiKey>("/v1/keys", { name, origins }))!;

export const revokeKey = (id: string) => deleteJSON(`/v1/keys/${id}`);

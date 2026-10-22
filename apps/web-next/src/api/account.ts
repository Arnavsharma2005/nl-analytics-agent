/** Signing in with GitHub (optional), and the history it keeps. */
import { getJSON, postJSON } from "./http";
import type { ConversationSummary, Me, Message } from "./types";

export const getMe = () => getJSON<Me>("/v1/me");

/** Where the browser goes to sign in; it comes back to `next` on this site. */
export const signInUrl = (next = "/") => `/v1/auth/github?next=${encodeURIComponent(next)}`;

export const signOut = () => postJSON("/v1/auth/logout");

export const listConversations = () => getJSON<ConversationSummary[]>("/v1/conversations");

export const getConversation = (id: string) => getJSON<Message[]>(`/v1/conversations/${id}/messages`);

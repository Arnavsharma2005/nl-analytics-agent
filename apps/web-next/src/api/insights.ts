/** The insights feed: the latest complete week's unusual moves, found by the nightly job. */
import { getJSON } from "./http";
import type { Insights } from "./types";

export const getInsights = () => getJSON<Insights>("/v1/insights");

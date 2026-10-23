# nl-analytics-sdk (TypeScript)

A small client for the analytics agent's `/v1` API, for Node 22+ and browsers. Its types are generated from the
API's OpenAPI spec (`npm run gen`), so they match the API of the same commit. No runtime dependencies.

```sh
npm install nl-analytics-sdk
```

Each app release publishes the SDK at the same version, so SDK 2.4 speaks to the API of release 2.4.

```ts
import { Analyst, ApiError } from "nl-analytics-sdk";

const analyst = new Analyst({ baseUrl: "https://nl-analytics-agent.onrender.com", apiKey: "aak_..." });

const chat = analyst.conversation();
const first = await chat.ask("Revenue by region in 2025");
console.log(first.answer.summary, first.answer.rows);

// Streamed: steps, then the table and chart, then the summary as it is written.
for await (const event of chat.stream("and in 2024?")) {
  if (event.type === "token") process.stdout.write(event.text);
  if (event.type === "done") console.log("\n", event.message.answer.sql);
}

const started = await analyst.startReport("How did 2025 go, and where should we focus next year?");
const report = await analyst.waitForReport(started.id);
```

Make a key at `/keys` on the web app after signing in. In a web page, use a key that lists the page's origin,
and treat it as public: anyone who views the page can read it. A refused request throws `ApiError` with a stable
`code` and, when the API gives one, `retryAfter` in seconds.

`npm test` runs the tests; `npm run build` writes `dist/`.

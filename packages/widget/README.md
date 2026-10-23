# `<nl-analyst>` widget

A web component that lets visitors to any site ask the analytics agent questions: answers stream in with a
summary, the first rows of the result and the SQL behind it. Follow-up questions are understood with the earlier
ones. About 9 KB, no dependencies, light and dark schemes, keyboard and screen-reader friendly.

```html
<script type="module" src="https://nl-analytics-agent.onrender.com/widget.js"></script>
<nl-analyst base-url="https://nl-analytics-agent.onrender.com" api-key="aak_..." heading="Ask our sales data"></nl-analyst>
```

Make the key at `/keys` on the app with the site's origin listed (e.g. `https://example.com`): the API refuses
the key from any other site. Anyone who views the page can read the key, so its questions count against your
limits. Colours can be set on the element with `--nl-accent`, `--nl-ink`, `--nl-muted`, `--nl-surface`,
`--nl-rule` and `--nl-font`.

The app serves the built file at `/widget.js` when `API_KEYS` is on. `npm run build` writes `dist/nl-analyst.js`
(bundling `packages/sdk-ts`); `npm test` runs it on a mocked site and API in Chromium.

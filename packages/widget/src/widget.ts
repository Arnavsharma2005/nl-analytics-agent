/**
 * <nl-analyst>: ask the analytics agent questions from any site.
 *
 *   <script type="module" src="https://nl-analytics-agent.onrender.com/widget.js"></script>
 *   <nl-analyst base-url="https://nl-analytics-agent.onrender.com" api-key="aak_..."></nl-analyst>
 *
 * The key must list the page's origin (made at /keys on the app). Anyone who views the page can read the key, so
 * the questions it asks count against its owner's limits. Attributes: `base-url` and `api-key` (required),
 * `heading` (default "Ask about the data"). Colours follow the page's light or dark scheme and can be set with
 * --nl-accent, --nl-ink, --nl-muted, --nl-surface, --nl-rule and --nl-font on the element.
 */
import { Analyst, ApiError, type Conversation, type Message } from "../../sdk-ts/src/index.ts";

const ROWS_SHOWN = 8;

const STYLE = `
:host { --nl-accent: #1f5f8b; --nl-ink: #1b1d21; --nl-muted: #5b6270; --nl-surface: #ffffff; --nl-rule: #d9dde3;
  --nl-font: system-ui, -apple-system, "Segoe UI", sans-serif; display: block; max-width: 640px;
  font: 15px/1.5 var(--nl-font); color: var(--nl-ink); }
@media (prefers-color-scheme: dark) {
  :host { --nl-accent: #7cc0ec; --nl-ink: #eef0f3; --nl-muted: #a5acb8; --nl-surface: #16181c; --nl-rule: #343943; }
}
.box { border: 1px solid var(--nl-rule); border-radius: 10px; background: var(--nl-surface); padding: 16px; }
h2 { margin: 0 0 10px; font-size: 1rem; font-weight: 600; }
form { display: flex; gap: 8px; }
input { flex: 1; min-width: 0; font: inherit; color: inherit; background: transparent; padding: 8px 10px;
  border: 1px solid var(--nl-rule); border-radius: 6px; }
input:focus-visible, button:focus-visible, summary:focus-visible { outline: 2px solid var(--nl-accent); outline-offset: 2px; }
button { font: inherit; font-weight: 600; padding: 8px 14px; border: 0; border-radius: 6px; cursor: pointer;
  background: var(--nl-accent); color: var(--nl-surface); }
button:disabled { opacity: .55; cursor: default; }
.turn { margin-top: 16px; border-top: 1px solid var(--nl-rule); padding-top: 12px; }
.q { font-weight: 600; margin: 0; }
.status { color: var(--nl-muted); font-size: .9rem; margin: 4px 0 0; }
.summary { margin: 6px 0 0; white-space: pre-wrap; }
.error { margin: 6px 0 0; color: #b42318; }
@media (prefers-color-scheme: dark) { .error { color: #ff9b8f; } }
.scroll { overflow-x: auto; margin-top: 10px; }
table { border-collapse: collapse; font-size: .875rem; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 4px 12px 4px 0; border-bottom: 1px solid var(--nl-rule); white-space: nowrap; }
th { font-weight: 600; }
details { margin-top: 8px; font-size: .875rem; color: var(--nl-muted); }
pre { overflow-x: auto; font-size: .8rem; white-space: pre-wrap; color: var(--nl-ink); }
.foot { margin: 12px 0 0; font-size: .8rem; color: var(--nl-muted); }
.foot a { color: inherit; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
`;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, unknown> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node: HTMLElementTagNameMap[K] = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(value);
}

export class NlAnalyst extends HTMLElement {
  static observedAttributes = ["base-url", "api-key", "heading"];
  private chat: Conversation | null = null;
  private busy = false;
  private readonly root: ShadowRoot;
  private readonly turns: HTMLElement;
  private readonly input: HTMLInputElement;
  private readonly button: HTMLButtonElement;
  private readonly heading: HTMLHeadingElement;
  private readonly foot: HTMLParagraphElement;

  constructor() {
    super();
    this.root = this.attachShadow({ mode: "open" });
    const id = `nl-q-${Math.random().toString(36).slice(2, 8)}`;
    this.heading = el("h2");
    this.input = el("input", { id, type: "text", maxLength: 500, autocomplete: "off", placeholder: "e.g. Revenue by region last quarter" });
    this.button = el("button", { type: "submit", textContent: "Ask" });
    const form = el("form", {}, el("label", { htmlFor: id, className: "sr", textContent: "Your question" }), this.input, this.button);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      void this.ask(this.input.value.trim());
    });
    this.turns = el("div");
    this.turns.setAttribute("role", "log");
    this.turns.setAttribute("aria-live", "polite");
    this.foot = el("p", { className: "foot" });
    this.root.append(el("style", { textContent: STYLE }), el("section", { className: "box" }, this.heading, form, this.turns, this.foot));
    this.render();
  }

  attributeChangedCallback() {
    this.chat = null; // a new address or key starts a new conversation
    this.render();
  }

  private render() {
    this.heading.textContent = this.getAttribute("heading") || "Ask about the data";
    const base = this.getAttribute("base-url");
    this.foot.replaceChildren(...(base ? ["Answers are written as SQL and run on the data. ", el("a", { href: base, target: "_blank", rel: "noopener", textContent: "Open the full app" })] : []));
  }

  private conversation(): Conversation {
    const baseUrl = this.getAttribute("base-url");
    const apiKey = this.getAttribute("api-key");
    if (!baseUrl || !apiKey) throw new ApiError(0, "widget_not_configured", "This widget needs base-url and api-key attributes");
    this.chat ??= new Analyst({ baseUrl, apiKey }).conversation();
    return this.chat;
  }

  private async ask(question: string) {
    if (this.busy || question.length < 3) return;
    this.busy = this.button.disabled = true;
    const status = el("p", { className: "status", textContent: "Thinking…" });
    const summary = el("p", { className: "summary" });
    const turn = el("article", { className: "turn" }, el("p", { className: "q", textContent: question }), status, summary);
    this.turns.append(turn);
    try {
      let text = "";
      for await (const event of this.conversation().stream(question)) {
        if (event.type === "step") status.textContent = `${event.label}…`;
        else if (event.type === "token") summary.textContent = text = event.replace ? event.text : text + event.text;
        else if (event.type === "done") this.finish(turn, status, summary, event.message);
      }
      this.input.value = "";
    } catch (e) {
      status.remove();
      const why = e instanceof ApiError ? e.title + (e.retryAfter ? ` Try again in ${Math.ceil(e.retryAfter / 60)} min.` : "") : "Could not reach the analyst. Check your connection and try again.";
      turn.append(el("p", { className: "error", textContent: why }));
    } finally {
      this.busy = this.button.disabled = false;
      this.input.focus();
    }
  }

  private finish(turn: HTMLElement, status: HTMLElement, summary: HTMLElement, message: Message) {
    const a = message.answer;
    status.remove();
    if (a.error) {
      turn.append(el("p", { className: "error", textContent: a.answer || "This question could not be answered from the data." }));
      return;
    }
    summary.textContent = a.summary || a.answer || "";
    const rows = (a.rows ?? []) as unknown[][];
    if (a.columns?.length && rows.length) {
      const head = el("tr", {}, ...a.columns.map((c) => el("th", { scope: "col", textContent: c })));
      const body = rows.slice(0, ROWS_SHOWN).map((r) => el("tr", {}, ...r.map((v) => el("td", { textContent: cell(v) }))));
      const caption = el("caption", { className: "sr", textContent: `Result for: ${message.question}` });
      turn.append(el("div", { className: "scroll" }, el("table", {}, caption, el("thead", {}, head), el("tbody", {}, ...body))));
      if (rows.length > ROWS_SHOWN) turn.append(el("p", { className: "status", textContent: `First ${ROWS_SHOWN} of ${rows.length} rows.` }));
    }
    if (a.sql) turn.append(el("details", {}, el("summary", { textContent: "The SQL behind this answer" }), el("pre", { textContent: a.sql })));
  }
}

if (!customElements.get("nl-analyst")) customElements.define("nl-analyst", NlAnalyst);

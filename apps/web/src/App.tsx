import { useEffect, useRef, useState } from "react";
import { askStream, getExamples } from "./api";
import ResultCard, { type Entry } from "./components/ResultCard";
import SchemaPanel from "./components/SchemaPanel";

export default function App() {
  const [question, setQuestion] = useState("");
  const [examples, setExamples] = useState<string[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);

  useEffect(() => {
    getExamples().then(setExamples).catch(() => setExamples([]));
  }, []);

  const update = (id: number, patch: (e: Entry) => Entry) =>
    setEntries((all) => all.map((e) => (e.id === id ? patch(e) : e)));

  async function ask(q: string) {
    const text = q.trim();
    if (text.length < 3 || busy) return;
    const id = nextId.current++;
    setEntries((all) => [{ id, question: text, steps: [] }, ...all]);
    setQuestion("");
    setBusy(true);
    try {
      const result = await askStream(text, (step) => update(id, (e) => ({ ...e, steps: [...e.steps, step] })));
      update(id, (e) => ({ ...e, result }));
    } catch (err) {
      update(id, (e) => ({ ...e, error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <header>
        <h1>Ask your business data</h1>
        <p className="muted">Type a question in plain English. You get a chart, a short insight, and the SQL behind it.</p>
      </header>

      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Which region grew revenue fastest from 2024 to 2025?"
          aria-label="Your question"
          maxLength={500}
          autoFocus
        />
        <button type="submit" disabled={busy || question.trim().length < 3}>
          {busy ? "Working…" : "Ask"}
        </button>
      </form>

      <div className="examples">
        {examples.map((ex) => (
          <button key={ex} type="button" disabled={busy} onClick={() => ask(ex)}>
            {ex}
          </button>
        ))}
      </div>

      <SchemaPanel />

      <section className="feed">
        {entries.map((e) => (
          <ResultCard key={e.id} entry={e} />
        ))}
      </section>
    </main>
  );
}

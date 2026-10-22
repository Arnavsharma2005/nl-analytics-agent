"""An analyst for each uploaded dataset: built on its first question from the stored Parquet, and kept open for
the next ones (only the few used most recently, since each holds its data in memory)."""

import threading
from collections import OrderedDict
from collections.abc import Callable
from uuid import UUID

from app.analyst import Analyst
from app.brief import Brief
from app.datasets.duck import DuckDBSource
from app.datasets.store import Datasets, DatasetV1
from app.llm import LLM

# A source dropped from the cache may still be answering a question; it is closed once that has surely finished.
CLOSE_AFTER_S = 120


def brief(dataset: DatasetV1) -> Brief:
    """What the agents are told about an uploaded file."""
    dates = [c.name for c in dataset.columns if c.kind in ("date", "datetime") and not c.personal]
    return Brief(
        about=f"a table uploaded by the user from a CSV file ({dataset.table}, {dataset.rows:,} rows)",
        rules="- Each row of the table is one record from the uploaded file. There are no other business rules: "
              "read the question using the column names and their notes.",
        time_column=dates[0] if dates else "the date column",
        money=None,
        out_of_scope=f"I can only answer questions about the data in {dataset.name}. Try one of the suggested "
                     "questions, or ask what the data contains.",
    )


def comments(dataset: DatasetV1) -> dict[str, str]:
    """Notes for the SQL agent on each column (keyed by name; "" is the table's own note)."""
    notes = {"": f"Uploaded from the file {dataset.name}"}
    for c in dataset.columns:
        parts = []
        if c.header != c.name:
            parts.append(f"heading in the file: {c.header[:60]}")
        if c.low is not None and c.high is not None:
            parts.append(f"from {c.low} to {c.high}")
        if c.empty:
            parts.append(f"{c.empty:,} rows have no value")
        if parts:
            notes[c.name] = "; ".join(parts)
    return notes


class DatasetAnalysts:
    def __init__(self, store: Datasets, llm: Callable[[], LLM], *, keep: int = 3, max_attempts: int = 3,
                 rows_for_llm: int = 50, **source_options):
        """`source_options` are DuckDBSource's: statement_timeout_ms, max_rows, memory_limit_mb."""
        self._store = store
        self._llm = llm
        self._keep = keep
        self._analyst_options = {"max_attempts": max_attempts, "rows_for_llm": rows_for_llm}
        self._source_options = source_options
        self._open: OrderedDict[UUID, tuple[Analyst, DuckDBSource]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, id: UUID, owner: str | None) -> tuple[DatasetV1, Analyst] | None:
        """The dataset and its analyst, if `owner` has a dataset `id` that has not expired."""
        dataset = self._store.get(id, owner)
        if dataset is None:
            self.forget(id)
            return None
        with self._lock:
            if id in self._open:
                self._open.move_to_end(id)
                return dataset, self._open[id][0]
        parquet = self._store.parquet(id)
        if parquet is None:  # expired a moment ago
            return None
        personal = {(dataset.table, c.name) for c in dataset.columns if c.personal}
        source = DuckDBSource.build({dataset.table: parquet}, comments={dataset.table: comments(dataset)},
                                    personal=personal, **self._source_options)
        analyst = Analyst(self._llm(), source, brief=brief(dataset), **self._analyst_options)
        with self._lock:
            if id in self._open:  # another request built it first
                self._retire(source)
                return dataset, self._open[id][0]
            self._open[id] = (analyst, source)
            while len(self._open) > self._keep:
                self._retire(self._open.popitem(last=False)[1][1])
        return dataset, analyst

    def forget(self, id: UUID) -> None:
        with self._lock:
            if opened := self._open.pop(id, None):
                self._retire(opened[1])

    def close(self) -> None:
        """Close every open source now (when the app stops)."""
        with self._lock:
            opened, self._open = list(self._open.values()), OrderedDict()
        for _, source in opened:
            source.close()

    @staticmethod
    def _retire(source: DuckDBSource) -> None:
        timer = threading.Timer(CLOSE_AFTER_S, source.close)
        timer.daemon = True
        timer.start()

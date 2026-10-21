import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import Database
from app.graph import build_graph, to_response
from app.llm import LangChainLLM

EXAMPLES = [
    "What was our monthly revenue in 2025?",
    "Which region grew revenue fastest from 2024 to 2025?",
    "Top 10 products by profit in 2025",
    "What share of 2025 revenue came from each channel?",
    "Which product category has the highest return rate?",
    "How many new customers signed up each quarter?",
    "Average order value by customer segment",
    "Who are our top 5 customers by lifetime revenue?",
]

STEP_LABELS = {
    "router": "Understanding the question",
    "sql_agent": "Writing SQL",
    "validate": "Checking the query against the schema",
    "execute": "Running the query",
    "chart_agent": "Choosing a chart",
    "summary_agent": "Writing the insight",
    "schema_answer": "Describing the data",
    "out_of_scope": "Done",
    "give_up": "Stopped",
}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@lru_cache
def get_db() -> Database:
    s = get_settings()
    return Database(s.database_url, s.statement_timeout_ms, s.max_result_rows)


@lru_cache
def get_graph():
    s = get_settings()
    return build_graph(LangChainLLM(s.llm_model), get_db(), s.max_sql_attempts, s.rows_for_llm)


def create_app(graph_factory=get_graph, db_factory=get_db) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="NL Business Analytics Agent")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    def health():
        try:
            with db_factory().engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"database unavailable: {e}") from e
        return {"status": "ok"}

    @app.get("/api/examples")
    def examples():
        return EXAMPLES

    @app.get("/api/schema")
    def schema():
        return [
            {"table": t.name, "description": t.comment, "columns": [{"name": c.name, "type": c.type, "description": c.comment} for c in t.columns]}
            for t in db_factory().schema().tables.values()
        ]

    @app.post("/api/ask")
    def ask(req: AskRequest):
        state = graph_factory().invoke({"question": req.question.strip()})
        return to_response(state)

    @app.post("/api/ask/stream")
    async def ask_stream(req: AskRequest):
        graph = graph_factory()

        async def events():
            final = None
            try:
                async for mode, chunk in graph.astream({"question": req.question.strip()}, stream_mode=["updates", "values"]):
                    if mode == "updates":
                        for node in chunk:
                            yield {"event": "step", "data": json.dumps({"node": node, "label": STEP_LABELS.get(node, node)})}
                    else:
                        final = chunk
                yield {"event": "result", "data": json.dumps(to_response(final or {}), default=str)}
            except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
                yield {"event": "error", "data": json.dumps({"error": str(e)})}

        return EventSourceResponse(events())

    # In production the built React app is served from the same origin.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (dist / path).resolve()
            ok = path and file.is_file() and file.is_relative_to(dist.resolve())
            return FileResponse(file if ok else dist / "index.html")

    return app


app = create_app()

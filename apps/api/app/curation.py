"""The Curator: answers people rated thumbs up, reviewed by a person before any of them joins the verified library.

A rating never changes what the analyst does by itself, since ratings can be gamed (OWASP LLM08). A thumbs up only
puts the answer in the Curator's queue. A curator (a GitHub login listed in CURATORS) approves or rejects it, and may
reword the question. Approved answers are kept here, since the messages they came from expire, until they are
exported as a pull request that adds them to semantic/queries.yml. There CI checks them like every other verified
query (they must run, and must not give away a held-out eval question), and a person merges the pull request.

Only answers about the demo data are queued: an uploaded file's tables aren't in the library. Answers that used a
verified query outright, failed, or had no SQL are left out too.
"""

import base64
import json
import secrets
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.semantic import Semantic, VerifiedQuery


class CandidateV1(BaseModel):
    """An answer rated thumbs up, waiting for a curator."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    message_id: UUID
    question: str = Field(description="The question as it was understood: a follow-up written out in full.")
    asked: str = Field(description="The question as it was typed.")
    sql: str
    summary: str | None
    comment: str | None = Field(description="What the person who rated it said, if anything.")
    rated_at: datetime


class CuratedV1(BaseModel):
    """A curator's decision on an answer."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    message_id: UUID
    status: Literal["approved", "rejected"]
    question: str
    sql: str
    reviewer: str
    reviewed_at: datetime
    pr_url: str | None = Field(description="The pull request it was exported in; none until then.")


class AlreadyVerified(Exception):
    """The question is in the verified library already, or approved and waiting to be exported."""


# Rows of app.messages that can be curated, as a WHERE clause over `m` (messages) and `f` (feedback).
_CANDIDATE = """
    f.rating = 1 AND m.dataset_id IS NULL AND m.answer IS NOT NULL
    AND m.answer->'answer'->>'intent' = 'data_query'
    AND m.answer->'answer'->>'sql' IS NOT NULL AND m.answer->'answer'->>'error' IS NULL
    AND m.answer->'answer'->>'verified_question' IS NULL
"""


def _candidate(row) -> CandidateV1:
    answer = row.answer["answer"]
    return CandidateV1(message_id=row.id, question=answer.get("standalone_question") or row.question,
                       asked=row.question, sql=answer["sql"].strip(), summary=answer.get("summary"),
                       comment=row.comment, rated_at=row.rated_at)


class Curation:
    def __init__(self, engine: Engine, library: Semantic):
        self._engine = engine
        self._library = {q.question.lower() for q in library.queries}

    def queue(self, limit: int = 50) -> list[CandidateV1]:
        """Answers rated thumbs up and not yet reviewed, newest rating first; one per question, leaving out
        questions the library or an approval already has."""
        with self._engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT m.id, m.question, m.answer, f.comment, f.created_at AS rated_at
                FROM app.feedback f JOIN app.messages m ON m.id = f.message_id
                WHERE {_CANDIDATE} AND NOT EXISTS (SELECT 1 FROM app.curation c WHERE c.message_id = m.id)
                ORDER BY f.created_at DESC LIMIT :limit
            """), {"limit": limit * 2}).all()
            taken = self._library | self._approved_questions(conn)
        queue = []
        for candidate in map(_candidate, rows):
            if candidate.question.lower() not in taken:
                taken.add(candidate.question.lower())
                queue.append(candidate)
        return queue[:limit]

    def review(self, message_id: UUID, status: Literal["approved", "rejected"], reviewer: str,
               question: str | None = None) -> CuratedV1 | None:
        """Approve or reject a queued answer, approving it under `question` if given. None if it isn't queued
        (unknown, expired, or reviewed already). Raises AlreadyVerified when approving a question that is taken."""
        with self._engine.begin() as conn:
            row = conn.execute(text(f"""
                SELECT m.id, m.question, m.answer, f.comment, f.created_at AS rated_at
                FROM app.feedback f JOIN app.messages m ON m.id = f.message_id
                WHERE m.id = :id AND {_CANDIDATE}
            """), {"id": message_id}).first()
            if row is None:
                return None
            candidate = _candidate(row)
            question = (question or candidate.question).strip()
            if status == "approved":
                conn.execute(text("LOCK TABLE app.curation IN SHARE ROW EXCLUSIVE MODE"))  # one approval at a time
                if question.lower() in self._library | self._approved_questions(conn):
                    raise AlreadyVerified()
            saved = conn.execute(text("""
                INSERT INTO app.curation (message_id, status, question, sql, reviewer)
                VALUES (:id, :status, :question, :sql, :reviewer)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING message_id, status, question, sql, reviewer, reviewed_at, pr_url
            """), {"id": message_id, "status": status, "question": question, "sql": candidate.sql,
                   "reviewer": reviewer}).first()
        return CuratedV1.model_validate(saved._asdict()) if saved else None

    def approved(self) -> list[CuratedV1]:
        """Approved answers not exported yet, oldest first."""
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT message_id, status, question, sql, reviewer, reviewed_at, pr_url FROM app.curation
                WHERE status = 'approved' AND pr_url IS NULL ORDER BY reviewed_at
            """)).all()
        return [CuratedV1.model_validate(row._asdict()) for row in rows]

    def claim(self) -> tuple[str, list[CuratedV1]]:
        """Take the approved answers waiting to be exported, so that two exports at once can't both add them.
        Returns a claim to pass to `exported` or `release`, and the answers."""
        claim = "claimed:" + secrets.token_hex(8)
        with self._engine.begin() as conn:
            rows = conn.execute(text("""
                UPDATE app.curation SET pr_url = :claim WHERE status = 'approved' AND pr_url IS NULL
                RETURNING message_id, status, question, sql, reviewer, reviewed_at, pr_url
            """), {"claim": claim}).all()
        return claim, sorted((CuratedV1.model_validate(row._asdict()) for row in rows), key=lambda c: c.reviewed_at)

    def exported(self, claim: str, pr_url: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("UPDATE app.curation SET pr_url = :url WHERE pr_url = :claim"),
                         {"url": pr_url, "claim": claim})

    def release(self, claim: str) -> None:
        """Give claimed answers back, after an export that failed."""
        with self._engine.begin() as conn:
            conn.execute(text("UPDATE app.curation SET pr_url = NULL WHERE pr_url = :claim"), {"claim": claim})

    @staticmethod
    def _approved_questions(conn) -> set[str]:
        return set(conn.execute(text(
            "SELECT lower(question) FROM app.curation WHERE status = 'approved'")).scalars())


def _scalar(value: str) -> str:
    """`value` as a YAML scalar: plain when that reads back the same, else double-quoted."""
    plain = not value.startswith(("'", '"', "-", "?", ":", "!", "&", "*", "[", "{", "|", ">", "%", "@", "`")) \
        and "#" not in value and ": " not in value
    return value if plain and yaml.safe_load(f"v: {value}")["v"] == value else json.dumps(value, ensure_ascii=False)


def library_entries(curated: list[CuratedV1], today: date) -> str:
    """The approved answers as entries of semantic/queries.yml, in its style."""
    entries = []
    for c in curated:
        sql = "\n".join(("      " + line.rstrip()) if line.strip() else "" for line in c.sql.strip().splitlines())
        entries.append(f"  - question: {_scalar(c.question)}  # approved in the Curator\n"
                       f"    verified_by: {_scalar(c.reviewer)}\n"
                       f"    verified_at: {today.isoformat()}\n"
                       f"    sql: |\n{sql}\n")
    return "\n".join(entries)


def add_to_library(current: str, entries: str) -> str:
    """queries.yml with `entries` added at the end. Raises ValueError if the result is not a valid library (a
    question that is there already, say), so nothing broken is ever proposed."""
    updated = current.rstrip("\n") + "\n\n" + entries
    queries = yaml.safe_load(updated)["queries"]
    Semantic(rules=[], queries=[VerifiedQuery.model_validate(q) for q in queries])
    return updated


class ExportFailed(Exception):
    pass


class LibraryPR:
    """Opens a pull request on GitHub that adds approved answers to the verified library."""

    def __init__(self, token: str, repo: str, base: str = "main", path: str = "apps/api/semantic/queries.yml",
                 transport: httpx.BaseTransport | None = None):
        self._repo, self._base, self._path = repo, base, path
        self._client = lambda: httpx.Client(
            base_url="https://api.github.com", transport=transport, timeout=20,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})

    def open(self, curated: list[CuratedV1], today: date) -> str:
        """Commit the answers to a new branch and open a draft pull request; returns its URL."""
        repo = f"/repos/{self._repo}"
        branch = f"curator/{today.isoformat()}-{secrets.token_hex(3)}"
        try:
            with self._client() as gh:
                head = self._ok(gh.get(f"{repo}/git/ref/heads/{self._base}"))["object"]["sha"]
                file = self._ok(gh.get(f"{repo}/contents/{self._path}", params={"ref": self._base}))
                current = base64.b64decode(file["content"]).decode("utf-8")
                try:
                    updated = add_to_library(current, library_entries(curated, today))
                except ValueError as e:
                    raise ExportFailed(f"the library on {self._base} doesn't take these answers: {e}") from e
                self._ok(gh.post(f"{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": head}))
                count = f"{len(curated)} verified {'query' if len(curated) == 1 else 'queries'}"
                self._ok(gh.put(f"{repo}/contents/{self._path}", json={
                    "message": f"Add {count} approved in the Curator", "branch": branch, "sha": file["sha"],
                    "content": base64.b64encode(updated.encode("utf-8")).decode("ascii")}))
                listed = "\n".join(f"- {c.question} (approved by {c.reviewer})" for c in curated)
                pr = self._ok(gh.post(f"{repo}/pulls", json={
                    "title": f"Add {count} from the Curator", "head": branch, "base": self._base, "draft": True,
                    "body": f"Answers people rated thumbs up, approved in the Curator:\n\n{listed}\n\n"
                            "CI checks that each one passes the SQL guard, runs, and gives away no held-out eval "
                            "question. Check the SQL, then merge to add them to the verified library."}))
        except httpx.HTTPError as e:
            raise ExportFailed(f"GitHub could not be reached: {e}") from e
        return pr["html_url"]

    @staticmethod
    def _ok(response: httpx.Response) -> dict:
        if response.is_error:
            raise ExportFailed(f"GitHub said {response.status_code} to {response.request.method} "
                               f"{response.request.url.path}: {response.text[:200]}")
        return response.json()

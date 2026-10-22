"""/v1 routes for the Curator (see app/curation.py): only for the GitHub logins listed in CURATORS."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.curation import AlreadyVerified, CandidateV1, CuratedV1, Curation, ExportFailed, LibraryPR, library_entries
from app.problems import Problem
from app.usage import Visitor

log = logging.getLogger(__name__)


class CuratorV1(BaseModel):
    queue: list[CandidateV1] = Field(description="Answers rated thumbs up, waiting for a decision.")
    approved: list[CuratedV1] = Field(description="Approved answers not yet exported to the library.")
    can_open_pr: bool = Field(description="Whether exporting opens a pull request (CURATOR_GITHUB_TOKEN is set); "
                                          "otherwise copy the YAML from GET /v1/curator/export.yml.")


class ReviewV1(BaseModel):
    decision: Literal["approve", "reject"]
    question: str | None = Field(default=None, min_length=3, max_length=300,
                                 description="Approve under this wording instead of the question as understood.")


class ExportV1(BaseModel):
    url: str = Field(description="The pull request that adds them to semantic/queries.yml.")
    count: int


def curator_router(*, curation: Callable[[], Curation], library_pr: Callable[[], LibraryPR | None],
                   identify: Callable[[Request], Visitor], login: Callable[[int], str | None],
                   curators: set[str]) -> APIRouter:
    """`login(user_id)` is a signed-in user's GitHub login; `curators` are the logins allowed here, lowercase."""
    router = APIRouter(prefix="/v1/curator", tags=["v1"])

    def curator(request: Request) -> str:
        user = identify(request).user
        name = login(user) if user else None
        if not name or name.lower() not in curators:
            raise Problem(403, "not_a_curator", "Only curators can see this",
                          "Sign in with a GitHub account listed in CURATORS.")
        return name

    @router.get("")
    def overview(request: Request) -> CuratorV1:
        """The review queue, and what is approved but not yet in the library."""
        curator(request)
        store = curation()
        return CuratorV1(queue=store.queue(), approved=store.approved(), can_open_pr=library_pr() is not None)

    @router.get("/export.yml", response_class=PlainTextResponse)
    def export_yaml(request: Request) -> str:
        """The approved answers as entries to paste at the end of semantic/queries.yml."""
        curator(request)
        return library_entries(curation().approved(), datetime.now(UTC).date())

    @router.post("/export")
    async def export(request: Request) -> ExportV1:
        """Open a draft pull request adding the approved answers to semantic/queries.yml, where CI checks them."""
        curator(request)
        pr = library_pr()
        if pr is None:
            raise Problem(503, "export_off", "Opening pull requests is not set up here",
                          "Set CURATOR_GITHUB_TOKEN and CURATOR_REPO, or copy the YAML instead.")
        store = curation()
        claim, approved = await asyncio.to_thread(store.claim)
        if not approved:
            raise Problem(422, "nothing_to_export", "Nothing is approved and waiting to be exported")
        try:
            url = await asyncio.to_thread(pr.open, approved, datetime.now(UTC).date())
        except Exception as e:
            await asyncio.to_thread(store.release, claim)  # so the next export takes them again
            if not isinstance(e, ExportFailed):
                raise
            log.warning("curator export failed: %s", e)
            raise Problem(502, "export_failed", "The pull request could not be opened", str(e)) from e
        await asyncio.to_thread(store.exported, claim, url)
        return ExportV1(url=url, count=len(approved))

    @router.post("/{message_id}")
    def review(message_id: UUID, body: ReviewV1, request: Request) -> CuratedV1:
        """Approve or reject a queued answer. Approving keeps it for the next export; nothing changes what the
        analyst does until that pull request is merged."""
        name = curator(request)
        try:
            done = curation().review(message_id, "approved" if body.decision == "approve" else "rejected", name,
                                     body.question)
        except AlreadyVerified as e:
            raise Problem(409, "already_verified", "This question is in the verified library already",
                          "Reject this one, or approve it under other wording.") from e
        if done is None:
            raise Problem(404, "candidate_not_found", "This answer isn't waiting for review",
                          "It was reviewed already, or the message it came from has expired.")
        return done

    return router

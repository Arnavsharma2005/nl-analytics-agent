"""/v1 routes for share links (see app/shares.py)."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Path, Request, Response

from app.datasets.store import Datasets
from app.messages import Messages
from app.problems import Problem
from app.shares import Shares, ShareV1
from app.usage import Visitor

NOT_FOUND = ("share_not_found", "This shared answer is not available",
             "Shared answers are kept for 90 days, and whoever shared one can delete it.")

# Tokens are 16 URL-safe characters; anything else can't be one, and is refused before touching the database.
Token = Path(pattern=r"^[A-Za-z0-9_-]{16}$")


def shares_router(*, shares: Callable[[], Shares], messages: Callable[[], Messages],
                  datasets: Callable[[], Datasets], identify: Callable[[Request], Visitor]) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["v1"])

    @router.post("/messages/{message_id}/share")
    def share(message_id: UUID, request: Request) -> ShareV1:
        """Share one of your answers: a read-only snapshot anyone with the link (/s/{id}) can open, without
        signing in. Sharing the same answer again gives the same link."""
        owner = identify(request).owner
        message = messages().get(message_id, owner) if owner else None
        if message is None:
            raise Problem(404, "message_not_found", "No such message",
                          "It may have expired, or it was asked in another session.")
        if message.answer.error:
            raise Problem(422, "nothing_to_share", "This question has no answer to share")
        about = None
        if message.dataset_id:
            dataset = datasets().get(message.dataset_id, owner)
            about = dataset.name if dataset else "an uploaded file"
        return shares().share(owner, message, about)

    @router.get("/shares/{share_id}")
    def get_share(share_id: str = Token) -> ShareV1:
        """A shared answer. Anyone with the link can read it; nothing is run again."""
        found = shares().get(share_id)
        if found is None:
            raise Problem(404, *NOT_FOUND)
        return found

    @router.delete("/shares/{share_id}", status_code=204)
    def delete_share(request: Request, share_id: str = Token) -> Response:
        """Stop sharing: the link stops working for everyone."""
        if not shares().delete(share_id, identify(request).owner):
            raise Problem(404, *NOT_FOUND)
        return Response(status_code=204)

    return router

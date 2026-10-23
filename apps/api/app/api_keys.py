"""/v1/keys: a signed-in user makes, lists and revokes their API keys (app/keys.py).

Managing keys needs the sign-in cookie: a request made with a key cannot make or revoke keys.
"""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.api_auth import signed_in_user
from app.keys import PER_USER, ApiKeys, KeyRequestV1, KeyV1, NewKeyV1
from app.problems import Problem
from app.sessions import Sessions


def keys_router(*, keys: Callable[[], ApiKeys], sessions: Callable[[], Sessions], enabled: bool) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["v1"])

    def user_of(request: Request) -> int:
        if not enabled:
            raise Problem(404, "api_keys_off", "API keys are not turned on here",
                          "Set API_KEYS=true on the server to offer them.")
        user = signed_in_user(sessions(), request)
        if user is None:
            raise Problem(401, "sign_in_required", "Sign in to manage API keys",
                          "Keys are made on the web app by a signed-in user, not with another key.")
        return user

    @router.post("/keys", status_code=201)
    def create_key(body: KeyRequestV1, request: Request) -> NewKeyV1:
        """Make an API key. Send it as `Authorization: Bearer <key>` to ask as yourself, under your own question
        limits. The key is in this response only: store it somewhere safe."""
        made = keys().create(user_of(request), body)
        if made is None:
            raise Problem(409, "too_many_keys", f"You can have {PER_USER} keys at a time",
                          "Revoke a key you no longer use first.")
        return made

    @router.get("/keys")
    def list_keys(request: Request) -> list[KeyV1]:
        """Your API keys that still work, oldest first, without the keys themselves."""
        return keys().list(user_of(request))

    @router.delete("/keys/{key_id}", status_code=204)
    def revoke_key(key_id: UUID, request: Request) -> Response:
        """Revoke a key. Requests made with it are refused from now on."""
        if not keys().revoke(key_id, user_of(request)):
            raise Problem(404, "key_not_found", "No such key", "It may have been revoked already.")
        return Response(status_code=204)

    return router

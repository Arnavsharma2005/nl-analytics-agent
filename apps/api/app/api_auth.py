"""/v1 routes for signing in with GitHub (see app/auth.py) and for who is signed in."""

import asyncio
import hmac
import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from app.auth import Attempt, GitHub, SignInFailed, Users
from app.messages import Messages
from app.problems import Problem
from app.sessions import COOKIE, Sessions

log = logging.getLogger(__name__)

USER_COOKIE = "aa_user"
ATTEMPT_COOKIE = "aa_signin"
ATTEMPT_TTL_S = 600
CALLBACK = "/v1/auth/github/callback"


class UserV1(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    login: str
    name: str | None
    avatar_url: str | None


class MeV1(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    user: UserV1 | None


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def signed_in_user(sessions: Sessions, request: Request) -> int | None:
    """The id of the user signed in on this request, if any."""
    value = sessions.signer("user").open(request.cookies.get(USER_COOKIE))
    return int(value) if value and value.isdigit() else None


def auth_router(*, sessions: Callable[[], Sessions], github: Callable[[], GitHub | None], users: Callable[[], Users],
                messages: Callable[[], Messages], public_url: str | None, user_session_days: int) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["v1"])

    def site(request: Request) -> str:
        """The web app's origin: where GitHub sends visitors back to, and where they land afterwards."""
        return (public_url or str(request.base_url)).rstrip("/")

    def back_to(request: Request, path: str) -> RedirectResponse:
        response = RedirectResponse(site(request) + path, status_code=303)
        response.delete_cookie(ATTEMPT_COOKIE, path="/v1/auth")
        return response

    @router.get("/auth/github", response_class=RedirectResponse, status_code=303)
    def sign_in(request: Request, next: Annotated[str, Query(max_length=500)] = "/"):
        """Start signing in with GitHub: redirects there, and GitHub sends the visitor back to the callback."""
        client = github()
        if client is None:
            raise Problem(404, "sign_in_off", "Sign-in is not available here")
        url, attempt = client.start(site(request) + CALLBACK, next)
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(ATTEMPT_COOKIE, sessions().signer("signin").seal(attempt.encode(), ATTEMPT_TTL_S),
                            max_age=ATTEMPT_TTL_S, path="/v1/auth", httponly=True, samesite="lax",
                            secure=is_https(request))
        return response

    @router.get("/auth/github/callback", include_in_schema=False)
    async def callback(request: Request, code: str | None = None, state: str | None = None):
        client = github()
        attempt = Attempt.decode(sessions().signer("signin").open(request.cookies.get(ATTEMPT_COOKIE)))
        if client is None or attempt is None or not state or not hmac.compare_digest(state, attempt.state):
            return back_to(request, "/?signin=failed")
        if not code:  # the visitor said no on GitHub
            return back_to(request, attempt.next)
        try:
            profile = await asyncio.to_thread(client.profile, site(request) + CALLBACK, code, attempt)
            user = await asyncio.to_thread(users().sign_in, profile)
        except SignInFailed:
            return back_to(request, "/?signin=failed")
        except Exception:  # noqa: BLE001 - the app database is down: say sign-in failed rather than show a 500
            log.exception("could not keep the signed-in user")
            return back_to(request, "/?signin=failed")
        if session := sessions().read(request.cookies.get(COOKIE)):
            try:  # the conversation they had before signing in becomes theirs
                await asyncio.to_thread(messages().adopt, session, f"user:{user.id}")
            except Exception:  # noqa: BLE001 - signing in matters more than keeping those few messages
                log.exception("could not move the session's messages to the user")
        response = back_to(request, attempt.next)
        ttl = user_session_days * 24 * 3600
        response.set_cookie(USER_COOKIE, sessions().signer("user").seal(str(user.id), ttl), max_age=ttl,
                            httponly=True, samesite="lax", secure=is_https(request))
        return response

    @router.post("/auth/logout", status_code=204)
    def sign_out(request: Request) -> Response:
        response = Response(status_code=204)
        response.delete_cookie(USER_COOKIE, httponly=True, samesite="lax", secure=is_https(request))
        return response

    @router.get("/me")
    def me(request: Request) -> MeV1:
        """Who is signed in, if anyone."""
        user_id = signed_in_user(sessions(), request)
        user = users().get(user_id) if user_id else None
        return MeV1(user=UserV1(login=user.login, name=user.name, avatar_url=user.avatar_url) if user else None)

    return router

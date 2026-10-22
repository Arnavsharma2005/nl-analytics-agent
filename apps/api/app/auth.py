"""Optional sign-in with GitHub. Signing in raises the question limits and keeps conversations for longer, and the
history follows the user to other devices. Nobody has to sign in to ask.

The flow is OAuth 2 authorization code with PKCE (Authlib does the protocol). Starting it sets a short-lived signed
cookie holding the state, the PKCE verifier and where to go afterwards; the callback checks the state against it,
fetches the GitHub profile, keeps a row in app.users, and sets a signed cookie with the user's id. Only the public
profile is asked for (no scope), and the GitHub token is dropped once the profile is read.
"""

import base64
import json
import logging
from dataclasses import dataclass

import httpx2  # what Authlib's client is built on (also what the OpenAI client uses)
from authlib.common.errors import AuthlibBaseError
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import OAuth2Client
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
PROFILE_URL = "https://api.github.com/user"


class SignInFailed(Exception):
    """GitHub did not confirm who the visitor is."""


@dataclass(frozen=True)
class Profile:
    github_id: int
    login: str
    name: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class Attempt:
    """A sign-in in progress, kept in a signed cookie between leaving for GitHub and coming back."""

    state: str
    verifier: str
    next: str

    def encode(self) -> str:
        raw = json.dumps({"s": self.state, "v": self.verifier, "n": self.next}).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str | None) -> "Attempt | None":
        if not value:
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            return cls(state=data["s"], verifier=data["v"], next=data["n"])
        except (ValueError, KeyError, TypeError):
            return None


def safe_next(path: str | None) -> str:
    """Where to send the visitor after signing in: a path on this site, never another site."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


class GitHub:
    def __init__(self, client_id: str, client_secret: str, transport: httpx2.BaseTransport | None = None):
        self._id = client_id
        self._secret = client_secret
        self._transport = transport  # tests swap in a fake GitHub

    def _client(self, redirect_uri: str) -> OAuth2Client:
        return OAuth2Client(self._id, self._secret, redirect_uri=redirect_uri, code_challenge_method="S256",
                            timeout=10, transport=self._transport)

    def start(self, redirect_uri: str, next: str) -> tuple[str, Attempt]:
        """The GitHub page to send the visitor to, and the attempt to keep until they come back."""
        verifier = generate_token(48)
        with self._client(redirect_uri) as client:
            url, state = client.create_authorization_url(AUTHORIZE_URL, code_verifier=verifier)
        return url, Attempt(state, verifier, safe_next(next))

    def profile(self, redirect_uri: str, code: str, attempt: Attempt) -> Profile:
        """Trade the code GitHub sent back for the visitor's public profile."""
        try:
            with self._client(redirect_uri) as client:
                client.fetch_token(TOKEN_URL, code=code, code_verifier=attempt.verifier)
                r = client.get(PROFILE_URL, headers={"Accept": "application/vnd.github+json"})
                r.raise_for_status()
                data = r.json()
            return Profile(int(data["id"]), str(data["login"]), data.get("name"), data.get("avatar_url"))
        except (AuthlibBaseError, httpx2.HTTPError, ValueError, KeyError, TypeError) as e:
            log.warning("GitHub sign-in failed: %s", e)
            raise SignInFailed() from e


@dataclass(frozen=True)
class User:
    id: int
    login: str
    name: str | None
    avatar_url: str | None


class Users:
    def __init__(self, engine: Engine):
        self._engine = engine

    def sign_in(self, profile: Profile) -> User:
        """The user for a GitHub account, made on first sign-in and brought up to date on every later one."""
        with self._engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO app.users (github_id, login, name, avatar_url) VALUES (:github_id, :login, :name, :avatar)
                ON CONFLICT (github_id) DO UPDATE
                SET login = EXCLUDED.login, name = EXCLUDED.name, avatar_url = EXCLUDED.avatar_url, last_seen_at = now()
                RETURNING id, login, name, avatar_url
            """), {"github_id": profile.github_id, "login": profile.login, "name": profile.name,
                   "avatar": profile.avatar_url}).one()
        return User(**row._asdict())

    def get(self, id: int) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(text("SELECT id, login, name, avatar_url FROM app.users WHERE id = :id"),
                               {"id": id}).first()
        return User(**row._asdict()) if row else None

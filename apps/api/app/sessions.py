"""Anonymous visitor sessions: a signed cookie, issued after an optional Cloudflare Turnstile bot check.

A session is what the hourly question quota counts against. With Turnstile configured, asking a question needs a
session, so a script has to pass the bot check before it can spend the free model quota, and again every
`ttl_s`. Without Turnstile, sessions are still issued (for the quota) but are not required.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

COOKIE = "aa_session"
SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class Turnstile:
    """Checks a Turnstile token with Cloudflare. Tokens are single-use and expire after five minutes."""

    def __init__(self, secret: str, post: Callable[..., httpx.Response] | None = None):
        self._secret = secret
        self._post = post or httpx.post

    def verify(self, token: str, ip: str | None) -> bool:
        if not token:
            return False
        try:
            r = self._post(SITEVERIFY, data={"secret": self._secret, "response": token, "remoteip": ip or ""},
                           timeout=10)
            result = r.json()
        except (httpx.HTTPError, ValueError):
            log.exception("Turnstile verification failed to complete")
            return False
        if not result.get("success"):
            log.info("Turnstile rejected a token: %s", result.get("error-codes"))
        return bool(result.get("success"))


class Signer:
    """Signed, expiring cookie values: `value.expires.signature`. The value must not contain dots.

    `purpose` keeps values signed for one cookie from being accepted as another's."""

    def __init__(self, key: bytes, purpose: str = "", clock: Callable[[], float] = time.time):
        self._key = key
        self._purpose = purpose
        self._clock = clock

    def seal(self, value: str, ttl_s: int) -> str:
        if "." in value:
            raise ValueError("a signed value must not contain dots")
        expires = int(self._clock()) + ttl_s
        return f"{value}.{expires}.{self._sign(value, expires)}"

    def open(self, token: str | None) -> str | None:
        """The value, if the signature is valid and it has not expired."""
        try:
            value, expires, signature = (token or "").split(".")
            fresh = int(expires) > self._clock()
        except ValueError:
            return None
        if fresh and value and hmac.compare_digest(signature, self._sign(value, int(expires))):
            return value
        return None

    def _sign(self, value: str, expires: int) -> str:
        # Without a purpose the message is the one session cookies have always been signed with.
        message = f"{self._purpose}|{value}.{expires}" if self._purpose else f"{value}.{expires}"
        mac = hmac.new(self._key, message.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")


class Sessions:
    def __init__(self, secret: str | None, ttl_s: int, turnstile: Turnstile | None = None,
                 clock: Callable[[], float] = time.time):
        if not secret:
            log.warning("SESSION_SECRET is not set; using a random one, so sessions end when the app restarts")
        self._key = (secret or secrets.token_urlsafe(32)).encode()
        self._clock = clock
        self._signer = Signer(self._key, clock=clock)
        self.ttl_s = ttl_s
        self.turnstile = turnstile

    def signer(self, purpose: str) -> Signer:
        """A signer for another cookie, with the same secret as sessions."""
        return Signer(self._key, purpose, self._clock)

    @property
    def required(self) -> bool:
        """Whether asking needs a session: only when the bot check is on."""
        return self.turnstile is not None

    def issue(self, token: str | None, ip: str | None) -> str | None:
        """A new signed session value, or None if the bot check failed."""
        if self.turnstile and not self.turnstile.verify(token or "", ip):
            return None
        return self._signer.seal(secrets.token_urlsafe(12), self.ttl_s)

    def read(self, value: str | None) -> str | None:
        """The session id in a cookie value, if the signature is valid and it has not expired."""
        return self._signer.open(value)

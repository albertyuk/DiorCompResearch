"""Console authentication — team passphrase + display name → signed, stateless
session cookie. Enforced whenever CONSOLE_PASSPHRASE is set (always, in hosted
mode); a bare local checkout stays login-free.

The cookie is a signed payload (itsdangerous, MM_SECRET_KEY), so sessions
survive machine restarts and there is no server-side session store.
"""
from __future__ import annotations

import hashlib
import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "mm_session"
SESSION_MAX_AGE = 30 * 24 * 3600            # ~30 days
_SALT = "mm-console-session"

# paths that never require a session
PUBLIC_PATHS = {"/login", "/healthz", "/lang", "/theme"}


class SessionAuth:
    def __init__(self, passphrase: str, secret: str, *, secure_cookie: bool):
        self.passphrase = passphrase
        self.secure_cookie = secure_cookie
        self._serializer = URLSafeTimedSerializer(secret, salt=_SALT)
        # sessions are bound to the passphrase generation: rotating
        # CONSOLE_PASSPHRASE instantly invalidates every outstanding cookie
        self._pass_digest = hashlib.sha256(
            ("mm-pv:" + passphrase).encode("utf-8")).hexdigest()[:16]

    # -- passphrase -----------------------------------------------------------

    def check_passphrase(self, supplied: str) -> bool:
        return hmac.compare_digest((supplied or "").encode("utf-8"),
                                   self.passphrase.encode("utf-8"))

    # -- cookie ---------------------------------------------------------------

    def make_token(self, display_name: str) -> str:
        return self._serializer.dumps({"name": display_name,
                                       "pv": self._pass_digest})

    def read_token(self, token: str | None) -> str | None:
        """Returns the display name, or None for a missing/invalid/expired
        token or one issued under a previous passphrase."""
        if not token:
            return None
        try:
            payload = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return None
        if not hmac.compare_digest((payload or {}).get("pv", ""),
                                   self._pass_digest):
            return None
        name = (payload or {}).get("name") or ""
        return name.strip()[:80] or None

    def set_cookie(self, response, display_name: str) -> None:
        response.set_cookie(
            SESSION_COOKIE, self.make_token(display_name),
            max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
            secure=self.secure_cookie, path="/")

    def actor_from_request(self, request) -> str | None:
        """Session cookie, or Authorization: Bearer <passphrase> (used by
        `mm screenshots --push` and other API clients)."""
        name = self.read_token(request.cookies.get(SESSION_COOKIE))
        if name:
            return name
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer ") and \
                self.check_passphrase(authz[7:].strip()):
            return "api-client"
        return None

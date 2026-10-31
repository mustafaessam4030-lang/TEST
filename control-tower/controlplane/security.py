"""
Security primitives: password hashing, opaque tokens, rate limiting and the
HTTP security headers.

Passwords are hashed with scrypt (RFC 7914; N=2^15, r=8, p=1, 16-byte salt),
the stored form carrying its own parameters so they can be raised later
without invalidating old hashes. Session, reset and worker tokens are random
256-bit values; only their SHA-256 is stored, so a copy of the database does
not let anyone sign in.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD = 12


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32)
    return "scrypt${0}${1}${2}${3}${4}".format(SCRYPT_N, SCRYPT_R, SCRYPT_P,
                                              _b64(salt), _b64(digest))


# Verifying against this when the account does not exist keeps a wrong email
# from answering faster than a wrong password.
_DUMMY = None


def verify_password(password, stored):
    global _DUMMY
    if not stored:
        # No account (or no local password): spend the same time, say no.
        if _DUMMY is None:
            _DUMMY = hash_password(secrets.token_hex(8))
        _check(password, _DUMMY)
        return False
    return _check(password, stored)


def _check(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(str(password).encode("utf-8"), salt=_unb64(salt),
                                n=int(n), r=int(r), p=int(p),
                                maxmem=SCRYPT_MAXMEM, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def password_problem(password, email=None):
    """None when acceptable, otherwise what to fix — said plainly."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return "Use at least {0} characters.".format(MIN_PASSWORD)
    if len(password) > 256:
        return "Use at most 256 characters."
    kinds = sum(bool(re.search(p, password)) for p in
                (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if kinds < 3:
        return "Mix at least three of: lower case, upper case, digits, symbols."
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in password.lower():
        return "Do not include your email name in the password."
    return None


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def token_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def same(a, b):
    return hmac.compare_digest(str(a or ""), str(b or ""))


class RateLimiter(object):
    """
    A sliding-window counter in memory. Per process: on one App Service
    instance that is the whole app; scaled out, each instance limits on its
    own (see PLATFORM.md, limitations).
    """

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key, limit, window_s):
        """Record one hit; False when the key is over its limit."""
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= now - window_s:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            if len(self._hits) > 50000:      # never grow without bound
                for stale in [k for k, v in self._hits.items() if not v][:10000]:
                    del self._hits[stale]
            return True

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


def script_hashes(html):
    """CSP hashes for the inline <script> blocks of a page."""
    out = []
    for match in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html,
                             re.S | re.I):
        digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
        out.append("'sha256-{0}'".format(base64.b64encode(digest).decode("ascii")))
    return out


def page_headers(script_sources=(), secure=True):
    """
    Headers for an HTML page. Scripts run only from this origin or as the
    exact inline blocks the page shipped with; nothing may frame the app; no
    third-party connection is allowed.
    """
    csp = ("default-src 'self'; script-src 'self' {0}; style-src 'self' "
           "'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; "
           "font-src 'self' data:; media-src 'self' blob:; object-src 'none'; "
           "base-uri 'none'; frame-ancestors 'none'; form-action 'self'").format(
               " ".join(script_sources)).replace("  ", " ")
    headers = dict(api_headers(secure))
    headers["Content-Security-Policy"] = csp
    return headers


def api_headers(secure=True):
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "no-store",
    }
    if secure:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers

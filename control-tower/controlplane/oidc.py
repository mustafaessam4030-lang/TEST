"""
Microsoft Entra ID sign-in: OpenID Connect, authorization code flow with
PKCE, for a confidential client (the client secret stays on the server).

    /auth/sso/start      -> Microsoft's sign-in page
    /auth/sso/callback   <- code + state; exchanged server-side for tokens

The ID token is checked before anyone is signed in: RS256 signature against
the tenant's published keys, issuer, audience, tenant, expiry, not-before
and the nonce this server generated for that very sign-in. A state value is
single-use and lives ten minutes.

Standard library only — the RSA check is the PKCS#1 v1.5 verification of
RFC 8017 §8.2.2, done with Python's integers.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request

from .db import now
from .security import new_token, token_hash

STATE_TTL_S = 600
CLOCK_SKEW_S = 120
# DER prefix of DigestInfo for SHA-256 (RFC 8017 §9.2, note 1).
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class SSOError(Exception):
    pass


def _b64d(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _int(b64):
    return int.from_bytes(_b64d(b64), "big")


def rs256_verify(signing_input, signature, jwk):
    """True when `signature` is a valid RS256 signature by this RSA JWK."""
    if jwk.get("kty") != "RSA":
        return False
    n, e = _int(jwk["n"]), _int(jwk["e"])
    k = (n.bit_length() + 7) // 8
    if len(signature) != k or n.bit_length() < 2048:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    digest = hashlib.sha256(signing_input).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(SHA256_PREFIX) - len(digest) - 3) + \
        b"\x00" + SHA256_PREFIX + digest
    return hmac.compare_digest(em, expected)


def decode_jwt(token):
    try:
        head, body, sig = str(token).split(".")
        return (json.loads(_b64d(head)), json.loads(_b64d(body)), _b64d(sig),
                (head + "." + body).encode("ascii"))
    except Exception:
        raise SSOError("The sign-in token from Microsoft is not readable.")


def _http(url, data=None, timeout=10):
    body = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
    request = urllib.request.Request(url, data=body, headers={
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"} if body else {
        "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class EntraID(object):
    def __init__(self, settings, db, fetch=None):
        self.s, self.db = settings, db
        self.fetch = fetch or _http
        self._jwks, self._jwks_at = None, 0

    @property
    def base(self):
        return "{0}/{1}".format(self.s.entra_authority, self.s.entra_tenant)

    @property
    def issuer(self):
        return "https://login.microsoftonline.com/{0}/v2.0".format(self.s.entra_tenant)

    # -- start -------------------------------------------------------------

    def start(self, next_path="/"):
        state, nonce, verifier = new_token(), new_token(), new_token(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
        self.db.execute("DELETE FROM sso_states WHERE created_at < ?",
                        (now() - STATE_TTL_S,))
        self.db.execute("INSERT INTO sso_states (state_hash, nonce, verifier, created_at, "
                        "next_path) VALUES (?, ?, ?, ?, ?)",
                        (token_hash(state), nonce, verifier, now(),
                         next_path if str(next_path).startswith("/") and
                         not str(next_path).startswith("//") else "/"))
        query = urllib.parse.urlencode({
            "client_id": self.s.entra_client_id, "response_type": "code",
            "redirect_uri": self.s.redirect_uri, "response_mode": "query",
            "scope": "openid profile email", "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "prompt": "select_account"})
        return "{0}/oauth2/v2.0/authorize?{1}".format(self.base, query)

    # -- callback ----------------------------------------------------------

    def finish(self, code, state):
        """The verified claims of the person who just signed in, and where to go."""
        if not code or not state:
            raise SSOError("Microsoft did not return a sign-in code.")
        with self.db.tx() as c:
            row = self.db.one("SELECT * FROM sso_states WHERE state_hash = ?" +
                              self.db.lock_clause, (token_hash(state),), c)
            if row is not None:
                self.db.execute("DELETE FROM sso_states WHERE state_hash = ?",
                                (token_hash(state),), c)
        if row is None or row["created_at"] < now() - STATE_TTL_S:
            raise SSOError("That sign-in link expired. Start again.")
        try:
            tokens = self.fetch(self.base + "/oauth2/v2.0/token", {
                "client_id": self.s.entra_client_id,
                "client_secret": self.s.entra_client_secret,
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.s.redirect_uri,
                "code_verifier": row["verifier"], "scope": "openid profile email"})
        except Exception:
            raise SSOError("Microsoft did not accept the sign-in code.")
        claims = self.verify_id_token(tokens.get("id_token"), row["nonce"])
        return claims, row["next_path"] or "/"

    def _keys(self, force=False):
        if force or self._jwks is None or time.time() - self._jwks_at > 3600:
            self._jwks = self.fetch(self.base + "/discovery/v2.0/keys").get("keys") or []
            self._jwks_at = time.time()
        return self._jwks

    def verify_id_token(self, id_token, nonce):
        if not id_token:
            raise SSOError("Microsoft returned no ID token.")
        header, claims, signature, signing_input = decode_jwt(id_token)
        if header.get("alg") != "RS256":
            raise SSOError("Unexpected token algorithm.")
        key = next((k for k in self._keys() if k.get("kid") == header.get("kid")), None)
        if key is None:            # keys rotate; look once more
            key = next((k for k in self._keys(force=True)
                        if k.get("kid") == header.get("kid")), None)
        if key is None or not rs256_verify(signing_input, signature, key):
            raise SSOError("The sign-in token's signature is not valid.")
        stamp = time.time()
        if claims.get("iss") != self.issuer:
            raise SSOError("The token was issued for another tenant.")
        if claims.get("aud") != self.s.entra_client_id:
            raise SSOError("The token was issued for another application.")
        if claims.get("tid") != self.s.entra_tenant:
            raise SSOError("The token was issued for another tenant.")
        if float(claims.get("exp") or 0) < stamp - CLOCK_SKEW_S:
            raise SSOError("The sign-in token has expired.")
        if float(claims.get("nbf") or 0) > stamp + CLOCK_SKEW_S:
            raise SSOError("The sign-in token is not valid yet.")
        if not hmac.compare_digest(str(claims.get("nonce") or ""), str(nonce)):
            raise SSOError("The sign-in token does not belong to this sign-in.")
        email = str(claims.get("email") or claims.get("preferred_username") or "").lower()
        if not email or "@" not in email:
            raise SSOError("Your Microsoft account has no work email.")
        if self.s.entra_allowed_domains and \
                email.split("@")[1] not in self.s.entra_allowed_domains:
            raise SSOError("Only {0} accounts may sign in.".format(
                ", ".join(self.s.entra_allowed_domains)))
        return {"email": email, "oid": claims.get("oid"),
                "name": claims.get("name") or email.split("@")[0],
                "roles": list(claims.get("roles") or [])}

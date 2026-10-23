"""Create an API token for a user: prints the token once and the hash to store in the users file."""
from __future__ import annotations

import hashlib
import secrets
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "new":
        print("usage: python -m si.tokens new <user-id>", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    print(f"user:          {argv[1]}")
    print(f"token:         {token}   (give this to the user once; it is not stored)")
    print(f"token_sha256:  {hashlib.sha256(token.encode()).hexdigest()}   (put this in the users file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

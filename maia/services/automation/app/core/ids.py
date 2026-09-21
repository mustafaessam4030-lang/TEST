"""Sortable identifiers. Run ids are ULID-shaped so they sort by time."""
from __future__ import annotations

import os
import time

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_B32[value & 31])
        value >>= 5
    return "".join(reversed(out))


def ulid() -> str:
    return _encode(int(time.time() * 1000), 10) + _encode(int.from_bytes(os.urandom(10), "big"), 16)


def run_id() -> str:
    return f"run_{ulid()}"

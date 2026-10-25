"""Shared FAKE data for the page tests (no Snowflake)."""
from test_native import responder

__all__ = ["responder", "FakeSession"]


class _Row:
    def __init__(self, d):
        self._d = d

    def as_dict(self):
        return self._d


class FakeSession:
    def __init__(self):
        self.statements = []

    def sql(self, q, params=None):
        self.statements.append((q, params))

        class _Q:
            def collect(self_inner):
                if "NEXTVAL" in q:
                    return [_Row({"N": 101})]
                if q.lstrip().upper().startswith("UPDATE"):
                    return [_Row({"number of rows updated": 1})]
                return []
        return _Q()

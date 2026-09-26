"""Smoke checks for the impact feature (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
Create nothing another user would see; clean up what you create."""


def register(ctx):
    pass

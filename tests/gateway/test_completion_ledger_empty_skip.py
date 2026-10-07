"""Empty profile stores do not need the expensive completion replay path."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from gateway.run_notifications import GatewayNotificationsMixin, _profile_may_have_async_delegations


def test_empty_completion_ledgers_skip_replay_but_uncertain_store_does_not(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    homes = {name: tmp_path / name for name in ("missing", "empty", "filled", "corrupt")}
    for home in homes.values():
        home.mkdir()
    for name in ("empty", "filled"):
        with sqlite3.connect(homes[name] / "state.db") as db:
            db.execute("CREATE TABLE async_delegations (id INTEGER)")
            if name == "filled":
                db.execute("INSERT INTO async_delegations VALUES (1)")
    (homes["corrupt"] / "state.db").write_bytes(b"not sqlite")

    assert not _profile_may_have_async_delegations(homes["missing"])
    assert not _profile_may_have_async_delegations(homes["empty"])
    assert _profile_may_have_async_delegations(homes["filled"])
    assert _profile_may_have_async_delegations(homes["corrupt"])

    entered = []

    @contextmanager
    def scope(home, *_args, **_kwargs):
        entered.append(home.name)
        yield

    monkeypatch.setattr(gateway_run, "_profile_runtime_scope", scope)
    runner = GatewayNotificationsMixin()
    runner._primary_profile_name = "default"
    rows = [(name, home) for name, home in homes.items()]
    runner._each_secondary_ledger(rows, lambda: 1, "Restored")
    assert entered == ["filled", "corrupt"]

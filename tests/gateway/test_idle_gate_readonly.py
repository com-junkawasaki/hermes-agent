"""The idle sweep must never pin one state.db connection per served profile."""

import sqlite3

from gateway import run_idle_gates as gates


def test_idle_gates_read_rows_and_close_every_connection(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE sessions (handoff_state TEXT)")
        db.execute("INSERT INTO state_meta VALUES (?, ?)", ("heartbeat:paused", '{"status":"paused"}'))
        db.execute("INSERT INTO state_meta VALUES (?, ?)", ("loop:cleared", '{"status":"cleared"}'))

    real_connect = sqlite3.connect
    opened = []

    class TrackedConnection:
        def __init__(self, db):
            self.db = db
            self.closed = False

        def execute(self, *args):
            return self.db.execute(*args)

        def close(self):
            self.closed = True
            self.db.close()

    def connect(*args, **kwargs):
        wrapped = TrackedConnection(real_connect(*args, **kwargs))
        opened.append(wrapped)
        assert kwargs.get("uri") is True and "mode=ro" in args[0]
        return wrapped

    monkeypatch.setattr(gates.sqlite3, "connect", connect)
    assert not gates.profile_has_active_heartbeat(tmp_path)
    assert not gates.profile_has_active_loop(tmp_path)
    assert not gates.profile_has_pending_handoff(tmp_path)
    assert not gates.profile_has_running_handoff(tmp_path)
    with real_connect(path) as db:
        db.execute("INSERT INTO state_meta VALUES (?, ?)", ("heartbeat:active", '{"status":"active"}'))
        db.execute("INSERT INTO state_meta VALUES (?, ?)", ("loop:active", '{"status":"active"}'))
        db.execute("INSERT INTO sessions VALUES ('pending')")
        db.execute("INSERT INTO sessions VALUES ('running')")
    assert gates.profile_has_active_heartbeat(tmp_path)
    assert gates.profile_has_active_loop(tmp_path)
    assert gates.profile_has_pending_handoff(tmp_path)
    assert gates.profile_has_running_handoff(tmp_path)
    assert len(opened) == 8 and all(db.closed for db in opened)


def test_idle_gate_missing_db_is_empty_and_unreadable_db_fails_open(tmp_path):
    assert not gates.profile_has_active_heartbeat(tmp_path)
    assert not (tmp_path / "state.db").exists()
    (tmp_path / "state.db").write_bytes(b"not a sqlite database")
    assert gates.profile_has_active_heartbeat(tmp_path)

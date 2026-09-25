"""Idle gates for the gateway's per-profile pollers (heartbeat restore, handoff watcher, loop wakeup).

Entering ``_profile_runtime_scope`` costs a config.yaml load, a ``.env`` parse, secret hydration and a
terminal-policy build; on a multiplex gateway the pollers paid that per profile per tick with nothing
to do. Each gate reads the durable rows through a short-lived, read-only SQLite connection. The
goals-cached SessionDB kept a writer and read pool open for every profile after a single idle probe,
exhausting the host's file descriptors before cron could start. An unavailable store, a failing
read or a corrupt row still fails open: it is "cannot prove emptiness", never "idle".
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("gateway.run")


class _ReadOnlyIdleProbe:
    def __init__(self, path: Path):
        self.path = path

    def _read(self, sql: str, params: tuple = ()) -> list:
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
            return db.execute(sql, params).fetchall()

    def list_meta_prefix(self, prefix: str) -> list:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return self._read("SELECT key, value FROM state_meta WHERE key LIKE ? ESCAPE '\\'", (escaped + "%",))

    def has_pending_handoffs(self) -> bool:
        return bool(self._read("SELECT 1 FROM sessions WHERE handoff_state = 'pending' LIMIT 1"))


def _profile_session_db_probe(profile_home: Path) -> Optional[Any]:
    """A short-lived read-only view; absent DB means no persisted work."""
    path = Path(profile_home) / "state.db"
    try:
        path.stat()
    except FileNotFoundError:
        return _EmptyIdleProbe()
    except OSError:
        logger.debug("session-db probe failed for %s", profile_home, exc_info=True)
        return None
    return _ReadOnlyIdleProbe(path)


class _EmptyIdleProbe:
    def list_meta_prefix(self, _prefix: str) -> list:
        return []

    def has_pending_handoffs(self) -> bool:
        return False


def _gate(profile_home: Path, store_has_work: Callable[[Any], bool]) -> bool:
    db = _profile_session_db_probe(profile_home)
    if db is None:
        return True
    try:
        return store_has_work(db)
    except Exception:
        logger.debug("idle probe failed for %s; keeping the full sweep", profile_home, exc_info=True)
        return True


def profile_has_active_heartbeat(profile_home: Path) -> bool:
    from hermes_cli.heartbeat import store_has_active_heartbeat

    return _gate(profile_home, store_has_active_heartbeat)


def profile_has_active_loop(profile_home: Path) -> bool:
    from hermes_cli.loops import store_has_active_loop

    return _gate(profile_home, store_has_active_loop)


def profile_has_pending_handoff(profile_home: Path) -> bool:
    return _gate(profile_home, lambda db: db.has_pending_handoffs())


async def off_loop_gate(runner: object, probe: Callable[[], bool]) -> bool:
    """Run a sync gate through the runner's executor hop. Runners without one (bare test stand-ins
    for the handoff watcher) keep the historical always-enter behaviour."""
    offload = getattr(runner, "_run_in_executor_with_context", None)
    if not callable(offload):
        return True
    return bool(await offload(probe))

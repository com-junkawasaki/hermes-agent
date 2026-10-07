"""The source updater bounds profile skill subprocesses on multiplexed hosts."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from hermes_cli import update_cmd_maint


def test_profile_skill_sync_is_bounded_parallel_and_keeps_failures(monkeypatch, capsys):
    from hermes_cli import profiles
    from plugins.memory.honcho import cli as honcho_cli

    names = [f"p{i}" for i in range(9)]
    monkeypatch.setattr(profiles, "list_profiles", lambda: [SimpleNamespace(name=n, path=n) for n in names])
    monkeypatch.setattr(profiles, "backfill_profile_envs", lambda quiet: [])
    monkeypatch.setattr(honcho_cli, "sync_honcho_profiles_quiet", lambda: [])
    gate = threading.Barrier(4, timeout=5)
    lock = threading.Lock()
    active = peak = 0
    visited = []

    def seed(path, quiet):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            visited.append(path)
        try:
            if path in names[:4]:
                gate.wait()
            time.sleep(0.01)
            if path == "p6":
                return None
            if path == "p7":
                raise RuntimeError("broken profile")
            return {"copied": [], "updated": []}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(profiles, "seed_profile_skills", seed)
    update_cmd_maint._sync_profiles_after_update()
    lines = capsys.readouterr().out.splitlines()
    assert peak == 4
    assert sorted(visited) == names
    assert [line.strip().split(":", 1)[0] for line in lines if line.startswith("  p")] == names
    assert "  p6: sync failed" in lines
    assert "  p7: error (broken profile)" in lines

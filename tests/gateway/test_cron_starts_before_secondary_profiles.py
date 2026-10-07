"""Cron must not wait for every secondary profile, and a secondary's config/plugin load must not run
on the event loop.

Measured 2026-09-23 on a 99-profile multiplexer: ``start()`` brought secondaries up one by one and the
cron ticker only started after ``start()`` returned, so no job on any profile fired for ~28 min after a
restart. Each secondary's ``discover_plugins()`` ran on the loop thread (plugin import under a per-plugin
deadline that start()s and join()s a worker), starving the liveness probe until the shutdown watchdog
killed the gateway mid-boot — twice that day, both dumps parked in
``plugins_loader.run_with_load_deadline``.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner


class _Adapter(BasePlatformAdapter):
    def __init__(self, platform: Platform):
        super().__init__(PlatformConfig(enabled=True, token="***"), platform)

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.mark.asyncio
async def test_primary_ready_hook_runs_before_secondary_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    events: list = []
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, cfg: _Adapter(platform))

    async def _secondaries():
        # The primary is already connected when the hook fires; secondaries come after it.
        events.append(("secondaries", Platform.TELEGRAM in runner.adapters))
        return 0

    monkeypatch.setattr(runner, "_start_secondary_profile_adapters", _secondaries)
    runner._on_primary_adapters_ready = lambda: events.append(("hook", Platform.TELEGRAM in runner.adapters))

    await runner.start()

    assert events == [("hook", True), ("secondaries", True)], events


@pytest.mark.asyncio
async def test_failing_primary_ready_hook_does_not_abort_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, cfg: _Adapter(platform))
    reached: list = []

    async def _secondaries():
        reached.append(True)
        return 0

    monkeypatch.setattr(runner, "_start_secondary_profile_adapters", _secondaries)

    def _boom():
        raise RuntimeError("cron provider unavailable")

    runner._on_primary_adapters_ready = _boom

    await runner.start()

    assert reached == [True]
    assert runner._running


_SCOPE: contextvars.ContextVar = contextvars.ContextVar("test_profile_scope", default=None)


@pytest.mark.asyncio
async def test_secondary_config_and_plugins_load_off_the_loop(monkeypatch, tmp_path):
    import gateway.config as gw_config
    import gateway.run as gw_run
    import hermes_cli.env_loader as env_loader
    import hermes_cli.plugins as plugins

    loop_thread = threading.get_ident()
    seen: dict = {}

    @contextlib.contextmanager
    def _scope(profile_home, prepared_secret_scope=None, *, hydrate_secrets=True):
        token = _SCOPE.set(str(profile_home))
        try:
            yield
        finally:
            _SCOPE.reset(token)

    def _record(name, value=None):
        def _fn(*_a, **_k):
            seen[name] = (threading.get_ident(), _SCOPE.get())
            return value
        return _fn

    monkeypatch.setattr(gw_run, "_profile_runtime_scope", _scope)
    monkeypatch.setattr(gw_run, "_load_gateway_config", _record("runtime_cfg", {"runtime": True}))
    monkeypatch.setattr(gw_run, "_own_policy_open_startup_violation", _record("violation", None))
    monkeypatch.setattr(gw_config, "load_gateway_config", _record("load_gateway_config", "PROFILE_CFG"))
    monkeypatch.setattr(plugins, "discover_plugins", _record("discover_plugins"))
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: "MANAGER")
    monkeypatch.setattr(env_loader, "hydrate_profile_secret_sources", lambda home: None)

    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / "sessions"))
    monkeypatch.setattr(runner, "_register_config_hooks", _record("register_hooks"))
    monkeypatch.setattr(runner, "_subscribe_plugin_rewire", _record("subscribe_rewire"))
    monkeypatch.setattr(runner, "_snapshot_profile_busy_modes", lambda name, cfg: None)

    home = tmp_path / "profiles" / "p2"
    heartbeats: list = []

    async def _heartbeat():
        for _ in range(20):
            heartbeats.append(1)
            await asyncio.sleep(0)

    beat = asyncio.ensure_future(_heartbeat())
    cfg = await runner._load_secondary_profile_config("p2", home)
    await beat

    assert cfg == "PROFILE_CFG"
    for name in ("runtime_cfg", "discover_plugins", "load_gateway_config", "violation"):
        thread, scope = seen[name]
        assert thread != loop_thread, f"{name} ran on the event loop"
        assert scope == str(home), f"{name} did not see the profile scope"
    for name in ("register_hooks", "subscribe_rewire"):
        # subscribe_rewire captures asyncio.get_running_loop(): off the loop it would get None (#87770).
        thread, scope = seen[name]
        assert thread == loop_thread, f"{name} left the event loop"
        assert scope == str(home), f"{name} did not see the profile scope"
    assert _SCOPE.get() is None, "profile scope leaked into the loop's context"

"""Cron-only profiles keep their schedules without booting unused adapters."""

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_cron_only_profile_skips_adapter_and_plugin_boot(tmp_path: Path, monkeypatch):
    from gateway.run_adapters import GatewayAdapterLifecycleMixin

    home = tmp_path / "cron-only"
    home.mkdir()
    (home / "config.yaml").write_text("gateway:\n  cron_only: true\n")
    runner = GatewayAdapterLifecycleMixin()

    async def forbidden(*_args):
        raise AssertionError("cron-only profile booted adapters")

    monkeypatch.setattr(runner, "_load_secondary_profile_config", forbidden)
    assert await runner._start_one_profile_adapters("cron-only", home, {}) == 0


@pytest.mark.asyncio
async def test_only_explicit_cron_only_profile_skips_adapters(tmp_path: Path, monkeypatch):
    from gateway.run_adapters import GatewayAdapterLifecycleMixin

    home = tmp_path / "regular"
    home.mkdir()
    (home / "config.yaml").write_text("gateway:\n  cron_only: false\n")
    runner = GatewayAdapterLifecycleMixin()

    async def reached(*_args):
        raise RuntimeError("adapter config was loaded")

    monkeypatch.setattr(runner, "_load_secondary_profile_config", reached)
    with pytest.raises(RuntimeError, match="adapter config was loaded"):
        await runner._start_one_profile_adapters("regular", home, {})


def test_cron_only_profile_remains_in_host_ticker_list(tmp_path: Path, monkeypatch):
    import gateway.run as gateway_run
    from gateway.config import GatewayConfig

    homes = [("default", tmp_path / "default"), ("cron-only", tmp_path / "cron-only")]
    monkeypatch.setattr(gateway_run, "_multiplex_profile_homes", lambda _config: homes)
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    assert gateway_run._cron_tick_profile_homes(GatewayConfig(multiplex_profiles=True)) == homes


def test_gateway_constructor_does_not_run_fleet_db_maintenance(monkeypatch):
    import gateway.run as gateway_run

    runner = gateway_run.GatewayRunner.__new__(gateway_run.GatewayRunner)
    monkeypatch.setattr(runner, "_open_session_db_for_active_scope", lambda **_kwargs: None)
    monkeypatch.setattr(gateway_run, "_housekeeping_chore", lambda *_args: (_ for _ in ()).throw(
        AssertionError("fleet DB maintenance ran before gateway startup")))
    runner._init_session_db()

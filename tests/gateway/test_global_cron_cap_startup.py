"""The gateway's typed config must not hide the raw host cron budget."""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_gateway_applies_global_cron_limit_from_raw_config(monkeypatch):
    import cron.scheduler as scheduler
    import cron.scheduler_provider as provider_module
    import cron.scheduler_thread as thread_module
    import gateway.run as gateway_run
    import hermes_cli.config as config_module
    from gateway.config import GatewayConfig, Platform

    class NoopThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    cron_provider = SimpleNamespace(start=lambda *_args, **_kwargs: None, name="test")
    monkeypatch.setattr(config_module, "load_config_readonly", lambda: {"cron": {"max_global_parallel_jobs": 8}})
    monkeypatch.setattr(gateway_run, "_cron_tick_profile_homes", lambda _config: [])
    monkeypatch.setattr(gateway_run, "_start_gateway_housekeeping", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(provider_module, "resolve_cron_scheduler", lambda: cron_provider)
    monkeypatch.setattr(provider_module, "scheduler_for_profile_mode", lambda *_args, **_kwargs: cron_provider)
    monkeypatch.setattr(thread_module, "SupervisedTickerThread", NoopThread)
    runner = SimpleNamespace(config=GatewayConfig(), adapters={Platform.API_SERVER: object()})
    try:
        gateway_run._start_gateway_start_cron_and_housekeeping(runner)
        assert scheduler._global_dispatch_semaphore._value == 8
    finally:
        scheduler.configure_global_parallel_limit(None)

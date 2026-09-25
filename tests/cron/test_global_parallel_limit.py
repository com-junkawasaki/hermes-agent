"""A multiplexed gateway must admit cron jobs against one host budget."""

import concurrent.futures
import threading

import cron.scheduler as sched
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def test_global_cap_defers_a_second_profile_without_a_false_receipt(tmp_path, monkeypatch):
    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()
    entered = threading.Event()
    release = threading.Event()
    receipts = []
    monkeypatch.setattr(sched, "create_execution", lambda job_id, **kw: receipts.append(job_id) or {"id": str(len(receipts))})
    sched.configure_global_parallel_limit(1)
    job = {"id": "same-id", "name": "same-id", "schedule": {"kind": "cron", "expr": "* * * * *"}}

    def process(_job):
        entered.set()
        assert release.wait(5)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            token_a = set_hermes_home_override(str(home_a))
            try:
                first = sched._submit_with_guard(job, pool, process)
            finally:
                reset_hermes_home_override(token_a)
            assert entered.wait(5)

            token_b = set_hermes_home_override(str(home_b))
            try:
                assert sched._submit_with_guard(job, pool, process) is None
                assert sched._inflight_key(job["id"], home_b) not in sched._running_job_ids
                assert receipts == [job["id"]]
            finally:
                reset_hermes_home_override(token_b)

            release.set()
            first.result(timeout=5)
            token_b = set_hermes_home_override(str(home_b))
            try:
                second = sched._submit_with_guard(job, pool, process)
            finally:
                reset_hermes_home_override(token_b)
            assert second is not None
            second.result(timeout=5)
            assert len(receipts) == 2
            assert sched.get_running_job_ids() == frozenset()
    finally:
        release.set()
        sched.configure_global_parallel_limit(None)


def test_invalid_global_limit_is_rejected():
    try:
        try:
            sched.configure_global_parallel_limit(-1)
        except ValueError as exc:
            assert "max_global_parallel_jobs" in str(exc)
        else:
            raise AssertionError("negative limit was accepted")
    finally:
        sched.configure_global_parallel_limit(None)

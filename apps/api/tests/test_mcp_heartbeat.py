"""MCP ownership renewal cannot queue behind unrelated executor work."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event, Timer
from threading import enumerate as threads

import pytest

from app.adapters.live.limited_mcp import LimitedMcp, McpCapacityExceeded
from app.contracts.capabilities import McpInvokeResult
from app.domain.mcp_capacity import McpCapacityStore
from app.ports.providers import ProviderUnavailable


def test_live_mcp_call_keeps_global_capacity_when_default_executor_is_saturated(tmp_path):
    async def scenario():
        loop = asyncio.get_running_loop()
        pool_started, provider_started, second_started, release_provider = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        )
        release_pool = Event()
        second_calls = 0

        class SlowProvider:
            async def invoke(self, name, _arguments):
                provider_started.set()
                await release_provider.wait()
                return McpInvokeResult(tool=name, result={"ok": True})

        class FastProvider:
            async def invoke(self, name, _arguments):
                nonlocal second_calls
                second_calls += 1
                second_started.set()
                return McpInvokeResult(tool=name, result={"ok": True})

        path = str(tmp_path / "capacity.sqlite3")
        stores = [McpCapacityStore(path, 1), McpCapacityStore(path, 1)]
        guards = [LimitedMcp(provider, max_calls=1, queue_timeout_seconds=0.05,
                             shared=store, lease_seconds=0.12)
                  for provider, store in zip((SlowProvider(), FastProvider()), stores, strict=True)]

        def occupy_default_executor():
            loop.call_soon_threadsafe(pool_started.set)
            release_pool.wait()

        pending = second = watching_second = None
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop.set_default_executor(executor)
            blocker = loop.run_in_executor(None, occupy_default_executor)
            await asyncio.wait_for(pool_started.wait(), timeout=1)
            try:
                pending = asyncio.create_task(guards[0].invoke("lookup", {}))
                await asyncio.wait_for(provider_started.wait(), timeout=1)
                # This is the existing capacity regression's lease/sleep boundary.
                await asyncio.sleep(0.18)
                second = asyncio.create_task(guards[1].invoke("lookup", {}))
                watching_second = asyncio.create_task(second_started.wait())
                done, _ = await asyncio.wait({second, watching_second}, timeout=1,
                                             return_when=asyncio.FIRST_COMPLETED)
                assert done, "Capacity admission did not finish within its bounded queue"
                assert second_calls == 0
                with pytest.raises(McpCapacityExceeded):
                    await second
                release_provider.set()
                assert (await asyncio.wait_for(pending, timeout=1)).result == {"ok": True}
                assert stores[0].db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
            finally:
                release_provider.set()
                release_pool.set()
                if watching_second is not None:
                    watching_second.cancel()
                await asyncio.gather(*(task for task in (pending, second, watching_second)
                                       if task is not None), return_exceptions=True)
                await blocker
                for store in stores:
                    store.db.close()

    asyncio.run(scenario())


class WaitingProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.ended = asyncio.Event()

    async def invoke(self, name, _arguments):
        self.started.set()
        try:
            await self.finish.wait()
            return McpInvokeResult(tool=name, result={"ok": True})
        finally:
            self.ended.set()


@pytest.mark.asyncio
async def test_caller_cancellation_stops_renewal_and_releases_capacity_with_request_context(
    tmp_path, monkeypatch,
):
    context = ContextVar("mcp_heartbeat_test_context", default=None)
    marker = context.set("synthetic-call")
    loop = asyncio.get_running_loop()
    renewed = asyncio.Event()
    seen_context = []
    store = McpCapacityStore(str(tmp_path / "capacity.sqlite3"), 1)
    original_renew = store.renew

    def observe_renew(token, seconds):
        seen_context.append(context.get())
        result = original_renew(token, seconds)
        loop.call_soon_threadsafe(renewed.set)
        return result

    monkeypatch.setattr(store, "renew", observe_renew)
    provider = WaitingProvider()
    guard = LimitedMcp(provider, max_calls=1, queue_timeout_seconds=0.05,
                       shared=store, lease_seconds=0.12)
    pending = asyncio.create_task(guard.invoke("lookup", {}))
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        await asyncio.wait_for(renewed.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)
        assert provider.ended.is_set()
        assert seen_context and set(seen_context) == {"synthetic-call"}
        assert store.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
        provider.finish.set()
        assert (await guard.invoke("lookup", {})).result == {"ok": True}
    finally:
        provider.finish.set()
        await asyncio.gather(pending, return_exceptions=True)
        store.db.close()
        context.reset(marker)


@pytest.mark.asyncio
async def test_lost_lease_cancels_provider_and_releases_capacity_before_retry(tmp_path, monkeypatch):
    store = McpCapacityStore(str(tmp_path / "capacity.sqlite3"), 1)
    original_renew = store.renew
    monkeypatch.setattr(store, "renew", lambda _token, _seconds: False)
    provider = WaitingProvider()
    guard = LimitedMcp(provider, max_calls=1, queue_timeout_seconds=0.05,
                       shared=store, lease_seconds=0.12)
    try:
        with pytest.raises(ProviderUnavailable, match="capacity lease lost"):
            await asyncio.wait_for(guard.invoke("lookup", {}), timeout=1)
        assert provider.started.is_set() and provider.ended.is_set()
        assert store.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
        monkeypatch.setattr(store, "renew", original_renew)
        provider.finish.set()
        assert (await guard.invoke("lookup", {})).result == {"ok": True}
    finally:
        provider.finish.set()
        store.db.close()


@pytest.mark.asyncio
async def test_cancelled_heartbeat_does_not_block_the_loop_while_renewal_finishes(tmp_path, monkeypatch):
    loop = asyncio.get_running_loop()
    renewing, finished = asyncio.Event(), asyncio.Event()
    release_renewal, stop, watchdog_used = Event(), Event(), Event()
    store = McpCapacityStore(str(tmp_path / "capacity.sqlite3"), 1)
    token = store.claim(0.12)
    assert token is not None

    def blocked_renew(_token, _seconds):
        loop.call_soon_threadsafe(renewing.set)
        release_renewal.wait()
        loop.call_soon_threadsafe(finished.set)
        return True

    def prevent_deadlock():
        watchdog_used.set()
        release_renewal.set()

    monkeypatch.setattr(store, "renew", blocked_renew)
    guard = LimitedMcp(WaitingProvider(), max_calls=1, queue_timeout_seconds=0.05,
                       shared=store, lease_seconds=0.12)
    heartbeat = asyncio.create_task(guard._renew(token, stop))
    watchdog = Timer(1, prevent_deadlock)
    release_on_loop = None
    try:
        await asyncio.wait_for(renewing.wait(), timeout=1)
        watchdog.start()
        stop.set()
        heartbeat.cancel()
        release_on_loop = loop.call_later(0.01, release_renewal.set)
        with pytest.raises(asyncio.CancelledError):
            await heartbeat
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert not watchdog_used.is_set(), "Heartbeat shutdown blocked event-loop progress"
    finally:
        stop.set()
        release_renewal.set()
        if release_on_loop is not None:
            release_on_loop.cancel()
        watchdog.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        store.release(token)
        store.db.close()


@pytest.mark.asyncio
async def test_repeated_mcp_calls_release_capacity_and_leave_no_heartbeat_workers(tmp_path):
    provider = WaitingProvider()
    provider.finish.set()
    store = McpCapacityStore(str(tmp_path / "capacity.sqlite3"), 1)
    guard = LimitedMcp(provider, max_calls=1, queue_timeout_seconds=0.05,
                       shared=store, lease_seconds=0.12)
    try:
        for _ in range(20):
            assert (await guard.invoke("lookup", {})).result == {"ok": True}
            assert store.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
        deadline = asyncio.get_running_loop().time() + 1
        while any(worker.name.startswith("mcp-heartbeat") for worker in threads()):
            assert asyncio.get_running_loop().time() < deadline, "Heartbeat workers did not stop"
            await asyncio.sleep(0.005)
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_drains_workers_before_releasing_capacity(
    tmp_path, monkeypatch,
):
    loop = asyncio.get_running_loop()
    renewing = asyncio.Event()
    release_renewal, renewal_finished = Event(), Event()
    store = McpCapacityStore(str(tmp_path / "capacity.sqlite3"), 1)
    original_renew, original_release = store.renew, store.release
    provider = WaitingProvider()
    release_observations = []

    def blocked_renew(token, seconds):
        loop.call_soon_threadsafe(renewing.set)
        release_renewal.wait()
        try:
            return original_renew(token, seconds)
        finally:
            renewal_finished.set()

    def observe_release(token):
        release_observations.append((provider.ended.is_set(), renewal_finished.is_set()))
        return original_release(token)

    class ObservedGuard(LimitedMcp):
        async def _renew(self, token, stop):
            self.heartbeat_stop = stop
            await super()._renew(token, stop)

    monkeypatch.setattr(store, "renew", blocked_renew)
    monkeypatch.setattr(store, "release", observe_release)
    guard = ObservedGuard(provider, max_calls=1, queue_timeout_seconds=0.05,
                          shared=store, lease_seconds=0.6)
    pending = asyncio.create_task(guard.invoke("lookup", {}))
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        await asyncio.wait_for(renewing.wait(), timeout=1)
        provider.finish.set()

        async def cleanup_started():
            while not guard.heartbeat_stop.is_set():
                await asyncio.sleep(0)

        await asyncio.wait_for(cleanup_started(), timeout=1)
        assert provider.ended.is_set()
        # Cancel after successful provider execution, while cleanup is draining
        # a renewal. Repeat cancellation at the next event-loop checkpoint.
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        assert not release_observations
        assert store.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 1
        release_renewal.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)
        assert release_observations == [(True, True)]
        assert store.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
        monkeypatch.setattr(store, "renew", original_renew)
        assert (await guard.invoke("lookup", {})).result == {"ok": True}
    finally:
        provider.finish.set()
        release_renewal.set()
        await asyncio.gather(pending, return_exceptions=True)
        # A failing implementation may leave its token behind; close the
        # synthetic store only after all worker access has ended.
        store.db.close()


@pytest.mark.asyncio
async def test_shared_release_failure_still_returns_local_capacity(tmp_path, monkeypatch):
    store = McpCapacityStore(str(tmp_path / "capacity.sqlite3"), 1)
    original_release = store.release
    provider = WaitingProvider()
    provider.finish.set()

    def failing_release(token):
        assert provider.ended.is_set()
        original_release(token)
        raise OSError("synthetic shared release failure")

    monkeypatch.setattr(store, "release", failing_release)
    guard = LimitedMcp(provider, max_calls=1, queue_timeout_seconds=0.05,
                       shared=store, lease_seconds=0.12)
    try:
        with pytest.raises(OSError, match="synthetic shared release failure"):
            await guard.invoke("lookup", {})
        assert store.db.execute("SELECT count(*) FROM mcp_execution_leases").fetchone()[0] == 0
        monkeypatch.setattr(store, "release", original_release)
        assert (await guard.invoke("lookup", {})).result == {"ok": True}
    finally:
        store.db.close()

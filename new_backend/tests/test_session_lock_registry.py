import asyncio

import pytest

from app.services.session_locks import SessionLockRegistry


@pytest.mark.asyncio
async def test_waiters_keep_the_same_lock_and_all_references_are_reclaimed():
    registry = SessionLockRegistry()
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first():
        async with registry.hold("session"):
            order.append("first")
            entered.set()
            await release.wait()

    async def waiter(name):
        async with registry.hold("session"):
            order.append(name)
            await asyncio.sleep(0)

    owner = asyncio.create_task(first())
    await entered.wait()
    waiting = asyncio.create_task(waiter("second"))
    await asyncio.sleep(0)
    assert registry.references("session") == 2
    third = asyncio.create_task(waiter("third"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(owner, waiting, third)
    assert order == ["first", "second", "third"]
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_remove_a_live_owner():
    registry = SessionLockRegistry()
    async with registry.hold("session"):
        waiting = asyncio.create_task(registry.hold("session").__aenter__())
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert registry.references("session") == 1
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_different_sessions_can_enter_concurrently():
    registry = SessionLockRegistry()
    async with registry.hold("first"), registry.hold("second"):
        assert len(registry) == 2
    assert len(registry) == 0

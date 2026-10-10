"""Complete repository operations leave the loop without splitting transactions."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from app.services.agent import AgentService


@pytest.mark.asyncio
async def test_database_transaction_runs_on_one_thread_without_blocking_the_loop():
    service = object.__new__(AgentService)
    service.store = SimpleNamespace(database=object())
    loop_thread = threading.get_ident()
    ticks = []

    def transaction():
        entered = threading.get_ident()
        time.sleep(0.05)
        return entered, threading.get_ident()

    async def ticker():
        for _ in range(4):
            await asyncio.sleep(0.005)
            ticks.append(True)

    result, _ = await asyncio.gather(service._db_call(transaction), ticker())
    assert result[0] == result[1]
    assert result[0] != loop_thread
    assert len(ticks) == 4


@pytest.mark.asyncio
async def test_cancel_waits_for_an_inflight_transaction_to_finish():
    service = object.__new__(AgentService)
    service.store = SimpleNamespace(database=object())
    entered = threading.Event()
    release = threading.Event()
    committed = threading.Event()

    def transaction():
        entered.set()
        assert release.wait(timeout=3)
        committed.set()

    task = asyncio.create_task(service._db_call(transaction))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert committed.is_set()

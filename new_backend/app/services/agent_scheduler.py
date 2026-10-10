"""Wake-driven durable admission with complete repository async boundaries."""

import asyncio
import logging
from functools import partial


class AgentSchedulerMixin:
    """Keep leasing in the store and scheduling/tracking on one event loop."""

    async def _db_call(self, method, *args, **kwargs):
        """Move a complete PostgreSQL repository operation to one worker thread.

        The legacy SQLite test store retains its single synchronous connection.
        No transaction is entered in the loop then continued on another thread.
        """
        if getattr(self.store, "database", None) is not None:
            task = asyncio.create_task(asyncio.to_thread(partial(method, *args, **kwargs)))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # to_thread cannot stop in-flight SQL. Wait for its transaction
                # outcome before releasing/recovering leases on shutdown.
                await task
                raise
        return method(*args, **kwargs)

    async def start(self):
        if self._scheduler is None:
            await self._db_call(self.store.recover_wakeups, self.resume_retry_seconds)
            self._scheduler = asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        if self._scheduler is not None:
            self._scheduler.cancel()
            try:
                await self._scheduler
            except asyncio.CancelledError:
                pass
            self._scheduler = None
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._db_call(
            self.store.release_owned_runs, self._instance_id, self.resume_retry_seconds
        )

    def _maintenance(self, run_ids):
        if self.compute_leases is not None:
            self.compute_leases.recover_expired()
        self.store.recover_wakeups(self.resume_retry_seconds)
        renewed = self.store.renew_leases(self._instance_id, run_ids)
        return renewed

    def _advance_jobs(self):
        if self.af3_executor == "mock":
            self.store.advance_mock_jobs(self.mock_af3_seconds)
        elif self.af3_executor == "callback":
            self.store.expire_queued_compute_jobs(self.af3_queue_timeout_seconds)
            self.store.expire_running_compute_jobs(self.af3_execution_timeout_seconds)
        return (
            self.store.db.execute(
                "SELECT 1 FROM agent_jobs WHERE status IN ('queued','running') LIMIT 1"
            ).fetchone()
            is not None
        )

    def _claim_batch(self):
        wakeups = self.store.claim_wakeups(
            self._instance_id,
            max_active=self.max_active_runs,
            max_user_active=self.max_active_runs_per_user,
        )
        queued = self.store.claim_queued_runs(
            self._instance_id,
            max_active=self.max_active_runs,
            max_user_active=self.max_active_runs_per_user,
        )
        return wakeups, queued

    async def _scheduler_loop(self):
        next_heartbeat = 0.0
        next_jobs = 0.0
        jobs_busy = False
        delay = 0.05
        while True:
            # Clear before starting any I/O; a wake arriving during claims or
            # maintenance remains set and prevents a lost-wakeup sleep.
            self._wake.clear()
            now = asyncio.get_running_loop().time()
            if now >= next_heartbeat:
                renewed = await self._db_call(self._maintenance, list(self._run_tasks))
                for run_id, task in tuple(self._run_tasks.items()):
                    if run_id not in renewed:
                        task.cancel()
                next_heartbeat = asyncio.get_running_loop().time() + 1.0
            if now >= next_jobs:
                jobs_busy = await self._db_call(self._advance_jobs)
                next_jobs = asyncio.get_running_loop().time() + (
                    0.05 if self.af3_executor == "mock" and (jobs_busy or self._run_tasks) else 1.0
                )
            claimed = False
            if len(self._run_tasks) < self.max_active_runs:
                wakeups, queued = await self._db_call(self._claim_batch)
                for run_id, user_id, session_id, job_id in wakeups:
                    self._track(run_id, self._resume(user_id, session_id, run_id, job_id))
                for run_id, user_id, session_id, content in queued:
                    self._track(run_id, self._execute(user_id, session_id, run_id, content))
                claimed = bool(wakeups or queued)
                if claimed and self.af3_executor == "mock":
                    next_jobs = min(next_jobs, asyncio.get_running_loop().time() + 0.05)
            delay = 0.05 if claimed or jobs_busy else min(1.0, delay * 2)
            until_heartbeat = max(0.001, next_heartbeat - asyncio.get_running_loop().time())
            until_jobs = max(0.001, next_jobs - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=min(delay, until_heartbeat, until_jobs)
                )
                delay = 0.05
                if self.af3_executor == "mock":
                    next_jobs = 0.0
            except TimeoutError:
                pass

    def _track(self, run_id, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        self._run_tasks[run_id] = task

        def completed(done):
            self._tasks.discard(done)
            if self._run_tasks.get(run_id) is done:
                self._run_tasks.pop(run_id, None)
            self._wake.set()
            if not done.cancelled() and done.exception() is not None:
                # Observe failure without logging provider bodies or credentials.
                logging.getLogger(__name__).error("Agent task failed for Run %s", run_id)

        task.add_done_callback(completed)

    def cancel_run(self, run_id):
        task = self._run_tasks.get(run_id)
        if task is not None:
            task.cancel()
        self._wake.set()

    def schedule(self, user_id, session_id, run_id, content):
        """Wake admission after the API's durable transaction has committed."""
        self._wake.set()
        if self._scheduler is None:
            # Retain direct-service usage outside the application lifespan.
            async def claim():
                if await self._db_call(
                    self.store.claim_initial_run,
                    run_id,
                    self._instance_id,
                    max_active=self.max_active_runs,
                    max_user_active=self.max_active_runs_per_user,
                ):
                    self._track(run_id, self._execute(user_id, session_id, run_id, content))

            task = asyncio.create_task(claim())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

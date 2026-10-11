import asyncio
import threading

import pytest

from service.lls.runtime import Runtime


async def test_canceled_thread_job_joins_late_failure_and_preserves_cancellation():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert release.wait(3)
        finished.set()
        raise OSError("late worker failure")

    task = asyncio.create_task(Runtime.thread_job(None, worker))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert task.cancelled()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_uncanceled_thread_job_preserves_worker_failure():
    def worker():
        raise OSError("worker failure")

    with pytest.raises(OSError, match="worker failure"):
        await Runtime.thread_job(None, worker)

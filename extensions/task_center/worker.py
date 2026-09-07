"""Run one worker per data directory. flock prevents overlapping recovery/execution."""
import asyncio
import fcntl
import logging
import os
from .store import Store
from .adapter import UpstreamAdapter, redact


async def serve():
    store = Store(os.environ.get('TC_DATA_DIR', '/data/task-center'))
    with (store.root / 'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.recover()
        adapter = UpstreamAdapter(store)
        while True:
            job = store.claim()
            if not job:
                await asyncio.sleep(1)
                continue
            try:
                await adapter.run(job)
            except Exception as exc:
                logging.error('Task %s failed: %s', job['id'], redact(exc))
                store.update(job['id'], 'failed', '处理失败', error=redact(exc))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())

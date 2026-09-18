"""CLI entry point for the separate RAG worker process."""

import asyncio
import os
import signal
import socket
from datetime import timedelta

from backend.config import ApplicationSettings
from backend.observability import configure_logging
from backend.rag import LightRAGRuntimeRegistry
from backend.worker.runner import Worker
from backend.worker.service import IndexingJobProcessor, PostgreSQLJobStore


async def main() -> None:
    """Run the worker and close workspace runtimes after graceful shutdown."""
    settings = ApplicationSettings()
    configure_logging(settings.logging)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for watched_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(watched_signal, stop.set)

    runtimes = LightRAGRuntimeRegistry(settings)
    owner = f"{socket.gethostname()}-{os.getpid()}"[:128]
    worker = Worker(
        PostgreSQLJobStore(settings.postgres),
        IndexingJobProcessor(settings, runtimes),
        owner=owner,
        lease_for=timedelta(seconds=settings.worker.lease_seconds),
        heartbeat_every=settings.worker.heartbeat_seconds,
        retry_after=timedelta(seconds=settings.worker.retry_seconds),
        poll_every=settings.worker.poll_seconds,
    )
    try:
        await worker.run(stop)
    finally:
        await runtimes.close()


if __name__ == "__main__":
    asyncio.run(main())

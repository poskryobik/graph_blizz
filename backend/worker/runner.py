"""Framework-independent leased job worker loop."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from backend.embeddings.client import EmbeddingError
from backend.jobs import Job, JobErrorCode
from backend.llm.client import LLMError
from backend.storage import ObjectStorageError


class JobStore(Protocol):
    """Committed persistence operations required by ``Worker``."""

    async def recover_expired(self, *, retry_after: timedelta) -> list[Job]: ...

    async def claim_next(self, *, owner: str, lease_for: timedelta) -> Job | None: ...

    async def heartbeat(
        self, *, job_id: UUID, owner: str, lease_for: timedelta
    ) -> Job | None: ...

    async def succeed(self, *, job_id: UUID, owner: str) -> Job | None: ...

    async def fail(
        self,
        *,
        job_id: UUID,
        owner: str,
        error_code: JobErrorCode,
        retry_after: timedelta,
    ) -> Job | None: ...


class JobProcessor(Protocol):
    """Execute the application operation represented by a claimed job."""

    async def process(self, job: Job) -> None: ...


class Worker:
    """Claim and execute one job at a time while maintaining its lease."""

    def __init__(
        self,
        store: JobStore,
        processor: JobProcessor,
        *,
        owner: str,
        lease_for: timedelta,
        heartbeat_every: float,
        retry_after: timedelta,
        poll_every: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Configure a single-concurrency worker without starting external I/O."""
        if not owner.strip() or len(owner) > 128:
            raise ValueError("owner must contain 1 to 128 non-blank chars")
        if heartbeat_every <= 0 or heartbeat_every >= lease_for.total_seconds():
            raise ValueError(
                "heartbeat interval must be positive and shorter than lease"
            )
        if poll_every <= 0:
            raise ValueError("poll interval must be positive")
        self._store = store
        self._processor = processor
        self._owner = owner
        self._lease_for = lease_for
        self._heartbeat_every = heartbeat_every
        self._retry_after = retry_after
        self._poll_every = poll_every
        self._sleep = sleep

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stopped; a signal stops claiming after current work finishes."""
        while not stop.is_set():
            worked = await self.run_once(stop)
            if not worked and not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_every)
                except TimeoutError:
                    pass

    async def run_once(self, stop: asyncio.Event | None = None) -> bool:
        """Recover stale leases and process at most one newly claimed job."""
        if stop is not None and stop.is_set():
            return False
        await self._store.recover_expired(retry_after=self._retry_after)
        if stop is not None and stop.is_set():
            return False
        job = await self._store.claim_next(owner=self._owner, lease_for=self._lease_for)
        if job is None:
            return False

        heartbeat = asyncio.create_task(self._heartbeat(job))
        processing = asyncio.create_task(self._processor.process(job))
        try:
            done, _ = await asyncio.wait(
                (processing, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                error = heartbeat.exception()
                if error is not None:
                    processing.cancel()
                    await asyncio.gather(processing, return_exceptions=True)
                    return True
            await processing
        except asyncio.CancelledError:
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
            raise
        except Exception as error:  # noqa: BLE001 - job failures are persisted safely
            await self._store.fail(
                job_id=job.id,
                owner=self._owner,
                error_code=classify_error(error),
                retry_after=self._retry_after,
            )
        else:
            await self._store.succeed(job_id=job.id, owner=self._owner)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def _heartbeat(self, job: Job) -> None:
        while True:
            await self._sleep(self._heartbeat_every)
            renewed = await self._store.heartbeat(
                job_id=job.id, owner=self._owner, lease_for=self._lease_for
            )
            if renewed is None:
                raise RuntimeError("job lease ownership was lost")


def classify_error(error: Exception) -> JobErrorCode:
    """Map an exception to allow-listed metadata without persisting its message."""
    if isinstance(
        error,
        (EmbeddingError, LLMError, ObjectStorageError, ConnectionError, TimeoutError),
    ):
        return JobErrorCode.DEPENDENCY_UNAVAILABLE
    return JobErrorCode.INDEXING_FAILED

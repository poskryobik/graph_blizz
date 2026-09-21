"""Workspace reindex orchestration."""

from uuid import UUID

from backend.index_versions import IndexContract
from backend.jobs import Job, JobRepository
from backend.workspaces import WorkspaceRepository


class WorkspaceReindexService:
    """Create persisted per-revision work for one maintenance request."""

    def __init__(
        self,
        workspaces: WorkspaceRepository,
        jobs: JobRepository,
        contract: IndexContract,
    ) -> None:
        """Bind the operation to a caller-owned PostgreSQL transaction."""
        self._workspaces = workspaces
        self._jobs = jobs
        self._contract = contract

    async def enqueue(self, workspace_id: UUID) -> list[Job]:
        """Reconcile the index contract and persist jobs for affected revisions."""
        try:
            await self._workspaces.ensure_index_contract(
                workspace_id,
                index_schema_version=self._contract.index_schema_version,
                embedding_profile=self._contract.embedding_profile,
            )
            jobs = await self._jobs.create_workspace_reindex(workspace_id)
            await self._jobs.commit()
            return jobs
        except BaseException:
            await self._jobs.rollback()
            raise

"""Full-stack restart and source-of-truth persistence coverage."""

import os
from uuid import uuid4

import httpx
import pytest

from tests.e2e.mvp_stack import (
    V1_FACT,
    V2_FACT,
    compose,
    inspect_source_of_truth,
    isolated_stack,
    resolve_api_url,
)
from tests.e2e.test_mvp_lifecycle import (
    _upsert,
    _wait_for_status,
    _wait_for_workspace,
)

pytestmark = pytest.mark.e2e


def test_restart_preserves_deleted_document_source_of_truth() -> None:
    """Restart every service and recover retained PostgreSQL/MinIO state."""
    with isolated_stack("restart") as stack:
        with httpx.Client(base_url=stack.api_url, timeout=240) as client:
            workspace = client.post(
                "/workspaces",
                json={"name": "F042 Restart", "slug": f"f042-restart-{os.getpid()}"},
            )
            workspace.raise_for_status()
            workspace_id = str(workspace.json()["id"])
            _wait_for_workspace(client, workspace_id)
            document_id = str(uuid4())

            _upsert(client, workspace_id, document_id, V1_FACT)
            _wait_for_status(client, workspace_id, document_id, "READY")
            unchanged = _upsert(client, workspace_id, document_id, V1_FACT)
            assert unchanged["action"] == "unchanged"
            assert unchanged["revision"] == 1
            assert unchanged["job_id"] is None
            _upsert(client, workspace_id, document_id, V2_FACT)
            _wait_for_status(client, workspace_id, document_id, "READY")
            deletion = client.delete(
                f"/v1/workspaces/{workspace_id}/documents/{document_id}"
            )
            assert deletion.status_code == 202
            _wait_for_status(client, workspace_id, document_id, "DELETED")

        before = inspect_source_of_truth(stack, document_id)
        compose(
            stack.project,
            stack.environment,
            "restart",
            "postgres",
            "minio",
            "neo4j",
            "qdrant",
            "rag-api",
            "rag-worker",
            timeout=180,
        )
        compose(
            stack.project,
            stack.environment,
            "up",
            "-d",
            "--wait",
            timeout=240,
        )
        after = inspect_source_of_truth(stack, document_id)
        restarted_api_url = resolve_api_url(stack.project, stack.environment)

        assert after == before
        assert after["document"] == ["DELETED", 2]
        assert [revision for revision, _uri in after["revisions"]] == [1, 2]
        assert after["sources"] == [[1, V1_FACT], [2, V2_FACT]]
        assert after["jobs"] == [
            ["INDEX_DOCUMENT", 1, "SUCCEEDED"],
            ["INDEX_DOCUMENT", 2, "SUCCEEDED"],
            ["DELETE_DOCUMENT", 2, "SUCCEEDED"],
        ]

        with httpx.Client(base_url=restarted_api_url, timeout=30) as client:
            persisted = client.get(
                f"/v1/workspaces/{workspace_id}/documents/{document_id}"
            )
            persisted.raise_for_status()
            assert persisted.json()["status"] == "DELETED"
            query = client.post(
                f"/v1/workspaces/{workspace_id}/query",
                json={"query": "Which signal does the Atlas beacon use?"},
            )
            query.raise_for_status()
            assert query.json()["sources"] == []
            assert V1_FACT.lower() not in query.json()["answer"].lower()
            assert V2_FACT.lower() not in query.json()["answer"].lower()

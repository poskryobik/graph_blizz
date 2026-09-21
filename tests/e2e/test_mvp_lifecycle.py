"""Public full-stack coverage for the single-owner MVP lifecycle."""

import os
import time
from typing import Any
from uuid import uuid4

import httpx
import pytest

from tests.e2e.mvp_stack import SOURCE_FILENAME, V1_FACT, V2_FACT, isolated_stack

pytestmark = pytest.mark.e2e


def test_public_mvp_lifecycle() -> None:
    """Create, no-op, replace, query, and delete through public Demo-owner APIs."""
    with (
        isolated_stack("lifecycle") as stack,
        httpx.Client(base_url=stack.api_url, timeout=240) as client,
    ):
        workspace = client.post(
            "/workspaces",
            json={"name": "F042 Lifecycle", "slug": f"f042-{os.getpid()}"},
        )
        workspace.raise_for_status()
        workspace_id = str(workspace.json()["id"])
        _wait_for_workspace(client, workspace_id)

        document_id = str(uuid4())
        created = _upsert(client, workspace_id, document_id, V1_FACT)
        assert created["action"] == "created"
        assert created["revision"] == 1
        assert created["status"] == "PENDING"
        assert created["job_id"] is not None
        _wait_for_status(client, workspace_id, document_id, "READY")

        unchanged = _upsert(client, workspace_id, document_id, V1_FACT)
        assert unchanged["action"] == "unchanged"
        assert unchanged["revision"] == 1
        assert unchanged["status"] == "READY"
        assert unchanged["job_id"] is None

        changed = _upsert(client, workspace_id, document_id, V2_FACT)
        assert changed["action"] == "updated"
        assert changed["revision"] == 2
        assert changed["status"] == "PENDING"
        assert changed["job_id"] is not None
        _wait_for_status(client, workspace_id, document_id, "READY")

        query = client.post(
            f"/v1/workspaces/{workspace_id}/query",
            json={"query": "Which signal does the Atlas beacon use?"},
        )
        query.raise_for_status()
        result = query.json()
        assert V2_FACT.lower() in result["answer"].lower()
        assert V1_FACT.lower() not in result["answer"].lower()
        matching_sources = [
            source
            for source in result["sources"]
            if source["document_id"] == document_id
            and source["filename"] == SOURCE_FILENAME
        ]
        assert matching_sources
        assert all(source.get("revision") in (None, 2) for source in matching_sources)

        deleted = client.delete(
            f"/v1/workspaces/{workspace_id}/documents/{document_id}"
        )
        assert deleted.status_code == 202
        assert deleted.json()["revision"] == 2
        assert deleted.json()["status"] == "PENDING"
        _wait_for_status(client, workspace_id, document_id, "DELETED")


def _upsert(
    client: httpx.Client, workspace_id: str, document_id: str, content: str
) -> dict[str, Any]:
    response = client.put(
        f"/v1/workspaces/{workspace_id}/documents/{document_id}",
        files={"file": (SOURCE_FILENAME, content, "text/plain")},
    )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


def _wait_for_status(
    client: httpx.Client, workspace_id: str, document_id: str, expected: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = client.get(f"/v1/workspaces/{workspace_id}/documents/{document_id}")
        response.raise_for_status()
        document: dict[str, Any] = response.json()
        if document["status"] == expected:
            return document
        if document["status"] == "FAILED":
            pytest.fail(f"document entered FAILED while waiting for {expected}")
        time.sleep(0.25)
    pytest.fail(f"document did not reach {expected}")


def _wait_for_workspace(client: httpx.Client, workspace_id: str) -> None:
    """Wait until a just-created workspace is visible to a new request."""
    deadline = time.monotonic() + 10
    last_response = "no request attempted"
    while time.monotonic() < deadline:
        response = client.get(f"/workspaces/{workspace_id}")
        if response.status_code == 200:
            return
        last_response = f"HTTP {response.status_code}: {response.text}"
        if response.status_code != 404:
            response.raise_for_status()
        time.sleep(0.05)
    pytest.fail(f"workspace {workspace_id} was not visible: {last_response}")

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

COMPOSE_FILE = Path(__file__).parents[2] / "docker-compose.yml"


def service_definition(name: str) -> str:
    """Return one top-level Compose service definition as source text."""
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    marker = f"  {name}:\n"
    start = compose.index(marker) + len(marker)
    next_service = re.search(r"(?m)^  [a-zA-Z0-9_-]+:\n", compose[start:])
    end = len(compose) if next_service is None else start + next_service.start()
    return compose[start:end]


def test_rag_api_publishes_configurable_host_port() -> None:
    rag_api = service_definition("rag-api")

    assert '"${GRAPH_BLIZZ_API_PORT:-8000}:8000"' in rag_api
    assert "      - backend\n      - host_access" in rag_api


def test_minio_publishes_api_and_web_console_ports() -> None:
    minio = service_definition("minio")

    assert 'command: server /data --console-address ":9001"' in minio
    assert '"${GRAPH_BLIZZ_MINIO_API_PORT:-9000}:9000"' in minio
    assert '"${GRAPH_BLIZZ_MINIO_CONSOLE_PORT:-9001}:9001"' in minio
    assert "      - backend\n      - host_access" in minio


def test_postgres_remains_internal_only() -> None:
    postgres = service_definition("postgres")

    assert "ports:" not in postgres
    assert "      - backend" in postgres
    assert "host_access" not in postgres


def test_neo4j_is_persistent_healthy_and_internal_only() -> None:
    neo4j = service_definition("neo4j")

    assert "image: neo4j:2025.07.1-community" in neo4j
    assert "      - neo4j_data:/data" in neo4j
    assert "healthcheck:" in neo4j
    assert "cypher-shell" in neo4j
    assert '"RETURN 1"' in neo4j
    assert "ports:" not in neo4j
    assert "      - backend" in neo4j
    assert "host_access" not in neo4j


def test_qdrant_publishes_configurable_web_port() -> None:
    qdrant = service_definition("qdrant")

    assert "image: qdrant/qdrant:v1.15.4" in qdrant
    assert '"${GRAPH_BLIZZ_QDRANT_PORT:-6333}:6333"' in qdrant
    assert "      - qdrant_data:/qdrant/storage" in qdrant
    assert "healthcheck:" in qdrant
    assert "GET /readyz HTTP/1.1" in qdrant
    assert 'test "$$status" = 200' in qdrant
    assert ":> /dev/tcp" not in qdrant
    assert "      - backend\n      - host_access" in qdrant


def test_host_access_network_is_not_internal() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "networks:\n  backend:\n    internal: true\n  host_access:\n" in compose

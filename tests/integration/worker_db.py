"""Opt-in live PostgreSQL fixture helpers for worker integration tests."""

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.config import PostgreSQLSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def live_worker_postgres() -> Iterator[PostgreSQLSettings]:
    """Start migrated PostgreSQL, or skip only when Docker is unavailable."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    for command in (["docker", "info"],):
        if subprocess.run(command, capture_output=True, check=False).returncode != 0:
            pytest.skip("Docker daemon is unavailable")
    name = f"graph-blizz-worker-{os.getpid()}-{time.time_ns()}"
    password = "worker-integration-password"
    started = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_DB=graph_blizz",
            "--env",
            "POSTGRES_USER=graph_blizz",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "postgres:17-alpine",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.fail(f"failed to start PostgreSQL: {started.stderr}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "graph_blizz"],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            pytest.fail("PostgreSQL did not become ready")
        port = (
            subprocess.run(
                ["docker", "port", name, "5432/tcp"],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        environment = os.environ.copy()
        environment.update(
            {
                "GRAPH_BLIZZ_POSTGRES__HOST": "127.0.0.1",
                "GRAPH_BLIZZ_POSTGRES__PORT": port,
                "GRAPH_BLIZZ_POSTGRES__DATABASE": "graph_blizz",
                "GRAPH_BLIZZ_POSTGRES__USERNAME": "graph_blizz",
                "GRAPH_BLIZZ_POSTGRES__PASSWORD": password,
                "GRAPH_BLIZZ_POSTGRES__SSL_MODE": "disable",
                "UV_CACHE_DIR": "/tmp/uv-cache",
            }
        )
        migrated = subprocess.run(
            ["uv", "run", "--frozen", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if migrated.returncode != 0:
            pytest.fail(f"migration failed: {migrated.stderr}")
        yield PostgreSQLSettings(
            host="127.0.0.1",
            port=int(port),
            database="graph_blizz",
            username="graph_blizz",
            password=password,
            ssl_mode="disable",
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name], capture_output=True, check=False
        )

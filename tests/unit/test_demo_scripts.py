import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
PROJECT_ROOT = Path(__file__).parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_init_runs_idempotent_bootstrap_steps_in_order(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "scripts", project / "scripts")
    shutil.copy2(PROJECT_ROOT / ".env.example", project / ".env.example")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    _write_executable(
        bin_dir / "docker",
        'printf "%q " "$@" >> "${TRACE_FILE}"\nprintf "\\n" >> "${TRACE_FILE}"\n',
    )

    result = subprocess.run(
        [project / "scripts" / "init.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "TRACE_FILE": str(trace)},
    )

    assert result.returncode == 0, result.stderr
    assert (project / ".env").read_text(encoding="utf-8") == (
        project / ".env.example"
    ).read_text(encoding="utf-8")
    calls = trace.read_text(encoding="utf-8").splitlines()
    assert calls[:4] == [
        "compose version ",
        "info ",
        "compose up -d --build --wait ",
        "compose exec -T rag-api /app/.venv/bin/alembic upgrade head ",
    ]
    assert calls[4].startswith("compose exec -T rag-api /app/.venv/bin/python -c ")
    assert "head_bucket" in calls[4]
    assert calls[5].startswith("compose exec -T rag-api /app/.venv/bin/python -c ")
    assert "/health/ready" in calls[5]
    assert calls[6].startswith("compose exec -T rag-api /app/.venv/bin/python -c ")
    assert "EmbeddingClient" in calls[6]
    assert "LLMClient" in calls[6]
    assert "await embedding.embed" in calls[6]
    assert "await llm.complete" in calls[6]
    assert "Demo is ready" in result.stdout


def test_init_reports_unavailable_docker_daemon(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "docker",
        'if [[ "$*" == "info" ]]; then exit 1; fi\n',
    )

    result = subprocess.run(
        [PROJECT_ROOT / "scripts" / "init.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 1
    assert "Docker daemon is unavailable" in result.stderr


def test_init_is_repeatable_and_preserves_existing_env(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "scripts", project / "scripts")
    shutil.copy2(PROJECT_ROOT / ".env.example", project / ".env.example")
    env_file = project / ".env"
    env_file.write_text("GRAPH_BLIZZ_API_PORT=8123\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "docker", "exit 0\n")
    environment = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    first = subprocess.run(
        [project / "scripts" / "init.sh"], check=False, env=environment
    )
    second = subprocess.run(
        [project / "scripts" / "init.sh"], check=False, env=environment
    )

    assert first.returncode == second.returncode == 0
    assert env_file.read_text(encoding="utf-8") == "GRAPH_BLIZZ_API_PORT=8123\n"


@pytest.mark.parametrize("failing_model", ["embedding", "llm"])
def test_init_does_not_report_ready_when_model_readiness_fails(
    tmp_path: Path, failing_model: str
) -> None:
    project = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "scripts", project / "scripts")
    shutil.copy2(PROJECT_ROOT / ".env.example", project / ".env.example")
    stubs = tmp_path / "stubs"
    for package in ("backend", "backend/embeddings", "backend/llm"):
        package_dir = stubs / package
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (stubs / "backend/config.py").write_text(
        "class ApplicationSettings:\n"
        "    def __init__(self):\n"
        "        self.embedding = object()\n"
        "        self.external_llm = object()\n",
        encoding="utf-8",
    )
    (stubs / "backend/embeddings/client.py").write_text(
        "import os\n"
        "class EmbeddingClient:\n"
        "    def __init__(self, settings): pass\n"
        "    async def __aenter__(self): return self\n"
        "    async def __aexit__(self, *args): pass\n"
        "    async def embed(self, text):\n"
        "        if os.environ['FAIL_MODEL'] == 'embedding': raise RuntimeError\n"
        "        return [1.0]\n",
        encoding="utf-8",
    )
    (stubs / "backend/llm/client.py").write_text(
        "import os\n"
        "class LLMClient:\n"
        "    def __init__(self, settings): pass\n"
        "    async def __aenter__(self): return self\n"
        "    async def __aexit__(self, *args): pass\n"
        "    async def complete(self, messages):\n"
        "        if os.environ['FAIL_MODEL'] == 'llm': raise RuntimeError\n"
        "        return 'READY'\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "docker",
        'if [[ "$*" == *"asyncio.run(check_models())"* ]]; then\n'
        '    PYTHONPATH="${MODEL_STUBS}" python3 "${@: -1}"\n'
        "fi\n",
    )

    result = subprocess.run(
        [project / "scripts" / "init.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "MODEL_STUBS": str(stubs),
            "FAIL_MODEL": failing_model,
        },
    )

    assert result.returncode == 1
    assert "Model readiness failed" in result.stderr
    assert "GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL" in result.stderr
    assert "GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL" in result.stderr
    assert "Demo is ready" not in result.stdout


def test_verify_demo_runs_required_gates_in_order(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "verify-demo.sh", scripts)
    trace = tmp_path / "trace"
    for name in ("verify-unit.sh", "verify-integration.sh"):
        _write_executable(
            scripts / name, f'printf "%s\\n" "{name}" >> "${{TRACE_FILE}}"\n'
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "uv",
        'printf "uv %s\\n" "$*" >> "${TRACE_FILE}"\n',
    )

    result = subprocess.run(
        [scripts / "verify-demo.sh"],
        check=False,
        env=os.environ
        | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "TRACE_FILE": str(trace)},
    )

    assert result.returncode == 0
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "verify-unit.sh",
        "verify-integration.sh",
        "uv run --frozen --no-cache pytest -q -m e2e tests/e2e/test_demo_graph_rag.py",
    ]


@pytest.mark.parametrize("script", ["init.sh", "verify-demo.sh"])
def test_demo_script_is_executable(script: str) -> None:
    assert os.access(PROJECT_ROOT / "scripts" / script, os.X_OK)

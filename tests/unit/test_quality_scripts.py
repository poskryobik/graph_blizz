import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[2]


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def run_script_with_fake_uv(
    tmp_path: Path,
    script_name: str,
    *,
    failing_invocation: str | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "uv",
        'printf "%s|%s\\n" "$PWD" "$*" >> "${TRACE_FILE}"\n'
        'if [[ "$*" == "${FAIL_INVOCATION:-}" ]]; then exit 9; fi\n',
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TRACE_FILE": str(tmp_path / "trace"),
    }
    if failing_invocation is not None:
        env["FAIL_INVOCATION"] = failing_invocation

    return subprocess.run(
        [PROJECT_ROOT / "scripts" / script_name],
        cwd=tmp_path,
        check=False,
        env=env,
        text=True,
    )


def read_trace(tmp_path: Path) -> list[str]:
    return (tmp_path / "trace").read_text(encoding="utf-8").splitlines()


def test_lint_script_checks_sources_and_format_from_project_root(
    tmp_path: Path,
) -> None:
    result = run_script_with_fake_uv(tmp_path, "lint.sh")

    assert result.returncode == 0
    assert read_trace(tmp_path) == [
        f"{PROJECT_ROOT}|run --frozen --no-cache ruff check backend tests",
        f"{PROJECT_ROOT}|run --frozen --no-cache ruff format --check backend tests",
    ]


def test_lint_script_stops_after_failed_check(tmp_path: Path) -> None:
    result = run_script_with_fake_uv(
        tmp_path,
        "lint.sh",
        failing_invocation="run --frozen --no-cache ruff check backend tests",
    )

    assert result.returncode == 9
    assert len(read_trace(tmp_path)) == 1


def test_typecheck_script_checks_production_sources_from_project_root(
    tmp_path: Path,
) -> None:
    result = run_script_with_fake_uv(tmp_path, "typecheck.sh")

    assert result.returncode == 0
    assert read_trace(tmp_path) == [
        f"{PROJECT_ROOT}|run --frozen --no-cache mypy backend",
    ]


@pytest.mark.parametrize("script_name", ["lint.sh", "typecheck.sh"])
def test_quality_script_is_executable_and_propagates_failure(
    tmp_path: Path,
    script_name: str,
) -> None:
    script = PROJECT_ROOT / "scripts" / script_name
    invocation = (
        "run --frozen --no-cache ruff check backend tests"
        if script_name == "lint.sh"
        else "run --frozen --no-cache mypy backend"
    )

    assert script.stat().st_mode & stat.S_IXUSR
    assert (
        run_script_with_fake_uv(
            tmp_path,
            script_name,
            failing_invocation=invocation,
        ).returncode
        == 9
    )


def test_mypy_configuration_supports_gradual_typing_and_detects_typed_errors(
    tmp_path: Path,
) -> None:
    config = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["mypy"]
    assert config["strict"] is False
    assert config["disallow_untyped_defs"] is False
    assert config["check_untyped_defs"] is False

    gradual_module = tmp_path / "gradual.py"
    gradual_module.write_text(
        "def untyped(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    typed_error_module = tmp_path / "typed_error.py"
    typed_error_module.write_text(
        'def typed() -> int:\n    return "not an int"\n',
        encoding="utf-8",
    )

    gradual_result = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--no-cache",
            "mypy",
            "--config-file",
            PROJECT_ROOT / "pyproject.toml",
            gradual_module,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    typed_error_result = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--no-cache",
            "mypy",
            "--config-file",
            PROJECT_ROOT / "pyproject.toml",
            typed_error_module,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert gradual_result.returncode == 0, gradual_result.stdout
    assert typed_error_result.returncode != 0
    assert "Incompatible return value type" in typed_error_result.stdout

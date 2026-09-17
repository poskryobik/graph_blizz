import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[2]
RUNNERS = [
    ("verify-unit.sh", "unit", "tests/unit"),
    ("verify-integration.sh", "integration and not gpu", "tests/integration"),
    ("verify-e2e.sh", "e2e", "tests/e2e"),
]


def write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize(("script", "marker", "suite"), RUNNERS)
def test_runner_selects_its_marker_and_suite(
    tmp_path: Path,
    script: str,
    marker: str,
    suite: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    write_executable(
        bin_dir / "uv",
        'printf "%s\\n" "$PWD" "$@" > "${TRACE_FILE}"\n',
    )

    result = subprocess.run(
        [PROJECT_ROOT / "scripts" / script],
        check=False,
        cwd=tmp_path,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TRACE_FILE": str(trace),
        },
        text=True,
    )

    assert result.returncode == 0
    assert trace.read_text(encoding="utf-8").splitlines() == [
        str(PROJECT_ROOT),
        "run",
        "--frozen",
        "--no-cache",
        "pytest",
        "-q",
        "-m",
        marker,
        suite,
    ]


@pytest.mark.parametrize(("script", "_marker", "_suite"), RUNNERS)
def test_runner_accepts_pytest_no_tests_collected(
    tmp_path: Path,
    script: str,
    _marker: str,
    _suite: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "uv", "exit 5\n")

    result = subprocess.run(
        [PROJECT_ROOT / "scripts" / script],
        check=False,
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0


@pytest.mark.parametrize(("script", "_marker", "_suite"), RUNNERS)
def test_runner_propagates_pytest_failure(
    tmp_path: Path,
    script: str,
    _marker: str,
    _suite: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "uv", "exit 7\n")

    result = subprocess.run(
        [PROJECT_ROOT / "scripts" / script],
        check=False,
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 7


def prepare_aggregate(tmp_path: Path, failing_check: str | None = None) -> Path:
    aggregate = tmp_path / "verify.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "verify.sh", aggregate)
    for check in ["lint", "typecheck"]:
        exit_code = 7 if check == failing_check else 0
        write_executable(
            tmp_path / f"{check}.sh",
            f'printf "%s\\n" "{check}" >> "${{TRACE_FILE}}"\nexit {exit_code}\n',
        )
    for script, marker, _suite in RUNNERS:
        group = marker.split()[0]
        exit_code = 7 if group == failing_check else 0
        write_executable(
            tmp_path / script,
            f'printf "%s\\n" "{group}" >> "${{TRACE_FILE}}"\nexit {exit_code}\n',
        )
    return aggregate


def test_aggregate_runs_all_groups(tmp_path: Path) -> None:
    trace = tmp_path / "trace"
    aggregate = prepare_aggregate(tmp_path)

    result = subprocess.run(
        [aggregate],
        check=False,
        env=os.environ | {"TRACE_FILE": str(trace)},
    )

    assert result.returncode == 0
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "lint",
        "typecheck",
        "unit",
        "integration",
        "e2e",
    ]


def test_aggregate_propagates_group_failure(tmp_path: Path) -> None:
    trace = tmp_path / "trace"
    aggregate = prepare_aggregate(tmp_path, failing_check="integration")

    result = subprocess.run(
        [aggregate],
        check=False,
        env=os.environ | {"TRACE_FILE": str(trace)},
    )

    assert result.returncode == 7
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "lint",
        "typecheck",
        "unit",
        "integration",
    ]


@pytest.mark.parametrize("failing_check", ["lint", "typecheck"])
def test_aggregate_stops_after_quality_failure(
    tmp_path: Path,
    failing_check: str,
) -> None:
    trace = tmp_path / "trace"
    aggregate = prepare_aggregate(tmp_path, failing_check=failing_check)

    result = subprocess.run(
        [aggregate],
        check=False,
        env=os.environ | {"TRACE_FILE": str(trace)},
    )

    expected = ["lint", "typecheck"]
    assert result.returncode == 7
    assert (
        trace.read_text(encoding="utf-8").splitlines()
        == expected[: expected.index(failing_check) + 1]
    )


@pytest.mark.parametrize(
    "script",
    ["lint.sh", "typecheck.sh", "verify.sh", *(item[0] for item in RUNNERS)],
)
def test_verification_script_is_executable(script: str) -> None:
    assert os.access(PROJECT_ROOT / "scripts" / script, os.X_OK)

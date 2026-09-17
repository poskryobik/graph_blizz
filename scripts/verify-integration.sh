#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(dirname -- "${SCRIPT_DIR}")"

cd -- "${PROJECT_ROOT}"
set +e
uv run --frozen --no-cache pytest -q -m "integration and not gpu" tests/integration
readonly status=$?
set -e

if (( status == 5 )); then
    exit 0
fi

exit "${status}"

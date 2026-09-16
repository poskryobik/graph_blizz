#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(dirname -- "${SCRIPT_DIR}")"

cd -- "${PROJECT_ROOT}"
set +e
uv run --frozen --no-cache pytest -q -m e2e tests/e2e
readonly status=$?
set -e

if (( status == 5 )); then
    exit 0
fi

exit "${status}"

#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(dirname -- "${SCRIPT_DIR}")"

cd -- "${PROJECT_ROOT}"
uv run --frozen --no-cache ruff check backend tests
uv run --frozen --no-cache ruff format --check backend tests

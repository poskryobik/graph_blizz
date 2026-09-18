#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(dirname -- "${SCRIPT_DIR}")"

cd -- "${PROJECT_ROOT}"

printf 'Running deterministic unit checks...\n'
"${SCRIPT_DIR}/verify-unit.sh"

printf 'Running deterministic integration checks...\n'
"${SCRIPT_DIR}/verify-integration.sh"

printf 'Running the full Demo Graph RAG happy path...\n'
uv run --frozen --no-cache pytest -q -m e2e tests/e2e/test_demo_graph_rag.py

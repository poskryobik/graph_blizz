#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"${SCRIPT_DIR}/lint.sh"
"${SCRIPT_DIR}/typecheck.sh"
"${SCRIPT_DIR}/verify-unit.sh"
"${SCRIPT_DIR}/verify-integration.sh"
"${SCRIPT_DIR}/verify-e2e.sh"

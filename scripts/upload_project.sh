#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  upload_project.sh DIRECTORY WORKSPACE_ID [options]

Options:
  --base-url URL   Graph Blizz API URL
                   Default: $GRAPH_BLIZZ_API_URL or http://localhost:8010
  --dry-run        Show files without uploading
  -h, --help       Show help

Example:
  ./scripts/upload_project.sh \
    ./my-project \
    "$WORKSPACE_ID" \
    --base-url http://localhost:8010
EOF
}

if (( $# < 2 )); then
  usage >&2
  exit 2
fi

ROOT=$1
WORKSPACE_ID=$2
shift 2

BASE_URL=${GRAPH_BLIZZ_API_URL:-http://localhost:8010}
DRY_RUN=0

while (( $# > 0 )); do
  case "$1" in
    --base-url)
      [[ $# -ge 2 ]] || { echo "ERROR: --base-url requires a value" >&2; exit 2; }
      BASE_URL=$2
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

BASE_URL=${BASE_URL%/}

[[ -d "$ROOT" ]] || { echo "ERROR: directory does not exist: $ROOT" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "ERROR: curl is required" >&2; exit 2; }

ROOT=$(cd "$ROOT" && pwd -P)
MAX_BYTES=$((10 * 1024 * 1024))

is_supported_file() {
  local name=${1,,}
  case "$name" in
    *.txt|*.md|*.markdown|*.py|*.js|*.jsx|*.ts|*.tsx|*.java|*.go) return 0 ;;
    *) return 1 ;;
  esac
}

get_mime_type() {
  local name=${1,,}
  case "$name" in
    *.txt)          echo "text/plain" ;;
    *.md|*.markdown) echo "text/markdown" ;;
    *.py)           echo "text/x-python" ;;
    *.js)           echo "text/javascript" ;;
    *.jsx)          echo "text/jsx" ;;
    *.ts)           echo "text/typescript" ;;
    *.tsx)          echo "text/tsx" ;;
    *.java)         echo "text/x-java-source" ;;
    *.go)           echo "text/x-go" ;;
    *)              echo "application/octet-stream" ;;
  esac
}

is_secret_file() {
  local base=${1##*/}
  local name=${base,,}
  case "$name" in
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|\
    id_rsa|id_dsa|id_ecdsa|id_ed25519|\
    credentials|credentials.json|secrets.json|secret.json)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

encode_relative_path() {
  local relative=$1
  printf '%s' "$relative" | sed 's#[/\\]#__#g; s#[^A-Za-z0-9._-]#_#g'
}

file_size() {
  wc -c < "$1" | tr -d '[:space:]'
}

is_valid_utf8() {
  local file=$1
  if command -v iconv >/dev/null 2>&1; then
    iconv -f UTF-8 -t UTF-8 "$file" >/dev/null 2>&1
  else
    return 0
  fi
}

if (( DRY_RUN == 0 )); then
  echo "Checking Graph Blizz API: $BASE_URL"

  curl -fsS --max-time 10 "$BASE_URL/health/live" >/dev/null \
    || { echo "ERROR: Graph Blizz API is not reachable at $BASE_URL" >&2; exit 1; }

  curl -fsS --max-time 10 "$BASE_URL/workspaces/$WORKSPACE_ID" >/dev/null \
    || { echo "ERROR: workspace not found: $WORKSPACE_ID" >&2; exit 1; }
fi

echo "Project:   $ROOT"
echo "Workspace: $WORKSPACE_ID"
echo "API:       $BASE_URL"
echo "Mode:      $([[ $DRY_RUN -eq 1 ]] && echo 'DRY RUN' || echo 'UPLOAD')"
echo

uploaded=0
skipped=0
failed=0
scanned=0

response_file=$(mktemp)
trap 'rm -f "$response_file"' EXIT

while IFS= read -r -d '' file; do
  ((scanned += 1))

  relative=${file#"$ROOT"/}

  if is_secret_file "$relative"; then
    printf 'SKIP   %-70s %s\n' "$relative" "(secret/sensitive)"
    ((skipped += 1))
    continue
  fi

  if ! is_supported_file "$relative"; then
    printf 'SKIP   %-70s %s\n' "$relative" "(unsupported extension)"
    ((skipped += 1))
    continue
  fi

  size=$(file_size "$file")

  if (( size == 0 )); then
    printf 'SKIP   %-70s %s\n' "$relative" "(empty)"
    ((skipped += 1))
    continue
  fi

  if (( size > MAX_BYTES )); then
    printf 'SKIP   %-70s %s\n' "$relative" "(larger than 10 MiB)"
    ((skipped += 1))
    continue
  fi

  if ! is_valid_utf8 "$file"; then
    printf 'SKIP   %-70s %s\n' "$relative" "(not UTF-8)"
    ((skipped += 1))
    continue
  fi

  upload_name=$(encode_relative_path "$relative")
  mime_type=$(get_mime_type "$relative")

  if (( DRY_RUN == 1 )); then
    printf 'UPLOAD %-70s -> %-50s [%s]\n' "$relative" "$upload_name" "$mime_type"
    ((uploaded += 1))
    continue
  fi

  printf 'UPLOAD %-70s -> %-50s [%s] ' \
    "$relative" "$upload_name" "$mime_type"

  : > "$response_file"

  if ! http_code=$(
    curl \
      -sS \
      --connect-timeout 10 \
      --max-time 300 \
      -o "$response_file" \
      -w '%{http_code}' \
      -X POST \
      -F "file=@${file};filename=${upload_name};type=${mime_type}" \
      "$BASE_URL/v1/workspaces/$WORKSPACE_ID/documents"
  ); then
    echo "NETWORK ERROR"
    ((failed += 1))
    continue
  fi

  if [[ "$http_code" =~ ^2[0-9][0-9]$ ]]; then
    echo "OK ($http_code)"
    ((uploaded += 1))

    if [[ -s "$response_file" ]]; then
      if command -v jq >/dev/null 2>&1; then
        jq -c . "$response_file" 2>/dev/null | sed 's/^/       /' || true
      else
        sed 's/^/       /' "$response_file"
        echo
      fi
    fi
  else
    echo "FAILED ($http_code)"
    if [[ -s "$response_file" ]]; then
      sed 's/^/       /' "$response_file"
      echo
    fi
    ((failed += 1))
  fi

done < <(
  find "$ROOT" \
    \( -type d \( \
      -name .git -o \
      -name .hg -o \
      -name .svn -o \
      -name .idea -o \
      -name .vscode -o \
      -name .venv -o \
      -name venv -o \
      -name node_modules -o \
      -name __pycache__ -o \
      -name .pytest_cache -o \
      -name .mypy_cache -o \
      -name .ruff_cache -o \
      -name .cache -o \
      -name dist -o \
      -name build -o \
      -name target -o \
      -name coverage -o \
      -name .next -o \
      -name vendor \
    \) -prune \) -o \
    -type f -print0
)

echo
echo "Summary"
echo "-------"
echo "Scanned:  $scanned"
echo "Uploaded: $uploaded"
echo "Skipped:  $skipped"
echo "Failed:   $failed"

(( failed == 0 )) || exit 1

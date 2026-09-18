#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(dirname -- "${SCRIPT_DIR}")"

fail() {
    printf 'Demo initialization failed: %s\n' "$1" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker CLI is not installed or not in PATH."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable."
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable. Start Docker and retry."

cd -- "${PROJECT_ROOT}"

if [[ ! -e .env ]]; then
    [[ -r .env.example ]] || fail ".env.example is missing or unreadable."
    cp -- .env.example .env
    printf 'Created .env from .env.example.\n'
elif [[ ! -f .env || ! -r .env ]]; then
    fail ".env must be a readable regular file."
fi

printf 'Starting the Demo Compose stack...\n'
docker compose up -d --build --wait || fail "Compose services did not become healthy."

printf 'Applying PostgreSQL migrations...\n'
docker compose exec -T rag-api /app/.venv/bin/alembic upgrade head \
    || fail "Alembic migrations failed."

printf 'Ensuring the Demo object-storage bucket exists...\n'
docker compose exec -T rag-api /app/.venv/bin/python -c '
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from backend.config import ApplicationSettings

settings = ApplicationSettings().minio
client = boto3.client(
    "s3",
    endpoint_url=str(settings.endpoint_url).rstrip("/"),
    region_name=settings.region,
    aws_access_key_id=settings.access_key.get_secret_value(),
    aws_secret_access_key=settings.secret_key.get_secret_value(),
    config=Config(s3={"addressing_style": "path"}),
)
try:
    client.head_bucket(Bucket=settings.bucket)
except ClientError as error:
    if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
        raise
    client.create_bucket(Bucket=settings.bucket)
' || fail "MinIO bucket initialization failed."

printf 'Checking application readiness...\n'
docker compose exec -T rag-api /app/.venv/bin/python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready', timeout=5)" \
    || fail "rag-api readiness check failed."

printf 'Checking configured embedding and LLM endpoints...\n'
docker compose exec -T rag-api /app/.venv/bin/python -c '
import asyncio
from backend.config import ApplicationSettings
from backend.embeddings.client import EmbeddingClient
from backend.llm.client import LLMClient

async def check_models():
    settings = ApplicationSettings()
    async with EmbeddingClient(settings.embedding) as embedding:
        await embedding.embed("Demo readiness check")
    async with LLMClient(settings.external_llm) as llm:
        await llm.complete([{"role": "user", "content": "Reply READY"}])

asyncio.run(check_models())
' || fail "Model readiness failed. Start the gpu profile for the default embedding endpoint or configure GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL; also ensure GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL is reachable from rag-api."

printf 'Demo is ready at http://localhost:${GRAPH_BLIZZ_API_PORT:-8000}.\n'

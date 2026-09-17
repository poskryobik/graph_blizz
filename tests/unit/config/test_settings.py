"""Unit tests for typed application configuration."""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from backend import create_app
from backend.config import (
    ApplicationEnvironment,
    ApplicationSettings,
    AuthMode,
    AuthSettings,
    EmbeddingSettings,
    ExternalLLMSettings,
    LightRAGSettings,
    LoggingSettings,
    MinIOSettings,
    Neo4jSettings,
    PostgreSQLSettings,
    QdrantSettings,
)

pytestmark = pytest.mark.unit
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clear_application_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the process environment from affecting settings tests."""
    for name in tuple(os.environ):
        if name.startswith("GRAPH_BLIZZ_"):
            monkeypatch.delenv(name, raising=False)


def test_defaults_cover_every_subsystem_without_real_secrets() -> None:
    settings = ApplicationSettings(_env_file=None)

    assert settings.environment is ApplicationEnvironment.DEVELOPMENT
    assert settings.auth == AuthSettings(mode=AuthMode.DEMO_OWNER)
    assert isinstance(settings.postgres, PostgreSQLSettings)
    assert isinstance(settings.minio, MinIOSettings)
    assert isinstance(settings.qdrant, QdrantSettings)
    assert isinstance(settings.neo4j, Neo4jSettings)
    assert isinstance(settings.lightrag, LightRAGSettings)
    assert isinstance(settings.embedding, EmbeddingSettings)
    assert isinstance(settings.external_llm, ExternalLLMSettings)
    assert isinstance(settings.logging, LoggingSettings)
    assert settings.postgres.password is None
    assert settings.minio.secret_key is None
    assert settings.neo4j.password is None
    assert settings.external_llm.api_key is None
    assert str(settings.external_llm.base_url) == "http://localhost:11434/v1"
    assert settings.external_llm.model == "qwen3:1.7b"


def test_example_environment_is_safe_and_loadable() -> None:
    settings = ApplicationSettings(_env_file=PROJECT_ROOT / ".env.example")

    assert settings.environment is ApplicationEnvironment.DEVELOPMENT
    assert settings.auth.mode is AuthMode.DEMO_OWNER
    assert settings.postgres.password is None
    assert settings.minio.secret_key is None
    assert settings.neo4j.password is None
    assert settings.external_llm.api_key is None


def test_nested_environment_variables_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        "GRAPH_BLIZZ_ENVIRONMENT": "test",
        "GRAPH_BLIZZ_AUTH__DEMO_OWNER_ID": "test-owner",
        "GRAPH_BLIZZ_POSTGRES__PORT": "5544",
        "GRAPH_BLIZZ_MINIO__BUCKET": "sources",
        "GRAPH_BLIZZ_QDRANT__TIMEOUT_SECONDS": "2.5",
        "GRAPH_BLIZZ_NEO4J__DATABASE": "knowledge",
        "GRAPH_BLIZZ_LIGHTRAG__MAX_PARALLEL_INSERT": "8",
        "GRAPH_BLIZZ_EMBEDDING__MAX_RETRIES": "5",
        "GRAPH_BLIZZ_EXTERNAL_LLM__MODEL": "provider/model",
        "GRAPH_BLIZZ_LOGGING__LEVEL": "DEBUG",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = ApplicationSettings(_env_file=None)

    assert settings.environment is ApplicationEnvironment.TEST
    assert settings.auth.demo_owner_id == "test-owner"
    assert settings.postgres.port == 5544
    assert settings.minio.bucket == "sources"
    assert settings.qdrant.timeout_seconds == 2.5
    assert settings.neo4j.database == "knowledge"
    assert settings.lightrag.max_parallel_insert == 8
    assert settings.embedding.max_retries == 5
    assert settings.external_llm.model == "provider/model"
    assert settings.logging.level == "DEBUG"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GRAPH_BLIZZ_POSTGRES__PORT", "70000"),
        ("GRAPH_BLIZZ_MINIO__ENDPOINT_URL", "not-a-url"),
        ("GRAPH_BLIZZ_QDRANT__TIMEOUT_SECONDS", "0"),
        ("GRAPH_BLIZZ_NEO4J__URI", "http://neo4j.internal"),
        ("GRAPH_BLIZZ_EMBEDDING__DIMENSION", "-1"),
        ("GRAPH_BLIZZ_EXTERNAL_LLM__MAX_RETRIES", "-1"),
        ("GRAPH_BLIZZ_LOGGING__LEVEL", "VERBOSE"),
    ],
)
def test_invalid_environment_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        ApplicationSettings(_env_file=None)


@pytest.mark.parametrize(
    ("settings_type", "field_name"),
    [
        (PostgreSQLSettings, "password"),
        (MinIOSettings, "access_key"),
        (MinIOSettings, "secret_key"),
        (QdrantSettings, "api_key"),
        (Neo4jSettings, "password"),
        (EmbeddingSettings, "api_key"),
        (ExternalLLMSettings, "api_key"),
    ],
)
def test_secret_fields_reject_blank_values(
    settings_type: type[BaseModel], field_name: str
) -> None:
    with pytest.raises(ValidationError, match="secret must not be blank"):
        settings_type(**{field_name: " \t "})


@pytest.mark.parametrize("values", [{"access_key": "key"}, {"secret_key": "secret"}])
def test_minio_static_credentials_must_be_a_pair(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="must be provided together"):
        MinIOSettings(**values)


def test_production_rejects_demo_mode_and_missing_secrets() -> None:
    with pytest.raises(ValidationError) as error:
        ApplicationSettings(environment="production", _env_file=None)

    message = str(error.value)
    for expected in (
        "auth.mode",
        "postgres.password",
        "minio.access_key",
        "minio.secret_key",
        "neo4j.password",
    ):
        assert expected in message


def test_production_rejects_weak_or_placeholder_secrets() -> None:
    with pytest.raises(ValidationError, match="at least 16"):
        ApplicationSettings(
            environment="production",
            auth={"mode": "oidc"},
            postgres={"password": "changeme"},
            minio={"access_key": "access-key", "secret_key": "replace-me"},
            neo4j={"password": "password"},
            external_llm={"api_key": "example"},
            _env_file=None,
        )


def test_complete_production_configuration_is_accepted() -> None:
    settings = ApplicationSettings(
        environment="production",
        auth={"mode": "oidc"},
        postgres={"password": "postgres-secret-32"},
        minio={"access_key": "production-access", "secret_key": "minio-secret-key-32"},
        neo4j={"password": "neo4j-secret-key-32"},
        _env_file=None,
    )

    assert settings.environment is ApplicationEnvironment.PRODUCTION
    assert settings.auth.mode is AuthMode.OIDC


def test_production_rejects_supplied_weak_external_llm_api_key() -> None:
    with pytest.raises(ValidationError, match="external_llm.api_key"):
        ApplicationSettings(
            environment="production",
            auth={"mode": "oidc"},
            postgres={"password": "postgres-secret-32"},
            minio={
                "access_key": "production-access",
                "secret_key": "minio-secret-key-32",
            },
            neo4j={"password": "neo4j-secret-key-32"},
            external_llm={"api_key": "example"},
            _env_file=None,
        )


def test_production_accepts_supplied_strong_external_llm_api_key() -> None:
    settings = ApplicationSettings(
        environment="production",
        auth={"mode": "oidc"},
        postgres={"password": "postgres-secret-32"},
        minio={"access_key": "production-access", "secret_key": "minio-secret-key-32"},
        neo4j={"password": "neo4j-secret-key-32"},
        external_llm={"api_key": "provider-secret-key-32"},
        _env_file=None,
    )

    assert settings.external_llm.api_key is not None


def test_application_factory_stores_settings_without_external_connections() -> None:
    settings = ApplicationSettings(environment="test", _env_file=None)

    application = create_app(settings)

    assert application.state.settings is settings

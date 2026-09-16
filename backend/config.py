"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AnyUrl,
    BaseModel,
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    UrlConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def _reject_blank_secret(value: SecretStr) -> SecretStr:
    """Reject secret values containing only whitespace."""
    if not value.get_secret_value().strip():
        raise ValueError("secret must not be blank")
    return value


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonEmptySecret = Annotated[SecretStr, AfterValidator(_reject_blank_secret)]
Port = Annotated[int, Field(ge=1, le=65535)]
PositiveSeconds = Annotated[float, Field(gt=0)]
Neo4jUrl = Annotated[
    AnyUrl,
    UrlConstraints(
        allowed_schemes=["bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"]
    ),
]


class ApplicationEnvironment(StrEnum):
    """Supported application runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    """Supported identity adapter modes."""

    DEMO_OWNER = "demo_owner"
    OIDC = "oidc"


class AuthSettings(BaseModel):
    """Security adapter settings for Demo/MVP and future production use."""

    mode: AuthMode = AuthMode.DEMO_OWNER
    demo_owner_id: NonEmptyText = "demo-owner"


class PostgreSQLSettings(BaseModel):
    """PostgreSQL connection and pool settings."""

    host: NonEmptyText = "localhost"
    port: Port = 5432
    database: NonEmptyText = "graph_blizz"
    username: NonEmptyText = "graph_blizz"
    password: NonEmptySecret | None = None
    ssl_mode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "prefer"
    pool_size: Annotated[int, Field(gt=0)] = 10
    max_overflow: Annotated[int, Field(ge=0)] = 10


class MinIOSettings(BaseModel):
    """MinIO/S3-compatible object storage settings."""

    endpoint_url: HttpUrl = HttpUrl("http://localhost:9000")
    region: NonEmptyText = "us-east-1"
    bucket: NonEmptyText = "graph-blizz"
    access_key: NonEmptySecret | None = None
    secret_key: NonEmptySecret | None = None
    force_path_style: bool = True

    @model_validator(mode="after")
    def validate_static_credentials(self) -> Self:
        """Require both static credential fields when either is configured."""
        if (self.access_key is None) != (self.secret_key is None):
            raise ValueError(
                "minio access_key and secret_key must be provided together"
            )
        return self


class QdrantSettings(BaseModel):
    """Qdrant vector storage settings."""

    url: HttpUrl = HttpUrl("http://localhost:6333")
    api_key: NonEmptySecret | None = None
    timeout_seconds: PositiveSeconds = 10.0


class Neo4jSettings(BaseModel):
    """Neo4j graph storage settings."""

    uri: Neo4jUrl = AnyUrl("bolt://localhost:7687")
    username: NonEmptyText = "neo4j"
    password: NonEmptySecret | None = None
    database: NonEmptyText = "neo4j"
    connection_timeout_seconds: PositiveSeconds = 10.0


class LightRAGSettings(BaseModel):
    """LightRAG runtime storage and concurrency settings."""

    kv_storage: Literal["PGKVStorage"] = "PGKVStorage"
    document_status_storage: Literal["PGDocStatusStorage"] = "PGDocStatusStorage"
    vector_storage: Literal["QdrantVectorDBStorage"] = "QdrantVectorDBStorage"
    graph_storage: Literal["Neo4JStorage"] = "Neo4JStorage"
    max_parallel_insert: Annotated[int, Field(gt=0)] = 4
    llm_cache_enabled: bool = True


class EmbeddingSettings(BaseModel):
    """OpenAI-compatible embedding endpoint settings."""

    base_url: HttpUrl = HttpUrl("http://localhost:8000/v1")
    model: NonEmptyText = "ai-sage/Giga-Embeddings-instruct-480M-0826"
    api_key: NonEmptySecret | None = None
    dimension: Annotated[int, Field(gt=0)] | None = None
    normalization: bool = True
    timeout_seconds: PositiveSeconds = 30.0
    max_retries: Annotated[int, Field(ge=0)] = 3


class ExternalLLMSettings(BaseModel):
    """OpenAI-compatible external generative model settings."""

    base_url: HttpUrl = HttpUrl("http://localhost:8001/v1")
    model: NonEmptyText = "local-model"
    api_key: NonEmptySecret | None = None
    timeout_seconds: PositiveSeconds = 60.0
    max_retries: Annotated[int, Field(ge=0)] = 3


class LoggingSettings(BaseModel):
    """Operational logging configuration consumed by the F005 implementation."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["json", "text"] = "json"
    service_name: NonEmptyText = "graph-blizz"


class ApplicationSettings(BaseSettings):
    """Graph Blizz settings with ``GRAPH_BLIZZ_`` nested environment overrides."""

    model_config = SettingsConfigDict(
        env_prefix="GRAPH_BLIZZ_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    environment: ApplicationEnvironment = ApplicationEnvironment.DEVELOPMENT
    auth: AuthSettings = Field(default_factory=AuthSettings)
    postgres: PostgreSQLSettings = Field(default_factory=PostgreSQLSettings)
    minio: MinIOSettings = Field(default_factory=MinIOSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    lightrag: LightRAGSettings = Field(default_factory=LightRAGSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    external_llm: ExternalLLMSettings = Field(default_factory=ExternalLLMSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @model_validator(mode="after")
    def validate_production_configuration(self) -> Self:
        """Reject demo auth, absent secrets, and weak secrets in production."""
        if self.environment is not ApplicationEnvironment.PRODUCTION:
            return self

        invalid: list[str] = []
        if self.auth.mode is AuthMode.DEMO_OWNER:
            invalid.append("auth.mode must not be demo_owner")

        required_secrets = {
            "postgres.password": self.postgres.password,
            "minio.access_key": self.minio.access_key,
            "minio.secret_key": self.minio.secret_key,
            "neo4j.password": self.neo4j.password,
            "external_llm.api_key": self.external_llm.api_key,
        }
        for name, value in required_secrets.items():
            if value is None:
                invalid.append(name)
            elif name != "minio.access_key" and _is_weak_production_secret(value):
                invalid.append(
                    f"{name} must contain at least 16 non-placeholder characters"
                )

        if invalid:
            raise ValueError(f"invalid production configuration: {', '.join(invalid)}")
        return self


def _is_weak_production_secret(value: SecretStr) -> bool:
    """Identify short or commonly copied placeholder secrets."""
    raw = value.get_secret_value().strip()
    normalized = raw.lower().replace("_", "-")
    placeholders = ("changeme", "change-me", "replace-me", "example", "password")
    return len(raw) < 16 or any(marker in normalized for marker in placeholders)

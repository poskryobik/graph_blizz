"""Alembic environment configured from application PostgreSQL settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, create_engine, pool

from backend.config import ApplicationSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> URL:
    """Build a SQLAlchemy URL without logging or persisting the password."""
    settings = ApplicationSettings().postgres
    password = (
        settings.password.get_secret_value() if settings.password is not None else None
    )
    return URL.create(
        "postgresql+psycopg",
        username=settings.username,
        password=password,
        host=settings.host,
        port=settings.port,
        database=settings.database,
        query={"sslmode": settings.ssl_mode},
    )


def run_migrations_offline() -> None:
    """Configure and run migrations without opening a database connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Configure and run migrations against the configured PostgreSQL database."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

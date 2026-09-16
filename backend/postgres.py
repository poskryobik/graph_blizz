"""PostgreSQL connectivity checks used by application readiness."""

import psycopg

from backend.config import PostgreSQLSettings


async def postgres_is_ready(settings: PostgreSQLSettings) -> bool:
    """Return whether PostgreSQL accepts an authenticated trivial query.

    Args:
        settings: Validated PostgreSQL connection settings.

    Returns:
        ``True`` after a successful ``SELECT 1``; otherwise ``False``.
    """
    password = (
        settings.password.get_secret_value() if settings.password is not None else None
    )
    try:
        async with await psycopg.AsyncConnection.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.username,
            password=password,
            sslmode=settings.ssl_mode,
            connect_timeout=2,
        ) as connection:
            cursor = await connection.execute("SELECT 1")
            return await cursor.fetchone() == (1,)
    except psycopg.Error:
        return False

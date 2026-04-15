from contextlib import asynccontextmanager
import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_db() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0,
            server_settings={
                "application_name": settings.app_name,
            },
        )
    return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        return await init_db()
    return _pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def transaction():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn

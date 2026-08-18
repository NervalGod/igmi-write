"""
Подключение к PostgreSQL через psycopg (v3) с пулом коннектов.
Поддерживает и async, и sync режимы — sync нужен для worker.py,
который работает в отдельном потоке.
"""
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncGenerator, Generator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


# ==========================================
# Конфигурация
# ==========================================
class Settings(BaseSettings):
    database_url: str = "postgresql://igmi_admin:StrongPassword123!@127.0.0.1:5432/igmi_db"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


# ==========================================
# Пулы коннектов
# ==========================================
_async_pool: AsyncConnectionPool | None = None
_sync_pool: ConnectionPool | None = None


async def init_async_pool() -> None:
    """Создаёт асинхронный пул для FastAPI-эндпоинтов."""
    global _async_pool
    _async_pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    await _async_pool.open()
    logger.info("Асинхронный пул psycopg создан")


async def close_async_pool() -> None:
    global _async_pool
    if _async_pool:
        await _async_pool.close()
        logger.info("Асинхронный пул psycopg закрыт")


def init_sync_pool() -> None:
    """Создаёт синхронный пул для worker-потока."""
    global _sync_pool
    _sync_pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    _sync_pool.open()
    logger.info("Синхронный пул psycopg создан")


def close_sync_pool() -> None:
    global _sync_pool
    if _sync_pool:
        _sync_pool.close()
        logger.info("Синхронный пул psycopg закрыт")


# ==========================================
# Контекстные менеджеры для получения коннекта
# ==========================================
@asynccontextmanager
async def get_async_conn() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    """Берёт async-коннект из пула."""
    if _async_pool is None:
        raise RuntimeError("Async пул не инициализирован")
    async with _async_pool.connection() as conn:
        yield conn


@contextmanager
def get_sync_conn() -> Generator[psycopg.Connection, None, None]:
    """Берёт sync-коннект из пула (для worker-потока)."""
    if _sync_pool is None:
        raise RuntimeError("Sync пул не инициализирован")
    with _sync_pool.connection() as conn:
        yield conn


# ==========================================
# Функции работы с БД (ASYNC — для эндпоинтов)
# ==========================================
async def get_or_create_user(user_ip: str) -> dict[str, Any]:
    """Возвращает пользователя по IP. Если нет — создаёт."""
    async with get_async_conn() as conn:
        row = await conn.execute(
            """
            INSERT INTO users (user_ip)
            VALUES (%s::inet)
            ON CONFLICT (user_ip) DO UPDATE SET user_ip = EXCLUDED.user_ip
            RETURNING id, user_ip, full_name
            """,
            (user_ip,),
        )
        return await row.fetchone()


async def add_file(user_id: str, project: str, path: str, size_bytes: int) -> dict[str, Any]:
    """Добавляет запись о сгенерированном файле."""
    async with get_async_conn() as conn:
        row = await conn.execute(
            """
            INSERT INTO files (user_id, project, path, size_bytes)
            VALUES (%s::uuid, %s, %s, %s)
            RETURNING id, project, path, size_bytes, created_at
            """,
            (user_id, project, path, size_bytes),
        )
        return await row.fetchone()


async def get_files_grouped() -> list[dict]:
    """Возвращает файлы, сгруппированные по проектам (новые сверху)."""
    async with get_async_conn() as conn:
        rows = await (await conn.execute(
            """
            SELECT project, path, size_bytes, created_at
            FROM files
            ORDER BY created_at DESC
            """
        )).fetchall()

    groups: dict[str, list] = {}
    for row in rows:
        proj = row["project"]
        groups.setdefault(proj, []).append({
            "name": row["path"].split("/")[-1],
            "rel_path": row["path"],
            "project": proj,
            "size": row["size_bytes"],
            "date": row["created_at"].isoformat(),
        })

    return [{"project": p, "files": f} for p, f in groups.items()]


async def get_stats() -> dict[str, Any]:
    """Статистика для страницы /stats.html."""
    async with get_async_conn() as conn:
        total_docs = (await (await conn.execute(
            "SELECT COUNT(*) AS cnt FROM files"
        )).fetchone())["cnt"]

        unique_users = (await (await conn.execute(
            "SELECT COUNT(*) AS cnt FROM users"
        )).fetchone())["cnt"]

        top_rows = await (await conn.execute(
            """
            SELECT u.user_ip::text AS ip, COUNT(f.id) AS cnt
            FROM users u
            LEFT JOIN files f ON f.user_id = u.id
            GROUP BY u.id
            ORDER BY cnt DESC
            LIMIT 10
            """
        )).fetchall()

        activity_rows = await (await conn.execute(
            """
            SELECT d.day::date AS day, COUNT(f.id) AS cnt
            FROM generate_series(
                CURRENT_DATE - INTERVAL '13 days',
                CURRENT_DATE,
                INTERVAL '1 day'
            ) AS d(day)
            LEFT JOIN files f
                ON f.created_at >= d.day
                AND f.created_at < d.day + INTERVAL '1 day'
            GROUP BY d.day
            ORDER BY d.day
            """
        )).fetchall()

    return {
        "counters": {
            "texts_created": total_docs,
            "unique_visitors": unique_users,
            "documents_30d": total_docs,
            "today_count": 0,
        },
        "activity_14d": [
            {"date": r["day"].strftime("%d.%m"), "value": r["cnt"]}
            for r in activity_rows
        ],
        "top_users": [{"name": r["ip"], "count": r["cnt"]} for r in top_rows],
    }


# ==========================================
# SYNC-версии для worker-потока
# ==========================================
def get_or_create_user_sync(user_ip: str) -> dict[str, Any]:
    """Синхронная версия для worker-потока."""
    with get_sync_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO users (user_ip)
            VALUES (%s::inet)
            ON CONFLICT (user_ip) DO UPDATE SET user_ip = EXCLUDED.user_ip
            RETURNING id, user_ip, full_name
            """,
            (user_ip,),
        ).fetchone()
        return row


def add_file_sync(user_id: str, project: str, path: str, size_bytes: int) -> dict[str, Any]:
    """Синхронная версия для worker-потока."""
    with get_sync_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO files (user_id, project, path, size_bytes)
            VALUES (%s::uuid, %s, %s, %s)
            RETURNING id, project, path, size_bytes, created_at
            """,
            (user_id, project, path, size_bytes),
        ).fetchone()
        return row

    # В конец database.py (ASYNC версия для server.py)
async def delete_file_from_db(rel_path: str) -> bool:
    """Удаляет запись о файле из БД."""
    async with get_async_conn() as conn:
        result = await conn.execute(
            "DELETE FROM files WHERE path = %s",
            (rel_path,)
        )
        return result == "DELETE 1"

# И SYNC версия, если вдруг понадобится в других местах
def delete_file_from_db_sync(rel_path: str) -> bool:
    with get_sync_conn() as conn:
        result = conn.execute(
            "DELETE FROM files WHERE path = %s",
            (rel_path,)
        )
        return result.rowcount == 1
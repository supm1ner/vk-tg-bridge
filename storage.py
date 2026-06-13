import aiosqlite
import logging
from contextlib import asynccontextmanager
from config import cfg

logger = logging.getLogger(__name__)

_pool: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _pool
    if _pool is None:
        _pool = await aiosqlite.connect(cfg.db_path)
        _pool.row_factory = aiosqlite.Row
        await _pool.execute("PRAGMA journal_mode=WAL")
        await _pool.execute("PRAGMA foreign_keys=ON")
    return _pool


@asynccontextmanager
async def db_cursor():
    db = await get_db()
    async with db.execute("BEGIN") as cur:
        yield cur


async def init_db():
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS msg_map (
            vk_msg_id   INTEGER PRIMARY KEY,
            tg_chat_id  INTEGER NOT NULL,
            tg_msg_id   INTEGER NOT NULL,
            tg_username TEXT,
            is_channel  INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_msg_map_tg
            ON msg_map(tg_chat_id, tg_msg_id);

        CREATE TABLE IF NOT EXISTS contacts (
            username    TEXT PRIMARY KEY,
            display     TEXT,
            tg_id       INTEGER
        );

        CREATE TABLE IF NOT EXISTS media_groups (
            tg_chat_id      INTEGER NOT NULL,
            tg_msg_id       INTEGER NOT NULL,
            group_id        TEXT NOT NULL,
            vk_msg_id       INTEGER,
            PRIMARY KEY (tg_chat_id, tg_msg_id)
        );

        CREATE INDEX IF NOT EXISTS idx_media_groups_group
            ON media_groups(group_id);

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );
    """)
    await db.commit()
    logger.info("Database initialized: %s", cfg.db_path)


async def close_db():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection closed")


async def save_mapping(
    vk_msg_id: int, tg_chat_id: int, tg_msg_id: int,
    tg_username: str | None = None, is_channel: bool = False,
):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO msg_map VALUES (?,?,?,?,?)",
        (vk_msg_id, tg_chat_id, tg_msg_id, tg_username, int(is_channel)),
    )
    await db.commit()


async def get_tg_by_vk(vk_msg_id: int) -> tuple | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT tg_chat_id, tg_msg_id, is_channel FROM msg_map WHERE vk_msg_id=?",
        (vk_msg_id,),
    )
    return await cur.fetchone()


async def get_vk_by_tg(tg_chat_id: int, tg_msg_id: int) -> tuple | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT vk_msg_id, is_channel FROM msg_map WHERE tg_chat_id=? AND tg_msg_id=?",
        (tg_chat_id, tg_msg_id),
    )
    return await cur.fetchone()


async def save_contact(username: str, display: str, tg_id: int):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO contacts VALUES (?,?,?)",
        (username.lstrip("@").lower(), display, tg_id),
    )
    await db.commit()


async def get_contacts() -> list[tuple[str, str, int]]:
    db = await get_db()
    cur = await db.execute("SELECT username, display, tg_id FROM contacts ORDER BY display")
    return await cur.fetchall()


async def save_media_group_msg(tg_chat_id: int, tg_msg_id: int, group_id: str, vk_msg_id: int | None = None):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO media_groups VALUES (?,?,?,?)",
        (tg_chat_id, tg_msg_id, group_id, vk_msg_id),
    )
    await db.commit()


async def get_media_group_msgs(group_id: str) -> list[tuple]:
    db = await get_db()
    cur = await db.execute(
        "SELECT tg_chat_id, tg_msg_id FROM media_groups WHERE group_id=? ORDER BY tg_msg_id",
        (group_id,),
    )
    return await cur.fetchall()


async def update_media_group_vk_msg(tg_chat_id: int, tg_msg_id: int, vk_msg_id: int):
    db = await get_db()
    await db.execute(
        "UPDATE media_groups SET vk_msg_id=? WHERE tg_chat_id=? AND tg_msg_id=?",
        (vk_msg_id, tg_chat_id, tg_msg_id),
    )
    await db.commit()

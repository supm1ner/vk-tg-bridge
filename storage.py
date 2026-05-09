"""
Хранит маппинг:
  tg_msg_id + tg_chat_id  <->  vk_msg_id
  vk_msg_id               ->   (tg_chat_id, tg_msg_id)
Также хранит контакты: username -> tg_entity
"""
import aiosqlite

DB_PATH = "bridge.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS msg_map (
                vk_msg_id   INTEGER PRIMARY KEY,
                tg_chat_id  INTEGER NOT NULL,
                tg_msg_id   INTEGER NOT NULL,
                tg_username TEXT,
                is_channel  INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                username    TEXT PRIMARY KEY,
                display     TEXT,
                tg_id       INTEGER
            )
        """)
        await db.commit()


async def save_mapping(vk_msg_id: int, tg_chat_id: int, tg_msg_id: int,
                       tg_username: str = None, is_channel: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO msg_map VALUES (?,?,?,?,?)",
            (vk_msg_id, tg_chat_id, tg_msg_id, tg_username, int(is_channel))
        )
        await db.commit()


async def get_tg_by_vk(vk_msg_id: int):
    """Возвращает (tg_chat_id, tg_msg_id, is_channel) или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_chat_id, tg_msg_id, is_channel FROM msg_map WHERE vk_msg_id=?",
            (vk_msg_id,)
        )
        row = await cur.fetchone()
        return row  # (tg_chat_id, tg_msg_id, is_channel) or None


async def save_contact(username: str, display: str, tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO contacts VALUES (?,?,?)",
            (username.lstrip("@").lower(), display, tg_id)
        )
        await db.commit()


async def get_contacts():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT username, display, tg_id FROM contacts")
        return await cur.fetchall()

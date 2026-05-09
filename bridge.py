"""
Логика моста: связывает TGClient и VKBot.
"""
import logging
import asyncio
import io
import aiohttp
from telethon.tl.types import (
    User, Channel, Chat,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage
)
from telethon.errors import FloodWaitError

from storage import init_db, save_mapping, get_tg_by_vk, save_contact, get_contacts

logger = logging.getLogger(__name__)


class Bridge:
    def __init__(self, tg: "TGClient", vk: "VKBot"):
        self.tg = tg
        self.vk = vk

    async def setup(self):
        await init_db()
        self.tg.set_message_handler(self._on_tg_message)
        self.vk.set_reply_handler(self._on_vk_reply)
        self.vk.set_command_handler(self._on_vk_command)

    # ------------------------------------------------------------------ #
    #  Telegram -> VK                                                      #
    # ------------------------------------------------------------------ #

    async def _on_tg_message(self, event):
        """Новое входящее сообщение в Telegram — пересылаем в VK."""
        try:
            msg = event.message
            chat = await event.get_chat()
            sender = await event.get_sender()

            is_channel = isinstance(chat, Channel) and chat.broadcast
            is_dm = isinstance(chat, User)

            sender_name = _entity_name(sender) if sender else "?"
            chat_name = _entity_name(chat)

            if is_dm:
                header = f"\U0001f4ac ЛС от {sender_name}:"
            elif is_channel:
                header = f"\U0001f4e2 Канал [{chat_name}]:"
            else:
                header = f"\U0001f465 [{chat_name}] {sender_name}:"

            text = msg.text or ""
            full_text = f"{header}\n{text}" if text else header

            vk_msg_id = 0
            if isinstance(msg.media, MessageMediaPhoto):
                photo_bytes = await self.tg.download_media(msg)
                vk_msg_id = await self.vk.send_photo(photo_bytes, full_text)

            elif isinstance(msg.media, MessageMediaDocument):
                doc = msg.media.document
                mime = doc.mime_type or ""
                if mime.startswith("video"):
                    vk_msg_id = await self.vk.send_message(
                        f"{full_text}\n[видео, {_size_str(doc.size)}]"
                    )
                elif mime.startswith("image"):
                    photo_bytes = await self.tg.download_media(msg)
                    vk_msg_id = await self.vk.send_photo(photo_bytes, full_text)
                else:
                    fname = _doc_filename(doc)
                    vk_msg_id = await self.vk.send_message(
                        f"{full_text}\n[файл: {fname}, {_size_str(doc.size)}]"
                    )
            elif isinstance(msg.media, MessageMediaWebPage):
                vk_msg_id = await self.vk.send_message(full_text)
            else:
                if full_text.strip():
                    vk_msg_id = await self.vk.send_message(full_text)

            if vk_msg_id:
                chat_id = chat.id if hasattr(chat, "id") else 0
                await save_mapping(
                    vk_msg_id=vk_msg_id,
                    tg_chat_id=chat_id,
                    tg_msg_id=msg.id,
                    tg_username=_entity_username(chat),
                    is_channel=is_channel,
                )
                logger.info(
                    "TG->VK: msg %s -> VK msg %s (chat %s)",
                    msg.id, vk_msg_id, chat_id
                )

                if is_dm and sender:
                    uname = _entity_username(sender) or str(sender.id)
                    await save_contact(uname, sender_name, sender.id)

        except FloodWaitError as e:
            logger.warning("FloodWait %s sec, retrying...", e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error("Error forwarding TG->VK: %s", e, exc_info=True)

    # ------------------------------------------------------------------ #
    #  VK -> Telegram                                                      #
    # ------------------------------------------------------------------ #

    async def _on_vk_reply(self, replied_vk_id: int, text: str,
                           attachments: list, vk_msg_id: int):
        """Пользователь ответил в VK на сообщение — отвечаем в TG."""
        row = await get_tg_by_vk(replied_vk_id)
        if not row:
            logger.warning("Mapping not found for VK msg %s", replied_vk_id)
            await self.vk.send_message("\u274c Не могу найти оригинальное сообщение в Telegram.")
            return

        tg_chat_id, tg_msg_id, is_channel = row

        if is_channel:
            await self.vk.send_message("\u274c Нельзя ответить в канал.")
            return

        try:
            entity = await self.tg.client.get_entity(tg_chat_id)
            logger.info(
                "VK->TG: reply to VK msg %s -> TG chat %s msg %s",
                replied_vk_id, tg_chat_id, tg_msg_id
            )

            file_to_send = None
            extra_text = ""

            for att in attachments:
                att_type = att.get("type")
                if att_type == "photo":
                    photo = att["photo"]
                    sizes = photo.get("sizes", [])
                    if sizes:
                        best = max(sizes, key=lambda s: s.get("width", 0))
                        url = best.get("url")
                        if url:
                            async with aiohttp.ClientSession() as s:
                                async with s.get(url) as r:
                                    file_to_send = io.BytesIO(await r.read())
                                    file_to_send.name = "photo.jpg"
                elif att_type == "video":
                    v = att["video"]
                    extra_text += f"\n[видео: {v.get('title', '')}]"
                elif att_type == "doc":
                    d = att["doc"]
                    url = d.get("url", "")
                    extra_text += f"\n[файл: {d.get('title', '')}] {url}"
                elif att_type == "sticker":
                    extra_text += "\n[стикер]"

            send_text = text + extra_text if (text or extra_text) else None

            await self.tg.send_message(
                entity,
                send_text or "\u2014",
                reply_to=tg_msg_id,
                file=file_to_send,
            )
            await self.vk.send_message("\u2705 Отправлено в Telegram.")

        except FloodWaitError as e:
            logger.warning("FloodWait %s sec in VK->TG reply", e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error("Error forwarding VK->TG: %s", e, exc_info=True)
            await self.vk.send_message(f"\u274c Ошибка: {e}")

    async def _on_vk_command(self, cmd: str, args: str, vk_msg_id: int):
        """Команды от пользователя в VK."""
        if cmd == "/send":
            await self._cmd_send(args, vk_msg_id)
        elif cmd in ("/contacts", "/contact"):
            await self._cmd_contacts()
        else:
            await self.vk.send_message(
                "Доступные команды:\n"
                "/send @username текст — написать в TG\n"
                "/contacts — список контактов"
            )

    async def _cmd_send(self, args: str, vk_msg_id: int):
        """Отправить сообщение в TG: /send @username текст."""
        parts = args.split(maxsplit=1)
        if not parts:
            await self.vk.send_message("Использование: /send @username текст")
            return

        username = parts[0].lstrip("@")
        text = parts[1] if len(parts) > 1 else ""

        if not text:
            await self.vk.send_message("Укажи текст сообщения после username.")
            return

        try:
            entity = await self.tg.get_entity(username)
            sent = await self.tg.send_message(entity, text)

            vk_sent_id = await self.vk.send_message(
                f"\u2705 Отправлено @{username}:\n{text}"
            )
            if vk_sent_id:
                await save_mapping(
                    vk_msg_id=vk_sent_id,
                    tg_chat_id=entity.id,
                    tg_msg_id=sent.id,
                    tg_username=username,
                    is_channel=False,
                )
            await save_contact(username, _entity_name(entity), entity.id)

        except Exception as e:
            await self.vk.send_message(f"\u274c Ошибка: {e}")

    async def _cmd_contacts(self):
        """Список сохранённых контактов."""
        contacts = await get_contacts()
        if not contacts:
            await self.vk.send_message("Контактов пока нет. Они появятся после первого ЛС.")
            return

        lines = ["\U0001f4cb Контакты:"]
        for username, display, tg_id in contacts:
            lines.append(f"\u2022 {display} (@{username})")
        await self.vk.send_message("\n".join(lines))


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _entity_name(entity) -> str:
    if hasattr(entity, "first_name"):
        parts = [entity.first_name or "", entity.last_name or ""]
        return " ".join(p for p in parts if p).strip() or str(entity.id)
    if hasattr(entity, "title"):
        return entity.title
    return str(getattr(entity, "id", "?"))


def _entity_username(entity) -> str:
    return getattr(entity, "username", None) or ""


def _doc_filename(doc) -> str:
    for attr in doc.attributes:
        if hasattr(attr, "file_name"):
            return attr.file_name
    return "file"


def _size_str(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 ** 2:
        return f"{size // 1024}KB"
    return f"{size // 1024 ** 2}MB"

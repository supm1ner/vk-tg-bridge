import asyncio
import io
import logging

import aiohttp
from telethon.tl.types import (
    User, Channel, Chat,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
    MessageMediaGeo, MessageMediaPoll,
)
from telethon.errors import FloodWaitError, RPCError

from config import cfg
from storage import (
    init_db, close_db,
    save_mapping, get_tg_by_vk, get_vk_by_tg,
    save_contact, get_contacts,
    save_chat, toggle_chat, toggle_all_chats,
    get_all_chat_settings, is_chat_enabled, update_chat_name,
)

logger = logging.getLogger(__name__)

MEDIA_GROUP_FLUSH_DELAY = 1.0


class Bridge:
    def __init__(self, tg: "TGClient", vk: "VKBot"):
        self.tg = tg
        self.vk = vk
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=cfg.bridge_queue_size)
        self._media_group_buffers: dict[str, list] = {}
        self._media_group_tasks: dict[str, asyncio.Task] = {}
        self._main_task: asyncio.Task | None = None

    async def setup(self):
        await init_db()
        self.tg.set_message_handler(self._on_tg_message)
        self.tg.set_edit_handler(self._on_tg_edit)
        self.vk.set_reply_handler(self._on_vk_reply)
        self.vk.set_command_handler(self._on_vk_command)
        self.vk.set_menu_handler(self._on_vk_menu)
        self._main_task = asyncio.create_task(self._queue_worker())
        await self.vk.send_with_menu("Мост запущен. Используй меню для управления.")

    async def _queue_worker(self):
        while True:
            try:
                msg = await self._queue.get()
                await msg
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Queue worker error: %s", e, exc_info=True)

    async def _enqueue(self, coro):
        await self._queue.put(coro)

    # ------------------------------------------------------------------ #
    #  Filters                                                             #
    # ------------------------------------------------------------------ #

    async def _is_chat_allowed(self, chat) -> bool:
        is_channel = isinstance(chat, Channel) and chat.broadcast
        is_dm = isinstance(chat, User)
        is_group = isinstance(chat, (Chat, Channel)) and not is_channel

        if not is_dm and not cfg.forward_groups:
            return False
        if is_dm and not cfg.forward_dms:
            return False
        if is_channel and not cfg.forward_channels:
            return False

        chat_id = chat.id if hasattr(chat, "id") else 0

        if cfg.chat_filters_whitelist and chat_id not in cfg.chat_filters_whitelist:
            return False
        if chat_id in cfg.chat_filters_blacklist:
            return False

        if not await is_chat_enabled(chat_id):
            return False

        return True

    # ------------------------------------------------------------------ #
    #  Telegram -> VK                                                      #
    # ------------------------------------------------------------------ #

    async def _on_tg_message(self, event):
        chat = await event.get_chat()
        if not await self._is_chat_allowed(chat):
            return
        msg = event.message

        if msg.grouped_id:
            await self._buffer_media_group(msg, chat)
            return

        await self._enqueue(self._forward_single(msg, chat))

    async def _on_tg_edit(self, event):
        chat = await event.get_chat()
        if not await self._is_chat_allowed(chat):
            return
        msg = event.message
        await self._enqueue(self._forward_edit(msg, chat))

    async def _buffer_media_group(self, msg, chat):
        group_id = str(msg.grouped_id)
        if group_id not in self._media_group_buffers:
            self._media_group_buffers[group_id] = []
            self._media_group_tasks[group_id] = asyncio.create_task(
                self._flush_media_group(group_id, chat)
            )
        self._media_group_buffers[group_id].append(msg)

    async def _flush_media_group(self, group_id: str, chat):
        try:
            await asyncio.sleep(MEDIA_GROUP_FLUSH_DELAY)
            msgs = self._media_group_buffers.pop(group_id, [])
            self._media_group_tasks.pop(group_id, None)
            if msgs:
                await self._enqueue(self._forward_media_group(msgs, chat))
        except asyncio.CancelledError:
            pass

    async def _forward_media_group(self, msgs, chat):
        try:
            sender = await msgs[0].get_sender()
            sender_name = _entity_name(sender) if sender else "?"
            chat_name = _entity_name(chat)
            header = _make_header(chat, sender_name, chat_name)

            text_parts = []
            photos = []
            documents = []
            for msg in msgs:
                if msg.text:
                    text_parts.append(msg.text)
                if isinstance(msg.media, MessageMediaPhoto):
                    photos.append(msg)
                elif isinstance(msg.media, MessageMediaDocument):
                    doc = msg.media.document
                    mime = doc.mime_type or ""
                    if mime.startswith("image"):
                        photos.append(msg)
                    else:
                        documents.append((msg, doc))

            full_text = f"{header}\n" + "\n".join(text_parts) if text_parts else header

            if photos:
                caption = full_text
                for i, photo_msg in enumerate(photos):
                    photo_bytes = await self.tg.download_media(photo_msg)
                    if photo_bytes:
                        if i == 0:
                            vk_id = await self.vk.send_photo(photo_bytes, caption)
                        else:
                            vk_id = await self.vk.send_photo(photo_bytes)
                        if i == 0 and vk_id:
                            chat_id = chat.id if hasattr(chat, "id") else 0
                            await save_mapping(
                                vk_msg_id=vk_id,
                                tg_chat_id=chat_id,
                                tg_msg_id=photo_msg.id,
                                tg_username=_entity_username(chat),
                                is_channel=isinstance(chat, Channel) and chat.broadcast,
                            )
            elif documents:
                await self.vk.send_message(f"{full_text}\n[альбом: {len(documents)} файлов]")
            else:
                await self.vk.send_message(full_text)
        except Exception as e:
            logger.error("Error forwarding media group: %s", e, exc_info=True)

    async def _forward_single(self, msg, chat):
        try:
            sender = await msg.get_sender()
            sender_name = _entity_name(sender) if sender else "?"
            chat_name = _entity_name(chat)
            header = _make_header(chat, sender_name, chat_name)

            text = msg.text or ""
            full_text = f"{header}\n{text}" if text else header

            vk_msg_id = await self._send_with_media(msg, full_text)

            if vk_msg_id:
                chat_id = chat.id if hasattr(chat, "id") else 0
                is_channel = isinstance(chat, Channel) and chat.broadcast
                await save_mapping(
                    vk_msg_id=vk_msg_id,
                    tg_chat_id=chat_id,
                    tg_msg_id=msg.id,
                    tg_username=_entity_username(chat),
                    is_channel=is_channel,
                )
                logger.info("TG->VK: msg %s -> VK msg %s (chat %s)", msg.id, vk_msg_id, chat_id)

                await save_chat(chat_id, chat_name)
                await update_chat_name(chat_id, chat_name)

                if isinstance(chat, User) and sender:
                    uname = _entity_username(sender) or str(sender.id)
                    await save_contact(uname, sender_name, sender.id)

        except FloodWaitError as e:
            logger.warning("FloodWait %s sec, sleeping", e.seconds)
            await asyncio.sleep(min(e.seconds, 300))
        except RPCError as e:
            logger.error("TG RPC error: %s", e)
        except Exception as e:
            logger.error("Error forwarding TG->VK: %s", e, exc_info=True)

    async def _send_with_media(self, msg, full_text: str) -> int:
        if isinstance(msg.media, MessageMediaPhoto):
            photo_bytes = await self.tg.download_media(msg)
            if photo_bytes:
                return await self.vk.send_photo(photo_bytes, full_text)
            return await self.vk.send_message(full_text)

        if isinstance(msg.media, MessageMediaDocument):
            doc = msg.media.document
            mime = doc.mime_type or ""
            fname = _doc_filename(doc)
            size = doc.size or 0

            if mime.startswith("video"):
                return await self.vk.send_message(
                    f"{full_text}\n[видео: {fname}, {_size_str(size)}]"
                )
            if mime.startswith("image"):
                photo_bytes = await self.tg.download_media(msg)
                if photo_bytes:
                    return await self.vk.send_photo(photo_bytes, full_text)
                return await self.vk.send_message(full_text)

            if size < cfg.max_media_size_mb * 1024 * 1024:
                file_bytes = await self.tg.download_media(msg)
                if file_bytes:
                    return await self.vk.send_document(file_bytes, fname, f"{full_text}\n[файл: {fname}]")
            return await self.vk.send_message(
                f"{full_text}\n[файл: {fname}, {_size_str(size)}]"
            )

        if isinstance(msg.media, MessageMediaWebPage):
            return await self.vk.send_message(full_text)

        if isinstance(msg.media, MessageMediaGeo):
            coords = f"{msg.media.lat}, {msg.media.long}" if hasattr(msg.media, 'lat') else ""
            return await self.vk.send_message(f"{full_text}\n[геолокация: {coords}]")

        if isinstance(msg.media, MessageMediaPoll):
            return await self.vk.send_message(f"{full_text}\n[опрос]")

        if full_text.strip():
            return await self.vk.send_message(full_text)

        return 0

    async def _forward_edit(self, msg, chat):
        try:
            chat_id = chat.id if hasattr(chat, "id") else 0
            row = await get_vk_by_tg(chat_id, msg.id)
            if not row:
                return

            vk_msg_id, is_channel = row
            if is_channel:
                return

            sender = await msg.get_sender()
            sender_name = _entity_name(sender) if sender else "?"
            chat_name = _entity_name(chat)
            header = _make_header(chat, sender_name, chat_name)

            if msg.text:
                await self.vk.send_message(
                    f"{header}\n✏️ (изменено):\n{msg.text}"
                )
        except Exception as e:
            logger.error("Error forwarding edit: %s", e, exc_info=True)

    # ------------------------------------------------------------------ #
    #  VK -> Telegram                                                      #
    # ------------------------------------------------------------------ #

    async def _on_vk_reply(self, replied_vk_id: int, text: str, attachments: list, vk_msg_id: int):
        row = await get_tg_by_vk(replied_vk_id)
        if not row:
            logger.warning("Mapping not found for VK msg %s", replied_vk_id)
            await self.vk.send_message("Не могу найти оригинальное сообщение в Telegram.")
            return

        tg_chat_id, tg_msg_id, is_channel = row

        if is_channel:
            await self.vk.send_message("Нельзя ответить в канал.")
            return

        await self._enqueue(self._send_reply(tg_chat_id, tg_msg_id, text, attachments, vk_msg_id))

    async def _send_reply(self, tg_chat_id: int, tg_msg_id: int, text: str, attachments: list, vk_msg_id: int):
        try:
            await self.vk.set_typing()
            entity = await self.tg.get_entity(tg_chat_id)
            logger.info("VK->TG: reply to VK msg %s -> TG chat %s msg %s", vk_msg_id, tg_chat_id, tg_msg_id)

            file_to_send = None
            extra_text = ""

            for att in attachments:
                att_type = att.get("type")
                if att_type == "photo":
                    file_to_send, extra = await self._download_vk_photo(att)
                    extra_text += extra
                elif att_type == "video":
                    v = att["video"]
                    extra_text += f"\n[видео: {v.get('title', '')}]"
                elif att_type == "doc":
                    d = att["doc"]
                    url = d.get("url", "")
                    extra_text += f"\n[файл: {d.get('title', '')}] {url}"
                elif att_type == "sticker":
                    extra_text += "\n[стикер]"
                elif att_type == "audio":
                    a = att.get("audio", {})
                    extra_text += f"\n[аудио: {a.get('artist', '')} - {a.get('title', '')}]"
                elif att_type == "wall":
                    extra_text += "\n[запись со стены]"
                elif att_type == "link":
                    extra_text += f"\n{att.get('link', {}).get('url', '')}"

            send_text = text + extra_text if (text or extra_text) else None

            await self.tg.send_message(
                entity,
                send_text or "\u2014",
                reply_to=tg_msg_id,
                file=file_to_send,
            )
            await self.vk.send_message("Отправлено в Telegram.")

        except FloodWaitError as e:
            logger.warning("FloodWait %s sec in VK->TG reply", e.seconds)
            await asyncio.sleep(min(e.seconds, 300))
        except RPCError as e:
            logger.error("TG RPC error in VK->TG: %s", e)
            await self.vk.send_message(f"Ошибка Telegram: {e}")
        except Exception as e:
            logger.error("Error forwarding VK->TG: %s", e, exc_info=True)
            await self.vk.send_message(f"Ошибка: {e}")

    async def _download_vk_photo(self, att: dict) -> tuple:
        photo = att.get("photo", {})
        sizes = photo.get("sizes", [])
        if sizes:
            best = max(sizes, key=lambda s: s.get("width", 0))
            url = best.get("url")
            if url:
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                            buf = io.BytesIO(await r.read())
                            buf.name = "photo.jpg"
                            return buf, ""
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.error("Failed to download VK photo: %s", e)
        return None, "\n[фото]"

    # ------------------------------------------------------------------ #
    #  VK Menu (кнопки и payload)                                          #
    # ------------------------------------------------------------------ #

    async def _on_vk_menu(self, cmd: str, payload: dict, vk_msg_id: int):
        if cmd == "menu":
            await self.vk.send_with_menu("Главное меню:")
        elif cmd == "chats":
            await self._menu_chats()
        elif cmd == "chats_page":
            await self._menu_chats_page(payload.get("page", 0))
        elif cmd == "toggle":
            tg_chat_id = payload.get("chat_id")
            if tg_chat_id:
                await self._cmd_toggle_id(tg_chat_id)
        elif cmd == "all_on":
            await toggle_all_chats(True)
            await self.vk.send_with_menu("🔔 Пересылка включена для всех чатов.")
        elif cmd == "all_off":
            await toggle_all_chats(False)
            await self.vk.send_with_menu("🔕 Пересылка выключена для всех чатов.")
        elif cmd == "settings":
            await self._menu_settings()
        elif cmd == "help":
            await self._menu_help()

    async def _menu_chats(self):
        settings = await get_all_chat_settings()
        if not settings:
            await self.vk.send_with_menu("Нет сохранённых чатов. Они появятся после первых сообщений.")
            return

        lines = ["📋 Чаты (нажми кнопку чтобы переключить):\n"]
        for i, (tg_id, name, enabled) in enumerate(settings, 1):
            icon = "✅" if enabled else "❌"
            lines.append(f"{icon} {name} (id: {tg_id})")

        await self.vk.send_message("\n".join(lines))

    async def _menu_chats_page(self, page: int):
        settings = await get_all_chat_settings()
        if not settings:
            await self.vk.send_with_menu("Нет сохранённых чатов.")
            return

        per_page = 6
        start = page * per_page
        chunk = settings[start:start + per_page]
        if not chunk:
            await self.vk.send_with_menu("Чaтов больше нет.")
            return

        lines = [f"📋 Чаты (стр. {page + 1}):\n"]
        for tg_id, name, enabled in chunk:
            icon = "✅" if enabled else "❌"
            lines.append(f"{icon} {name} (id: {tg_id})")
        lines.append(f"\nстр. {page + 1}/{max(1, (len(settings) + per_page - 1) // per_page)}")

    async def _menu_settings(self):
        total = await get_all_chat_settings()
        enabled = sum(1 for s in total if s[2]) if total else 0
        all_count = len(total)
        await self.vk.send_with_menu(
            "⚙️ Настройки:\n\n"
            f"📊 Всего чатов: {all_count}\n"
            f"🔔 Включено: {enabled}\n"
            f"🔕 Выключено: {all_count - enabled}\n\n"
            f"TG: {cfg.tg_session}\n"
            f"Очередь: {self._queue.qsize()}/{self._queue.maxsize}\n\n"
            "Используй меню для управления."
        )

    async def _menu_help(self):
        await self.vk.send_with_menu(
            "❓ Помощь\n\n"
            "📋 Список чатов — показать все чаты с настройками\n"
            "🔔 Вкл все — включить пересылку из всех чатов\n"
            "🔕 Выкл все — выключить пересылку из всех чатов\n"
            "⚙️ Настройки — статистика\n\n"
            "Команды:\n"
            "/send @username текст — написать в TG\n"
            "/t <id> — переключить чат по ID\n"
            "/contacts — список контактов\n"
            "/menu — показать меню\n"
            "/status — статус\n"
            "/ping — проверка"
        )

    # ------------------------------------------------------------------ #
    #  VK Commands                                                         #
    # ------------------------------------------------------------------ #

    async def _on_vk_command(self, cmd: str, args: str, vk_msg_id: int):
        if cmd == "/send":
            await self._cmd_send(args, vk_msg_id)
        elif cmd in ("/contacts", "/contact"):
            await self._cmd_contacts()
        elif cmd == "/help":
            await self._menu_help()
        elif cmd == "/status":
            await self._cmd_status()
        elif cmd == "/chats":
            await self._menu_chats()
        elif cmd == "/menu":
            await self.vk.send_with_menu("Главное меню:")
        elif cmd == "/ping":
            await self.vk.send_message("pong")
        elif cmd in ("/t", "/toggle"):
            await self._cmd_toggle(args)
        elif cmd in ("/all_on", "/enable_all"):
            await toggle_all_chats(True)
            await self.vk.send_with_menu("🔔 Пересылка включена для всех чатов.")
        elif cmd in ("/all_off", "/disable_all"):
            await toggle_all_chats(False)
            await self.vk.send_with_menu("🔕 Пересылка выключена для всех чатов.")
        else:
            await self._menu_help()

    async def _cmd_toggle(self, args: str):
        try:
            tg_chat_id = int(args.strip())
            await self._cmd_toggle_id(tg_chat_id)
        except ValueError:
            await self.vk.send_message("Использование: /t <id чата>. ID можно узнать через /chats")

    async def _cmd_toggle_id(self, tg_chat_id: int):
        new_state = await toggle_chat(tg_chat_id)
        icon = "🔔" if new_state else "🔕"
        await self.vk.send_with_menu(f"{icon} Чат {tg_chat_id} — {'включён' if new_state else 'выключен'}.")

    async def _cmd_status(self):
        total = await get_all_chat_settings()
        enabled = sum(1 for s in total if s[2]) if total else 0
        await self.vk.send_message(
            "📊 Статус:\n"
            f"TG: {cfg.tg_session}\n"
            f"VK группа: {cfg.vk_group_id}\n"
            f"Чаты: {enabled}/{len(total)} вкл\n"
            f"Очередь: {self._queue.qsize()}/{self._queue.maxsize}"
        )

    async def _cmd_send(self, args: str, vk_msg_id: int):
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

            vk_sent_id = await self.vk.send_message(f"Отправлено @{username}:\n{text}")
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
            await self.vk.send_message(f"Ошибка: {e}")

    async def _cmd_contacts(self):
        contacts = await get_contacts()
        if not contacts:
            await self.vk.send_message(
                "Контактов пока нет. Они появятся после первого ЛС."
            )
            return

        lines = ["Контакты:"]
        for username, display, tg_id in contacts:
            lines.append(f"• {display} (@{username})")
        await self.vk.send_message("\n".join(lines))

    async def shutdown(self):
        for task in self._media_group_tasks.values():
            task.cancel()
        if self._main_task:
            self._main_task.cancel()
        await close_db()


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
    if size < 1024 ** 3:
        return f"{size // 1024 ** 2}MB"
    return f"{size // 1024 ** 3}GB"


def _make_header(chat, sender_name: str, chat_name: str) -> str:
    is_channel = isinstance(chat, Channel) and chat.broadcast
    is_dm = isinstance(chat, User)

    if is_dm:
        return f"\U0001f4ac ЛС от {sender_name}:"
    if is_channel:
        return f"\U0001f4e2 Канал [{chat_name}]:"
    return f"\U0001f465 [{chat_name}] {sender_name}:"

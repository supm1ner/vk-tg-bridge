"""
Telethon клиент — слушает входящие сообщения и уведомления.
"""
import logging
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import (
    User, Chat, Channel,
    MessageMediaPhoto, MessageMediaDocument,
    MessageMediaWebPage
)
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from config import TG_API_ID, TG_API_HASH, TG_SESSION

logger = logging.getLogger(__name__)


class TGClient:
    def __init__(self):
        self.client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)
        self._on_new_message = None  # async callback(event, sender_name, chat_name, is_channel)

    def set_message_handler(self, fn):
        self._on_new_message = fn

    async def start(self):
        await self.client.start()
        logger.info("Telegram client started as: %s",
                    await self.client.get_me())

        @self.client.on(events.NewMessage(incoming=True))
        async def handler(event):
            if self._on_new_message:
                await self._on_new_message(event)

        await self.client.run_until_disconnected()

    async def send_message(self, entity, text: str, reply_to=None, file=None):
        return await self.client.send_message(
            entity, text, reply_to=reply_to, file=file
        )

    async def get_entity(self, username: str):
        return await self.client.get_entity(username)

    async def get_dialogs(self, limit=50):
        return await self.client.get_dialogs(limit=limit)

    async def download_media(self, message, path: str = None):
        return await self.client.download_media(message, file=path or bytes)

    async def stop(self):
        await self.client.disconnect()

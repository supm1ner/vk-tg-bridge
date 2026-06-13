import logging
from telethon import TelegramClient, events
from telethon.tl.types import (
    User, Chat, Channel,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
    MessageMediaGeo,
)
from config import cfg

logger = logging.getLogger(__name__)


class TGClient:
    def __init__(self):
        proxy = None
        if cfg.tg_proxy:
            protocol, rest = cfg.tg_proxy.split("://", 1)
            host, _, port_str = rest.partition(":")
            proxy = (protocol, host, int(port_str))

        self.client = TelegramClient(
            cfg.tg_session,
            cfg.tg_api_id,
            cfg.tg_api_hash,
            proxy=proxy,
            connection_retries=None,
            retry_delay=cfg.tg_reconnect_delay,
        )
        self._on_new_message = None
        self._on_edit_message = None
        self._running = False

    def set_message_handler(self, fn):
        self._on_new_message = fn

    def set_edit_handler(self, fn):
        self._on_edit_message = fn

    async def start(self):
        self._running = True
        await self.client.start()
        me = await self.client.get_me()
        logger.info("Telegram client started as: %s", me.username or me.id)

        @self.client.on(events.NewMessage(incoming=True))
        async def on_new(event):
            if self._on_new_message:
                try:
                    await self._on_new_message(event)
                except Exception as e:
                    logger.error("Error in message handler: %s", e, exc_info=True)

        @self.client.on(events.MessageEdited(incoming=True))
        async def on_edit(event):
            if self._on_edit_message:
                try:
                    await self._on_edit_message(event)
                except Exception as e:
                    logger.error("Error in edit handler: %s", e, exc_info=True)

        await self.client.run_until_disconnected()

    async def send_message(self, entity, text: str | None, reply_to=None, file=None, formatting=None):
        return await self.client.send_message(entity, text, reply_to=reply_to, file=file, formatting=formatting)

    async def get_entity(self, identifier: str | int):
        return await self.client.get_entity(identifier)

    async def get_dialogs(self, limit=50):
        return await self.client.get_dialogs(limit=limit)

    async def download_media(self, message, path: str | None = None):
        return await self.client.download_media(message, file=path or bytes)

    async def forward_messages(self, entity, messages):
        return await self.client.forward_messages(entity, messages)

    async def edit_message(self, entity, message_id, text):
        return await self.client.edit_message(entity, message_id, text)

    async def delete_messages(self, entity, message_ids):
        return await self.client.delete_messages(entity, message_ids)

    async def get_me(self):
        return await self.client.get_me()

    async def stop(self):
        self._running = False
        await self.client.disconnect()
        logger.info("Telegram client stopped")

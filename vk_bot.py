"""
VK Long Poll бот.
Отправляет сообщения пользователю и слушает ответы.
"""
import asyncio
import logging
import random
import aiohttp
from config import VK_TOKEN, VK_GROUP_ID, VK_TARGET_USER_ID

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"


class VKBot:
    def __init__(self):
        self.token = VK_TOKEN
        self.group_id = VK_GROUP_ID
        self.target_user = VK_TARGET_USER_ID
        self._on_reply = None
        self._on_command = None
        self._session: aiohttp.ClientSession = None

    def set_reply_handler(self, fn):
        self._on_reply = fn

    def set_command_handler(self, fn):
        self._on_command = fn

    async def _api(self, method: str, **params):
        params.update({"access_token": self.token, "v": VK_VERSION})
        async with self._session.post(f"{VK_API}/{method}", data=params) as r:
            data = await r.json()
            if "error" in data:
                logger.error("VK API error %s: %s", method, data["error"])
                return None
            return data.get("response")

    async def send_message(self, text: str, attachment: str = None) -> int:
        """Отправить сообщение целевому пользователю. Возвращает vk_msg_id."""
        params = {
            "user_id": self.target_user,
            "message": text or "\u2014",
            "random_id": random.randint(0, 2**31),
        }
        if attachment:
            params["attachment"] = attachment
        resp = await self._api("messages.send", **params)
        if isinstance(resp, int):
            return resp
        if isinstance(resp, dict):
            return resp.get("message_id") or resp.get("peer_id", 0)
        if isinstance(resp, list):
            return resp[0] if resp else 0
        return 0

    async def send_photo(self, photo_bytes: bytes, caption: str = "") -> int:
        """Загрузить фото и отправить."""
        resp = await self._api("photos.getMessagesUploadServer",
                               peer_id=self.target_user)
        if not resp:
            return await self.send_message(f"[фото] {caption}")

        upload_url = resp["upload_url"]
        form = aiohttp.FormData()
        form.add_field("photo", photo_bytes, filename="photo.jpg",
                       content_type="image/jpeg")
        async with self._session.post(upload_url, data=form) as r:
            uploaded = await r.json()

        saved = await self._api("photos.saveMessagesPhoto",
                                server=uploaded["server"],
                                photo=uploaded["photo"],
                                hash=uploaded["hash"])
        if not saved:
            return await self.send_message(f"[фото] {caption}")

        p = saved[0]
        attachment = f"photo{p['owner_id']}_{p['id']}"
        return await self.send_message(caption, attachment=attachment)

    async def send_video_link(self, caption: str, url: str) -> int:
        text = f"{caption}\n{url}" if url else caption
        return await self.send_message(text)

    async def _get_long_poll_server(self):
        return await self._api("groups.getLongPollServer", group_id=self.group_id)

    async def _poll_loop(self):
        server = key = ts = None

        while True:
            try:
                if server is None:
                    server_info = await self._get_long_poll_server()
                    if not server_info:
                        logger.error("Failed to get Long Poll server, retry in 10s")
                        await asyncio.sleep(10)
                        continue
                    server = server_info["server"]
                    key = server_info["key"]
                    ts = server_info["ts"]
                    logger.info("Long Poll connected: server=%s", server)

                url = f"{server}?act=a_check&key={key}&ts={ts}&wait=25"
                async with self._session.get(url) as r:
                    data = await r.json()

                if "failed" in data:
                    if data["failed"] == 1:
                        ts = data["ts"]
                    else:
                        server = None
                    continue

                ts = data["ts"]
                for event in data.get("updates", []):
                    await self._handle_event(event)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("VK poll error: %s", e, exc_info=True)
                server = None
                await asyncio.sleep(5)

    async def _handle_event(self, event: dict):
        if event.get("type") != "message_new":
            return

        obj = event["object"]["message"]
        msg_id = obj["id"]
        text = obj.get("text", "")
        from_id = obj.get("from_id", 0)
        peer_id = obj.get("peer_id", 0)
        reply_to = obj.get("reply_message")

        # Приводим from_id к int (на случай если VK вернёт строку)
        try:
            from_id = int(from_id)
        except (TypeError, ValueError):
            pass

        # Проверяем, что сообщение от нашего пользователя
        if from_id != self.target_user:
            logger.debug(
                "Ignored VK message from_id=%s peer_id=%s target_user=%s text=%s",
                from_id, peer_id, self.target_user, text[:50]
            )
            return

        # Ответ на сообщение (проверяем ДО команды, чтобы reply с "/" работал)
        if reply_to and self._on_reply:
            replied_vk_id = reply_to["id"]
            attachments = obj.get("attachments", [])
            logger.info(
                "VK reply to msg %s: text=%s",
                replied_vk_id, text[:50]
            )
            await self._on_reply(replied_vk_id, text, attachments, msg_id)
            return

        # Команды
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            if self._on_command:
                await self._on_command(cmd, args, msg_id)
            return

        # Обычное сообщение без reply
        await self.send_message(
            "Чтобы ответить в Telegram, используй reply на сообщение.\n"
            "Команды: /send @username, /contacts"
        )

    async def start(self):
        self._session = aiohttp.ClientSession()
        logger.info("VK bot started")
        await self._poll_loop()

    async def stop(self):
        if self._session:
            await self._session.close()

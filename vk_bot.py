import asyncio
import logging
import random
import time
from collections import deque

import aiohttp
from config import cfg

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"

MAX_RETRIES = 5
BASE_DELAY = 1.0
MAX_DELAY = 60.0


class RateLimiter:
    def __init__(self, calls_per_sec: float = 3.0):
        self.interval = 1.0 / calls_per_sec
        self._timestamps: deque[float] = deque(maxlen=100)

    async def acquire(self):
        now = time.monotonic()
        if self._timestamps:
            elapsed = now - self._timestamps[-1]
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
        self._timestamps.append(time.monotonic())


class VKBot:
    def __init__(self):
        self.token = cfg.vk_token
        self.group_id = cfg.vk_group_id
        self.target_user = cfg.vk_target_user_id
        self._on_reply = None
        self._on_command = None
        self._session: aiohttp.ClientSession | None = None
        self._rate_limiter = RateLimiter()
        self._running = False

    def set_reply_handler(self, fn):
        self._on_reply = fn

    def set_command_handler(self, fn):
        self._on_command = fn

    async def _api(self, method: str, **params) -> dict | list | int | None:
        params.update({"access_token": self.token, "v": VK_VERSION})
        last_error = None

        for attempt in range(MAX_RETRIES):
            await self._rate_limiter.acquire()
            try:
                async with self._session.post(
                    f"{VK_API}/{method}", data=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    data = await r.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                delay = min(BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), MAX_DELAY)
                logger.warning("VK API %s attempt %d failed: %s, retry in %.1fs", method, attempt + 1, e, delay)
                await asyncio.sleep(delay)
                continue

            if "error" in data:
                err = data["error"]
                code = err.get("error_code", 0)
                if code == 6:  # Too many requests
                    delay = min(BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), MAX_DELAY)
                    logger.warning("VK rate limited on %s, retry in %.1fs", method, delay)
                    await asyncio.sleep(delay)
                    continue
                if code == 14:  # Captcha needed
                    logger.error("VK captcha required for %s", method)
                    return None
                logger.error("VK API error %s: code=%d msg=%s", method, code, err.get("error_msg", ""))
                return None

            return data.get("response")

        logger.error("VK API %s failed after %d retries: %s", method, MAX_RETRIES, last_error)
        return None

    async def send_message(self, text: str, attachment: str | None = None) -> int:
        params = {
            "user_id": self.target_user,
            "message": text or "\u2014",
            "random_id": int(time.time() * 1_000_000) + random.randint(0, 9999),
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
        resp = await self._api("photos.getMessagesUploadServer", peer_id=self.target_user)
        if not resp:
            return await self.send_message(f"[фото] {caption}")

        upload_url = resp["upload_url"]
        form = aiohttp.FormData()
        form.add_field("photo", photo_bytes, filename="photo.jpg", content_type="image/jpeg")
        try:
            async with self._session.post(upload_url, data=form, timeout=aiohttp.ClientTimeout(total=60)) as r:
                uploaded = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("VK photo upload failed: %s", e)
            return await self.send_message(f"[фото] {caption}")

        saved = await self._api(
            "photos.saveMessagesPhoto",
            server=uploaded["server"],
            photo=uploaded["photo"],
            hash=uploaded["hash"],
        )
        if not saved:
            return await self.send_message(f"[фото] {caption}")

        p = saved[0]
        attachment = f"photo{p['owner_id']}_{p['id']}"
        return await self.send_message(caption, attachment=attachment)

    async def send_document(self, doc_bytes: bytes, filename: str, caption: str = "") -> int:
        resp = await self._api("docs.getMessagesUploadServer", peer_id=self.target_user, type="doc")
        if not resp:
            return await self.send_message(f"[{filename}] {caption}")

        upload_url = resp["upload_url"]
        form = aiohttp.FormData()
        form.add_field("file", doc_bytes, filename=filename)
        try:
            async with self._session.post(upload_url, data=form, timeout=aiohttp.ClientTimeout(total=120)) as r:
                uploaded = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error("VK doc upload failed: %s", e)
            return await self.send_message(f"[{filename}] {caption}")

        saved = await self._api(
            "docs.save", file=uploaded["file"]
        )
        if not saved:
            return await self.send_message(f"[{filename}] {caption}")

        d = saved["doc"] if isinstance(saved, dict) and "doc" in saved else saved[0] if isinstance(saved, list) else saved
        if isinstance(d, dict):
            attachment = f"doc{d['owner_id']}_{d['id']}"
            return await self.send_message(caption, attachment=attachment)

        return await self.send_message(f"[{filename}] {caption}")

    async def set_typing(self):
        await self._api("messages.setActivity", user_id=self.target_user, type="typing")

    async def _get_long_poll_server(self) -> dict | None:
        return await self._api("groups.getLongPollServer", group_id=self.group_id)

    async def _poll_loop(self):
        server: str | None = None
        key: str | None = None
        ts: str | None = None
        fail_count = 0

        while self._running:
            try:
                if server is None:
                    fail_count += 1
                    server_info = await self._get_long_poll_server()
                    if not server_info:
                        delay = min(BASE_DELAY * (2 ** min(fail_count, 6)) + random.uniform(0, 1), MAX_DELAY)
                        logger.error("Failed to get Long Poll server, retry in %.1fs", delay)
                        await asyncio.sleep(delay)
                        continue
                    server = server_info["server"]
                    key = server_info["key"]
                    ts = server_info["ts"]
                    fail_count = 0
                    logger.info("Long Poll connected")

                url = f"{server}?act=a_check&key={key}&ts={ts}&wait={cfg.vk_poll_wait}"
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=cfg.vk_poll_wait + 10)) as r:
                    data = await r.json()

                if "failed" in data:
                    failed = data["failed"]
                    if failed == 1:
                        ts = data["ts"]
                    elif failed == 2:
                        logger.warning("Long Poll key expired, reconnecting")
                        server = None
                    elif failed == 3:
                        logger.warning("Long Poll info lost, reconnecting")
                        server = None
                    continue

                ts = data["ts"]
                for event in data.get("updates", []):
                    await self._handle_event(event)

            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                continue
            except aiohttp.ClientError as e:
                logger.warning("VK poll connection error: %s", e)
                server = None
                await asyncio.sleep(5)
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

        try:
            from_id = int(from_id)
        except (TypeError, ValueError):
            pass

        if from_id != self.target_user:
            logger.debug("Ignored VK message from_id=%s", from_id)
            return

        if reply_to and self._on_reply:
            replied_vk_id = reply_to["id"]
            attachments = obj.get("attachments", [])
            logger.info("VK reply to msg %s: text=%s", replied_vk_id, text[:80])
            await self._on_reply(replied_vk_id, text, attachments, msg_id)
            return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            if self._on_command:
                await self._on_command(cmd, args, msg_id)
            return

        await self.send_message(
            "Чтобы ответить в Telegram, используй reply на сообщение.\n"
            "Команды: /send @username, /contacts"
        )

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("VK bot started (target user: %s)", self.target_user)
        await self._poll_loop()

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("VK bot stopped")

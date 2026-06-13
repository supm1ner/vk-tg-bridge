import asyncio
import logging
import signal
import sys

from config import cfg
from tg_client import TGClient
from vk_bot import VKBot
from bridge import Bridge

logger = logging.getLogger(__name__)


def setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    if cfg.log_file:
        from logging.handlers import RotatingFileHandler
        handlers.append(
            RotatingFileHandler(cfg.log_file, maxBytes=10_485_760, backupCount=3)
        )

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


async def main():
    setup_logging()
    logger.info("Starting VK-TG Bridge...")

    tg = TGClient()
    vk = VKBot()
    bridge = Bridge(tg, vk)

    await bridge.setup()
    logger.info("Bridge initialized")

    vk_task = asyncio.create_task(vk.start())

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    tg_task = asyncio.create_task(tg.start())

    cancel_event = asyncio.Event()

    async def _waiter():
        tg_done = asyncio.create_task(tg_task)
        vk_done = asyncio.create_task(vk_task)
        stop_wait = asyncio.create_task(stop_event.wait())

        await asyncio.wait(
            [tg_done, vk_done, stop_wait],
            return_when=asyncio.FIRST_COMPLETED,
        )

    try:
        await _waiter()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Shutting down...")
        vk_task.cancel()
        tg_task.cancel()

        await bridge.shutdown()

        try:
            await asyncio.wait_for(vk_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        await tg.stop()
        await vk.stop()

        logger.info("Shutdown complete")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

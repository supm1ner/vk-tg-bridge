"""
Точка входа. Запускает TG клиент и VK бот параллельно.
"""
import asyncio
import logging
from tg_client import TGClient
from vk_bot import VKBot
from bridge import Bridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    tg = TGClient()
    vk = VKBot()
    bridge = Bridge(tg, vk)

    await bridge.setup()

    # VK бот в отдельной задаче
    vk_task = asyncio.create_task(vk.start())

    # TG клиент блокирует до отключения
    try:
        await tg.start()
    finally:
        vk_task.cancel()
        await tg.stop()
        await vk.stop()


if __name__ == "__main__":
    asyncio.run(main())

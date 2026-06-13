import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        print(f"FATAL: {key} не установлен в .env или окружении", file=sys.stderr)
        sys.exit(1)
    return val


def _optional_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class Config:
    tg_api_id: int
    tg_api_hash: str
    tg_session: str = "bridge_session"
    tg_proxy: str | None = None

    vk_token: str
    vk_group_id: int
    vk_target_user_id: int

    db_path: str = "bridge.db"
    log_level: str = "INFO"
    log_file: str | None = None

    max_media_size_mb: int = 50
    tg_reconnect_delay: int = 5
    vk_poll_wait: int = 25
    bridge_queue_size: int = 100

    chat_filters_whitelist: list[int] = field(default_factory=list)
    chat_filters_blacklist: list[int] = field(default_factory=list)
    forward_dms: bool = True
    forward_channels: bool = True
    forward_groups: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            tg_api_id=int(_require_env("TG_API_ID")),
            tg_api_hash=_require_env("TG_API_HASH"),
            tg_session=_optional_env("TG_SESSION", "bridge_session"),
            tg_proxy=_optional_env("TG_PROXY") or None,
            vk_token=_require_env("VK_TOKEN"),
            vk_group_id=int(_require_env("VK_GROUP_ID")),
            vk_target_user_id=int(_require_env("VK_TARGET_USER_ID")),
            db_path=_optional_env("DB_PATH", "bridge.db"),
            log_level=_optional_env("LOG_LEVEL", "INFO"),
            log_file=_optional_env("LOG_FILE") or None,
            max_media_size_mb=int(_optional_env("MAX_MEDIA_SIZE_MB", "50")),
            tg_reconnect_delay=int(_optional_env("TG_RECONNECT_DELAY", "5")),
            vk_poll_wait=int(_optional_env("VK_POLL_WAIT", "25")),
            bridge_queue_size=int(_optional_env("BRIDGE_QUEUE_SIZE", "100")),
            chat_filters_whitelist=[
                int(x) for x in _optional_env("CHAT_FILTERS_WHITELIST", "").split(",") if x
            ],
            chat_filters_blacklist=[
                int(x) for x in _optional_env("CHAT_FILTERS_BLACKLIST", "").split(",") if x
            ],
            forward_dms=_optional_env("FORWARD_DMS", "1") == "1",
            forward_channels=_optional_env("FORWARD_CHANNELS", "1") == "1",
            forward_groups=_optional_env("FORWARD_GROUPS", "1") == "1",
        )


cfg = Config.from_env()

# VK-TG Bridge

Двусторонний мост между Telegram и VK. Пересылает входящие сообщения из Telegram в VK (выбранному пользователю), а ответы из VK отправляет обратно в Telegram.

## Возможности

- Пересылка сообщений из Telegram (ЛС, группы, каналы) в VK
- Ответы из VK в Telegram через reply
- Поддержка фото, документов, видео, геолокации
- Поддержка медиа-альбомов (группировка фото)
- Команды в VK: `/send @username`, `/contacts`, `/chats`, `/status`
- Фильтрация чатов (whitelist / blacklist)
- Работа через прокси (SOCKS5)
- Graceful shutdown

## Установка

### Локально

```bash
cp .env.example .env
# отредактируйте .env — вставьте свои API ключи
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Docker

```bash
cp .env.example .env
# отредактируйте .env
docker compose up -d --build
```

## Переменные окружения

| Переменная | Обязательно | Описание |
|---|---|---|
| `TG_API_ID` | да | API ID приложения Telegram (my.telegram.org) |
| `TG_API_HASH` | да | API Hash приложения Telegram |
| `VK_TOKEN` | да | Токен группы VK (Long Poll включён) |
| `VK_GROUP_ID` | да | ID группы VK (число) |
| `VK_TARGET_USER_ID` | да | ID пользователя VK для уведомлений |
| `TG_PROXY` | нет | Прокси (socks5://127.0.0.1:1080) |
| `LOG_LEVEL` | нет | DEBUG/INFO/WARNING/ERROR |
| `LOG_FILE` | нет | Путь к лог-файлу |
| `DB_PATH` | нет | Путь к SQLite БД |
| `CHAT_FILTERS_WHITELIST` | нет | ID чатов через запятую (только их) |
| `CHAT_FILTERS_BLACKLIST` | нет | ID чатов через запятую (исключить) |
| `FORWARD_DMS` | нет | Пересылать ЛС (1/0, по умолч. 1) |
| `FORWARD_CHANNELS` | нет | Пересылать каналы (1/0, по умолч. 1) |
| `FORWARD_GROUPS` | нет | Пересылать группы (1/0, по умолч. 1) |
| `MAX_MEDIA_SIZE_MB` | нет | Макс. размер файла для загрузки (50) |

## Команды VK

- `/send @username текст` — написать пользователю в Telegram
- `/contacts` — список сохранённых контактов
- `/chats` — последние 15 чатов Telegram
- `/status` — статус bridge
- `/ping` — pong
- `/help` — эта справка

## Структура

```
vk-tg-bridge/
├── main.py          # точка входа, graceful shutdown
├── bridge.py        # логика моста, очередь, фильтры
├── config.py        # конфигурация (dataclass + .env)
├── tg_client.py     # клиент Telethon
├── vk_bot.py        # VK Long Poll + API (rate limiter, retry)
├── storage.py       # SQLite (WAL mode, индексы)
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

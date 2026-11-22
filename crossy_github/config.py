"""
Модуль конфигурации проекта crosspost-bot.
Загружает переменные окружения из .env и валидирует основные параметры.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_allowed_ids(raw_value: str | None) -> list[int]:
    """Парсинг строки с ID пользователей Telegram (через запятую)."""
    if not raw_value:
        return []
    ids: list[int] = []
    for item in raw_value.split(","):
        cleaned = item.strip()
        if cleaned.isdigit():
            ids.append(int(cleaned))
        elif cleaned.startswith("-") and cleaned[1:].isdigit():
            ids.append(int(cleaned))
    return ids


# Telegram
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_IDS: list[int] = _parse_allowed_ids(os.getenv("ALLOWED_IDS"))
MAX_TG_MB: int = int(os.getenv("MAX_TG_MB", "20"))

# Файловый сервер для отдачи видео (требуется для Instagram Reels)
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
FILE_SERVER_HOST: str = os.getenv("FILE_SERVER_HOST", "0.0.0.0").strip()
FILE_SERVER_PORT: int = int(os.getenv("FILE_SERVER_PORT", "8899"))

# Пути к рабочим директориям
VIDEO_DIR: Path = BASE_DIR / os.getenv("VIDEO_DIR", "downloads").strip()
TOKENS_DIR: Path = BASE_DIR / os.getenv("TOKENS_DIR", "tokens").strip()

VIDEO_DIR.mkdir(parents=True, exist_ok=True)
TOKENS_DIR.mkdir(parents=True, exist_ok=True)

def get_user_tokens_dir(user_id: int | str) -> Path:
    """Получение персональной директории токенов для конкретного пользователя Telegram."""
    path = TOKENS_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_yt_token_path(user_id: int | str) -> Path:
    return get_user_tokens_dir(user_id) / "youtube.json"


def get_user_ig_token_path(user_id: int | str) -> Path:
    return get_user_tokens_dir(user_id) / "instagram.json"


def get_user_tt_token_path(user_id: int | str) -> Path:
    return get_user_tokens_dir(user_id) / "tiktok.json"

# Instagram API with Instagram Login
GRAPH_VERSION: str = os.getenv("GRAPH_VERSION", "v23.0").strip()
IG_ACCESS_TOKEN: str = os.getenv("IG_ACCESS_TOKEN", "").strip()
IG_USER_ID: str = os.getenv("IG_USER_ID", "").strip()

# TikTok Content Posting API
# Режимы: 'draft' (черновики в Inbox, без аудита) или 'direct' (прямой пост)
TT_MODE: str = os.getenv("TT_MODE", "draft").strip().lower()
TT_CLIENT_KEY: str = os.getenv("TT_CLIENT_KEY", "").strip()
TT_CLIENT_SECRET: str = os.getenv("TT_CLIENT_SECRET", "").strip()
TT_REDIRECT_URI: str = os.getenv("TT_REDIRECT_URI", "http://127.0.0.1:8010/callback").strip()
TT_SCOPES: str = os.getenv("TT_SCOPES", "video.upload").strip()
TT_PRIVACY_LEVEL: str = os.getenv("TT_PRIVACY_LEVEL", "SELF_ONLY").strip()

# YouTube Data API v3
YT_PRIVACY_STATUS: str = os.getenv("YT_PRIVACY_STATUS", "public").strip()
YT_CATEGORY_ID: str = os.getenv("YT_CATEGORY_ID", "22").strip()

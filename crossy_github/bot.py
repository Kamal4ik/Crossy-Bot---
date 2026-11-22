"""
Точка входа Telegram-бота кросспостинга видео.
Запускает polling aiogram, локальный файловый сервер и фоновые задачи очистки/рефреша токенов.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
from fileserver import start_file_server
from handlers import router as main_router
from publishers.instagram import _load_instagram_token, refresh_instagram_token

# Настройка структурированного логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("crosspost-bot")


def cleanup_old_videos(video_dir: Path, max_age_seconds: int = 86400) -> None:
    """Удаление временных видеофайлов старше 24 часов из каталога загрузок."""
    if not video_dir.exists():
        return

    now = time.time()
    count = 0
    valid_suffixes = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}

    for item in video_dir.iterdir():
        if item.is_file() and item.suffix.lower() in valid_suffixes:
            try:
                if now - item.stat().st_mtime > max_age_seconds:
                    item.unlink()
                    count += 1
            except Exception as exc:
                logger.warning("Не удалось удалить устаревший файл %s: %s", item, exc)

    if count > 0:
        logger.info("Очищено устаревших видеофайлов: %d", count)


def _refresh_all_instagram_tokens():
    """Сбор всех доступных ID пользователей для обновления токенов."""
    uids: list[str | None] = [str(x) for x in config.ALLOWED_IDS]
    if config.TOKENS_DIR.exists():
        for d in config.TOKENS_DIR.iterdir():
            if d.is_dir() and d.name.isdigit() and d.name not in uids:
                uids.append(d.name)
    if None not in uids and not uids:
        uids = [None]
    return uids


async def check_and_refresh_instagram_tokens() -> None:
    """Проверка и обновление токенов Instagram для всех пользователей."""
    for uid in _refresh_all_instagram_tokens():
        try:
            access_token, user_id, expires_at, token_path = _load_instagram_token(uid)
            if access_token and user_id and expires_at > 0:
                remaining = expires_at - time.time()
                if 0 < remaining < (7 * 86400):
                    logger.info("Регламентный рефреш токена Instagram (%s)...", token_path)
                    await refresh_instagram_token(access_token, user_id, token_path)
        except Exception as exc:
            logger.warning("Ошибка проверки токена Instagram для %s: %s", uid, exc)


async def periodic_maintenance_task() -> None:
    """
    Фоновая задача, выполняющаяся раз в 24 часа:
    1. Очистка старых видеофайлов.
    2. Проверка и автоматический рефреш токенов Instagram при остатке < 7 дней.
    """
    while True:
        try:
            await asyncio.sleep(86400)  # раз в 24 часа
            logger.info("Выполнение регламентного обслуживания...")
            cleanup_old_videos(config.VIDEO_DIR)
            await check_and_refresh_instagram_tokens()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Ошибка в регламентной фоновой задаче: %s", exc)


async def main() -> None:
    """Инициализация и запуск всех компонентов бота."""
    logger.info("Запуск crosspost-bot...")

    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN не задан в .env! Бот не может быть запущен.")
        sys.exit(1)

    # 1. Очистка старых файлов при старте
    cleanup_old_videos(config.VIDEO_DIR)

    # 2. Первоначальный рефреш токенов Instagram при старте (если осталось < 7 дней)
    await check_and_refresh_instagram_tokens()

    # 3. Запуск встроенного HTTP-сервера раздачи видео для Instagram Reels
    file_runner = None
    try:
        file_runner = await start_file_server(
            host=config.FILE_SERVER_HOST,
            port=config.FILE_SERVER_PORT,
            video_dir=config.VIDEO_DIR,
        )
    except Exception as exc:
        logger.error(
            "Не удалось запустить файловый сервер на %s:%s: %s. "
            "Instagram-паблишер может быть недоступен.",
            config.FILE_SERVER_HOST,
            config.FILE_SERVER_PORT,
            exc,
        )

    # 4. Инициализация Aiogram
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)

    # 5. Запуск фоновой задачи регламентного обслуживания
    maintenance_task = asyncio.create_task(periodic_maintenance_task())

    try:
        # Проверка подключения к Telegram
        me = await bot.get_me()
        logger.info("Успешная авторизация в Telegram: @%s (ID: %s)", me.username, me.id)
        logger.info(
            "Ограничение доступа (ALLOWED_IDS): %s",
            config.ALLOWED_IDS if config.ALLOWED_IDS else "ОТКРЫТ ВСЕМ",
        )

        # Сброс накопившихся вебхуков и запуск polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота и очистка ресурсов...")
        maintenance_task.cancel()
        if file_runner:
            await file_runner.cleanup()
        await bot.session.close()
        logger.info("Бот полностью остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение работы программы.")

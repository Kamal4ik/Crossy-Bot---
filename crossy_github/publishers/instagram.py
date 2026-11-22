"""
Паблишер для Instagram Reels через официальный Instagram API with Instagram Login.
Хост: https://graph.instagram.com/{GRAPH_VERSION}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import aiohttp

import config
from publishers.base import PublishResult, ProgressCallback, safe_progress

logger = logging.getLogger(__name__)


def _load_instagram_token(user_id_tg: int | str | None = None) -> tuple[str, str, float, Path]:
    """
    Загрузка токена, user_id и срока действия из tokens/{user_id}/instagram.json.
    Возвращает (access_token, ig_user_id, expires_at, token_path).
    """
    token_path = config.get_user_ig_token_path(user_id_tg) if user_id_tg else (config.TOKENS_DIR / "instagram.json")
    if not token_path.is_file() and (config.TOKENS_DIR / "instagram.json").is_file():
        # Резервный поиск в общем каталоге
        token_path = config.TOKENS_DIR / "instagram.json"

    access_token = ""
    user_id = ""
    expires_at = 0.0

    if token_path.is_file():
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            access_token = data.get("access_token", "").strip()
            user_id = str(data.get("user_id", "")).strip()
            expires_at = float(data.get("expires_at", 0.0))
        except Exception as exc:
            logger.error("Ошибка чтения %s: %s", token_path, exc)

    # Засев из .env при первом старте, если токен еще не создан
    if not access_token and config.IG_ACCESS_TOKEN and not user_id_tg:
        access_token = config.IG_ACCESS_TOKEN
        user_id = config.IG_USER_ID
        expires_at = time.time() + (60 * 86400)
        try:
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "access_token": access_token,
                        "user_id": user_id,
                        "expires_at": expires_at,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.info("tokens/instagram.json успешно создан из данных .env")
        except Exception as exc:
            logger.error("Не удалось записать tokens/instagram.json: %s", exc)

    return access_token, user_id, expires_at, token_path


async def refresh_instagram_token(current_token: str, user_id: str, token_path: Optional[Path] = None) -> str:
    """
    Рефреш долгоживущего токена Instagram.
    Для токенов 'IGAA...' используется graph.instagram.com/refresh_access_token.
    Для токенов 'EA...' (Unified Login / System User) токен действует длительно.
    """
    if current_token.startswith("EA"):
        logger.info("Токен Unified Login (EA...) активен и не требует стандартного ig_refresh_token.")
        return current_token

    if not token_path:
        token_path = config.TOKENS_DIR / "instagram.json"

    refresh_url = f"https://graph.instagram.com/refresh_access_token"
    params = {
        "grant_type": "ig_refresh_token",
        "access_token": current_token,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(refresh_url, params=params) as resp:
                data = await resp.json()
                if "access_token" in data:
                    new_token = data["access_token"]
                    expires_in = data.get("expires_in", 60 * 86400)
                    new_expires_at = time.time() + float(expires_in)
                    with open(token_path, "w", encoding="utf-8") as f:
                        json.dump(
                            {
                                "access_token": new_token,
                                "user_id": user_id,
                                "expires_at": new_expires_at,
                            },
                            f,
                            indent=2,
                            ensure_ascii=False,
                        )
                    logger.info("Токен Instagram успешно обновлен в %s.", token_path)
                    return new_token
                else:
                    err_msg = data.get("error", {}).get("message", "Неизвестная ошибка")
                    logger.warning("Рефреш токена Instagram не выполнен: %s", err_msg)
    except Exception as exc:
        logger.error("Исключение при обновлении токена Instagram: %s", exc)

    return current_token


class InstagramPublisher:
    """Паблишер видео в Instagram Reels."""

    def __init__(self) -> None:
        pass

    async def _ensure_token(self, user_id_tg: int | str | None = None) -> tuple[str, str, str]:
        """Проверка токена и упреждающий рефреш, если осталось менее 7 дней."""
        access_token, user_id, expires_at, token_path = _load_instagram_token(user_id_tg)
        if not access_token:
            return "", "", ""

        # Для токенов IGAA рефрешим, если осталось менее 7 дней
        if not access_token.startswith("EA"):
            remaining = expires_at - time.time()
            if 0 < remaining < (7 * 86400):
                logger.info("До истечения токена Instagram осталось менее 7 дней. Запуск рефреша...")
                access_token = await refresh_instagram_token(access_token, user_id, token_path)

        base_url = _get_api_base_url(access_token)
        return access_token, user_id, base_url

    async def publish(
        self,
        video_path: str,
        caption: str,
        progress: Optional[ProgressCallback] = None,
        user_id: int | str | None = None,
    ) -> PublishResult:
        """Публикация видео в Instagram Reels."""
        access_token, ig_user_id, base_url = await self._ensure_token(user_id)

        if not access_token or not ig_user_id:
            return PublishResult(
                ok=False,
                error="Instagram не настроен для вашего аккаунта (используйте команду /connect)",
            )

        if not config.PUBLIC_BASE_URL:
            return PublishResult(
                ok=False,
                error="IG: не настроен PUBLIC_BASE_URL в .env (Meta требует публичный URL для видео)",
            )

        video_filename = os.path.basename(video_path)
        public_video_url = f"{config.PUBLIC_BASE_URL}/f/{video_filename}"
        clean_caption = caption.strip()[:2200]

        async with aiohttp.ClientSession() as session:
            # Шаг 1: Создание медиа-контейнера
            await safe_progress(progress, "Instagram: Создание контейнера...")
            create_url = f"{base_url}/{user_id}/media"
            create_payload = {
                "media_type": "REELS",
                "video_url": public_video_url,
                "caption": clean_caption,
                "share_to_feed": "true",
                "access_token": access_token,
            }

            try:
                async with session.post(create_url, data=create_payload) as resp:
                    data = await resp.json()
                    if "error" in data:
                        err_msg = data["error"].get("message", "Неизвестная ошибка")
                        return PublishResult(ok=False, error=f"IG: {err_msg}")
                    container_id = data.get("id")
                    if not container_id:
                        return PublishResult(ok=False, error="IG: Не получен ID медиа-контейнера")
            except Exception as exc:
                return PublishResult(ok=False, error=f"IG: Ошибка запроса создания: {exc}")

            # Шаг 2: Опрос статуса готовности контейнера (каждые 10 с, максимум 60 попыток = 10 мин)
            status_url = f"{base_url}/{container_id}"
            status_params = {
                "fields": "status_code,status",
                "access_token": access_token,
            }

            container_ready = False
            for attempt in range(1, 61):
                await safe_progress(
                    progress,
                    f"Instagram: Обработка видео серверами Meta ({attempt}/60)...",
                )
                await asyncio.sleep(10)

                try:
                    async with session.get(status_url, params=status_params) as resp:
                        s_data = await resp.json()
                        if "error" in s_data:
                            err_msg = s_data["error"].get("message", "Неизвестная ошибка")
                            return PublishResult(ok=False, error=f"IG: {err_msg}")

                        status_code = s_data.get("status_code", "").upper()
                        if status_code == "FINISHED":
                            container_ready = True
                            break
                        elif status_code in ("ERROR", "EXPIRED"):
                            return PublishResult(
                                ok=False,
                                error=f"IG: Ошибка обработки контейнера ({status_code}): {s_data}",
                            )
                except Exception as exc:
                    logger.warning("Сбой проверки статуса контейнера IG: %s", exc)

            if not container_ready:
                return PublishResult(
                    ok=False,
                    error="IG: Превышено время ожидания обработки видео (таймаут 10 минут)",
                )

            # Шаг 3: Публикация контейнера
            await safe_progress(progress, "Instagram: Публикация Reels...")
            publish_url = f"{base_url}/{user_id}/media_publish"
            publish_payload = {
                "creation_id": container_id,
                "access_token": access_token,
            }

            try:
                async with session.post(publish_url, data=publish_payload) as resp:
                    p_data = await resp.json()
                    if "error" in p_data:
                        err_msg = p_data["error"].get("message", "Неизвестная ошибка публикации")
                        return PublishResult(ok=False, error=f"IG: {err_msg}")
                    media_id = p_data.get("id")
                    if not media_id:
                        return PublishResult(ok=False, error="IG: Не получен ID опубликованного медиа")
            except Exception as exc:
                return PublishResult(ok=False, error=f"IG: Ошибка запроса публикации: {exc}")

            # Шаг 4: Получение постоянной ссылки на публикацию
            permalink = None
            try:
                media_info_url = f"{base_url}/{media_id}"
                info_params = {
                    "fields": "permalink",
                    "access_token": access_token,
                }
                async with session.get(media_info_url, params=info_params) as resp:
                    m_data = await resp.json()
                    permalink = m_data.get("permalink")
            except Exception as exc:
                logger.warning("Не удалось получить permalink Instagram: %s", exc)

            return PublishResult(
                ok=True,
                url=permalink,
                note="Опубликовано в Instagram Reels",
            )

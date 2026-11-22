"""
Паблишер для TikTok через официальный TikTok Content Posting API v2.
Поддерживает два режима:
1. draft (дефолт, scope video.upload) — черновики/Inbox (работает без аудита приложения).
2. direct (scope video.publish) — прямая публикация Direct Post (требует закрытый аккаунт или аудит).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

import config
from publishers.base import PublishResult, ProgressCallback, safe_progress

logger = logging.getLogger(__name__)

# Словарь подсказок по типовым ошибкам TikTok API
TIKTOK_ERROR_HINTS: dict[str, str] = {
    "access_token_invalid": "Токен TikTok недействителен или истек — перезапустите scripts/tiktok_auth.py",
    "unaudited_client_can_only_post_to_private_accounts": (
        "Неаудированное приложение TikTok может публиковать только в закрытый аккаунт с SELF_ONLY. "
        "Включите закрытый аккаунт в приложении TikTok или используйте режим черновиков (TT_MODE=draft)."
    ),
    "spam_risk": "TikTok временно отклонил запрос из-за фильтра спам-активности. Попробуйте позже.",
    "rate_limit_exceeded": "Превышен лимит запросов TikTok API (максимум 6 init в минуту).",
    "scope_not_authorized": "Недостаточно прав токена. Переавторизуйтесь через scripts/tiktok_auth.py с нужными TT_SCOPES.",
}


def _get_tt_token_path(user_id_tg: int | str | None = None) -> Path:
    """Определение пути к файлу токена TikTok пользователя."""
    if user_id_tg:
        user_path = config.get_user_tt_token_path(user_id_tg)
        if user_path.is_file():
            return user_path
    # Резервный общий путь
    return config.TOKENS_DIR / "tiktok.json"


def _load_tiktok_tokens(user_id_tg: int | str | None = None) -> dict[str, Any] | None:
    """Загрузка данных авторизации TikTok из файла tokens/{user_id}/tiktok.json."""
    token_path = _get_tt_token_path(user_id_tg)
    if not token_path.is_file():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Ошибка чтения %s: %s", token_path, exc)
        return None


def _save_tiktok_tokens(data: dict[str, Any], user_id_tg: int | str | None = None) -> None:
    """Сохранение токенов TikTok в JSON-файл пользователя."""
    token_path = config.get_user_tt_token_path(user_id_tg) if user_id_tg else (config.TOKENS_DIR / "tiktok.json")
    try:
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Токены TikTok успешно обновлены в %s.", token_path)
    except Exception as exc:
        logger.error("Ошибка записи %s: %s", token_path, exc)


async def _refresh_tiktok_token_if_needed(tokens: dict[str, Any], user_id_tg: int | str | None = None) -> dict[str, Any]:
    """Авто-рефреш токена TikTok при остатке времени жизни менее 120 секунд."""
    expires_at = float(tokens.get("expires_at", 0))
    if time.time() < (expires_at - 120):
        return tokens

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        logger.warning("Отсутствует refresh_token TikTok для автоматического обновления.")
        return tokens

    logger.info("Обновление истекающего токена TikTok через refresh_token...")
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "client_key": config.TT_CLIENT_KEY,
        "client_secret": config.TT_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=payload) as resp:
                data = await resp.json()
                if "access_token" in data:
                    tokens["access_token"] = data["access_token"]
                    tokens["refresh_token"] = data.get("refresh_token", refresh_token)
                    expires_in = data.get("expires_in", 86400)
                    tokens["expires_at"] = time.time() + float(expires_in)
                    refresh_expires_in = data.get("refresh_expires_in", 86400 * 365)
                    tokens["refresh_expires_at"] = time.time() + float(refresh_expires_in)
                    tokens["open_id"] = data.get("open_id", tokens.get("open_id"))
                    tokens["scope"] = data.get("scope", tokens.get("scope"))
                    _save_tiktok_tokens(tokens, user_id_tg)
                else:
                    err = data.get("error_description") or data.get("message") or str(data)
                    logger.error("Ошибка рефреша токена TikTok: %s", err)
    except Exception as exc:
        logger.exception("Исключение при рефреше токена TikTok: %s", exc)

    return tokens


class TikTokPublisher:
    """Паблишер видео в TikTok."""

    async def publish(
        self,
        video_path: str,
        caption: str,
        progress: Optional[ProgressCallback] = None,
        user_id: int | str | None = None,
    ) -> PublishResult:
        """Публикация видео в TikTok."""
        path = Path(video_path)
        if not path.is_file():
            return PublishResult(ok=False, error=f"Файл видео не найден: {video_path}")

        tokens = _load_tiktok_tokens(user_id)
        if not tokens or not tokens.get("access_token"):
            return PublishResult(
                ok=False,
                error="TikTok не авторизован для вашего аккаунта (используйте команду /connect)",
            )

        # Авто-рефреш при необходимости
        tokens = await _refresh_tiktok_token_if_needed(tokens, user_id)
        access_token = tokens["access_token"]

        file_size = path.stat().st_size
        chunk_size = min(file_size, 64 * 1024 * 1024)  # Чанки до 64 МБ
        total_chunks = math.ceil(file_size / chunk_size)

        is_draft = config.TT_MODE == "draft"
        clean_caption = caption.strip()[:2200]

        # В режиме draft используется эндпоинт inbox/video/init/, в режиме direct — video/init/
        init_url = (
            "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
            if is_draft
            else "https://open.tiktokapis.com/v2/post/publish/video/init/"
        )
        init_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

        # Тело запроса инициализации
        init_body: dict[str, Any] = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            }
        }

        # В режиме direct добавляется post_info; в draft — тело БЕЗ post_info
        if not is_draft:
            init_body["post_info"] = {
                "title": clean_caption,
                "privacy_level": config.TT_PRIVACY_LEVEL,
            }

        publish_id = ""
        upload_url = ""

        # Шаг 1: Инициализация загрузки (с поддержкой повторов при rate_limit_exceeded)
        await safe_progress(progress, "TikTok: Инициализация публикации...")
        async with aiohttp.ClientSession() as session:
            for retry in range(3):
                try:
                    async with session.post(init_url, headers=init_headers, json=init_body) as resp:
                        init_res = await resp.json()
                        err = init_res.get("error", {})
                        code = err.get("code")

                        if code == "ok" or ("data" in init_res and "publish_id" in init_res["data"]):
                            data = init_res.get("data", {})
                            publish_id = data.get("publish_id", "")
                            upload_url = data.get("upload_url", "")
                            break

                        log_id = err.get("log_id", "N/A")
                        err_msg = err.get("message", "Неизвестная ошибка")

                        # Лимит 6 запросов init/мин
                        if code == "rate_limit_exceeded" and retry < 2:
                            await safe_progress(
                                progress, "TikTok: Лимит запросов, ожидание 15 сек..."
                            )
                            await asyncio.sleep(15)
                            continue

                        hint = TIKTOK_ERROR_HINTS.get(code, err_msg)
                        return PublishResult(
                            ok=False,
                            error=f"TikTok [{code}] (log_id={log_id}): {hint}",
                        )
                except Exception as exc:
                    if retry == 2:
                        return PublishResult(ok=False, error=f"TikTok: Сбой инициализации: {exc}")
                    await asyncio.sleep(5)

            if not publish_id or not upload_url:
                return PublishResult(
                    ok=False,
                    error="TikTok: Не удалось получить URL для загрузки видео",
                )

            # Шаг 2: Загрузка видео чанками через PUT
            with open(path, "rb") as video_file:
                for chunk_idx in range(total_chunks):
                    start = chunk_idx * chunk_size
                    chunk_data = video_file.read(chunk_size)
                    end = start + len(chunk_data) - 1

                    chunk_headers = {
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                    }

                    pct = int(((chunk_idx + 1) / total_chunks) * 100)
                    await safe_progress(
                        progress,
                        f"TikTok: Загрузка видео {pct}% (чанк {chunk_idx + 1}/{total_chunks})...",
                    )

                    upload_success = False
                    for up_retry in range(3):
                        try:
                            async with session.put(
                                upload_url, headers=chunk_headers, data=chunk_data
                            ) as up_resp:
                                # Статусы 200, 201, 206 нормальны для чанковой загрузки
                                if up_resp.status in (200, 201, 206):
                                    upload_success = True
                                    break
                                else:
                                    logger.warning(
                                        "Статус загрузки чанка TikTok: %s, попытка %s",
                                        up_resp.status,
                                        up_retry + 1,
                                    )
                        except Exception as exc:
                            logger.warning("Ошибка PUT чанка TikTok: %s", exc)

                        await asyncio.sleep(3)

                    if not upload_success:
                        return PublishResult(
                            ok=False,
                            error=f"TikTok: Ошибка передачи чанка {chunk_idx + 1}/{total_chunks}",
                        )

            # Шаг 3: Завершение публикации
            # В режиме draft опрос статуса не требуется — видео сразу попадает в Inbox пользователя
            if is_draft:
                return PublishResult(
                    ok=True,
                    url=None,
                    note="видео отправлено в Inbox TikTok — откройте приложение TikTok и опубликуйте его оттуда",
                )

            # В режиме direct — опрос статуса обработки видео
            status_url = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
            status_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            }
            status_body = {"publish_id": publish_id}

            for poll_attempt in range(1, 31):
                await safe_progress(
                    progress,
                    f"TikTok: Проверка статуса публикации ({poll_attempt}/30)...",
                )
                await asyncio.sleep(10)

                try:
                    async with session.post(
                        status_url, headers=status_headers, json=status_body
                    ) as resp:
                        s_data = await resp.json()
                        data = s_data.get("data", {})
                        status = data.get("status", "").upper()

                        if status in ("PUBLISHED", "PUBLISH_COMPLETE"):
                            note = (
                                "опубликовано «только я» — для публичных постов нужен аудит приложения или TT_MODE=draft"
                                if config.TT_PRIVACY_LEVEL == "SELF_ONLY"
                                else "видео успешно опубликовано в TikTok"
                            )
                            return PublishResult(ok=True, url=None, note=note)

                        if status == "FAILED":
                            fail_reason = data.get("fail_reason", "Неизвестная причина")
                            return PublishResult(
                                ok=False,
                                error=f"TikTok: Ошибка публикации: {fail_reason}",
                            )
                except Exception as exc:
                    logger.warning("Ошибка проверки статуса TikTok: %s", exc)

            return PublishResult(
                ok=False,
                error="TikTok: Превышено время ожидания проверки статуса Direct Post (таймаут 5 минут)",
            )

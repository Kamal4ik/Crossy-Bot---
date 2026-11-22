"""
Паблишер для YouTube Shorts / видео через официальный YouTube Data API v3.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config
from publishers.base import PublishResult, ProgressCallback, safe_progress

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _get_youtube_credentials(token_path: Path) -> Credentials | None:
    """Загрузка и валидация OAuth-учетных данных YouTube."""
    if not token_path.is_file():
        return None

    try:
        with open(token_path, "r", encoding="utf-8") as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    except Exception as exc:
        logger.error("Ошибка чтения токена YouTube (%s): %s", token_path, exc)
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            logger.info("Обновление истекшего токена YouTube (%s)...", token_path)
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("Токен YouTube успешно обновлен и сохранен.")
        except Exception as exc:
            logger.error("Не удалось обновить токен YouTube: %s", exc)
            return None

    if creds and creds.valid:
        return creds

    return None


def _parse_caption(caption: str) -> tuple[str, str]:
    """Разбивка текста подписи на заголовок (первая строка) и описание."""
    cleaned = caption.strip()
    if not cleaned:
        return "Видео", ""

    lines = cleaned.splitlines()
    title = lines[0].strip() or "Видео"
    description = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    # YouTube ограничения: заголовок до 100 символов, описание до 5000
    return title[:100], description[:5000]


def _sync_upload_video(
    video_path: str,
    title: str,
    description: str,
    loop: asyncio.AbstractEventLoop,
    progress_cb: Optional[ProgressCallback],
    user_id: int | str | None = None,
) -> PublishResult:
    """Синхронная загрузка видео через googleapiclient с порционным прогрессом."""
    token_path = config.get_user_yt_token_path(user_id) if user_id else (config.TOKENS_DIR / "youtube.json")
    if not token_path.is_file():
        # Резервный поиск в общем каталоге
        token_path = config.TOKENS_DIR / "youtube.json"

    creds = _get_youtube_credentials(token_path)
    if not creds:
        return PublishResult(
            ok=False,
            error="YouTube не авторизован или токен недействителен (используйте команду /connect)",
        )

    try:
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": config.YT_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": config.YT_PRIVACY_STATUS,
                "selfDeclaredMadeForKids": False,
            },
        }

        # Resumable upload чанками по 8 МБ
        chunksize = 8 * 1024 * 1024
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            chunksize=chunksize,
            resumable=True,
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        last_reported_pct = 0

        # Уведомляем о старте
        if progress_cb:
            asyncio.run_coroutine_threadsafe(
                safe_progress(progress_cb, "YouTube: Загрузка 0%..."), loop
            ).result()

        while response is None:
            status, response = request.next_chunk(num_retries=3)
            if status and progress_cb:
                progress_pct = int(status.progress() * 100)
                # Оповещаем ступенями по 20%
                if progress_pct >= last_reported_pct + 20:
                    last_reported_pct = (progress_pct // 20) * 20
                    asyncio.run_coroutine_threadsafe(
                        safe_progress(progress_cb, f"YouTube: Загрузка {last_reported_pct}%..."),
                        loop,
                    ).result()

        video_id = response.get("id")
        if not video_id:
            return PublishResult(ok=False, error="YouTube: Не получен идентификатор видео в ответе API")

        url = f"https://www.youtube.com/watch?v={video_id}"
        note = (
            "видео появится после обработки на стороне YouTube"
            if config.YT_PRIVACY_STATUS.lower() == "public"
            else f"статус доступа: {config.YT_PRIVACY_STATUS}"
        )
        return PublishResult(ok=True, url=url, note=note)

    except HttpError as exc:
        logger.error("HttpError при загрузке на YouTube: %s", exc)
        err_content = exc.content.decode("utf-8", errors="replace") if hasattr(exc, "content") else str(exc)
        if "quotaExceeded" in err_content:
            return PublishResult(
                ok=False,
                error="Превышена суточная квота YouTube Data API (10 000 единиц). Попробуйте завтра.",
            )
        return PublishResult(
            ok=False,
            error=f"Ошибка YouTube API ({exc.resp.status}): {exc.reason}",
        )
    except Exception as exc:
        logger.exception("Неожиданная ошибка при загрузке на YouTube: %s", exc)
        return PublishResult(ok=False, error=f"Ошибка YouTube: {str(exc)}")


class YouTubePublisher:
    """Паблишер видео на YouTube."""

    async def publish(
        self,
        video_path: str,
        caption: str,
        progress: Optional[ProgressCallback] = None,
        user_id: int | str | None = None,
    ) -> PublishResult:
        """Асинхронная публикация видео."""
        path = Path(video_path)
        if not path.is_file():
            return PublishResult(ok=False, error=f"Файл видео не найден: {video_path}")

        title, description = _parse_caption(caption)
        loop = asyncio.get_running_loop()

        return await asyncio.to_thread(
            _sync_upload_video,
            str(path),
            title,
            description,
            loop,
            progress,
            user_id,
        )

"""
Базовые типы и интерфейс паблишеров в социальные сети.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class PublishResult:
    """Результат публикации видео на целевой платформе."""
    ok: bool
    url: Optional[str] = None    # Ссылка на опубликованное видео (если доступно)
    error: Optional[str] = None  # Понятная пользователю ошибка с рекомендацией
    note: Optional[str] = None   # Дополнительное пояснение (например, статус черновика)


async def safe_progress(progress: Optional[ProgressCallback], text: str) -> None:
    """
    Безопасный вызов коллбэка для оповещения пользователя о статусе.
    Любые ошибки внутри коллбэка не прерывают процесс публикации.
    """
    if progress is None:
        return
    try:
        await progress(text)
    except Exception as exc:
        logger.debug("Ошибка в progress-коллбэке: %s", exc)


class Publisher(Protocol):
    """Протокол паблишера в социальную сеть."""

    async def publish(
        self,
        video_path: str,
        caption: str,
        progress: Optional[ProgressCallback] = None,
    ) -> PublishResult:
        ...

"""
Локальный HTTP-сервер для временной отдачи видеофайлов платформе Instagram Reels.
Meta требует публичный URL видео для создания медиа-контейнера.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)

# Шаблон имени файла: латиница, цифры, дефис, подчеркивание и допустимое расширение
SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+\.(mp4|mov|webm|mkv|m4v)$", re.IGNORECASE)


async def handle_video(request: web.Request) -> web.StreamResponse:
    """Обработчик отдачи видеофайла по GET /f/{name}."""
    name = request.match_info.get("name", "")
    video_dir: Path = request.app["video_dir"]

    # Защита от path traversal и скрытых файлов
    if not SAFE_FILENAME_REGEX.match(name) or ".." in name or "/" in name or "\\" in name:
        return web.Response(status=404, text="File not found or invalid filename")

    target_file = (video_dir / name).resolve()

    # Проверка, что файл находится строго внутри разрешенной директории
    try:
        target_file.relative_to(video_dir.resolve())
    except ValueError:
        return web.Response(status=404, text="Access denied")

    if not target_file.is_file():
        return web.Response(status=404, text="File not found")

    return web.FileResponse(
        path=target_file,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Content-Type": "video/mp4",
        },
    )


def create_app(video_dir: Path) -> web.Application:
    """Создание aiohttp-приложения файлового сервера."""
    app = web.Application()
    app["video_dir"] = video_dir
    app.router.add_get("/f/{name}", handle_video)
    return app


async def start_file_server(host: str, port: int, video_dir: Path) -> web.AppRunner:
    """Запуск фонового файлового сервера."""
    app = create_app(video_dir)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Файловый сервер запущен на http://%s:%s/f/", host, port)
    return runner

"""
Основной FSM-сценарий публикации видео и сервисные команды (/start, /cancel, /setup).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramObject,
)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config
from publishers.instagram import InstagramPublisher, refresh_instagram_token
from publishers.tiktok import TikTokPublisher
from publishers.youtube import YouTubePublisher

logger = logging.getLogger(__name__)

router = Router(name="publish_router")


# =====================================================================
# Middleware контроля доступа (ALLOWED_IDS)
# =====================================================================
@router.message.outer_middleware()
@router.callback_query.outer_middleware()
async def access_control_middleware(
    handler: Callable[[TelegramObject, Dict[str, Any]], Any],
    event: TelegramObject,
    data: Dict[str, Any],
) -> Any:
    """Фильтрация доступа по списку ALLOWED_IDS."""
    user = getattr(event, "from_user", None)
    if config.ALLOWED_IDS and user:
        if user.id not in config.ALLOWED_IDS:
            if isinstance(event, Message):
                await event.answer("⛔ Доступ к данному боту ограничен администратором.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ ограничен.", show_alert=True)
            return None
    return await handler(event, data)


# =====================================================================
# Состояния FSM
# =====================================================================
class PublishFSM(StatesGroup):
    waiting_video = State()
    waiting_caption_tt = State()
    waiting_caption_ig = State()
    waiting_caption_yt = State()
    waiting_confirm = State()
    waiting_ig_token = State()


# =====================================================================
# Вспомогательные функции клавиатур
# =====================================================================
def get_caption_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой быстрого дублирования подписи."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Взять текст предыдущей подписи")],
            [KeyboardButton(text="/skip")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def is_platform_configured(platform: str, user_id: int | str | None = None) -> bool:
    """Проверка наличия токенов/конфигурации платформы для конкретного пользователя."""
    if platform == "youtube":
        if user_id and config.get_user_yt_token_path(user_id).is_file():
            return True
        return (config.TOKENS_DIR / "youtube.json").is_file()
    elif platform == "instagram":
        if user_id and config.get_user_ig_token_path(user_id).is_file():
            return True
        return (config.TOKENS_DIR / "instagram.json").is_file() or bool(config.IG_ACCESS_TOKEN)
    elif platform == "tiktok":
        if user_id and config.get_user_tt_token_path(user_id).is_file():
            return True
        return (config.TOKENS_DIR / "tiktok.json").is_file()
    return False


def build_confirm_keyboard(selected: dict[str, bool]) -> InlineKeyboardMarkup:
    """Генерация инлайн-клавиатуры тумблеров и кнопок действий."""
    buttons = []
    labels = {
        "youtube": "YouTube",
        "instagram": "Instagram Reels",
        "tiktok": "TikTok",
    }
    for key, name in labels.items():
        is_on = selected.get(key, False)
        icon = "✅" if is_on else "❌"
        buttons.append(
            [InlineKeyboardButton(text=f"[{icon}] {name}", callback_data=f"cp:toggle:{key}")]
        )

    action_buttons = [
        InlineKeyboardButton(text="🚀 Опубликовать", callback_data="cp:publish"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cp:cancel"),
    ]
    buttons.append(action_buttons)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def safe_cleanup_file(file_path: Optional[str]) -> None:
    """Безопасное удаление временного видеофайла."""
    if not file_path:
        return
    try:
        p = Path(file_path)
        if p.is_file():
            p.unlink(missing_ok=True)
            logger.info("Удален временный файл: %s", file_path)
    except Exception as exc:
        logger.warning("Не удалось удалить временный файл %s: %s", file_path, exc)


# =====================================================================
# Базовые команды: /start, /cancel
# =====================================================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветствие и переход в ожидание видео."""
    data = await state.get_data()
    safe_cleanup_file(data.get("video_path"))
    await state.clear()
    await state.set_state(PublishFSM.waiting_video)

    text = (
        "👋 **Привет! Я бот для кросспостинга видео в YouTube Shorts, Instagram Reels и TikTok.**\n\n"
        "✨ **Как это работает:**\n"
        "1. Отправьте вертикальное видео (до 20 МБ через стандартный Telegram Bot API).\n"
        "2. Введите подписи для каждой платформы (или используйте кнопку «Взять текст предыдущей» / команду `/skip`).\n"
        "3. Переключите тумблеры площадок и нажмите «Опубликовать».\n\n"
        "⚙️ Используйте команду `/setup` в любой момент для проверки статуса токенов и конфигурации.\n"
        "❌ Команда `/cancel` отменяет текущую публикацию на любом этапе.\n\n"
        "📹 **Отправьте видео для начала:**"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Сброс FSM и удаление временного файла."""
    data = await state.get_data()
    safe_cleanup_file(data.get("video_path"))
    await state.clear()
    await state.set_state(PublishFSM.waiting_video)
    await message.answer(
        "🚫 Публикация отменена. Временные файлы удалены.\n\n"
        "Отправьте новое видео, когда будете готовы.",
        reply_markup=ReplyKeyboardRemove(),
    )


# =====================================================================
# Шаг 1: Прием видеофайла
# =====================================================================
@router.message(PublishFSM.waiting_video, F.video | F.document)
async def process_video(message: Message, state: FSMContext) -> None:
    """Загрузка видеофайла от пользователя."""
    bot = message.bot
    if not bot:
        return

    video_obj = message.video
    filename = "video.mp4"

    if message.document:
        doc = message.document
        doc_mime = (doc.mime_type or "").lower()
        doc_name = (doc.file_name or "").lower()
        valid_extensions = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
        if not (doc_mime.startswith("video/") or any(doc_name.endswith(ext) for ext in valid_extensions)):
            await message.answer("⚠️ Пожалуйста, отправьте видеофайл (MP4, MOV, WEBM, MKV).")
            return
        video_obj = doc
        filename = doc.file_name or "video.mp4"
    elif message.video:
        filename = message.video.file_name or "video.mp4"

    if not video_obj:
        await message.answer("⚠️ Не удалось распознать видеофайл. Попробуйте еще раз.")
        return

    # Проверка размера файла
    max_bytes = config.MAX_TG_MB * 1024 * 1024
    if video_obj.file_size and video_obj.file_size > max_bytes:
        await message.answer(
            f"❌ Размер файла ({video_obj.file_size // (1024 * 1024)} МБ) превышает лимит {config.MAX_TG_MB} МБ.\n"
            "Telegram Bot API не позволяет скачивать файлы больше 20 МБ без локального Bot API сервера. "
            "Пожалуйста, сожмите видео или отправьте файл меньшего размера."
        )
        return

    download_msg = await message.answer("⏳ Скачиваю видео...")

    unique_name = f"{uuid.uuid4().hex}.mp4"
    destination = config.VIDEO_DIR / unique_name

    try:
        file_info = await bot.get_file(video_obj.file_id)
        if not file_info.file_path:
            await download_msg.edit_text("❌ Ошибка получения пути к файлу от Telegram API.")
            return

        await bot.download_file(file_info.file_path, destination)
    except Exception as exc:
        logger.exception("Ошибка при скачивании видео: %s", exc)
        await download_msg.edit_text(f"❌ Ошибка загрузки видео: {exc}")
        return

    await state.update_data(
        video_path=str(destination),
        video_name=filename,
        caption_tt="",
        caption_ig="",
        caption_yt="",
    )

    await download_msg.delete()
    await state.set_state(PublishFSM.waiting_caption_tt)

    await message.answer(
        "📝 **Шаг 1 из 3: Подпись для TikTok** (до 2200 символов):\n"
        "Введите текст подписи с хэштегами или отправьте `/skip`, чтобы оставить пустой:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )


# =====================================================================
# Шаг 2: Подпись для TikTok
# =====================================================================
@router.message(PublishFSM.waiting_caption_tt)
async def process_caption_tt(message: Message, state: FSMContext) -> None:
    """Обработка подписи TikTok."""
    text = "" if message.text == "/skip" else (message.text or "")
    await state.update_data(caption_tt=text.strip())
    await state.set_state(PublishFSM.waiting_caption_ig)

    await message.answer(
        "📝 **Шаг 2 из 3: Подпись для Instagram Reels** (до 2200 символов):\n"
        "Введите текст подписи, нажмите кнопку ниже, чтобы продублировать подпись TikTok, "
        "или отправьте `/skip`:",
        parse_mode="Markdown",
        reply_markup=get_caption_keyboard(),
    )


# =====================================================================
# Шаг 3: Подпись для Instagram Reels
# =====================================================================
@router.message(PublishFSM.waiting_caption_ig)
async def process_caption_ig(message: Message, state: FSMContext) -> None:
    """Обработка подписи Instagram."""
    data = await state.get_data()
    user_text = (message.text or "").strip()

    if user_text == "Взять текст предыдущей подписи":
        caption_ig = data.get("caption_tt", "")
    elif user_text == "/skip":
        caption_ig = ""
    else:
        caption_ig = user_text

    await state.update_data(caption_ig=caption_ig)
    await state.set_state(PublishFSM.waiting_caption_yt)

    await message.answer(
        "📝 **Шаг 3 из 3: Подпись для YouTube Shorts**:\n"
        "Первая строка станет названием (до 100 симв.), остальной текст — описанием (до 5000 симв.).\n"
        "Введите текст, используйте кнопку предыдущей подписи или отправьте `/skip`:",
        parse_mode="Markdown",
        reply_markup=get_caption_keyboard(),
    )


# =====================================================================
# Шаг 4: Подпись для YouTube & Экран подтверждения
# =====================================================================
@router.message(PublishFSM.waiting_caption_yt)
async def process_caption_yt(message: Message, state: FSMContext) -> None:
    """Обработка подписи YouTube и показ экрана подтверждения."""
    data = await state.get_data()
    user_text = (message.text or "").strip()

    if user_text == "Взять текст предыдущей подписи":
        caption_yt = data.get("caption_ig", "") or data.get("caption_tt", "")
    elif user_text == "/skip":
        caption_yt = ""
    else:
        caption_yt = user_text

    # Формируем выбор платформ по умолчанию для текущего пользователя
    user_id = message.from_user.id if message.from_user else None
    selected_platforms = {
        "youtube": is_platform_configured("youtube", user_id),
        "instagram": is_platform_configured("instagram", user_id),
        "tiktok": is_platform_configured("tiktok", user_id),
    }

    await state.update_data(caption_yt=caption_yt, selected=selected_platforms)
    await state.set_state(PublishFSM.waiting_confirm)

    await send_confirm_screen(message, state)


async def send_confirm_screen(target: Message, state: FSMContext) -> None:
    """Формирование и отправка окна подтверждения публикации."""
    data = await state.get_data()
    video_name = data.get("video_name", "video.mp4")
    c_tt = (data.get("caption_tt") or "(без подписи)")[:60]
    c_ig = (data.get("caption_ig") or "(без подписи)")[:60]
    c_yt = (data.get("caption_yt") or "(без подписи)")[:60]
    selected = data.get("selected", {})

    text = (
        f"🎬 **Готово к публикации!**\n\n"
        f"📁 **Файл:** `{video_name}`\n\n"
        f"📱 **TikTok:** {c_tt}\n"
        f"📸 **Instagram:** {c_ig}\n"
        f"▶️ **YouTube:** {c_yt}\n\n"
        f"Включите нужные площадки тумблерами и нажмите «Опубликовать»:"
    )
    kb = build_confirm_keyboard(selected)
    await target.answer(
        text,
        parse_mode="Markdown",
        reply_markup=kb,
    )


# =====================================================================
# Инлайн-кнопки экрана подтверждения
# =====================================================================
@router.callback_query(PublishFSM.waiting_confirm, F.data.startswith("cp:toggle:"))
async def toggle_platform(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключение тумблера площадки."""
    platform = callback.data.split(":")[-1]
    data = await state.get_data()
    selected: dict[str, bool] = data.get("selected", {})
    selected[platform] = not selected.get(platform, False)

    await state.update_data(selected=selected)
    kb = build_confirm_keyboard(selected)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(PublishFSM.waiting_confirm, F.data == "cp:cancel")
async def cancel_publication(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена публикации через инлайн-кнопку."""
    data = await state.get_data()
    safe_cleanup_file(data.get("video_path"))
    await state.clear()
    await state.set_state(PublishFSM.waiting_video)
    try:
        await callback.message.edit_text("🚫 Публикация отменена. Временный файл удален.")
    except TelegramBadRequest:
        pass
    await callback.answer("Отменено")


@router.callback_query(PublishFSM.waiting_confirm, F.data == "cp:publish")
async def start_publication(callback: CallbackQuery, state: FSMContext) -> None:
    """Запуск параллельной публикации во все выбранные социальные сети."""
    data = await state.get_data()
    video_path = data.get("video_path")
    selected: dict[str, bool] = data.get("selected", {})

    active_platforms = [k for k, v in selected.items() if v]
    if not active_platforms:
        await callback.answer("⚠️ Выберите хотя бы одну платформу!", show_alert=True)
        return

    if not video_path or not Path(video_path).is_file():
        await callback.answer("❌ Файл видео не найден. Загрузите видео заново.", show_alert=True)
        await state.clear()
        await state.set_state(PublishFSM.waiting_video)
        return

    await callback.answer("Публикация запущена!")

    # Удаляем клавиатуру и переводим в информационное сообщение
    status_msg = await callback.message.answer(
        "🚀 **Публикация начата...**\nОжидайте обновления статусов.",
        parse_mode="Markdown",
    )
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Инициализация словаря статусов
    platform_names = {
        "youtube": "YouTube",
        "instagram": "Instagram Reels",
        "tiktok": "TikTok",
    }
    status_dict: dict[str, str] = {p: "⏳ Запуск..." for p in active_platforms}
    status_notes: dict[str, Optional[str]] = {}
    status_urls: dict[str, Optional[str]] = {}

    status_lock = asyncio.Lock()

    async def update_status_message(final: bool = False) -> None:
        """Потокобезопасное обновление статусного сообщения в Telegram."""
        async with status_lock:
            header = "🏁 **Результат публикации:**" if final else "🚀 **Публикация выполняется:**"
            lines = [header, ""]
            for p in active_platforms:
                p_label = platform_names.get(p, p)
                st_text = status_dict.get(p, "")
                lines.append(f"• **{p_label}**: {st_text}")
                note = status_notes.get(p)
                if note:
                    lines.append(f"   ℹ️ _{note}_")
            content = "\n".join(lines)
            try:
                await status_msg.edit_text(content, parse_mode="Markdown")
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    logger.debug("Ошибка обновления статусного сообщения: %s", exc)
            except Exception as exc:
                logger.debug("Исключение при обновлении статуса: %s", exc)

    def make_progress_callback(platform_key: str):
        async def _cb(text: str) -> None:
            status_dict[platform_key] = f"⏳ {text}"
            await update_status_message()
        return _cb

    # Задачи параллельной публикации
    async def publish_task(platform_key: str) -> None:
        try:
            if platform_key == "youtube":
                pub = YouTubePublisher()
                caption = data.get("caption_yt", "")
            elif platform_key == "instagram":
                pub = InstagramPublisher()
                caption = data.get("caption_ig", "")
            elif platform_key == "tiktok":
                pub = TikTokPublisher()
                caption = data.get("caption_tt", "")
            else:
                return

            cb = make_progress_callback(platform_key)
            caller_id = callback.from_user.id if callback.from_user else None
            result = await pub.publish(video_path, caption, cb, user_id=caller_id)

            if result.ok:
                url_str = f" [Ссылка]({result.url})" if result.url else ""
                status_dict[platform_key] = f"✅ Готово!{url_str}"
                status_notes[platform_key] = result.note
                status_urls[platform_key] = result.url
            else:
                status_dict[platform_key] = f"❌ Ошибка: {result.error}"
        except Exception as exc:
            logger.exception("Исключение в воркере публикации %s: %s", platform_key, exc)
            status_dict[platform_key] = f"❌ Сбой: {str(exc)}"

    # Параллельный запуск
    await asyncio.gather(*(publish_task(p) for p in active_platforms))

    # Финальное обновление сообщения и удаление временного видео
    await update_status_message(final=True)
    safe_cleanup_file(video_path)

    # Возврат в исходное состояние
    await state.clear()
    await state.set_state(PublishFSM.waiting_video)
    await status_msg.reply(
        "🎉 Обработка завершена! Вы можете отправить следующее видео для новой публикации."
    )


# =====================================================================
# Команда /connect и /accounts: Управление подключенными соцсетями
# =====================================================================
def build_accounts_keyboard(user_id: int | str) -> InlineKeyboardMarkup:
    """Клавиатура управления подключенными аккаунтами."""
    yt_ok = is_platform_configured("youtube", user_id)
    ig_ok = is_platform_configured("instagram", user_id)
    tt_ok = is_platform_configured("tiktok", user_id)

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if yt_ok else '➕'} YouTube",
                callback_data="acc:yt",
            ),
            InlineKeyboardButton(
                text=f"{'✅' if ig_ok else '➕'} Instagram",
                callback_data="acc:ig",
            ),
            InlineKeyboardButton(
                text=f"{'✅' if tt_ok else '➕'} TikTok",
                callback_data="acc:tt",
            ),
        ],
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="acc:refresh")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("connect"))
@router.message(Command("accounts"))
async def cmd_accounts(message: Message, state: FSMContext) -> None:
    """Просмотр и подключение персональных аккаунтов соцсетей."""
    user = message.from_user
    if not user:
        return

    yt_ok = is_platform_configured("youtube", user.id)
    ig_ok = is_platform_configured("instagram", user.id)
    tt_ok = is_platform_configured("tiktok", user.id)

    status_lines = [
        f"👤 **Управление аккаунтами для {user.first_name}** (`ID: {user.id}`):\n",
        f"• **YouTube:** {'✅ Подключен' if yt_ok else '❌ Не подключен'}",
        f"• **Instagram:** {'✅ Подключен' if ig_ok else '❌ Не подключен'}",
        f"• **TikTok:** {'✅ Подключен' if tt_ok else '❌ Не подключен'}",
        "\nНажмите кнопку нужной платформы ниже для подключения или настройки:",
    ]

    await message.answer(
        "\n".join(status_lines),
        parse_mode="Markdown",
        reply_markup=build_accounts_keyboard(user.id),
    )


@router.callback_query(F.data == "acc:refresh")
async def acc_refresh(callback: CallbackQuery) -> None:
    """Обновление статусов аккаунтов."""
    user = callback.from_user
    if not user or not callback.message:
        return

    yt_ok = is_platform_configured("youtube", user.id)
    ig_ok = is_platform_configured("instagram", user.id)
    tt_ok = is_platform_configured("tiktok", user.id)

    text = (
        f"👤 **Управление аккаунтами для {user.first_name}** (`ID: {user.id}`):\n\n"
        f"• **YouTube:** {'✅ Подключен' if yt_ok else '❌ Не подключен'}\n"
        f"• **Instagram:** {'✅ Подключен' if ig_ok else '❌ Не подключен'}\n"
        f"• **TikTok:** {'✅ Подключен' if tt_ok else '❌ Не подключен'}\n\n"
        "Нажмите кнопку нужной платформы ниже для подключения или настройки:"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown", reply_markup=build_accounts_keyboard(user.id)
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Статус обновлен")


@router.callback_query(F.data == "acc:ig")
async def acc_connect_ig(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрос токена Instagram от пользователя."""
    await state.set_state(PublishFSM.waiting_ig_token)
    await callback.answer()
    await callback.message.answer(
        "📸 **Подключение вашего Instagram:**\n\n"
        "Отправьте сюда ваш токен доступа (начинается с `EA...` или `IGAA...`).\n\n"
        "💡 _Как получить:_\n"
        "1. Администратор бота добавляет ваш Instagram-аккаунт в **Instagram Testers** в Meta for Developers.\n"
        "2. Вы принимаете приглашение в Instagram (Настройки → Разрешения для сайтов → Приглашения тестировщиков).\n"
        "3. Генерируете токен в Meta Dashboard и отправляете его сюда сообщением.\n\n"
        "Отправьте токен или `/cancel` для отмены:",
        parse_mode="Markdown",
    )


@router.message(PublishFSM.waiting_ig_token)
async def process_ig_token_input(message: Message, state: FSMContext) -> None:
    """Валидация и сохранение присланного пользователем токена Instagram."""
    token = (message.text or "").strip()
    user = message.from_user
    if not user:
        return

    if token.startswith("/"):
        await state.clear()
        await state.set_state(PublishFSM.waiting_video)
        await message.answer("Отменено.")
        return

    check_msg = await message.answer("🔍 Проверяю ваш токен Instagram через Graph API...")

    # Проверка через facebook graph или instagram graph
    if token.startswith("EA"):
        endpoint = f"https://graph.facebook.com/{config.GRAPH_VERSION}/me"
        params = {"fields": "id,username,name", "access_token": token}
    else:
        endpoint = f"https://graph.instagram.com/me"
        params = {"fields": "user_id,username", "access_token": token}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, params=params) as resp:
                data = await resp.json()
                if "error" in data:
                    err_msg = data["error"].get("message", "Неизвестная ошибка")
                    await check_msg.edit_text(f"❌ Токен отклонён Meta API: {err_msg}")
                    return

                ig_user_id = str(data.get("user_id") or data.get("id", ""))
                ig_username = data.get("username") or data.get("name", "N/A")

                # Сохраняем в персональную папку tokens/{user_id}/instagram.json
                user_tokens_dir = config.get_user_tokens_dir(user.id)
                ig_file = user_tokens_dir / "instagram.json"
                with open(ig_file, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "access_token": token,
                            "user_id": ig_user_id,
                            "username": ig_username,
                            "expires_at": time.time() + 60 * 86400,
                        },
                        f,
                        indent=2,
                        ensure_ascii=False,
                    )

                await check_msg.edit_text(
                    f"✅ **Instagram успешно привязан!**\n\n"
                    f"👤 Аккаунт: **@{ig_username}**\n"
                    f"🆔 Instagram User ID: `{ig_user_id}`\n\n"
                    f"Теперь ваши Reels будут публиковаться в этот профиль.",
                    parse_mode="Markdown",
                )
                await state.clear()
                await state.set_state(PublishFSM.waiting_video)
    except Exception as exc:
        await check_msg.edit_text(f"❌ Ошибка проверки токена: {exc}")


@router.callback_query(F.data == "acc:yt")
async def acc_connect_yt(callback: CallbackQuery) -> None:
    """Подсказка по подключению YouTube для пользователя."""
    user = callback.from_user
    uid = user.id if user else "USER_ID"
    await callback.answer()
    await callback.message.answer(
        f"▶️ **Подключение вашего YouTube-канала:**\n\n"
        f"1. Добавьте ваш Google-аккаунт в **Test users** в Google Cloud Console проекта бота.\n"
        f"2. Запустите в терминале на хосте бота авторизацию с указанием вашего ID:\n"
        f"```powershell\npython scripts/yt_auth.py --user {uid}\n```\n"
        f"После прохождения авторизации в браузере ваш канал будет привязан к вашему Telegram ID `{uid}`.",
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "acc:tt")
async def acc_connect_tt(callback: CallbackQuery) -> None:
    """Подсказка по подключению TikTok для пользователя."""
    user = callback.from_user
    uid = user.id if user else "USER_ID"
    await callback.answer()
    await callback.message.answer(
        f"🎵 **Подключение вашего TikTok-аккаунта:**\n\n"
        f"1. Добавьте ваш логин TikTok в **Test Accounts (Sandbox)** в TikTok Developer Portal.\n"
        f"2. Запустите авторизацию на хосте бота с указанием вашего ID:\n"
        f"```powershell\npython scripts/tiktok_auth.py --user {uid}\n```\n"
        f"После входа ваши черновики будут отправляться в ваш личный профиль TikTok.",
        parse_mode="Markdown",
    )


# =====================================================================
# Команда /setup: Чек-лист проверки готовности
# =====================================================================
@router.message(Command("setup"))
async def cmd_setup(message: Message) -> None:
    """Мастер проверки всех интеграций и токенов для вызывающего пользователя."""
    bot = message.bot
    caller_id = message.from_user.id if message.from_user else None
    status_msg = await message.answer("🔍 Проверяю конфигурацию и токены...")
    report_lines = [f"📋 **Чек-лист проверки конфигурации (пользователь {caller_id}):**\n"]

    # 1. BOT_TOKEN
    if config.BOT_TOKEN:
        try:
            me = await bot.get_me()
            report_lines.append(f"✅ **BOT_TOKEN:** валиден (@{me.username})")
        except Exception as exc:
            report_lines.append(f"❌ **BOT_TOKEN:** ошибка авторизации: {exc}")
    else:
        report_lines.append("❌ **BOT_TOKEN:** не задан в .env")

    # 2. ALLOWED_IDS
    if config.ALLOWED_IDS:
        report_lines.append(f"✅ **ALLOWED_IDS:** ограничено ({len(config.ALLOWED_IDS)} ID)")
    else:
        report_lines.append("⚠️ **ALLOWED_IDS:** пусто (доступ открыт ВСЕМ, не рекомендуется)")

    # 3. YouTube Data API v3
    yt_token_file = config.get_user_yt_token_path(caller_id) if caller_id else (config.TOKENS_DIR / "youtube.json")
    if not yt_token_file.is_file():
        yt_token_file = config.TOKENS_DIR / "youtube.json"

    if yt_token_file.is_file():
        try:
            with open(yt_token_file, "r", encoding="utf-8") as f:
                t_data = json.load(f)
            creds = Credentials.from_authorized_user_info(
                t_data, [
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/youtube.force-ssl",
                ]
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(yt_token_file, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())

            yt_service = build("youtube", "v3", credentials=creds, cache_discovery=False)
            ch_resp = await asyncio.to_thread(
                yt_service.channels().list(part="snippet,status", mine=True).execute
            )
            items = ch_resp.get("items", [])
            if items:
                ch_title = items[0].get("snippet", {}).get("title", "YouTube канал")
                report_lines.append(f"✅ **YouTube:** «{ch_title}» (готов к загрузке)")
            else:
                report_lines.append("⚠️ **YouTube:** токен принят, но канал не найден")
        except Exception as exc:
            report_lines.append(
                f"❌ **YouTube:** ошибка токена ({exc}). Запустите `python scripts/yt_auth.py --user {caller_id}`"
            )
    else:
        report_lines.append(
            f"❌ **YouTube:** токен не найден. Запустите `python scripts/yt_auth.py --user {caller_id}`"
        )

    # 4. Instagram API
    ig_token, ig_user_id, ig_expires_at, ig_token_file = _load_instagram_token(caller_id)

    if ig_token:
        try:
            if ig_token.startswith("EA"):
                target_endpoint = f"https://graph.facebook.com/{config.GRAPH_VERSION}/{ig_user_id or 'me'}"
                req_params = {"fields": "id,username,name", "access_token": ig_token}
            else:
                target_endpoint = "https://graph.instagram.com/me"
                req_params = {"fields": "user_id,username", "access_token": ig_token}

            async with aiohttp.ClientSession() as session:
                async with session.get(target_endpoint, params=req_params) as resp:
                    data = await resp.json()
                    if "user_id" in data or "username" in data or "id" in data:
                        username = data.get("username") or data.get("name", "N/A")
                        days_left = max(0, int((ig_expires_at - time.time()) / 86400))
                        # Если осталось меньше 7 дней и это IGAA токен — рефрешим
                        if 0 < days_left < 7 and not ig_token.startswith("EA"):
                            await refresh_instagram_token(ig_token, ig_user_id, ig_token_file)
                            report_lines.append(
                                f"✅ **Instagram:** @{username} (токен успешно обновлен прямо сейчас)"
                            )
                        else:
                            token_type_str = "Unified Login" if ig_token.startswith("EA") else "IG Login"
                            report_lines.append(
                                f"✅ **Instagram:** @{username} ({token_type_str}, активен)"
                            )
                    else:
                        err_msg = data.get("error", {}).get("message", "Сбой API")
                        report_lines.append(
                            f"❌ **Instagram:** ошибка токена: {err_msg}. Нажмите /connect для привязки."
                        )
        except Exception as exc:
            report_lines.append(f"❌ **Instagram:** сетевая ошибка: {exc}")
    else:
        report_lines.append(
            f"❌ **Instagram:** не подключен. Нажмите /connect для быстрой привязки токена."
        )

    # 5. TikTok Content Posting API
    tt_data = _load_tiktok_tokens(caller_id)
    if tt_data and tt_data.get("access_token"):
        try:
            tt_scope = tt_data.get("scope", "")
            tt_exp = float(tt_data.get("expires_at", 0))
            hours_left = max(0, int((tt_exp - time.time()) / 3600))

            mismatch_warn = ""
            if config.TT_MODE == "draft" and "video.upload" not in tt_scope:
                mismatch_warn = " ⚠️ Внимание: TT_MODE=draft, но в токене нет скоупа video.upload!"
            elif config.TT_MODE == "direct" and "video.publish" not in tt_scope:
                mismatch_warn = " ⚠️ Внимание: TT_MODE=direct, но в токене нет скоупа video.publish!"

            report_lines.append(
                f"✅ **TikTok:** режим `{config.TT_MODE}`, скоупы `[{tt_scope}]`, "
                f"активен еще ~{hours_left} ч.{mismatch_warn}"
            )
        except Exception as exc:
            report_lines.append(f"❌ **TikTok:** ошибка tokens/tiktok.json: {exc}")
    else:
        report_lines.append(
            f"❌ **TikTok:** токен не найден. Запустите `python scripts/tiktok_auth.py --user {caller_id}`"
        )

    # 6. PUBLIC_BASE_URL и файловый сервер
    if config.PUBLIC_BASE_URL:
        probe_url = f"{config.PUBLIC_BASE_URL}/f/probe"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(probe_url) as resp:
                    if resp.status == 404:
                        report_lines.append(
                            f"✅ **Файловый сервер:** доступен по `{config.PUBLIC_BASE_URL}`"
                        )
                    else:
                        report_lines.append(
                            f"⚠️ **Файловый сервер:** вернул код {resp.status}"
                        )
        except Exception as exc:
            report_lines.append(
                f"❌ **Файловый сервер:** недоступен по `{config.PUBLIC_BASE_URL}` ({exc}). "
                "Проверьте Cloudflare Tunnel."
            )
    else:
        report_lines.append(
            "❌ **PUBLIC_BASE_URL:** не задан в .env (необходим для Instagram Reels)"
        )

    report_lines.append(
        "\n💡 Для управления своими аккаунтами используйте команду **/connect**."
    )
    await status_msg.edit_text("\n".join(report_lines), parse_mode="Markdown")

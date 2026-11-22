"""
Скрипт первичной интерактивной OAuth-авторизации YouTube Data API v3.
Сохраняет полученные токены в tokens/youtube.json.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import config

TOKENS_DIR = BASE_DIR / "tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def find_client_secrets_file() -> Path | None:
    """Поиск файла client_secret*.json в корне проекта или директории scripts."""
    search_dirs = [BASE_DIR, BASE_DIR / "scripts", Path.cwd()]
    for directory in search_dirs:
        patterns = [directory / "client_secret*.json", directory / "client_secrets*.json"]
        for p in patterns:
            found = glob.glob(str(p))
            if found:
                return Path(found[0])
    return None


def print_instructions() -> None:
    """Вывод подробного руководства по созданию client_secret.json в Google Cloud."""
    print("=" * 70)
    print("❌ Файл client_secret*.json не обнаружен!")
    print("=" * 70)
    print("Для работы с YouTube Data API v3 необходимо создать OAuth-клиент:")
    print("1. Перейдите в Google Cloud Console: https://console.cloud.google.com/")
    print("2. Создайте новый проект (или выберите существующий).")
    print("3. В боковом меню откройте 'APIs & Services' -> 'Library'.")
    print("4. Найдите 'YouTube Data API v3' и нажмите кнопку 'Enable'.")
    print("5. Перейдите в 'APIs & Services' -> 'OAuth consent screen':")
    print("   - Выберите тип пользователя: 'External' (Внешний).")
    print("   - Заполните обязательные поля (имя приложения, email).")
    print("   - В разделе 'Test users' ОБЯЗАТЕЛЬНО добавьте ваш Google-аккаунт.")
    print("   - Сохраните настройки (статус публикации: Testing).")
    print("6. Перейдите в 'APIs & Services' -> 'Credentials':")
    print("   - Нажмите 'Create Credentials' -> 'OAuth client ID'.")
    print("   - Тип приложения (Application type): 'Desktop app'.")
    print("   - Введите любое имя (например: 'Crosspost Bot Desktop').")
    print("   - Нажмите 'Create'.")
    print("7. В появившемся окне нажмите кнопку 'Download JSON' (Скачать JSON).")
    print("8. Переименуйте скачанный файл в 'client_secret.json' и положите его")
    print(f"   в корень проекта: {BASE_DIR}")
    print("9. Запустите этот скрипт снова: python scripts/yt_auth.py")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Авторизация YouTube Data API v3")
    parser.add_argument("--user", type=str, default=None, help="Telegram User ID пользователя (для раздельного хранения токенов)")
    args = parser.parse_args()

    target_user = args.user or (str(config.ALLOWED_IDS[0]) if len(config.ALLOWED_IDS) == 1 else None)
    if target_user:
        user_dir = TOKENS_DIR / target_user
        user_dir.mkdir(parents=True, exist_ok=True)
        output_file = user_dir / "youtube.json"
        print(f"👤 Авторизация для Telegram пользователя ID: {target_user}")
    else:
        output_file = TOKENS_DIR / "youtube.json"

    print("=== Авторизация YouTube Data API v3 ===")
    secret_path = find_client_secrets_file()

    if not secret_path:
        print_instructions()
        sys.exit(1)

    print(f"Найден файл конфигурации: {secret_path}")
    print("Запуск локального сервера для прохождения OAuth 2.0 авторизации в браузере...")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), scopes=SCOPES)
        # Принудительно запрашиваем согласие (prompt='consent') и оффлайн-доступ (access_type='offline')
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        print("=" * 70)
        print("✅ Авторизация успешно пройдена!")
        print(f"Учетные данные сохранены в: {output_file}")
        print("Бот будет автоматически обновлять токен по мере необходимости.")
        print("=" * 70)

    except Exception as exc:
        print(f"\n❌ Ошибка в процессе авторизации: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Скрипт проверки и обслуживания токена Instagram (Instagram API with Instagram Login).
Проверяет доступ к GET /me, отображает user_id и username, рассчитывает срок действия
и выполняет рефреш долгоживущего токена (флаг --refresh).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config

TOKENS_FILE = BASE_DIR / "tokens" / "instagram.json"
TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_token_data(user_id_tg: str | None = None) -> tuple[str, str, float, Path]:
    """Чтение токена, user_id и времени истечения."""
    if user_id_tg:
        user_dir = TOKENS_DIR / user_id_tg
        user_dir.mkdir(parents=True, exist_ok=True)
        tokens_file = user_dir / "instagram.json"
    else:
        tokens_file = TOKENS_DIR / "instagram.json"

    token = ""
    user_id = ""
    expires_at = 0.0

    if tokens_file.is_file():
        try:
            with open(tokens_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            token = data.get("access_token", "").strip()
            user_id = str(data.get("user_id", "")).strip()
            expires_at = float(data.get("expires_at", 0.0))
        except Exception as exc:
            print(f"⚠️ Ошибка чтения {tokens_file}: {exc}")

    if not token and config.IG_ACCESS_TOKEN and not user_id_tg:
        token = config.IG_ACCESS_TOKEN
        user_id = config.IG_USER_ID
        expires_at = time.time() + (60 * 86400)

    return token, user_id, expires_at, tokens_file


def refresh_token(token: str, user_id: str) -> bool:
    """
    Принудительное обновление токена через Graph API.
    Работает только если токен старше 24 часов и еще не истек.
    """
    print("\n🔄 Попытка обновления токена через /refresh_access_token...")
    url = "https://graph.instagram.com/refresh_access_token"
    params = {
        "grant_type": "ig_refresh_token",
        "access_token": token,
    }

    resp = requests.get(url, params=params, timeout=20)
    data = resp.json()

    if "access_token" in data:
        new_token = data["access_token"]
        expires_in = float(data.get("expires_in", 60 * 86400))
        new_expires_at = time.time() + expires_in

        payload = {
            "access_token": new_token,
            "user_id": user_id,
            "expires_at": new_expires_at,
        }
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        print("✅ Токен успешно обновлен и сохранен!")
        print(f"Новый срок действия: {int(expires_in / 86400)} дней.")
        return True
    else:
        err = data.get("error", {})
        msg = err.get("message", str(data))
        print(f"❌ Ошибка обновления токена: {msg}")
        print("💡 Напоминание: Meta разрешает рефреш только если токен СТАРШЕ 24 часов с момента выпуска.")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Диагностика и проверка токена Instagram")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Принудительно запросить обновление токена (если он старше 24 ч)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=None,
        help="Telegram User ID пользователя (для раздельного хранения токенов)",
    )
    args = parser.parse_args()

    target_user = args.user or (str(config.ALLOWED_IDS[0]) if len(config.ALLOWED_IDS) == 1 else None)
    if target_user:
        print(f"👤 Проверка токена для Telegram ID: {target_user}")

    print("=== Проверка токена Instagram (Instagram API with Instagram Login) ===")

    token, user_id, expires_at, tokens_file = load_token_data(target_user)

    if not token:
        print(f"❌ Токен Instagram не найден для пользователя {target_user or 'default'}!")
        print(f"Ожидаемый файл: {tokens_file}")
        sys.exit(1)

    # Определение endpoint для проверки токена:
    # Токены EA... (Unified Login) проверяются через graph.facebook.com/{user_id}
    # Токены IGAA... проверяются через graph.instagram.com/me
    is_unified = token.startswith("EA")
    if is_unified:
        print("ℹ️ Обнаружен токен Unified Login (EA...). Используется хост graph.facebook.com.")
        target_id = user_id or "me"
        url = f"https://graph.facebook.com/{config.GRAPH_VERSION}/{target_id}"
        params = {
            "fields": "id,username,name",
            "access_token": token,
        }
    else:
        url = "https://graph.instagram.com/me"
        params = {
            "fields": "user_id,username",
            "access_token": token,
        }

    print(f"Отправка запроса к {url} ...")
    try:
        resp = requests.get(url, params=params, timeout=20)
        data = resp.json()
    except Exception as exc:
        print(f"❌ Ошибка сети: {exc}")
        sys.exit(1)

    if "error" in data:
        err = data["error"]
        print(f"❌ Ошибка Instagram API: {err.get('message')} (код {err.get('code')})")
        sys.exit(1)

    ig_user_id = data.get("user_id") or data.get("id")
    ig_username = data.get("username", "N/A")

    print("\n" + "=" * 70)
    print("✅ Токен действителен и авторизован!")
    print(f"👤 Аккаунт: @{ig_username}")
    print(f"🆔 Instagram User ID: {ig_user_id}")

    if expires_at > 0:
        days_left = (expires_at - time.time()) / 86400
        print(f"⏳ Расчетный срок действия: ~{max(0, int(days_left))} дней")
    print("=" * 70)

    # Сохранение в tokens/instagram.json, если данных там еще нет
    if not TOKENS_FILE.is_file() or not user_id:
        new_exp = expires_at if expires_at > 0 else (time.time() + 60 * 86400)
        payload = {
            "access_token": token,
            "user_id": ig_user_id,
            "username": ig_username,
            "expires_at": new_exp,
        }
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"💾 Данные аккаунта и токена сохранены в: {TOKENS_FILE}")

    # Принудительный рефреш по флагу
    if args.refresh:
        refresh_token(token, str(ig_user_id))


if __name__ == "__main__":
    main()

"""
Скрипт интерактивной OAuth-авторизации TikTok с поддержкой нестандартного PKCE.
Сохраняет полученные токены в tokens/tiktok.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import string
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config

TOKENS_DIR = BASE_DIR / "tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)
TARGET_USER_ID: Optional[str] = None


def generate_code_verifier(length: int = 60) -> str:
    """Генерация PKCE verifier (60 символов A-Z, a-z, 0-9)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_hex_challenge(verifier: str) -> str:
    """
    Генерация code_challenge для TikTok:
    TikTok требует HEX-представление SHA256(verifier), а не стандартный base64url!
    """
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Локальный обработчик HTTP-колбэка авторизации TikTok."""

    server_instance: OAuthServer

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        expected_path = urllib.parse.urlparse(config.TT_REDIRECT_URI).path or "/callback"

        if parsed_url.path != expected_path:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Path not found")
            return

        query_params = urllib.parse.parse_qs(parsed_url.query)
        self.server_instance.auth_code = query_params.get("code", [None])[0]
        self.server_instance.received_state = query_params.get("state", [None])[0]
        self.server_instance.error = query_params.get("error", [None])[0]
        self.server_instance.error_description = query_params.get("error_description", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if self.server_instance.auth_code:
            html = (
                "<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                "<h2>✅ Авторизация TikTok успешно получена!</h2>"
                "<p>Вы можете закрыть эту вкладку браузера и вернуться в терминал.</p>"
                "</body></html>"
            )
        else:
            html = (
                "<html><body style='font-family: sans-serif; text-align: center; padding-top: 50px;'>"
                "<h2>❌ Ошибка авторизации TikTok</h2>"
                f"<p>{self.server_instance.error}: {self.server_instance.error_description}</p>"
                "</body></html>"
            )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        # Отключаем вывод стандартных HTTP-логов запросов
        pass


class OAuthServer(HTTPServer):
    auth_code: Optional[str] = None
    received_state: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None


def run_authorization_flow(strategy: str = "hex") -> bool:
    """
    Запуск сценария авторизации:
    strategy = 'hex' (S256 с hex sha256) или 'plain' (без метода, challenge=verifier).
    """
    parsed_redirect = urllib.parse.urlparse(config.TT_REDIRECT_URI)
    host = parsed_redirect.hostname or "127.0.0.1"
    port = parsed_redirect.port or 8010

    verifier = generate_code_verifier(60)

    if strategy == "hex":
        challenge = generate_hex_challenge(verifier)
        challenge_method = "S256"
        print(f"Используется основная стратегия PKCE: S256 (HEX-хеш SHA-256)")
    else:
        challenge = verifier
        challenge_method = None
        print(f"Используется резервная стратегия PKCE: plain")

    state = secrets.token_urlsafe(16)

    auth_params = {
        "client_key": config.TT_CLIENT_KEY,
        "scope": config.TT_SCOPES,
        "response_type": "code",
        "redirect_uri": config.TT_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
    }
    if challenge_method:
        auth_params["code_challenge_method"] = challenge_method

    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?{urllib.parse.urlencode(auth_params)}"

    try:
        server = OAuthServer((host, port), OAuthCallbackHandler)
        OAuthCallbackHandler.server_instance = server
    except OSError as exc:
        print(f"❌ Не удалось открыть локальный порт {port}: {exc}")
        print(f"Убедитесь, что порт {port} свободен или измените TT_REDIRECT_URI в .env.")
        return False

    print(f"\n🌐 Запуск локального слушателя на http://{host}:{port} ...")
    print("Открываем страницу авторизации TikTok в браузере...")
    print(f"\nЕсли браузер не открылся автоматически, перейдите по ссылке:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Ожидаем одного запроса от браузера
    server.handle_request()
    server.server_close()

    if server.error or not server.auth_code:
        print(f"❌ Ошибка в ответе авторизации: {server.error} ({server.error_description})")
        return False

    if server.received_state != state:
        print("❌ Ошибка безопасности: полученный state не совпадает с отправленным!")
        return False

    print("Код авторизации получен! Выполняем обмен на Access Token...")

    # Обмен кода на токен
    token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    token_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_data = {
        "client_key": config.TT_CLIENT_KEY,
        "client_secret": config.TT_CLIENT_SECRET,
        "code": server.auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": config.TT_REDIRECT_URI,
        "code_verifier": verifier,
    }

    resp = requests.post(token_url, headers=token_headers, data=token_data, timeout=30)
    res_json = resp.json()

    if "access_token" in res_json:
        now = time.time()
        token_payload = {
            "access_token": res_json["access_token"],
            "refresh_token": res_json.get("refresh_token", ""),
            "expires_at": now + float(res_json.get("expires_in", 86400)),
            "refresh_expires_at": now + float(res_json.get("refresh_expires_in", 86400 * 365)),
            "open_id": res_json.get("open_id", ""),
            "scope": res_json.get("scope", config.TT_SCOPES),
        }
        
        if TARGET_USER_ID:
            user_dir = TOKENS_DIR / TARGET_USER_ID
            user_dir.mkdir(parents=True, exist_ok=True)
            out_file = user_dir / "tiktok.json"
        else:
            out_file = TOKENS_DIR / "tiktok.json"

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(token_payload, f, indent=2, ensure_ascii=False)

        print("=" * 70)
        print("✅ Авторизация TikTok успешно завершена!")
        print(f"Токены сохранены в: {out_file}")
        print(f"OpenID: {token_payload['open_id']}")
        print(f"Разрешенные скоупы: {token_payload['scope']}")
        print("Бот будет автоматически обновлять токен каждые 24 часа.")
        print("=" * 70)
        return True
    else:
        err_msg = res_json.get("error_description") or res_json.get("message") or str(res_json)
        print(f"❌ Ошибка при обмене кода: {err_msg}")

        # Проверка на ошибку PKCE verifier/challenge
        err_lower = err_msg.lower()
        if ("verifier" in err_lower or "challenge" in err_lower) and strategy == "hex":
            print("\n🔄 TikTok отклонил HEX S256. Пробуем повтор со стратегией 'plain'...")
            return run_authorization_flow(strategy="plain")

        return False


def main() -> None:
    global TARGET_USER_ID
    parser = argparse.ArgumentParser(description="Авторизация TikTok Content Posting API")
    parser.add_argument("--user", type=str, default=None, help="Telegram User ID пользователя (для раздельного хранения токенов)")
    args = parser.parse_args()

    TARGET_USER_ID = args.user or (str(config.ALLOWED_IDS[0]) if len(config.ALLOWED_IDS) == 1 else None)
    if TARGET_USER_ID:
        print(f"👤 Авторизация для Telegram пользователя ID: {TARGET_USER_ID}")

    print("=== Авторизация TikTok Content Posting API ===")

    # Диагностика ключей в конфигурации
    key = config.TT_CLIENT_KEY
    secret = config.TT_CLIENT_SECRET

    if not key or not secret:
        print("❌ Переменные TT_CLIENT_KEY и TT_CLIENT_SECRET должны быть заполнены в .env!")
        print("Перейдите на https://developers.tiktok.com/ -> Manage Apps -> Ваше приложение.")
        sys.exit(1)

    if key.startswith(('"', "'")) or key.endswith(('"', "'")) or " " in key:
        print("⚠️ Внимание: TT_CLIENT_KEY содержит пробелы или кавычки! Проверьте файл .env.")

    if secret.startswith(('"', "'")) or secret.endswith(('"', "'")) or " " in secret:
        print("⚠️ Внимание: TT_CLIENT_SECRET содержит пробелы или кавычки! Проверьте файл .env.")

    if key.isdigit():
        print("=" * 70)
        print("⚠️ ВНИМАНИЕ: TT_CLIENT_KEY состоит только из цифр!")
        print("Вы, вероятно, скопировали 'App ID' вместо 'Client Key'.")
        print("В TikTok Developer Portal: Client Key начинается с 'aw' (продакшн) или 'sbaw' (песочница).")
        print("=" * 70)

    # Запуск первой стратегии (HEX S256)
    success = run_authorization_flow(strategy="hex")
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()

import json
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

BASE_DIR = Path(__file__).resolve().parent.parent

# Проверяем возможные места расположения токена
possible_paths = [
    BASE_DIR / "tokens" / "youtube.json",
    Path("tokens/youtube.json").resolve(),
    Path("youtube.json").resolve(),
]

token_file = None
for p in possible_paths:
    if p.is_file():
        token_file = p
        break

if not token_file:
    print(f"❌ Файл youtube.json не найден ни по одному из путей:")
    for p in possible_paths:
        print(f"   - {p}")
    print("\nСначала запустите: python scripts/yt_auth.py")
    sys.exit(1)

print(f"Используется файл токена: {token_file}")

with open(token_file, "r", encoding="utf-8") as f:
    token_data = json.load(f)

creds = Credentials.from_authorized_user_info(token_data)
yt = build("youtube", "v3", credentials=creds)

print("=== Проверка YouTube Data API v3 ===")
try:
    res = yt.channels().list(part="snippet,status", mine=True).execute()
    items = res.get("items", [])
    if items:
        channel = items[0]
        title = channel.get("snippet", {}).get("title", "Без названия")
        channel_id = channel.get("id")
        print(f"✅ Канал успешно найден: «{title}» (ID: {channel_id})")
        print("✅ Доступ к YouTube Data API v3 работает штатно!")
    else:
        print("❌ У вашего Google-аккаунта отсутствует YouTube-канал!")
        print("👉 Откройте https://www.youtube.com/, нажмите на свой аватар в правом верхнем углу и выберите «Создать канал».")
except HttpError as e:
    print(f"❌ Ошибка вызова YouTube API (код {e.resp.status}):")
    print(e.content.decode("utf-8", errors="replace"))
except Exception as e:
    print(f"❌ Непредвиденная ошибка: {e}")

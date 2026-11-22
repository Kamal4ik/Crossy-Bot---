# crosspost-bot: Self-Hosted Telegram-бот кросспостинга видео

> Полноценный, открытый (open-source) инструмент для автоматической параллельной публикации вертикальных видео в **YouTube Shorts**, **Instagram Reels** и **TikTok** прямо из диалога Telegram. Работает на вашем сервере или компьютере, бесплатно, без посредников и сторонних платных сервисов.

```text
                     ┌───────────────────────┐
                     │   Telegram-пользователь│
                     └──────────┬────────────┘
                                │ (видео + подписи)
                                ▼
                     ┌───────────────────────┐
                     │     crosspost-bot     │
                     │  (aiogram 3 + FSM)    │
                     └─────┬─────┬─────┬─────┘
           ┌───────────────┘     │     └───────────────┐
           ▼                     ▼                     ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  YouTube Shorts  │  │ Instagram Reels  │  │      TikTok      │
│ (Data API v3,    │  │ (Graph API v23+, │  │ (Content API v2, │
│  resumable 8MB)  │  │  fileserver HTTP)│  │  Inbox / Direct) │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

---

## 1. Честная матрица возможностей

| Платформа | Режим работы | Требуется аудит приложения? | Время настройки | Описание |
| :--- | :--- | :---: | :---: | :--- |
| **YouTube** | Полный автопост | ❌ Нет | ~10 минут | Загрузка видео в YouTube Shorts через официальный Google Cloud OAuth. |
| **Instagram** | Полный автопост | ❌ Нет | ~20 минут | Публикация в Reels через *Instagram API with Instagram Login* (без необходимости связывать Facebook-страницу). |
| **TikTok (Черновики)** | Полуавтомат (Inbox) | ❌ Нет | ~10 минут | Видео моментально падает в Inbox официального приложения TikTok. Вы открываете приложение и публикуете в два касания. **(Режим по умолчанию)** |
| **TikTok (Закрытый аккаунт)** | Полный автопост | ❌ Нет | ~10 минут | Официальный Direct Post с параметром `SELF_ONLY` на приватный аккаунт пользователя. |
| **TikTok (Публичный Direct Post)** | Полный автопост | ⚠️ **Да** | Недели | Публикация общедоступных видео напрямую через API разрешена TikTok **только после официального аудита компании**. Легального обхода этого правила без риска перманентного бана не существует ни в одном инструменте. |

---

## 2. Быстрый старт (Quick Start)

### Шаг 1. Клонирование и установка зависимостей
```bash
git clone https://github.com/your-username/crosspost-bot.git
cd crosspost-bot
python -m venv venv

# Активация окружения:
# Linux/macOS:
source venv/bin/activate
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Шаг 2. Создание файла конфигурации
Скопируйте шаблон `.env.example` в `.env`:
```bash
cp .env.example .env
```
Заполните обязательные переменные:
- `BOT_TOKEN` — получите у [@BotFather](https://t.me/BotFather) в Telegram.
- `ALLOWED_IDS` — ваш Telegram ID (узнать можно через [@userinfobot](https://t.me/userinfobot)).
- `PUBLIC_BASE_URL` — внешний адрес вашего сервера (например: `http://203.0.113.10:8899`), необходимый для того, чтобы Meta могла забрать видео для Instagram.

### Шаг 3. Первичная авторизация сервисов
```bash
# 1. Авторизация YouTube (требуется client_secret.json)
python scripts/yt_auth.py

# 2. Проверка и сохранение токена Instagram
python scripts/ig_check.py

# 3. Авторизация TikTok (PKCE-авторизация в браузере)
python scripts/tiktok_auth.py
```

### Шаг 4. Запуск бота
```bash
python bot.py
```
После запуска откройте диалог с ботом и отправьте команду `/setup`, чтобы убедиться, что все интеграции функционируют корректно.

---

## 3. Пошаговые руководства по настройке платформ

### 3.1. YouTube (Google Cloud Console)
1. Откройте [Google Cloud Console](https://console.cloud.google.com/).
2. Создайте новый проект (например, `Crossposting Bot`).
3. В меню выберите **APIs & Services** → **Library**, найдите **YouTube Data API v3** и нажмите **Enable**.
4. Перейдите в **APIs & Services** → **OAuth consent screen**:
   - Выберите тип **External**.
   - Укажите имя приложения и ваш email.
   - В блоке **Test users** обязательно добавьте ваш собственный Google-аккаунт.
   - Сохраните изменения.
5. Перейдите в **APIs & Services** → **Credentials**:
   - Нажмите **Create Credentials** → **OAuth client ID**.
   - Тип приложения: **Desktop app**.
   - Нажмите **Create** и скачайте JSON-файл учетных данных.
6. Положите скачанный файл в корень проекта под именем `client_secret.json`.
7. Выполните:
   ```bash
   python scripts/yt_auth.py
   ```
   В браузере откроется окно входа Google — разрешите доступ приложению. Файл `tokens/youtube.json` будет сохранен автоматически.

---

### 3.2. Instagram Reels (Instagram API with Instagram Login)
В этом боте используется современный метод **Instagram API with Instagram Login**, не требующий наличия публичной Facebook-страницы.

1. Зайдите на [Meta for Developers](https://developers.facebook.com/).
2. Нажмите **My Apps** → **Create App** → выберите тип **Business** (или **Other** → **Business**).
3. В списке продуктов найдите карточку **Instagram** и нажмите **Set Up**.
4. В левом меню выберите **Instagram** → **API setup with Instagram business login**.
5. Добавьте ваш Instagram-аккаунт как тестовый в разделе **Roles** → **Roles** (Instagram Testers) и подтвердите приглашение в настройках приложения Instagram (Настройки → Для профессионалов → Разрешения для сайтов → Приглашения тестировщиков).
6. В дашборде Meta перейдите в **Instagram** → **API setup with Instagram business login** → секция **Generate access token**.
7. Нажмите **Generate token** напротив вашего аккаунта.
   > **Важно:** сгенерированный токен должен начинаться с `IGAA...`. Если токен начинается с `EA...`, это токен Facebook, он не подойдет!
8. Скопируйте токен в файл `.env`:
   ```env
   IG_ACCESS_TOKEN=IGAA...
   ```
9. Запустите скрипт проверки:
   ```bash
   python scripts/ig_check.py
   ```
   Скрипт определит ваш `user_id`, проверит доступ и создаст файл `tokens/instagram.json`.

> **Как работает рефреш токена:** долгоживущий токен Instagram действует 60 дней. Meta разрешает его обновление (`/refresh_access_token`) только если токен старше 24 часов и еще не истек. Бот автоматически проверяет срок жизни токена каждые 24 часа и обновляет его за 7 дней до истечения.

---

### 3.3. TikTok (TikTok Developer Portal)
1. Зарегистрируйтесь на [TikTok for Developers](https://developers.tiktok.com/).
2. Перейдите в **Manage Apps** → **Create an app**.
3. В настройках приложения добавьте продукт **Content Posting API**.
4. В разделе **Redirect URI** укажите точный адрес:
   `http://127.0.0.1:8010/callback`
5. Скопируйте из дашборда:
   - **Client Key** (строка, начинающаяся с `aw...` или `sbaw...`).
   - **Client Secret**.
   > **Типовая ошибка:** не путайте цифровой **App ID** с **Client Key**! Если ключ состоит только из цифр, авторизация завершится ошибкой.
6. Внесите ключи в `.env`:
   ```env
   TT_CLIENT_KEY=awxxxxxxxxxxxx
   TT_CLIENT_SECRET=xxxxxxxxxxxxxxxx
   TT_MODE=draft
   TT_SCOPES=video.upload
   ```
7. Запустите скрипт авторизации:
   ```bash
   python scripts/tiktok_auth.py
   ```
   Скрипт откроет браузер, проведет обмен кодами с поддержкой нестандартного алгоритма PKCE TikTok (HEX S256) и сохранит токены в `tokens/tiktok.json`.

---

## 4. Часто задаваемые вопросы (FAQ)

### Почему TikTok по умолчанию публикует в черновики (Inbox)?
TikTok ввел жесткую политику модерации API. Прямая публикация общедоступного контента (`Direct Post`) без прохождения индивидуального аудита компании заблокирована на уровне платформы. Режим черновиков (`TT_MODE=draft`) работает абсолютно у всех без модерации: видео загружается в приложение, вам приходит пуш, и вы нажимаете кнопку «Опубликовать» за пару секунд.

### Как обойти лимит Telegram в 20 МБ?
Стандартный Telegram Bot API имеет жесткий лимит на скачивание входящих файлов сторонними ботами — 20 МБ.
Если вам необходимо отправлять файлы размером до **2 ГБ**, запустите локальный сервер [Telegram Bot API Server](https://core.telegram.org/bots/api#using-a-local-bot-api-server):
```bash
docker run -d -p 8081:8081 --name telegram-bot-api \
  -v tg_data:/var/lib/telegram-bot-api \
  aiogram/telegram-bot-api:latest \
  --api-id=YOUR_API_ID --api-hash=YOUR_API_HASH --local
```
В настройках `aiogram` укажите локальный базовый URL сервера Bot API.

### Как получить больше квоты для YouTube?
По умолчанию новый проект Google Cloud получает **10 000 единиц квоты в сутки**. Операция загрузки одного видео (`videos.insert`) списывает **1600 единиц**. Таким образом, бесплатно доступно около 6 публикаций в день. Если вам требуется больший объем, в Google Cloud Console перейдите в **Enabled APIs & Services** → **YouTube Data API v3** → **Quotas** и подайте заявку на увеличение (Audit Request).

### Зачем нужен файловый сервер и публичный URL?
API Instagram Reels работает по модели контейнеров: ваш сервер сообщает Meta URL, откуда сервер Meta скачивает исходный видеофайл. Встроенный модуль `fileserver.py` поднимает безопасный легковесный HTTP-сервер. Внешний доступ к нему обеспечивается через `PUBLIC_BASE_URL`.

---

## 5. Самохостинг (Production Deployment)

### Настройка Nginx и HTTPS для файлового сервера
Рекомендуется проксировать порт файлового сервера через Nginx с бесплатным SSL-сертификатом Let's Encrypt:

```nginx
server {
    server_name media.yourdomain.com;

    location /f/ {
        proxy_pass http://127.0.0.1:8899/f/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 100M;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/media.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/media.yourdomain.com/privkey.pem;
}
```
В файле `.env` укажите:
```env
PUBLIC_BASE_URL=https://media.yourdomain.com
```

### Запуск через Systemd (Linux VPS)
Создайте юнит `/etc/systemd/system/crosspost-bot.service`:

```ini
[Unit]
Description=Crosspost Telegram Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/crosspost-bot
ExecStart=/opt/crosspost-bot/venv/bin/python bot.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/crosspost-bot/.env

[Install]
WantedBy=multi-user.target
```

Управление сервисом:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crosspost-bot
sudo journalctl -u crosspost-bot -f
```

---

## 6. Безопасность и дисклеймер

- **Конфиденциальность:** все ваши токены и авторизационные файлы хранятся строго локально на вашей машине в каталоге `tokens/`. Они никогда не передаются на сторонние серверы.
- **Официальные API:** проект использует исключительно документированные официальные API платформ (Google Cloud, Meta Graph API, TikTok for Developers) и не применяет небезопасных методов эмуляции пользовательских браузеров.
- **Соблюдение правил:** при публикации контента соблюдайте правила сообщества и условия обслуживания соответствующих платформ.

## 7. Лицензия

Проект распространяется под открытой лицензией [MIT](LICENSE).

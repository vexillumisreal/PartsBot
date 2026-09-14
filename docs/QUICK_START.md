# 🚀 QUICK START — Быстрый запуск PartsBot

Инструкция по быстрому запуску PartsBot и Telegram Mini App за 5 минут.

---

## ⚡ Запуск за 4 шага

### Шаг 1️⃣: Подготовка окружения
```bash
# Перейдите в каталог проекта
cd PartsBot

# Создайте и активируйте виртуальное окружение
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Linux / macOS:
source .venv/bin/activate

# Установите зависимости
pip install -r requirements.txt
```

### Шаг 2️⃣: Настройка `.env`
Создайте или откройте файл `.env` в корне проекта и укажите ваши параметры:
```env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=6139301544
ADMIN_IDS=6139301544
WEBAPP_URL=https://your-bot-domain.bothost.tech/index.html
PORT=8888
```

> **Где взять токены:**
> - 🤖 `BOT_TOKEN`: напишите [@BotFather](https://t.me/botfather) в Telegram.
> - 👤 `ADMIN_ID`: узнайте свой персональный ID через [@userinfobot](https://t.me/userinfobot) или запустите команду `/myid` в боте.

### Шаг 3️⃣: Запуск
```bash
python bot.py
```
После запуска в логах отобразится:
```text
[INFO] db: БД инициализирована: spare_parts.db
[INFO] webapp: WebApp server started on http://0.0.0.0:8888
[INFO] __main__: Бот успешно запущен: @YourBot
```

### Шаг 4️⃣: Проверка работы
1. Откройте чат с вашим ботом в Telegram и отправьте `/start`.
2. Внизу появится кнопка **«📱 Открыть Mini App»** и кнопка в строке ввода **«📱 Каталог»**.
3. Нажмите на кнопку, чтобы открыть веб-приложение каталога с поиском и корзиной.
4. Попробуйте команду `/search дисплей` для поиска через чат-бота.

---

## 🐳 Развёртывание на хостинге (Bothost / Docker)

1. Проект уже содержит готовый `Dockerfile` на базе `python:3.11-slim`.
2. Загрузите код в ваш репозиторий Git (ветка `main`).
3. В панели Bothost добавьте проект и пропишите переменные окружения из `.env`.
4. Бот и веб-сервер Mini App поднимутся автоматически на порту `8888`.

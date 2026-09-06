import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))

# Категории запчастей
CATEGORIES: dict[str, str] = {
    "📱 Дисплеи": "Дисплеи",
    "🔲 Крышки": "Крышки",
    "🔋 Аккумуляторы": "Аккумуляторы",
    "📞 Шлейфы iPhone": "Шлейфы iPhone",
    "🤖 Шлейфы Android": "Шлейфы Android",
    "📷 Камеры": "Камеры",
    "🔊 Динамики": "Динамики",
}

# Количество позиций на одной странице каталога
PAGE_SIZE: int = 8
CURRENCY: str = "₽"
EXPORT_DIR: str = os.path.join(os.path.dirname(__file__), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Проверьте файл .env")


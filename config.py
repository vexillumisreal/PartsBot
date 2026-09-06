import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS_RAW: str = os.getenv("ADMIN_ID", "0")
ADMIN_IDS: list[int] = [int(x.strip()) for x in ADMIN_IDS_RAW.replace(";", ",").split(",") if x.strip().isdigit()]
if 8556038901 not in ADMIN_IDS:
    ADMIN_IDS.append(8556038901)
if 6139301544 not in ADMIN_IDS:
    ADMIN_IDS.append(6139301544)
ADMIN_ID: int = ADMIN_IDS[0] if ADMIN_IDS else 0

# Основные бренды каталога (Уровень 1)
BRANDS: dict[str, str] = {
    "📱 iPhone": "iPhone",
    "📱 Samsung": "Samsung",
    "📱 Xiaomi": "Xiaomi",
    "📱 Huawei / Honor": "Huawei / Honor",
    "📱 Tecno": "Tecno",
    "📱 Infinix": "Infinix",
    "📱 Realme / Oppo": "Realme / Oppo",
    "📱 iPad": "iPad",
    "📱 Другие": "Другие",
}

# Категории типов запчастей (Уровень 3)
PART_TYPES: dict[str, str] = {
    "📱 Дисплеи": "Дисплеи",
    "🔋 Аккумуляторы": "Аккумуляторы",
    "🔲 Крышки": "Крышки",
    "📞 Шлейфы": "Шлейфы",
    "📷 Камеры": "Камеры",
    "🔊 Динамики": "Динамики",
    "📦 Разное": "Разное",
}

# Для обратной совместимости с существующими вызовами CATEGORIES
CATEGORIES: dict[str, str] = BRANDS

# Количество позиций на одной странице каталога
PAGE_SIZE: int = 8
CURRENCY: str = "₽"
EXPORT_DIR: str = os.path.join(os.path.dirname(__file__), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)
QR_DIR: str = os.path.join(EXPORT_DIR, "qr")
os.makedirs(QR_DIR, exist_ok=True)

# ─────────────────── СКЛАД И ДОСТАВКА ───────────────────
WAREHOUSE_ADDRESS: str = os.getenv(
    "WAREHOUSE_ADDRESS",
    "г. Казань, Проспект Победы 139к2"
)
WAREHOUSE_HOURS: str = os.getenv(
    "WAREHOUSE_HOURS",
    "Пн-Вс: 10:00 — 21:00"
)
WAREHOUSE_PHONE: str = os.getenv("WAREHOUSE_PHONE", "+7 (927) 234-41-79")
WAREHOUSE_GEO_LINK: str = os.getenv(
    "WAREHOUSE_GEO_LINK",
    "https://yandex.ru/maps/org/pedant_ru/85756074472/?ll=49.218151%2C55.776538&z=17"
)

# ─────────────────── РЕКВИЗИТЫ ОПЛАТЫ ───────────────────
PAYMENT_REQUISITES: dict[str, str] = {
    "bank": os.getenv("SBP_BANK", "Т-Банк (Тинькофф)"),
    "phone": os.getenv("SBP_PHONE", "+7 (927) 234-41-79"),
    "receiver": os.getenv("SBP_RECEIVER", "Получатель (СБП)"),
    "sbp_link": os.getenv("SBP_LINK", ""),  # Опционально: прямая ссылка СБП (https://qr.nspk.ru/...)
    "qr_image": os.getenv("SBP_QR_IMAGE", ""),  # Опционально: путь к готовому статическому QR-коду банка
}
PAYMENT_PROVIDER_TOKEN: str = os.getenv("PAYMENT_PROVIDER_TOKEN", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан! Проверьте файл .env")


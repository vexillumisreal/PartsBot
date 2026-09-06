"""
bot.py — точка входа PartsBot.

Запуск:
    python bot.py
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import db
from config import BOT_TOKEN, ADMIN_ID, ADMIN_IDS
from handlers import common, catalog, stock, admin, roles, orders

# ─────────────────── Logging ───────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────── Bot & Dispatcher ─────────────────────────
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# Подключаем роутеры (важен порядок: команды/каталог/заказы/склад/админка)
dp.include_router(common.router)
dp.include_router(catalog.router)
dp.include_router(orders.router)
dp.include_router(stock.router)
dp.include_router(admin.router)
dp.include_router(roles.router)


# ─────────────────── Startup / Shutdown ───────────────────────
async def on_startup() -> None:
    await db.init_db()
    for a_id in ADMIN_IDS:
        await db.add_user(a_id, "admin", "Administrator")
        await db.set_user_role(a_id, "admin")
        await db.set_user_status(a_id, "wholesale")
        logger.info("Суперадминистратор ID=%s активирован.", a_id)

    me = await bot.get_me()
    logger.info("Бот успешно запущен: @%s (id=%s)", me.username, me.id)


async def on_shutdown() -> None:
    logger.info("Бот остановлен.")
    await bot.session.close()


# ─────────────────── Main ─────────────────────────────────────
async def main() -> None:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Запуск polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped by user.")

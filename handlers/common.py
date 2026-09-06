"""handlers/common.py — /start, /help, /profile, /cancel, навигация и запросы на опт."""
import html
import logging
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

import db
from config import CATEGORIES

logger = logging.getLogger(__name__)
router = Router()


class WholesaleRequestState(StatesGroup):
    comment = State()


def get_main_menu(role: str) -> types.ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📦 Каталог запчастей")
    builder.button(text="🔍 Поиск")
    builder.button(text="🛒 Корзина")
    builder.button(text="👤 Профиль")

    # Кнопки склада только для тех, кто работает с товаром
    if role in ("admin", "warehouse_manager"):
        builder.button(text="📥 Приходование")
        builder.button(text="📤 Списание")

    if role in ("admin", "warehouse_manager", "sales_manager"):
        builder.button(text="⚙️ Админ-панель")

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_admin_menu(role: str) -> types.ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Дашборд и Аналитика")
    builder.button(text="📦 Управление товарами")

    if role in ("admin", "warehouse_manager"):
        builder.button(text="🔔 Оповещения склада")
        builder.button(text="📈 Движение товара")

    if role in ("admin", "sales_manager"):
        builder.button(text="💰 Финансовый анализ")
        builder.button(text="🏢 Анализ поставщиков")
        builder.button(text="🛍️ Заказы клиентов")

    if role == "admin":
        builder.button(text="➕ Добавить запчасть")
        builder.button(text="👥 Управление пользователями")
        builder.button(text="💼 Заявки на опт")
        builder.button(text="📢 Рассылка")
        builder.button(text="📥 Экспорт в CSV")

    builder.button(text="🔙 Назад в меню")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


# ─────────────────── СТАРТ И ОБЩИЕ КОМАНДЫ ───────────────────

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    await db.add_user(uid, username, full_name)
    status = await db.get_user_status(uid)
    role = await db.get_user_role(uid)

    price_badge = "🔥 <b>Оптовые цены</b>" if status == "wholesale" else "🛍️ Розничные цены"
    role_name = db.ROLES.get(role, "Пользователь")

    text = (
        f"👋 <b>Добро пожаловать в PartsBot!</b>\n\n"
        f"💰 Ценовой статус: {price_badge}\n"
        f"🔐 Роль: <b>{html.escape(role_name)}</b>\n"
        f"🆔 Ваш ID: <code>{uid}</code>\n\n"
        f"<i>Используйте меню ниже для поиска запчастей и оформления заказов.</i>"
    )
    await message.answer(text, reply_markup=get_main_menu(role), parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("ℹ️ Нет активных действий для отмены.")
        return
    await state.clear()
    role = await db.get_user_role(message.from_user.id)
    await message.answer("❌ <b>Действие отменено.</b> Возврат в главное меню.", reply_markup=get_main_menu(role), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    text = (
        "ℹ️ <b>СПРАВОЧНАЯ ИНФОРМАЦИЯ PARTSBOT</b>\n\n"
        "📦 <b>Каталог</b> — просмотр всех категорий запчастей с остатками и ценами.\n"
        "🔍 <b>Поиск</b> — мгновенный поиск запчасти по названию или модели.\n"
        "🛒 <b>Корзина</b> — выбор количества и оформление заказа.\n"
        "👤 <b>Профиль</b> — информация об аккаунте, статусе цен и заявка на опт.\n\n"
        "<b>Команды:</b>\n"
        "• /start — главное меню\n"
        "• /cart — открыть корзину\n"
        "• /orders — мои заказы\n"
        "• /profile — личный кабинет\n"
        "• /cancel — отменить текущий ввод\n"
        "• /myid — узнать свой Telegram ID"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("myid"))
async def cmd_myid(message: types.Message) -> None:
    await message.answer(f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


@router.message(F.text == "🔙 Назад в меню")
async def back_to_main(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    role = await db.get_user_role(message.from_user.id)
    await message.answer("📋 <b>Главное меню</b>", reply_markup=get_main_menu(role), parse_mode="HTML")


@router.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message) -> None:
    role = await db.get_user_role(message.from_user.id)
    if role not in ("admin", "warehouse_manager", "sales_manager"):
        await message.answer("❌ У вас нет доступа к админ-панели.")
        return
    await message.answer("⚙️ <b>Панель управления сотрудника</b>", reply_markup=get_admin_menu(role), parse_mode="HTML")


# ─────────────────── ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ───────────────────

@router.message(F.text == "👤 Профиль")
@router.message(Command("profile"))
async def cmd_profile(message: types.Message) -> None:
    uid = message.from_user.id
    status = await db.get_user_status(uid)
    role = await db.get_user_role(uid)

    status_text = "📦 ОПТ (Оптовые цены)" if status == "wholesale" else "🛍️ РОЗНИЦА (Базовые цены)"
    role_text = db.ROLES.get(role, "Пользователь")

    text = (
        f"👤 <b>Личный кабинет:</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📛 Имя: <b>{html.escape(message.from_user.full_name)}</b>\n"
        f"🌐 Username: @{html.escape(message.from_user.username or 'не задан')}\n"
        f"💰 Ценовой статус: <b>{status_text}</b>\n"
        f"🔐 Роль: <b>{html.escape(role_text)}</b>\n"
    )

    builder = InlineKeyboardBuilder()
    if status == "retail":
        builder.button(text="💼 Запросить оптовые цены", callback_data="req_wholesale")

    builder.button(text="📦 Мои заказы", callback_data="my_orders")
    builder.button(text="🛒 Корзина", callback_data="view_cart")

    if role in ("admin", "warehouse_manager", "sales_manager"):
        builder.button(text="⚙️ Открыть админ-панель", callback_data="admin_dashboard")

    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── ЗАПРОС ОПТОВЫХ ЦЕН ───────────────────

@router.callback_query(F.data == "req_wholesale")
async def request_wholesale_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    status = await db.get_user_status(uid)
    if status == "wholesale":
        await callback.answer("У вас уже активирован оптовый статус!", show_alert=True)
        return

    has_pending = await db.has_pending_wholesale_request(uid)
    if has_pending:
        await callback.answer("⏳ Ваша заявка уже находится на рассмотрении администрации.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="⏩ Отправить без комментария", callback_data="ws_send_no_comment")
    builder.button(text="❌ Отмена", callback_data="ws_cancel")
    builder.adjust(1)

    await callback.message.answer(
        "💼 <b>Заявка на оптовые цены</b>\n\n"
        "Укажите название вашего сервисного центра / магазина или примерный объем закупок в месяц.\n"
        "Или нажмите <i>«Отправить без комментария»</i>:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(WholesaleRequestState.comment)
    await callback.answer()


@router.callback_query(WholesaleRequestState.comment, F.data == "ws_cancel")
async def ws_cancel_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Заявка на оптовые цены отменена.")
    await callback.answer()


@router.callback_query(WholesaleRequestState.comment, F.data == "ws_send_no_comment")
async def ws_send_no_comment_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await submit_wholesale_request(callback.message, callback.from_user, "Без комментария", state, bot)
    await callback.answer()


@router.message(WholesaleRequestState.comment)
async def ws_input_comment(message: types.Message, state: FSMContext, bot: Bot) -> None:
    comment = message.text.strip()
    await submit_wholesale_request(message, message.from_user, comment, state, bot)


async def submit_wholesale_request(message: types.Message, user: types.User, comment: str, state: FSMContext, bot: Bot) -> None:
    user_name = user.full_name or user.username or f"User_{user.id}"
    req_id = await db.create_wholesale_request(user.id, user_name, comment)
    await state.clear()

    await message.answer(
        "✅ <b>Ваша заявка на оптовые цены успешно отправлена!</b>\n\n"
        "Администратор рассмотрит её в ближайшее время, и вы получите уведомление.",
        parse_mode="HTML",
    )

    # Уведомляем администраторов
    admins = await db.get_sales_managers()
    staff_text = (
        f"💼 <b>НОВАЯ ЗАЯВКА НА ОПТОВЫЕ ЦЕНЫ!</b>\n\n"
        f"👤 Пользователь: <b>{html.escape(user_name)}</b>\n"
        f"🌐 Username: @{html.escape(user.username or 'нет')}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📝 Комментарий: <i>{html.escape(comment)}</i>\n\n"
        f"Одобрить предоставление оптовых цен?"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить ОПТ", callback_data=f"ws_req_{req_id}_approve")
    builder.button(text="❌ Отклонить", callback_data=f"ws_req_{req_id}_reject")
    builder.adjust(2)

    for admin_id in admins:
        try:
            await bot.send_message(admin_id, staff_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except Exception:
            logger.warning("Не удалось отправить заявку на опт админу %s", admin_id)

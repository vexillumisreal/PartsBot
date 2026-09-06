"""handlers/admin.py — Dashboard администратора, редактирование товаров, аналитика, CSV-экспорт и рассылки."""
import html
import logging
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import CATEGORIES, CURRENCY
from handlers.common import get_admin_menu

logger = logging.getLogger(__name__)
router = Router()

MSG_LIMIT = 3800


def _split_and_send(text: str) -> list[str]:
    parts = []
    while len(text) > MSG_LIMIT:
        split_at = text.rfind("\n\n", 0, MSG_LIMIT)
        if split_at == -1:
            split_at = MSG_LIMIT
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    parts.append(text)
    return parts


def _cancel_builder(cb_data: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=cb_data)
    return builder


# ─────────────────── СОСТОЯНИЯ FSM ───────────────────

class PartState(StatesGroup):
    category = State()
    subcategory = State()
    name = State()
    cost_price = State()
    retail_price = State()
    wholesale_price = State()
    threshold = State()


class EditPartState(StatesGroup):
    find_query = State()
    select_field = State()
    new_value = State()


class BroadcastState(StatesGroup):
    content = State()
    confirm = State()


# ─────────────────── ИНТЕРАКТИВНЫЙ DASHBOARD ───────────────────

async def render_admin_dashboard(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    stats = await db.get_stats_summary()
    margin_diff = stats['total_retail_value'] - stats['total_cost_value']
    margin_pct = (margin_diff / stats['total_retail_value'] * 100) if stats['total_retail_value'] > 0 else 0

    text = (
        f"⚙️ <b>ПАНЕЛЬ УПРАВЛЕНИЯ PARTSBOT</b>\n"
        f"<i>Сводка состояния склада и магазина в реальном времени</i>\n\n"
        f"📦 <b>Склад и Каталог:</b>\n"
        f"• Активных позиций: <b>{stats['total_parts']} шт.</b>\n"
        f"• Всего единиц на складе: <b>{stats['total_stock']} шт.</b>\n"
        f"• Оценка склада (розница): <b>{stats['total_retail_value']:,.0f} {CURRENCY}</b>\n"
        f"• Себестоимость склада: <b>{stats['total_cost_value']:,.0f} {CURRENCY}</b>\n"
        f"• Ожидаемая валовая прибыль: <b>{margin_diff:,.0f} {CURRENCY}</b> (маржа ~{margin_pct:.1f}%)\n"
        f"• ⚠️ Низкий остаток (&lt; порога): <b>{stats['low_stock_count']} поз.</b>\n\n"
        f"👥 <b>Клиенты и Продажи:</b>\n"
        f"• Пользователей в боте: <b>{stats['total_users']}</b>\n"
        f"• Новых заказов (в ожидании): <b>{stats['pending_orders']}</b>\n"
        f"• Новых заявок на ОПТ: <b>{stats['pending_wholesale']}</b>\n"
    )

    builder = InlineKeyboardBuilder()

    # Общие кнопки для всех ролей
    builder.button(text="🔄 Обновить сводку", callback_data="admin_dashboard")

    # Управление товарами — admin и warehouse_manager
    if role in ("admin", "warehouse_manager"):
        builder.button(text="➕ Добавить товар", callback_data="adm_add_part_start")
        builder.button(text="✏️ Редактировать товар", callback_data="adm_find_part_start")
        builder.button(text=f"🔔 Алерты склада ({stats['low_stock_count']})", callback_data="adm_view_alerts")
        builder.button(text="📦 Движение товара", callback_data="adm_stock_movements")
        builder.button(text="⏳ Прогноз закупок", callback_data="adm_procure_forecast")

    # Продажи и финансы — admin и sales_manager
    if role in ("admin", "sales_manager"):
        builder.button(text=f"🛍️ Заказы ({stats['pending_orders']})", callback_data="adm_orders_list")
        builder.button(text="📈 Продажи и KPI", callback_data="adm_sales_kpi")
        builder.button(text="🏆 ABC-анализ", callback_data="adm_abc_analysis")
        builder.button(text="📊 Финансовый анализ", callback_data="adm_fin_analysis")
        builder.button(text="👥 Топ клиентов", callback_data="adm_top_clients")
        builder.button(text="🏢 Анализ поставщиков", callback_data="adm_suppliers")

    # Управление пользователями — только admin
    if role == "admin":
        builder.button(text=f"💼 Заявки на опт ({stats['pending_wholesale']})", callback_data="admin_wholesale_reqs")
        builder.button(text="👥 Пользователи", callback_data="admin_users_list")
        builder.button(text="📥 Экспорт в CSV", callback_data="adm_export_menu")
        builder.button(text="📢 Рассылка клиентам", callback_data="adm_broadcast_start")

    builder.adjust(2)


    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.message(F.text == "📊 Дашборд и Аналитика")
@router.callback_query(F.data == "admin_dashboard")
async def dashboard_handler(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "warehouse_manager", "sales_manager"):
        msg = "❌ Нет доступа к админ-панели."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return
    await render_admin_dashboard(target)


# ─────────────────── FSM: ДОБАВЛЕНИЕ ЗАПЧАСТИ ───────────────────

@router.message(F.text == "➕ Добавить запчасть")
@router.callback_query(F.data == "adm_add_part_start")
async def add_new_part(target: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role != "admin":
        msg = "❌ Только администратор может добавлять запчасти."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    builder = InlineKeyboardBuilder()
    for emoji_name, category in CATEGORIES.items():
        builder.button(text=emoji_name, callback_data=f"add_cat_{category}")
    builder.button(text="❌ Отмена", callback_data="part_add_cancel")
    builder.adjust(2)

    text = "➕ <b>Добавление запчасти</b>\n\nВыберите категорию:"
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(PartState.category)


@router.callback_query(F.data == "part_add_cancel")
async def part_add_cancel_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Добавление запчасти отменено.")
    await callback.answer()


@router.callback_query(PartState.category, F.data.startswith("add_cat_"))
async def select_add_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    category = callback.data[8:]
    await state.update_data(category=category)

    subcats = await db.get_subcategories(category)
    builder = InlineKeyboardBuilder()
    if subcats:
        for subcat in subcats:
            builder.button(text=f"📂 {subcat}", callback_data=f"add_sub_{subcat}")
    builder.button(text="➕ Новая подкатегория", callback_data="add_sub_new")
    builder.button(text="⏩ Без подкатегории", callback_data="add_sub_skip")
    builder.button(text="❌ Отмена", callback_data="part_add_cancel")
    builder.adjust(1)

    await callback.message.edit_text(
        f"➕ Категория: <b>{html.escape(category)}</b>\nВыберите подкатегорию или создайте новую:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(PartState.subcategory)
    await callback.answer()


@router.callback_query(PartState.subcategory, F.data.startswith("add_sub_"))
async def select_add_subcategory(callback: types.CallbackQuery, state: FSMContext) -> None:
    builder = _cancel_builder("part_add_cancel")
    if callback.data == "add_sub_new":
        await callback.message.edit_text(
            "➕ Введите название <b>новой подкатегории</b>:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    elif callback.data == "add_sub_skip":
        await state.update_data(subcategory="")
        await callback.message.edit_text("➕ Введите <b>название</b> запчасти:", reply_markup=builder.as_markup(), parse_mode="HTML")
        await state.set_state(PartState.name)
    else:
        subcategory = callback.data[8:]
        await state.update_data(subcategory=subcategory)
        await callback.message.edit_text("➕ Введите <b>название</b> запчасти:", reply_markup=builder.as_markup(), parse_mode="HTML")
        await state.set_state(PartState.name)
    await callback.answer()


@router.message(PartState.subcategory)
async def input_new_subcategory(message: types.Message, state: FSMContext) -> None:
    subcat = message.text.strip()
    await state.update_data(subcategory=subcat)
    builder = _cancel_builder("part_add_cancel")
    await message.answer("➕ Введите <b>название</b> запчасти:", reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(PartState.name)


@router.message(PartState.name)
async def input_part_name(message: types.Message, state: FSMContext) -> None:
    name = message.text.strip()
    await state.update_data(name=name)
    builder = _cancel_builder("part_add_cancel")
    await message.answer("💰 Введите <b>себестоимость</b> (в рублях):", reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(PartState.cost_price)


@router.message(PartState.cost_price)
async def input_cost_price(message: types.Message, state: FSMContext) -> None:
    try:
        cost_price = float(message.text.replace(",", ".").strip())
        if cost_price < 0:
            raise ValueError
    except ValueError:
        builder = _cancel_builder("part_add_cancel")
        await message.answer("❌ Введите положительное число:", reply_markup=builder.as_markup())
        return

    await state.update_data(cost_price=cost_price)
    builder = _cancel_builder("part_add_cancel")
    await message.answer("💰 Введите <b>розничную цену</b> (в рублях):", reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(PartState.retail_price)


@router.message(PartState.retail_price)
async def input_retail_price(message: types.Message, state: FSMContext) -> None:
    try:
        retail_price = float(message.text.replace(",", ".").strip())
        if retail_price < 0:
            raise ValueError
    except ValueError:
        builder = _cancel_builder("part_add_cancel")
        await message.answer("❌ Введите положительное число:", reply_markup=builder.as_markup())
        return

    await state.update_data(retail_price=retail_price)
    builder = _cancel_builder("part_add_cancel")
    await message.answer("💰 Введите <b>оптовую цену</b> (в рублях):", reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(PartState.wholesale_price)


@router.message(PartState.wholesale_price)
async def input_wholesale_price(message: types.Message, state: FSMContext) -> None:
    try:
        wholesale_price = float(message.text.replace(",", ".").strip())
        if wholesale_price < 0:
            raise ValueError
    except ValueError:
        builder = _cancel_builder("part_add_cancel")
        await message.answer("❌ Введите положительное число:", reply_markup=builder.as_markup())
        return

    await state.update_data(wholesale_price=wholesale_price)
    builder = InlineKeyboardBuilder()
    builder.button(text="Оставить по умолчанию (3 шт.)", callback_data="add_thresh_default")
    builder.button(text="❌ Отмена", callback_data="part_add_cancel")
    builder.adjust(1)

    await message.answer(
        "🔔 Введите <b>порог для оповещения о низком остатке</b> (по умолчанию 3 шт.):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(PartState.threshold)


@router.callback_query(PartState.threshold, F.data == "add_thresh_default")
async def default_threshold_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await save_new_part(callback.message, state, threshold=3)
    await callback.answer()


@router.message(PartState.threshold)
async def input_threshold(message: types.Message, state: FSMContext) -> None:
    try:
        thresh = int(message.text.strip())
        if thresh < 0:
            raise ValueError
    except ValueError:
        thresh = 3
    await save_new_part(message, state, threshold=thresh)


async def save_new_part(target: types.Message, state: FSMContext, threshold: int) -> None:
    data = await state.get_data()
    warnings = ""
    if data["wholesale_price"] > data["retail_price"]:
        warnings = "\n⚠️ <i>Внимание: Оптовая цена установлена выше розничной!</i>"

    try:
        part_id = await db.add_part(
            category=data["category"],
            subcategory=data.get("subcategory", ""),
            name=data["name"],
            cost_price=data["cost_price"],
            retail_price=data["retail_price"],
            wholesale_price=data["wholesale_price"],
            low_stock_threshold=threshold,
        )
        margin = ((data["retail_price"] - data["cost_price"]) / data["retail_price"] * 100
                  if data["retail_price"] > 0 else 0)

        builder = InlineKeyboardBuilder()
        builder.button(text="📥 Оприходовать количество", callback_data="start_sin")
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        builder.adjust(1)

        sub_str = f" → {data.get('subcategory')}" if data.get("subcategory") else ""
        text = (
            f"✅ <b>Запчасть успешно добавлена в базу!</b> (ID: <code>{part_id}</code>)\n\n"
            f"📦 <b>{html.escape(data['name'])}</b>\n"
            f"📁 Категория: <b>{html.escape(data['category'])}</b>{sub_str}\n"
            f"💸 Себестоимость: <code>{data['cost_price']:,.0f} {CURRENCY}</code>\n"
            f"🛍️ Розница: <code>{data['retail_price']:,.0f} {CURRENCY}</code>\n"
            f"📦 Опт: <code>{data['wholesale_price']:,.0f} {CURRENCY}</code>\n"
            f"📈 Расчётная маржа: <b>{margin:.1f}%</b>\n"
            f"🔔 Порог алерта: <code>{threshold} шт.</code>"
            + warnings
        )
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        logger.exception("add_part failed")
        await target.answer("❌ Ошибка при сохранении запчасти в базе данных.")

    await state.clear()


# ─────────────────── РЕДАКТИРОВАНИЕ ТОВАРОВ ───────────────────

@router.message(F.text == "📦 Управление товарами")
@router.callback_query(F.data == "adm_find_part_start")
async def start_find_part_edit(target: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "warehouse_manager"):
        msg = "❌ Нет прав для редактирования товаров."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_dashboard")
    text = (
        "✏️ <b>Редактирование запчасти:</b>\n\n"
        "Введите <b>ID запчасти</b> (число) или <b>название детали</b> для поиска:"
    )
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(EditPartState.find_query)


@router.message(EditPartState.find_query)
async def process_find_part(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    await state.clear()

    if query.isdigit():
        part = await db.get_part(int(query))
        if part:
            await render_part_editor(message, part[0])
            return

    parts, total = await db.search_parts(query, page=0, page_size=10)
    if not parts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Попробовать снова", callback_data="adm_find_part_start")
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        builder.adjust(1)
        await message.answer(f"❌ Запчасти по запросу «{html.escape(query)}» не найдены.", reply_markup=builder.as_markup(), parse_mode="HTML")
        return

    builder = InlineKeyboardBuilder()
    for row in parts:
        builder.button(text=f"ID {row[0]}: {row[1][:25]}", callback_data=f"adm_edit_part_{row[0]}")
    builder.button(text="❌ Отмена", callback_data="admin_dashboard")
    builder.adjust(1)

    await message.answer(
        f"🔍 Найдено {total} запчастей. Выберите для редактирования:",
        reply_markup=builder.as_markup(),
    )


async def render_part_editor(target: types.Message | types.CallbackQuery, part_id: int) -> None:
    part = await db.get_part(part_id)
    if not part:
        msg = "❌ Запчасть не найдена."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    pid, name, cost_price, ret_price, wh_price, qty, category, subcategory, supplier, threshold = part

    text = (
        f"✏️ <b>Карточка редактирования товара:</b>\n\n"
        f"ID: <code>{pid}</code>\n"
        f"📦 Название: <b>{html.escape(name)}</b>\n"
        f"📁 Категория: {html.escape(category)} ({html.escape(subcategory or 'нет')})\n"
        f"📊 Остаток: <b>{qty} шт.</b>\n"
        f"💸 Себестоимость: <code>{cost_price:,.0f} {CURRENCY}</code>\n"
        f"🛍️ Розничная цена: <code>{ret_price:,.0f} {CURRENCY}</code>\n"
        f"📦 Оптовая цена: <code>{wh_price:,.0f} {CURRENCY}</code>\n"
        f"🔔 Порог низкого остатка: <code>{threshold} шт.</code>\n"
        f"🏭 Поставщик: <i>{html.escape(supplier or 'Не указан')}</i>\n"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Изменить розничную цену", callback_data=f"adm_setfield_{pid}_retail_price")
    builder.button(text="📦 Изменить оптовую цену", callback_data=f"adm_setfield_{pid}_wholesale_price")
    builder.button(text="💸 Изменить себестоимость", callback_data=f"adm_setfield_{pid}_cost_price")
    builder.button(text="🔢 Изменить остаток вручную", callback_data=f"adm_setfield_{pid}_quantity")
    builder.button(text="🔔 Изменить порог алерта", callback_data=f"adm_setfield_{pid}_low_stock_threshold")
    builder.button(text="🗑️ Удалить/скрыть запчасть", callback_data=f"adm_del_part_{pid}")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(1)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_edit_part_"))
async def adm_edit_part_cb(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[14:])
    await render_part_editor(callback, part_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm_setfield_"))
async def adm_setfield_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    # adm_setfield_{pid}_{field}
    parts = callback.data.split("_", 3)
    part_id = int(parts[2])
    field = parts[3]

    field_names = {
        "retail_price": "розничную цену (руб)",
        "wholesale_price": "оптовую цену (руб)",
        "cost_price": "себестоимость (руб)",
        "quantity": "новое точное количество на складе (шт)",
        "low_stock_threshold": "порог алерта о низком остатке (шт)",
    }

    await state.update_data(part_id=part_id, field=field)
    builder = _cancel_builder(f"adm_edit_part_{part_id}")

    await callback.message.edit_text(
        f"✏️ Введите новое значение для: <b>{field_names.get(field, field)}</b>:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(EditPartState.new_value)
    await callback.answer()


@router.message(EditPartState.new_value)
async def input_new_field_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    part_id = data["part_id"]
    field = data["field"]
    val_str = message.text.replace(",", ".").strip()

    try:
        if field in ("retail_price", "wholesale_price", "cost_price"):
            val = float(val_str)
            if val < 0:
                raise ValueError
        elif field in ("quantity", "low_stock_threshold"):
            val = int(val_str)
            if val < 0:
                raise ValueError
        else:
            val = val_str
    except ValueError:
        builder = _cancel_builder(f"adm_edit_part_{part_id}")
        await message.answer("❌ Введите корректное положительное число:", reply_markup=builder.as_markup())
        return

    if field == "quantity":
        await db.set_part_quantity(part_id, val, user_id=message.from_user.id, reason="Корректировка из админки")
    else:
        await db.update_part_field(part_id, field, val)

    await state.clear()
    await message.answer("✅ <b>Значение успешно сохранено!</b>", parse_mode="HTML")
    await render_part_editor(message, part_id)


@router.callback_query(F.data.startswith("adm_del_part_"))
async def adm_del_part_cb(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[13:])
    ok = await db.deactivate_part(part_id)
    if ok:
        await callback.answer("Запчасть скрыта из каталога", show_alert=True)
        await render_admin_dashboard(callback)
    else:
        await callback.answer("Ошибка при удалении", show_alert=True)


# ─────────────────── ОПОВЕЩЕНИЯ СКЛАДА ───────────────────

@router.message(F.text == "🔔 Оповещения склада")
@router.callback_query(F.data == "adm_view_alerts")
async def show_low_stock_alerts(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "warehouse_manager"):
        msg = "❌ Нет прав для просмотра оповещений."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    alerts = await db.get_low_stock_alerts(unread_only=False)

    if not alerts:
        text = "✅ <b>Все запчасти в норме!</b>\nНет активных оповещений о низких остатках."
        builder = InlineKeyboardBuilder()
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    else:
        text = f"🔔 <b>Оповещения о низких остатках ({len(alerts)} шт.):</b>\n\n"
        for a in alerts[:25]:
            status_dot = "🔴" if a.get("sent") == 0 else "⚪"
            text += (
                f"{status_dot} <b>{html.escape(a['part_name'])}</b>\n"
                f"   📁 {html.escape(a['category'])}\n"
                f"   📦 Остаток: <b>{a['quantity']} шт.</b> (порог: {a['low_stock_threshold']} шт.)\n"
                f"   🕐 <code>{a['date'][:16]}</code>\n\n"
            )

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Отметить все прочитанными", callback_data="adm_alerts_mark_all")
        builder.button(text="🗑️ Очистить журнал алертов", callback_data="adm_alerts_clear")
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        builder.adjust(1)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "adm_alerts_mark_all")
async def mark_all_alerts_cb(callback: types.CallbackQuery) -> None:
    await db.mark_all_alerts_sent()
    await callback.answer("Все оповещения отмечены как прочитанные")
    await show_low_stock_alerts(callback)


@router.callback_query(F.data == "adm_alerts_clear")
async def clear_alerts_cb(callback: types.CallbackQuery) -> None:
    await db.clear_low_stock_alerts()
    await callback.answer("Журнал оповещений очищен")
    await show_low_stock_alerts(callback)


# ─────────────────── ДВИЖЕНИЕ ТОВАРА И ОТЧЕТЫ ───────────────────

@router.message(F.text == "📈 Движение товара")
@router.callback_query(F.data == "adm_stock_movements")
async def show_stock_movement(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "warehouse_manager"):
        msg = "❌ Нет прав для просмотра отчётов."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    report = await db.get_stock_movement_report(days=30)
    if not report:
        text = "⚠️ <b>Нет данных о движении товара за последние 30 дней.</b>"
    else:
        text = "📊 <b>Отчёт о движении товара за последние 30 дней:</b>\n\n"
        for row in report[:30]:
            text += (
                f"📦 <b>{html.escape(row['part_name'])}</b>\n"
                f"   📁 {html.escape(row['category'])}\n"
                f"   📈 Приход: <code>+{row['inflow']} шт.</code> | 📉 Расход: <code>-{row['outflow']} шт.</code>\n"
                f"   🏭 <i>{html.escape(row['supplier'] or 'Поставщик не указан')}</i>\n\n"
            )

    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Скачать полный отчёт (CSV)", callback_data="export_movements_csv")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(1)

    for chunk in _split_and_send(text):
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(chunk, reply_markup=builder.as_markup(), parse_mode="HTML")
            await target.answer()
        else:
            await target.answer(chunk, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── ФИНАНСОВЫЙ АНАЛИЗ ───────────────────

@router.message(F.text == "💰 Финансовый анализ")
@router.callback_query(F.data == "adm_fin_analysis")
async def show_financial_analysis(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра финансовых данных."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    report = await db.get_financial_summary()
    if not report:
        text = "⚠️ Нет финансовых данных."
    else:
        text = (
            "💰 <b>Финансовый анализ склада:</b>\n\n"
            f"💵 Общая выручка (розница): <b>{report['total_revenue']:,.2f} {CURRENCY}</b>\n"
            f"💸 Общие затраты (себестоимость): <b>{report['total_cost']:,.2f} {CURRENCY}</b>\n"
            f"📈 Потенциальная валовая прибыль: <b>{report['total_profit']:,.2f} {CURRENCY}</b>\n"
            f"📊 Средняя торговая маржа: <b>{report['avg_margin']:.1f}%</b>\n\n"
        )
        profitability = await db.get_category_profitability()
        if profitability:
            text += "🏆 <b>Топ-10 товаров по маржинальности:</b>\n"
            for i, row in enumerate(profitability, 1):
                text += f"{i}. <b>{html.escape(row['part_name'])}</b> — <code>{row['margin_percent']}%</code> маржи\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Выгрузить остатки (CSV)", callback_data="export_stock_csv")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(1)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── АНАЛИЗ ПОСТАВЩИКОВ ───────────────────

@router.message(F.text == "🏢 Анализ поставщиков")
@router.callback_query(F.data == "adm_suppliers")
async def show_supplier_analysis(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра анализа поставщиков."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    report = await db.get_supplier_analysis()
    if not report:
        text = "⚠️ Нет данных о поставщиках."
    else:
        text = f"🏢 <b>Анализ поставщиков ({len(report)} компаний):</b>\n\n"
        for row in report:
            text += (
                f"🏭 <b>{html.escape(row['supplier'])}</b>\n"
                f"   📦 Всего поставок: <code>{row['delivery_count']}</code>\n"
                f"   📊 Суммарно принято: <b>{row['total_received']} шт.</b>\n"
                f"   📈 Средний объём партии: <code>{row['avg_delivery_qty']} шт.</code>\n"
                f"   🕐 Последняя доставка: <code>{row['last_delivery']}</code>\n\n"
            )

    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── ЭКСПОРТ В CSV ───────────────────

@router.message(F.text == "📥 Экспорт в CSV")
@router.callback_query(F.data == "adm_export_menu")
async def export_menu(target: types.Message | types.CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Аналитика продаж и KPI (CSV)", callback_data="export_analytics_csv")
    builder.button(text="📦 Экспорт остатков склада (CSV)", callback_data="export_stock_csv")
    builder.button(text="📈 Экспорт движения товара (CSV)", callback_data="export_movements_csv")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(1)

    text = "📥 <b>Экспорт данных в Excel / CSV:</b>\nВыберите тип формируемого файла:"
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "export_analytics_csv")
async def export_analytics_csv_cb(callback: types.CallbackQuery) -> None:
    await callback.answer("Формирование комплексной аналитики...")
    filepath = await db.export_analytics_csv()
    doc = FSInputFile(filepath, filename="Аналитика_Продаж_PartsBot.csv")
    await callback.message.answer_document(
        doc,
        caption=(
            "📊 <b>Комплексная аналитика продаж PartsBot</b>\n\n"
            "Файл включает 4 аналитических блока:\n"
            "1. 📈 Финансовые KPI (выручка, средний чек, доставка, оплата)\n"
            "2. 👥 Топ клиентов (LTV и повторные заказы)\n"
            "3. 🏆 ABC-анализ ассортимента (ключевые позиции и неликвид)\n"
            "4. ⏳ План-прогноз потребности в закупках (на 30 дней)\n\n"
            "<i>Формат: CSV (разделитель «;», кодировка UTF-8 с BOM для мгновенного открытия в Excel).</i>"
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "export_stock_csv")
async def export_stock_csv_cb(callback: types.CallbackQuery) -> None:
    await callback.answer("Формирование файла...")
    filepath = await db.export_stock_csv()
    doc = FSInputFile(filepath, filename="Склад_Остатки.csv")
    await callback.message.answer_document(
        doc,
        caption="📦 <b>Выгрузка остатков склада</b>\nФормат: CSV (разделитель точка с запятой, UTF-8 с BOM для Excel).",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "export_movements_csv")
async def export_movements_csv_cb(callback: types.CallbackQuery) -> None:
    await callback.answer("Формирование файла...")
    filepath = await db.export_movements_csv(days=60)
    doc = FSInputFile(filepath, filename="Движение_Товара_60_дней.csv")
    await callback.message.answer_document(
        doc,
        caption="📈 <b>Выгрузка истории движения товаров (за 60 дней)</b>\nФормат: CSV.",
        parse_mode="HTML",
    )


# ─────────────────── РАССЫЛКА СООБЩЕНИЙ ───────────────────

@router.message(F.text == "📢 Рассылка")
@router.callback_query(F.data == "adm_broadcast_start")
async def start_broadcast(target: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role != "admin":
        msg = "❌ Только администратор может отправлять рассылки."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_dashboard")

    text = (
        "📢 <b>Создание массовой рассылки:</b>\n\n"
        "Отправьте текст сообщения для рассылки всем пользователям бота.\n"
        "<i>(Поддерживается базовое форматирование)</i>:"
    )
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(BroadcastState.content)


@router.message(BroadcastState.content)
async def process_broadcast_content(message: types.Message, state: FSMContext) -> None:
    text = message.html_text if hasattr(message, "html_text") else message.text
    await state.update_data(broadcast_text=text)

    users = await db.get_all_users()
    count = len(users)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"🚀 Отправить всем ({count} польз.)", callback_data="broadcast_confirm")
    builder.button(text="❌ Отмена", callback_data="admin_dashboard")
    builder.adjust(1)

    await message.answer(
        f"📢 <b>Предпросмотр рассылки:</b>\n\n"
        f"{text}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 Получателей: <b>{count} пользователей</b>\n"
        f"Отправить сообщение?",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(BroadcastState.confirm)


@router.callback_query(BroadcastState.confirm, F.data == "broadcast_confirm")
async def execute_broadcast(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    await state.clear()

    await callback.message.edit_text("⏳ Идёт отправка сообщений...")
    users = await db.get_all_users()

    sent = 0
    failed = 0

    for u in users:
        uid = u["user_id"]
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

    await callback.message.answer(
        f"✅ <b>Рассылка успешно завершена!</b>\n\n"
        f"• Доставлено: <b>{sent}</b>\n"
        f"• Ошибок / заблокировано: <b>{failed}</b>",
        parse_mode="HTML",
    )
    await render_admin_dashboard(callback.message)


# ─────────────────── РАСШИРЕННАЯ АНАЛИТИКА: KPI И ПРОДАЖИ ───────────────────

@router.message(F.text == "📈 Продажи и KPI")
@router.callback_query(F.data == "adm_sales_kpi")
async def show_sales_kpi(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра аналитики продаж."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    data = await db.get_sales_kpi()
    t = data.get("today", {})
    w = data.get("week", {})
    m = data.get("month", {})
    tot = data.get("all_time", {})

    del_dist = data.get("delivery_dist", {})
    pay_dist = data.get("payment_dist", {})

    text = (
        "📈 <b>ДИНАМИКА ПРОДАЖ И ФИНАНСОВЫЕ KPI</b>\n\n"
        "📅 <b>Сегодня:</b>\n"
        f"• Выручка: <b>{t.get('revenue', 0):,.0f} {CURRENCY}</b>\n"
        f"• Оформлено заказов: <b>{t.get('orders_count', 0)} шт.</b>\n"
        f"• Средний чек: <b>{t.get('avg_check', 0):,.0f} {CURRENCY}</b>\n\n"
        "🗓️ <b>За 7 дней (неделя):</b>\n"
        f"• Выручка: <b>{w.get('revenue', 0):,.0f} {CURRENCY}</b>\n"
        f"• Заказов: <b>{w.get('orders_count', 0)} шт.</b>\n"
        f"• Средний чек: <b>{w.get('avg_check', 0):,.0f} {CURRENCY}</b>\n\n"
        "📊 <b>За 30 дней (месяц):</b>\n"
        f"• Выручка: <b>{m.get('revenue', 0):,.0f} {CURRENCY}</b>\n"
        f"• Заказов: <b>{m.get('orders_count', 0)} шт.</b>\n"
        f"• Средний чек: <b>{m.get('avg_check', 0):,.0f} {CURRENCY}</b>\n\n"
        "🏆 <b>За всё время:</b>\n"
        f"• Суммарный оборот: <b>{tot.get('revenue', 0):,.0f} {CURRENCY}</b>\n"
        f"• Успешных заказов: <b>{tot.get('orders_count', 0)} шт.</b>\n"
        f"• Средний чек: <b>{tot.get('avg_check', 0):,.0f} {CURRENCY}</b>\n\n"
        "🚚 <b>Способы доставки:</b>\n"
        f"• 🏬 Самовывоз: <b>{del_dist.get('pickup', 0)}</b> | "
        f"🚴 Курьер: <b>{del_dist.get('courier', 0)}</b> | "
        f"📦 СДЭК/ТК: <b>{del_dist.get('cdek', 0)}</b>\n\n"
        "💳 <b>Способы оплаты:</b>\n"
        f"• 💵 При получении: <b>{pay_dist.get('cash', 0)}</b> | "
        f"⚡ СБП: <b>{pay_dist.get('sbp', 0)}</b>\n"
        # f"• 🏢 Безнал (счёт): <b>{pay_dist.get('invoice', 0)}</b>\n"  # TODO: продолжим позже
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🏆 ABC-анализ", callback_data="adm_abc_analysis")
    builder.button(text="⏳ Прогноз закупок", callback_data="adm_procure_forecast")
    builder.button(text="👥 Топ клиентов", callback_data="adm_top_clients")
    builder.button(text="📥 Скачать Аналитику (CSV)", callback_data="export_analytics_csv")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(2, 1, 1, 1)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── РАСШИРЕННАЯ АНАЛИТИКА: ABC-АНАЛИЗ ───────────────────

@router.message(F.text == "🏆 ABC-анализ")
@router.callback_query(F.data == "adm_abc_analysis")
async def show_abc_analysis(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра аналитики."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    data = await db.get_abc_analysis()
    tot_rev = data.get("total_revenue", 0)
    counts = data.get("counts", {})
    revs = data.get("revenue", {})

    pct_a = (revs.get('A', 0) / tot_rev * 100) if tot_rev > 0 else 0
    pct_b = (revs.get('B', 0) / tot_rev * 100) if tot_rev > 0 else 0
    pct_c = (revs.get('C', 0) / tot_rev * 100) if tot_rev > 0 else 0

    text = (
        "🏆 <b>ABC-АНАЛИЗ АССОРТИМЕНТА (МАТРИЦА ПРИБЫЛИ)</b>\n"
        "<i>Классификация запчастей по правилу Парето (вклад в общую выручку)</i>\n\n"
        f"📊 <b>Сводка по категориям:</b>\n"
        f"🟢 <b>Группа A</b> (Топ-выручка): <b>{counts.get('A', 0)} поз.</b> — <b>{revs.get('A', 0):,.0f} {CURRENCY}</b> ({pct_a:.1f}%)\n"
        f"🟡 <b>Группа B</b> (Стабильный спрос): <b>{counts.get('B', 0)} поз.</b> — <b>{revs.get('B', 0):,.0f} {CURRENCY}</b> ({pct_b:.1f}%)\n"
        f"⚪ <b>Группа C</b> (Неликвид / Редкий спрос): <b>{counts.get('C', 0)} поз.</b> — <b>{revs.get('C', 0):,.0f} {CURRENCY}</b> ({pct_c:.1f}%)\n\n"
    )

    group_a = data.get("group_a", [])
    if group_a:
        text += "🟢 <b>Ключевые локомотивы продаж (Группа A):</b>\n"
        for i, item in enumerate(group_a[:8], 1):
            text += (
                f"{i}. <b>{html.escape(item['name'])}</b>\n"
                f"   Выручка: <code>{item['total_revenue']:,.0f} {CURRENCY}</code> (доля: <b>{item.get('revenue_share', 0)}%</b>) | Остаток: <code>{item['stock_qty']} шт.</code>\n"
            )
        text += "\n"

    group_c = data.get("group_c", [])
    if group_c:
        text += "⚪ <b>Кандидаты на акции / скидки (Группа C с остатком):</b>\n"
        c_with_stock = [it for it in group_c if it["stock_qty"] > 0][:5]
        if c_with_stock:
            for it in c_with_stock:
                text += f"• <b>{html.escape(it['name'])}</b> — на складе: <code>{it['stock_qty']} шт.</code> (продаж: {it['sold_qty']} шт.)\n"
        else:
            text += "<i>Нет зависших остатков в группе C.</i>\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="⏳ Прогноз закупок", callback_data="adm_procure_forecast")
    builder.button(text="📈 Продажи и KPI", callback_data="adm_sales_kpi")
    builder.button(text="📥 Скачать Аналитику (CSV)", callback_data="export_analytics_csv")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(2, 1, 1)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── РАСШИРЕННАЯ АНАЛИТИКА: ПРОГНОЗ ЗАКУПОК ───────────────────

@router.message(F.text == "⏳ Прогноз закупок")
@router.callback_query(F.data == "adm_procure_forecast")
async def show_procurement_forecast(target: types.Message | types.CallbackQuery) -> None:
    if isinstance(target, types.CallbackQuery):
        await target.answer()

    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "warehouse_manager"):
        msg = "❌ Нет прав для просмотра прогноза закупок."
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(msg)
        else:
            await target.answer(msg)
        return

    try:
        forecast = await db.get_procurement_forecast(days_window=30)
        if not forecast:
            text = "✅ <b>На складе нет дефицита!</b> Все позиции укомплектованы."
        else:
            empty_cnt = sum(1 for x in forecast if x["urgency"] == "CRITICAL_EMPTY")
            high_cnt = sum(1 for x in forecast if x["urgency"] == "HIGH")
            med_cnt = sum(1 for x in forecast if x["urgency"] == "MEDIUM")

            text = (
                "⏳ <b>ПРОГНОЗ ЗАКУПОК И ДЕФИЦИТА (SMART FORECAST)</b>\n"
                "<i>Расчёт остатка дней при текущем темпе продаж за 30 дней</i>\n\n"
                f"🚨 Закончились полностью (0 шт.): <b>{empty_cnt} поз.</b>\n"
                f"⚠️ Закончатся менее чем за 7 дней: <b>{high_cnt} поз.</b>\n"
                f"⏳ Хватит на 7-14 дней: <b>{med_cnt} поз.</b>\n\n"
                "📋 <b>Список первоочередных позиций к закупке:</b>\n\n"
            )

            critical_items = [x for x in forecast if x["urgency"] in ("CRITICAL_EMPTY", "HIGH", "MEDIUM")][:8]
            if not critical_items:
                critical_items = forecast[:6]

            for it in critical_items:
                days_str = f"хватит на {it['days_left']} дн." if (it["days_left"] > 0 and it["days_left"] < 900) else ("закончился" if it["stock_qty"] == 0 else "нет продаж")
                text += (
                    f"{it['urgency_label']} <b>{html.escape(it['name'][:34])}</b>\n"
                    f"   📦 Остаток: <b>{it['stock_qty']} шт.</b> ({days_str})\n"
                    f"   📉 Расход: <code>{it['daily_burn']} шт./дн.</code> | Рекомендовано: <b>+{it['recommended_order']} шт.</b>\n"
                    f"   🏭 Поставщик: <i>{html.escape(it['supplier'] or 'Не указан')}</i>\n\n"
                )

        builder = InlineKeyboardBuilder()
        builder.button(text="🏆 ABC-анализ", callback_data="adm_abc_analysis")
        builder.button(text="📈 Продажи и KPI", callback_data="adm_sales_kpi")
        builder.button(text="📥 Скачать Аналитику (CSV)", callback_data="export_analytics_csv")
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        builder.adjust(2, 1, 1)

        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception as e:
        logger.exception("show_procurement_forecast error: %s", e)
        err_builder = InlineKeyboardBuilder()
        err_builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        err_text = "⚠️ <b>Не удалось сформировать прогноз закупок.</b>\nПопробуйте снова через несколько минут."
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(err_text, reply_markup=err_builder.as_markup(), parse_mode="HTML")
        else:
            await target.answer(err_text, reply_markup=err_builder.as_markup(), parse_mode="HTML")


# ─────────────────── УПРАВЛЕНИЕ ЗАКАЗАМИ В АДМИНКЕ ───────────────────

ORDER_STATUSES: dict[str, str] = {
    "pending": "⏳ В ожидании",
    "processing": "📦 В обработке",
    "shipped": "🚚 Отправлен / Готов",
    "completed": "✅ Выполнен",
    "cancelled": "❌ Отменен",
}


@router.message(F.text == "🛍️ Заказы клиентов")
@router.callback_query(F.data == "adm_orders_list")
@router.callback_query(F.data.startswith("adm_ord_list_"))
async def adm_orders_list_handler(target: types.Message | types.CallbackQuery) -> None:
    if isinstance(target, types.CallbackQuery):
        await target.answer()

    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра заказов."
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(msg)
        else:
            await target.answer(msg)
        return

    status_filter = "all"
    page = 0
    if isinstance(target, types.CallbackQuery) and target.data.startswith("adm_ord_list_"):
        raw = target.data[13:]
        if "_p" in raw:
            status_filter, p_str = raw.rsplit("_p", 1)
            page = int(p_str) if p_str.isdigit() else 0
        else:
            status_filter = raw

    page_size = 6
    db_status = None if status_filter == "all" else status_filter
    orders, total = await db.get_all_orders_paged(page=page, page_size=page_size, status=db_status)

    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    status_title = ORDER_STATUSES.get(status_filter, "Все заказы")
    text = (
        f"🛍️ <b>УПРАВЛЕНИЕ ЗАКАЗАМИ КЛИЕНТОВ</b>\n"
        f"Фильтр: <b>{status_title}</b> | Стр. {page + 1} из {total_pages} (всего: {total})\n\n"
    )

    builder = InlineKeyboardBuilder()

    if not orders:
        text += "<i>Заказов с таким статусом пока нет.</i>\n\n"
    else:
        for o in orders:
            o_id = o["id"]
            st = o.get("status", "pending")
            st_icon = {"pending": "⏳", "processing": "📦", "shipped": "🚚", "completed": "✅", "cancelled": "❌"}.get(st, "📋")
            time_str = o.get("created_at", "")[:16]
            c_name = o.get("user_name") or f"Клиент #{o['user_id']}"
            amt = o.get("total_amount", 0)

            text += (
                f"{st_icon} <b>Заказ #{o_id}</b> ({time_str})\n"
                f"   👤 {html.escape(c_name)} | Сумма: <b>{amt:,.0f} {CURRENCY}</b>\n"
                f"   Статус: <code>{st}</code>\n\n"
            )
            btn_txt = f"{st_icon} #{o_id} — {amt:,.0f} {CURRENCY} ({html.escape(c_name)[:14]})"
            builder.button(text=btn_txt, callback_data=f"adm_ord_v_{o_id}")
        builder.adjust(1)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="◀ Пред.", callback_data=f"adm_ord_list_{status_filter}_p{page - 1}"))
    if total_pages > 1:
        nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * page_size < total:
        nav.append(types.InlineKeyboardButton(text="След. ▶", callback_data=f"adm_ord_list_{status_filter}_p{page + 1}"))
    if nav:
        builder.row(*nav)

    filter_row = [
        types.InlineKeyboardButton(text="Все", callback_data="adm_ord_list_all"),
        types.InlineKeyboardButton(text="⏳ Новые", callback_data="adm_ord_list_pending"),
        types.InlineKeyboardButton(text="📦 В работе", callback_data="adm_ord_list_processing"),
        types.InlineKeyboardButton(text="✅ Завершенные", callback_data="adm_ord_list_completed"),
    ]
    builder.row(*filter_row)

    builder.row(types.InlineKeyboardButton(text="⚙️ В админ-панель", callback_data="admin_dashboard"))

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


async def _render_order_view(target: types.CallbackQuery | types.Message, order_id: int) -> None:
    order = await db.get_order_details(order_id)
    if not order:
        if isinstance(target, types.CallbackQuery):
            await target.message.answer("❌ Заказ не найден.")
        else:
            await target.answer("❌ Заказ не найден.")
        return

    st = order.get("status", "pending")
    st_name = ORDER_STATUSES.get(st, st)
    st_icon = {"pending": "⏳", "processing": "📦", "shipped": "🚚", "completed": "✅", "cancelled": "❌"}.get(st, "📋")
    pay_status = "Оплачен 💳" if order.get("payment_status") == "paid" else "Не оплачен ⏳"

    items_text = ""
    for idx, it in enumerate(order.get("items", []), 1):
        pname = html.escape(it.get("part_name", ""))
        iqty = it.get("quantity", 0)
        iprice = it.get("price", 0)
        items_text += f"{idx}. <b>{pname}</b>\n   <code>{iqty} шт. × {iprice:,.0f} = {iqty * iprice:,.0f} {CURRENCY}</code>\n"

    deliv_method = {"pickup": "Самовывоз со склада", "courier": "Доставка курьером", "cdek": "СДЭК"}.get(order.get("delivery_method"), order.get("delivery_method"))
    pay_method = {"cash": "При получении", "sbp": "СБП (перевод/QR)", "invoice": "По счёту (юрлицо)"}.get(order.get("payment_method"), order.get("payment_method"))

    text = (
        f"{st_icon} <b>КАРТОЧКА ЗАКАЗА #{order_id}</b>\n\n"
        f"📅 Создан: <code>{order.get('created_at', '')[:19]}</code>\n"
        f"👤 Клиент: <b>{html.escape(order.get('user_name', ''))}</b> (ID: <code>{order.get('user_id')}</code>)\n"
        f"📞 Контакт: <code>{html.escape(order.get('contact', 'Не указан'))}</code>\n"
        f"🚚 Доставка: <b>{deliv_method}</b>\n"
        f"📍 Адрес: <i>{html.escape(order.get('delivery_address') or 'Склад Казань')}</i>\n"
        f"💳 Оплата: <b>{pay_method}</b> ({pay_status})\n"
        f"📝 Комментарий: <i>{html.escape(order.get('notes') or 'Нет')}</i>\n\n"
        f"📦 <b>Состав заказа:</b>\n{items_text}\n"
        f"💰 <b>ИТОГО К ОПЛАТЕ: {order.get('total_amount', 0):,.0f} {CURRENCY}</b>\n"
        f"📊 Текущий статус: <b>{st_name}</b>"
    )

    builder = InlineKeyboardBuilder()

    builder.button(text="⏳ В ожидании", callback_data=f"adm_ost_{order_id}_pending")
    builder.button(text="📦 В обработку", callback_data=f"adm_ost_{order_id}_processing")
    builder.button(text="🚚 Отправлен", callback_data=f"adm_ost_{order_id}_shipped")
    builder.button(text="✅ Выполнен", callback_data=f"adm_ost_{order_id}_completed")
    builder.button(text="❌ Отменить", callback_data=f"adm_ost_{order_id}_cancelled")

    if order.get("payment_status") == "paid":
        builder.button(text="⏳ Отметить: НЕ оплачен", callback_data=f"adm_opay_{order_id}_unpaid")
    else:
        builder.button(text="💳 Отметить: ОПЛАЧЕН", callback_data=f"adm_opay_{order_id}_paid")

    builder.button(text="🔙 К списку заказов", callback_data="adm_orders_list")
    builder.adjust(3, 2, 1, 1)

    try:
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("_render_order_view edit_text error: %s", e)


@router.callback_query(F.data.startswith("adm_ord_v_"))
async def adm_order_view_handler(callback: types.CallbackQuery) -> None:
    try:
        await callback.answer()
    except Exception:
        pass
    order_id = int(callback.data[10:])
    await _render_order_view(callback, order_id)


@router.callback_query(F.data.startswith("adm_ost_"))
async def adm_order_set_status_handler(callback: types.CallbackQuery, bot: Bot) -> None:
    parts = callback.data[8:].split("_")
    order_id = int(parts[0])
    new_status = parts[1]

    order = await db.get_order_details(order_id)
    if not order:
        try:
            await callback.answer("Заказ не найден", show_alert=True)
        except Exception:
            pass
        return

    deduct = (new_status == "completed" and order.get("status") != "completed")
    ok = await db.update_order_status(order_id, new_status, deduct_stock=deduct, staff_id=callback.from_user.id)
    if ok:
        try:
            await callback.answer(f"Статус заказа #{order_id} изменён на '{new_status}'!", show_alert=True)
        except Exception:
            pass
        st_name = ORDER_STATUSES.get(new_status, new_status)
        try:
            await bot.send_message(
                order["user_id"],
                f"🔔 <b>Статус вашего заказа #{order_id} обновлен!</b>\n\n"
                f"Новый статус: <b>{st_name}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        try:
            await callback.answer("❌ Ошибка при обновлении статуса", show_alert=True)
        except Exception:
            pass

    await _render_order_view(callback, order_id)


@router.callback_query(F.data.startswith("adm_opay_"))
async def adm_order_set_pay_handler(callback: types.CallbackQuery) -> None:
    parts = callback.data[9:].split("_")
    order_id = int(parts[0])
    new_pay = parts[1]

    ok = await db.set_order_payment_status(order_id, new_pay)
    if ok:
        pay_label = "Оплачен" if new_pay == "paid" else "Не оплачен"
        try:
            await callback.answer(f"Статус оплаты заказа #{order_id} изменен: {pay_label}!", show_alert=True)
        except Exception:
            pass
    else:
        try:
            await callback.answer("❌ Ошибка при обновлении оплаты", show_alert=True)
        except Exception:
            pass

    await _render_order_view(callback, order_id)


@router.callback_query(F.data == "start_sin")
async def start_sin_callback_handler(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Обработчик кнопки 'Оприходовать' из оповещения о низком остатке."""
    await callback.answer()
    from handlers.stock import start_stock_in
    await start_stock_in(callback.message, state)


# ─────────────────── РАСШИРЕННАЯ АНАЛИТИКА: ТОП КЛИЕНТОВ ───────────────────

@router.message(F.text == "👥 Топ клиентов")
@router.callback_query(F.data == "adm_top_clients")
async def show_top_clients(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра базы клиентов."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    clients = await db.get_top_clients(limit=10)
    if not clients:
        text = "⚠️ <b>Пока нет заказов от клиентов.</b>"
    else:
        text = (
            "👥 <b>РЕЙТИНГ КЛЮЧЕВЫХ КЛИЕНТОВ (LTV & ОБЪЁМ)</b>\n"
            "<i>Топ-10 покупателей по сумме выкупа</i>\n\n"
        )
        for i, c in enumerate(clients, 1):
            client_type = "ОПТ 📦" if c.get("client_type") == "wholesale" else "Розница 🛍️"
            text += (
                f"{i}. <b>{html.escape(c['full_name'])}</b> ({client_type})\n"
                f"   💰 Сумма выкупа: <b>{c['total_spent']:,.0f} {CURRENCY}</b>\n"
                f"   📦 Заказов: <code>{c['orders_count']} шт.</code> | Средний чек: <code>{c['avg_check']:,.0f} {CURRENCY}</code>\n"
                f"   🕐 Последний заказ: <code>{c['last_order_date'][:16]}</code>\n\n"
            )

    builder = InlineKeyboardBuilder()
    builder.button(text="📈 Продажи и KPI", callback_data="adm_sales_kpi")
    builder.button(text="📥 Скачать Аналитику (CSV)", callback_data="export_analytics_csv")
    builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    builder.adjust(2, 1)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


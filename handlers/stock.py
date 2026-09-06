"""handlers/stock.py — приходование и списание товара (FSM) с защитой ввода и отменой."""
import html
import logging
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import CATEGORIES
from handlers.common import get_main_menu

logger = logging.getLogger(__name__)
router = Router()

LOW_STOCK_THRESHOLD = db.LOW_STOCK_THRESHOLD


# ─────────────────────── FSM States ───────────────────────────

class StockInState(StatesGroup):
    search = State()
    category = State()
    subcategory = State()
    part_type = State()
    part = State()
    quantity = State()
    cost_price = State()
    supplier = State()
    notes = State()
    confirm = State()


class StockOutState(StatesGroup):
    search = State()
    category = State()
    subcategory = State()
    part = State()
    quantity = State()
    notes = State()
    confirm = State()


# ─────────────────────── helpers ───────────────────────────────

def format_part_button_text(name: str, qty: int) -> str:
    """Очищает шаблонные префиксы в названии, чтобы на кнопке была видна суть (модель и тип)."""
    clean = name
    for prefix in (
        "Дисплей в сборе с тачскрином для ",
        "Дисплейный модуль для ",
        "Дисплей для ",
        "Аккумулятор для Apple ",
        "Аккумулятор для ",
        "Задняя крышка для ",
        "Шлейф для ",
        "Шлейф ",
        "Камера для ",
    ):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    if len(clean) > 34:
        clean = clean[:31] + "..."
    return f"{clean} ({qty} шт.)"


def _cancel_builder(cb_data: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=cb_data)
    return builder


def _skip_and_cancel_builder(skip_cb: str, cancel_cb: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏩ Пропустить", callback_data=skip_cb)
    builder.button(text="❌ Отмена", callback_data=cancel_cb)
    builder.adjust(2)
    return builder


async def _send_category_selector(
    target: types.Message | types.CallbackQuery,
    cb_prefix: str,
    title: str,
    cancel_cb: str,
) -> None:
    builder = InlineKeyboardBuilder()
    brands = await db.get_brands()
    for brand, cnt in brands:
        builder.button(text=f"📱 {brand} ({cnt})", callback_data=f"{cb_prefix}_{brand}")
    if cb_prefix == "sin_cat":
        builder.button(text="🔍 Быстрый поиск товара", callback_data="sin_search_start")
    elif cb_prefix == "sout_cat":
        builder.button(text="🔍 Быстрый поиск товара", callback_data="sout_search_start")
    builder.button(text="❌ Отмена", callback_data=cancel_cb)
    builder.adjust(2)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(title, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(title, reply_markup=builder.as_markup(), parse_mode="HTML")


async def _notify_low_stock(bot: Bot, part_name: str, qty: int) -> None:
    """Уведомляет всех менеджеров склада и администраторов о низком остатке."""
    managers = await db.get_warehouse_managers()
    text = (
        f"🔔 <b>НИЗКИЙ ОСТАТОК НА СКЛАДЕ!</b>\n\n"
        f"Запчасть: <b>{html.escape(part_name)}</b>\n"
        f"Остаток: <code>{qty} шт.</code> (порог: <code>{LOW_STOCK_THRESHOLD} шт.</code>)"
    )
    for uid in managers:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            logger.warning("Не удалось отправить уведомление пользователю %s", uid)


# ═══════════════════════════════════════════════════════════
#                    ПРИХОДОВАНИЕ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "📥 Приходование")
async def start_stock_in(message: types.Message, state: FSMContext) -> None:
    role = await db.get_user_role(message.from_user.id)
    if role not in ("admin", "warehouse_manager"):
        await message.answer("❌ Только менеджер склада или администратор может приходовать товар.")
        return

    await _send_category_selector(message, "sin_cat", "📥 <b>Выберите категорию для приходования:</b>", "sin_cancel")
    await state.set_state(StockInState.category)


@router.callback_query(F.data == "sin_cancel")
async def sin_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Приходование отменено.")
    await callback.answer()


@router.callback_query(F.data == "sin_search_start")
async def sin_search_start_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="📂 Выбрать из категорий", callback_data="sin_back_categories")
    builder.button(text="❌ Отмена", callback_data="sin_cancel")
    builder.adjust(1)
    await callback.message.edit_text(
        "🔍 <b>Быстрый поиск запчасти для приходования</b>\n\n"
        "Введите название или модель (например: <code>iPhone 13</code>, <code>Redmi 9</code>, <code>A51</code>):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.search)
    await callback.answer()


@router.callback_query(F.data == "sin_back_categories")
async def sin_back_categories_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _send_category_selector(callback, "sin_cat", "📥 <b>Выберите категорию для приходования:</b>", "sin_cancel")
    await state.set_state(StockInState.category)
    await callback.answer()


@router.message(StockInState.search)
async def sin_search_query_msg(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    if len(query) < 2:
        builder = _cancel_builder("sin_cancel")
        await message.answer("❌ Запрос слишком короткий. Введите минимум 2 символа:", reply_markup=builder.as_markup())
        return

    parts, total = await db.search_parts(query, page=0, page_size=20)
    if not parts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Искать снова", callback_data="sin_search_start")
        builder.button(text="📂 Выбрать из категорий", callback_data="sin_back_categories")
        builder.button(text="❌ Отмена", callback_data="sin_cancel")
        builder.adjust(1)
        await message.answer(
            f"🔍 По запросу «<b>{html.escape(query)}</b>» ничего не найдено.\n"
            f"Попробуйте другой запрос или выберите категорию вручную:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    for row in parts:
        # row: (id, name, retail_price, wholesale_price, quantity, category)
        part_id, name, _, _, qty, cat = row
        builder.button(text=format_part_button_text(name, qty), callback_data=f"sin_part_{part_id}")

    builder.button(text="🔍 Другой поиск", callback_data="sin_search_start")
    builder.button(text="📂 К категориям", callback_data="sin_back_categories")
    builder.button(text="❌ Отмена", callback_data="sin_cancel")
    builder.adjust(1)

    await message.answer(
        f"🔍 Найдено совпадений: <b>{total}</b> (показано {len(parts)}):\n"
        f"Выберите запчасть для оприходования:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.part)


@router.callback_query(StockInState.category, F.data.startswith("sin_cat_"))
async def sin_select_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    brand = callback.data[8:]
    await state.update_data(brand=brand, category=brand)
    await _show_sin_models(callback, state, brand, page=0)
    await callback.answer()


async def _show_sin_models(callback: types.CallbackQuery, state: FSMContext, brand: str, page: int = 0) -> None:
    models = await db.get_models_by_brand(brand)
    if not models:
        await _show_sin_parts(callback, state, brand, "", None, page=0)
        await state.set_state(StockInState.part)
        return

    page_size = 8
    total = len(models)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    page_models = models[page * page_size : (page + 1) * page_size]

    model_names = [m[0] for m in models]
    await state.update_data(model_names=model_names)

    builder = InlineKeyboardBuilder()
    for idx, (m_name, cnt) in enumerate(page_models, start=page * page_size):
        clean_btn = m_name[:26]
        builder.button(text=f"📂 {clean_btn} ({cnt})", callback_data=f"sin_m_{idx}")
    builder.adjust(1)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="◀ Пред.", callback_data=f"sin_mp_{page - 1}"))
    if total_pages > 1:
        nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * page_size < total:
        nav.append(types.InlineKeyboardButton(text="След. ▶", callback_data=f"sin_mp_{page + 1}"))
    if nav:
        builder.row(*nav)

    builder.row(
        types.InlineKeyboardButton(text="🔙 К брендам", callback_data="sin_back_categories"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="sin_cancel"),
    )

    await callback.message.edit_text(
        f"📱 <b>{html.escape(brand)}</b> — выберите модель (всего {total}):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.subcategory)


@router.callback_query(StockInState.subcategory, F.data.startswith("sin_mp_"))
async def sin_paginate_models(callback: types.CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data[7:])
    data = await state.get_data()
    await _show_sin_models(callback, state, data.get("brand", ""), page=page)
    await callback.answer()


@router.callback_query(StockInState.subcategory, F.data.startswith("sin_m_"))
async def sin_select_model(callback: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data[6:])
    data = await state.get_data()
    model_names = data.get("model_names", [])
    model = model_names[idx] if idx < len(model_names) else ""
    brand = data.get("brand", "")
    await state.update_data(model=model, subcategory=model)

    types_list = await db.get_part_types_by_model(brand, model)
    builder = InlineKeyboardBuilder()
    for ptype, cnt in types_list:
        builder.button(text=f"⚙️ {ptype} ({cnt})", callback_data=f"sin_t_{ptype}")
    builder.button(text="📦 Все запчасти модели", callback_data="sin_t_all")
    builder.button(text="🔙 К моделям", callback_data=f"sin_cat_{brand}")
    builder.button(text="❌ Отмена", callback_data="sin_cancel")
    builder.adjust(1)

    await callback.message.edit_text(
        f"📱 <b>{html.escape(brand)} → {html.escape(model)}</b>\n"
        f"Выберите категорию запчасти:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.part_type)
    await callback.answer()


@router.callback_query(StockInState.part_type, F.data.startswith("sin_t_"))
async def sin_select_part_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    raw_type = callback.data[6:]
    part_type = None if raw_type == "all" else raw_type
    await state.update_data(part_type=part_type)
    data = await state.get_data()
    await _show_sin_parts(callback, state, data["brand"], data["model"], part_type, page=0)
    await state.set_state(StockInState.part)
    await callback.answer()


@router.callback_query(StockInState.part, F.data.startswith("sin_pg_"))
async def sin_paginate_parts(callback: types.CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data[7:])
    data = await state.get_data()
    await _show_sin_parts(callback, state, data.get("brand", ""), data.get("model", ""), data.get("part_type"), page=page)
    await callback.answer()


@router.callback_query(StockInState.part, F.data == "sin_back_to_model")
async def sin_back_to_model(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    brand = data.get("brand", "")
    model = data.get("model", "")
    types_list = await db.get_part_types_by_model(brand, model)

    builder = InlineKeyboardBuilder()
    for ptype, cnt in types_list:
        builder.button(text=f"⚙️ {ptype} ({cnt})", callback_data=f"sin_t_{ptype}")
    builder.button(text="📦 Все запчасти модели", callback_data="sin_t_all")
    builder.button(text="🔙 К моделям", callback_data=f"sin_cat_{brand}")
    builder.button(text="❌ Отмена", callback_data="sin_cancel")
    builder.adjust(1)

    await callback.message.edit_text(
        f"📱 <b>{html.escape(brand)} → {html.escape(model)}</b>\n"
        f"Выберите категорию запчасти:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.part_type)
    await callback.answer()


async def _show_sin_parts(
    callback: types.CallbackQuery,
    state: FSMContext,
    brand: str,
    model: str,
    part_type: str | None,
    page: int = 0,
) -> None:
    page_size = 8
    parts, total = await db.get_parts_by_brand_model_type(brand, model, part_type, page=page, page_size=page_size)
    if not parts:
        builder = _cancel_builder("sin_cancel")
        await callback.message.edit_text(
            "⚠️ В этой категории пока нет запчастей для приходования.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    for row in parts:
        part_id, name, _, _, qty = row[:5]
        builder.button(text=format_part_button_text(name, qty), callback_data=f"sin_part_{part_id}")

    total_pages = max(1, (total + page_size - 1) // page_size)
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="◀ Пред.", callback_data=f"sin_pg_{page - 1}"))
    if total_pages > 1:
        nav_row.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * page_size < total:
        nav_row.append(types.InlineKeyboardButton(text="След. ▶", callback_data=f"sin_pg_{page + 1}"))

    if nav_row:
        builder.row(*nav_row)

    back_target = "sin_back_to_model" if model else f"sin_cat_{brand}"
    builder.row(
        types.InlineKeyboardButton(text="🔙 Назад", callback_data=back_target),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="sin_cancel"),
    )
    builder.adjust(1)

    type_str = f" → <i>{html.escape(part_type)}</i>" if part_type else ""
    await callback.message.edit_text(
        f"📦 <b>{html.escape(brand)} → {html.escape(model)}</b>{type_str} (всего {total} поз.):\n"
        f"Выберите запчасть для поступления:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(StockInState.part, F.data.startswith("sin_part_"))
async def sin_select_part(callback: types.CallbackQuery, state: FSMContext) -> None:
    part_id = int(callback.data[9:])
    part = await db.get_part(part_id)
    if not part:
        await callback.answer("❌ Запчасть не найдена", show_alert=True)
        return

    await state.update_data(
        part_id=part_id,
        part_name=part[1],
        current_qty=part[5],
        retail_price=part[3],
    )
    builder = _cancel_builder("sin_cancel")

    await callback.message.edit_text(
        f"📥 <b>{html.escape(part[1])}</b>\n"
        f"Текущий остаток: <code>{part[5]} шт.</code>\n"
        f"Цена продажи: <code>{part[3]:,.0f} ₽</code>\n\n"
        f"Введите <b>количество</b> поступившего товара (целое число > 0):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.quantity)
    await callback.answer()


@router.message(StockInState.quantity)
async def sin_input_quantity(message: types.Message, state: FSMContext) -> None:
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        builder = _cancel_builder("sin_cancel")
        await message.answer("❌ Введите целое положительное число больше нуля:", reply_markup=builder.as_markup())
        return

    await state.update_data(quantity=qty)
    data = await state.get_data()
    ret_price = data.get("retail_price", 0.0)

    builder = _cancel_builder("sin_cancel")
    await message.answer(
        f"💰 Введите <b>закупочную цену за 1 шт.</b> в рублях:\n"
        f"<i>(Цена продажи в каталоге: {ret_price:,.0f} ₽)</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.cost_price)


@router.message(StockInState.cost_price)
async def sin_input_cost_price(message: types.Message, state: FSMContext) -> None:
    try:
        raw_val = message.text.strip().replace(" ", "").replace(",", ".")
        cost = float(raw_val)
        if cost < 0:
            raise ValueError
    except ValueError:
        builder = _cancel_builder("sin_cancel")
        await message.answer("❌ Введите корректную цену (число от 0):", reply_markup=builder.as_markup())
        return

    await state.update_data(cost_price=cost)
    builder = _skip_and_cancel_builder("sin_skip_supplier", "sin_cancel")
    await message.answer(
        "🏭 Введите <b>поставщика</b> (или нажмите <i>«Пропустить»</i>):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.supplier)


@router.callback_query(StockInState.supplier, F.data == "sin_skip_supplier")
async def sin_skip_supplier_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(supplier=None)
    builder = _skip_and_cancel_builder("sin_skip_notes", "sin_cancel")
    await callback.message.edit_text(
        "📝 Введите <b>примечания к поставке</b> (номер накладной, дефекты и т.д.) или нажмите <i>«Пропустить»</i>:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.notes)
    await callback.answer()


@router.message(StockInState.supplier)
async def sin_input_supplier(message: types.Message, state: FSMContext) -> None:
    supplier = None if message.text.strip().lower() in ("/skip", "пропустить", "-") else message.text.strip()
    await state.update_data(supplier=supplier)
    builder = _skip_and_cancel_builder("sin_skip_notes", "sin_cancel")
    await message.answer(
        "📝 Введите <b>примечания к поставке</b> (или нажмите <i>«Пропустить»</i>):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockInState.notes)


@router.callback_query(StockInState.notes, F.data == "sin_skip_notes")
async def sin_skip_notes_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(notes=None)
    await _show_sin_confirm(callback.message, state)
    await callback.answer()


@router.message(StockInState.notes)
async def sin_input_notes(message: types.Message, state: FSMContext) -> None:
    notes = None if message.text.strip().lower() in ("/skip", "пропустить", "-") else message.text.strip()
    await state.update_data(notes=notes)
    await _show_sin_confirm(message, state)


async def _show_sin_confirm(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить приход", callback_data="sin_confirm")
    builder.button(text="❌ Отмена", callback_data="sin_cancel")
    builder.adjust(2)

    cost_price = data.get("cost_price", 0.0)
    qty = data["quantity"]
    total_cost = cost_price * qty
    cost_line = (
        f"💰 Закупочная цена: <b>{cost_price:,.0f} ₽/шт.</b> (сумма: <b>{total_cost:,.0f} ₽</b>)\n"
        if cost_price > 0 else "💰 Закупочная цена: <i>Не указана (0 ₽)</i>\n"
    )

    text = (
        f"📋 <b>Сводка приходования:</b>\n\n"
        f"📦 Запчасть: <b>{html.escape(data['part_name'])}</b>\n"
        f"🔢 Количество: <code>+{qty} шт.</code>\n"
        f"{cost_line}"
        f"📊 Новый остаток: <code>{data.get('current_qty', 0) + qty} шт.</code>\n"
        f"🏭 Поставщик: <i>{html.escape(data.get('supplier') or 'Не указан')}</i>\n"
        f"📝 Примечания: <i>{html.escape(data.get('notes') or 'Нет')}</i>\n\n"
        f"Подтвердить внесение на склад?"
    )
    await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(StockInState.confirm)


@router.callback_query(StockInState.confirm, F.data == "sin_confirm")
async def sin_confirm_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    cost_price = data.get("cost_price", 0.0)
    ok = await db.add_stock_movement(
        part_id=data["part_id"],
        quantity=data["quantity"],
        movement_type="incoming",
        supplier=data.get("supplier"),
        notes=data.get("notes"),
        user_id=callback.from_user.id,
        cost_price=cost_price,
    )

    if ok:
        new_qty = await db.get_part_quantity(data["part_id"])
        if new_qty <= LOW_STOCK_THRESHOLD:
            await db.create_low_stock_alert(data["part_id"], new_qty)
            await _notify_low_stock(bot, data["part_name"], new_qty)

        cost_txt = f"\n💰 Закупка: <code>{cost_price:,.0f} ₽/шт.</code>" if cost_price > 0 else ""
        await callback.message.edit_text(
            f"✅ <b>Товар успешно оприходован!</b>\n\n"
            f"📦 Запчасть: <b>{html.escape(data['part_name'])}</b>\n"
            f"🔢 Добавлено: <code>+{data['quantity']} шт.</code>\n"
            f"📊 Текущий остаток: <b>{new_qty} шт.</b>"
            f"{cost_txt}\n"
            f"🏭 Поставщик: <i>{html.escape(data.get('supplier') or 'Не указан')}</i>",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text("❌ Ошибка при сохранении данных в БД.")

    await state.clear()
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#                       СПИСАНИЕ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "📤 Списание")
async def start_stock_out(message: types.Message, state: FSMContext) -> None:
    role = await db.get_user_role(message.from_user.id)
    if role not in ("admin", "warehouse_manager"):
        await message.answer("❌ Только менеджер склада или администратор может выполнять списание.")
        return

    await _send_category_selector(message, "sout_cat", "📤 <b>Выберите категорию для списания:</b>", "sout_cancel")
    await state.set_state(StockOutState.category)


@router.callback_query(F.data == "sout_cancel")
async def sout_cancel_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Списание отменено.")
    await callback.answer()


@router.callback_query(F.data == "sout_search_start")
async def sout_search_start_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="📂 Выбрать из категорий", callback_data="sout_back_categories")
    builder.button(text="❌ Отмена", callback_data="sout_cancel")
    builder.adjust(1)
    await callback.message.edit_text(
        "🔍 <b>Быстрый поиск запчасти для списания</b>\n\n"
        "Введите название или модель (например: <code>iPhone 13</code>, <code>Redmi 9</code>):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockOutState.search)
    await callback.answer()


@router.callback_query(F.data == "sout_back_categories")
async def sout_back_categories_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _send_category_selector(callback, "sout_cat", "📤 <b>Выберите категорию для списания:</b>", "sout_cancel")
    await state.set_state(StockOutState.category)
    await callback.answer()


@router.message(StockOutState.search)
async def sout_search_query_msg(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    if len(query) < 2:
        builder = _cancel_builder("sout_cancel")
        await message.answer("❌ Запрос слишком короткий. Введите минимум 2 символа:", reply_markup=builder.as_markup())
        return

    parts, total = await db.search_parts(query, page=0, page_size=20)
    in_stock = [p for p in parts if p[4] > 0]
    if not in_stock:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Искать снова", callback_data="sout_search_start")
        builder.button(text="📂 Выбрать из категорий", callback_data="sout_back_categories")
        builder.button(text="❌ Отмена", callback_data="sout_cancel")
        builder.adjust(1)
        await message.answer(
            f"🔍 По запросу «<b>{html.escape(query)}</b>» не найдено товаров в наличии для списания.\n"
            f"Попробуйте другой запрос или выберите категорию вручную:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    for row in in_stock:
        part_id, name, _, _, qty, cat = row
        builder.button(text=format_part_button_text(name, qty), callback_data=f"sout_part_{part_id}")

    builder.button(text="🔍 Другой поиск", callback_data="sout_search_start")
    builder.button(text="📂 К категориям", callback_data="sout_back_categories")
    builder.button(text="❌ Отмена", callback_data="sout_cancel")
    builder.adjust(1)

    await message.answer(
        f"🔍 Найдено в наличии: <b>{len(in_stock)}</b>:\n"
        f"Выберите запчасть для списания:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockOutState.part)


@router.callback_query(StockOutState.category, F.data.startswith("sout_cat_"))
async def sout_select_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    category = callback.data[9:]
    await state.update_data(category=category)

    subcats = await db.get_subcategories(category)
    if subcats:
        builder = InlineKeyboardBuilder()
        for subcat in subcats:
            builder.button(text=f"📂 {subcat}", callback_data=f"sout_sub_{subcat}")
        builder.button(text="📦 Все запчасти категории", callback_data="sout_sub_all")
        builder.button(text="🔙 К категориям", callback_data="sout_back_categories")
        builder.button(text="❌ Отмена", callback_data="sout_cancel")
        builder.adjust(1)
        await callback.message.edit_text(
            f"📦 <b>{html.escape(category)}</b> — Выберите подкатегорию:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        await state.set_state(StockOutState.subcategory)
    else:
        await state.update_data(subcategory="")
        await _show_sout_parts(callback, category, "", page=0)
        await state.set_state(StockOutState.part)

    await callback.answer()


@router.callback_query(StockOutState.subcategory, F.data.startswith("sout_sub_"))
async def sout_select_subcategory(callback: types.CallbackQuery, state: FSMContext) -> None:
    sub = callback.data[9:]
    subcategory = "" if sub == "all" else sub
    data = await state.get_data()
    await state.update_data(subcategory=subcategory)
    await _show_sout_parts(callback, data["category"], subcategory, page=0)
    await state.set_state(StockOutState.part)
    await callback.answer()


@router.callback_query(StockOutState.part, F.data.startswith("sout_pg_"))
async def sout_paginate_parts(callback: types.CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data[8:])
    data = await state.get_data()
    await _show_sout_parts(callback, data.get("category", ""), data.get("subcategory", ""), page=page)
    await callback.answer()


async def _show_sout_parts(callback: types.CallbackQuery, category: str, subcategory: str, page: int = 0) -> None:
    page_size = 10
    parts, total = await db.get_parts_by_category(category, subcategory or None, page=page, page_size=page_size)
    in_stock = [p for p in parts if p[4] > 0]

    if not in_stock:
        builder = InlineKeyboardBuilder()
        back_cb = f"sout_cat_{category}" if subcategory else "sout_back_categories"
        builder.button(text="🔙 Назад", callback_data=back_cb)
        builder.button(text="❌ Отмена", callback_data="sout_cancel")
        builder.adjust(1)
        await callback.message.edit_text(
            "⚠️ В этой подкатегории нет запчастей в наличии для списания.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    for row in in_stock:
        part_id, name, _, _, qty = row[:5]
        builder.button(text=format_part_button_text(name, qty), callback_data=f"sout_part_{part_id}")

    total_pages = max(1, (total + page_size - 1) // page_size)
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sout_pg_{page - 1}"))
    if total_pages > 1:
        nav_row.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * page_size < total:
        nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"sout_pg_{page + 1}"))

    if nav_row:
        builder.row(*nav_row)

    back_cb = f"sout_cat_{category}" if subcategory else "sout_back_categories"
    builder.row(
        types.InlineKeyboardButton(text="🔙 Назад", callback_data=back_cb),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="sout_cancel"),
    )
    builder.adjust(1)

    title_sub = f" → <i>{html.escape(subcategory)}</i>" if subcategory else ""
    await callback.message.edit_text(
        f"📤 <b>{html.escape(category)}</b>{title_sub} — Выберите запчасть для списания:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(StockOutState.part, F.data.startswith("sout_part_"))
async def sout_select_part(callback: types.CallbackQuery, state: FSMContext) -> None:
    part_id = int(callback.data[10:])
    part = await db.get_part(part_id)
    if not part:
        await callback.answer("❌ Запчасть не найдена", show_alert=True)
        return
    qty = part[5]
    await state.update_data(part_id=part_id, part_name=part[1], current_qty=qty)
    builder = _cancel_builder("sout_cancel")

    await callback.message.edit_text(
        f"📤 <b>{html.escape(part[1])}</b>\n"
        f"В наличии: <code>{qty} шт.</code>\n\n"
        f"Введите <b>количество</b> для списания (1 .. {qty}):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockOutState.quantity)
    await callback.answer()


@router.message(StockOutState.quantity)
async def sout_input_quantity(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
        if qty > data["current_qty"]:
            builder = _cancel_builder("sout_cancel")
            await message.answer(
                f"❌ На складе только <code>{data['current_qty']} шт.</code>! Введите меньшее количество:",
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
            return
    except ValueError:
        builder = _cancel_builder("sout_cancel")
        await message.answer("❌ Введите целое положительное число:", reply_markup=builder.as_markup())
        return

    await state.update_data(quantity=qty)
    builder = _skip_and_cancel_builder("sout_skip_notes", "sout_cancel")
    await message.answer(
        "📝 Введите <b>причину списания / примечание</b> (продажа, брак, инвентаризация) или нажмите <i>«Пропустить»</i>:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(StockOutState.notes)


@router.callback_query(StockOutState.notes, F.data == "sout_skip_notes")
async def sout_skip_notes_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(notes=None)
    await _show_sout_confirm(callback.message, state)
    await callback.answer()


@router.message(StockOutState.notes)
async def sout_input_notes(message: types.Message, state: FSMContext) -> None:
    notes = None if message.text.strip().lower() in ("/skip", "пропустить", "-") else message.text.strip()
    await state.update_data(notes=notes)
    await _show_sout_confirm(message, state)


async def _show_sout_confirm(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить списание", callback_data="sout_confirm")
    builder.button(text="❌ Отмена", callback_data="sout_cancel")
    builder.adjust(2)

    text = (
        f"📋 <b>Сводка списания:</b>\n\n"
        f"📦 Запчасть: <b>{html.escape(data['part_name'])}</b>\n"
        f"🔢 Списание: <code>-{data['quantity']} шт.</code>\n"
        f"📊 Остаток после списания: <code>{data['current_qty'] - data['quantity']} шт.</code>\n"
        f"📝 Причина/примечание: <i>{html.escape(data.get('notes') or 'Нет')}</i>\n\n"
        f"Подтвердить списание со склада?"
    )
    await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(StockOutState.confirm)


@router.callback_query(StockOutState.confirm, F.data == "sout_confirm")
async def sout_confirm_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    ok = await db.add_stock_movement(
        part_id=data["part_id"],
        quantity=data["quantity"],
        movement_type="outgoing",
        notes=data.get("notes"),
        user_id=callback.from_user.id,
    )

    if ok:
        new_qty = await db.get_part_quantity(data["part_id"])
        if new_qty <= LOW_STOCK_THRESHOLD:
            await db.create_low_stock_alert(data["part_id"], new_qty)
            await _notify_low_stock(bot, data["part_name"], new_qty)

        await callback.message.edit_text(
            f"✅ <b>Списание выполнено!</b>\n\n"
            f"📦 Запчасть: <b>{html.escape(data['part_name'])}</b>\n"
            f"🔢 Списано: <code>-{data['quantity']} шт.</code>\n"
            f"📊 Текущий остаток: <b>{new_qty} шт.</b>",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text("❌ Ошибка при списании: недостаточно товара на складе.")

    await state.clear()
    await callback.answer()

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
    category = State()
    subcategory = State()
    part = State()
    quantity = State()
    supplier = State()
    notes = State()
    confirm = State()


class StockOutState(StatesGroup):
    category = State()
    subcategory = State()
    part = State()
    quantity = State()
    notes = State()
    confirm = State()


# ─────────────────────── helpers ───────────────────────────────

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
    target: types.Message,
    cb_prefix: str,
    title: str,
    cancel_cb: str,
) -> None:
    builder = InlineKeyboardBuilder()
    for emoji_name, category in CATEGORIES.items():
        builder.button(text=emoji_name, callback_data=f"{cb_prefix}_{category}")
    builder.button(text="❌ Отмена", callback_data=cancel_cb)
    builder.adjust(2)
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


@router.callback_query(StockInState.category, F.data.startswith("sin_cat_"))
async def sin_select_category(callback: types.CallbackQuery, state: FSMContext) -> None:
    category = callback.data[8:]
    await state.update_data(category=category)

    subcats = await db.get_subcategories(category)
    if subcats:
        builder = InlineKeyboardBuilder()
        for subcat in subcats:
            builder.button(text=f"📂 {subcat}", callback_data=f"sin_sub_{subcat}")
        builder.button(text="📦 Все запчасти категории", callback_data="sin_sub_all")
        builder.button(text="❌ Отмена", callback_data="sin_cancel")
        builder.adjust(1)
        await callback.message.edit_text(
            f"📦 <b>{html.escape(category)}</b> — Выберите подкатегорию:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        await state.set_state(StockInState.subcategory)
    else:
        await state.update_data(subcategory="")
        await _show_sin_parts(callback, category, "")
        await state.set_state(StockInState.part)

    await callback.answer()


@router.callback_query(StockInState.subcategory, F.data.startswith("sin_sub_"))
async def sin_select_subcategory(callback: types.CallbackQuery, state: FSMContext) -> None:
    sub = callback.data[8:]
    subcategory = "" if sub == "all" else sub
    data = await state.get_data()
    await state.update_data(subcategory=subcategory)
    await _show_sin_parts(callback, data["category"], subcategory)
    await state.set_state(StockInState.part)
    await callback.answer()


async def _show_sin_parts(callback: types.CallbackQuery, category: str, subcategory: str) -> None:
    parts, _ = await db.get_parts_by_category(category, subcategory or None, page_size=60)
    if not parts:
        builder = _cancel_builder("sin_cancel")
        await callback.message.edit_text(
            "⚠️ В этой категории пока нет запчастей для приходования.\n"
            "Сначала добавьте запчасть в базу через админ-панель.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    for part_id, name, _, _, qty in parts:
        builder.button(text=f"{name[:30]} ({qty} шт.)", callback_data=f"sin_part_{part_id}")
    builder.button(text="❌ Отмена", callback_data="sin_cancel")
    builder.adjust(1)

    title_sub = f" → <i>{html.escape(subcategory)}</i>" if subcategory else ""
    await callback.message.edit_text(
        f"📦 <b>{html.escape(category)}</b>{title_sub} — Выберите запчасть для поступления:",
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

    await state.update_data(part_id=part_id, part_name=part[1], current_qty=part[5])
    builder = _cancel_builder("sin_cancel")

    await callback.message.edit_text(
        f"📥 <b>{html.escape(part[1])}</b>\n"
        f"Текущий остаток: <code>{part[5]} шт.</code>\n\n"
        f"Введите <b>количество</b> поступившего товара (целое положительное число):",
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

    text = (
        f"📋 <b>Сводка приходования:</b>\n\n"
        f"📦 Запчасть: <b>{html.escape(data['part_name'])}</b>\n"
        f"🔢 Количество: <code>+{data['quantity']} шт.</code>\n"
        f"📊 Новый остаток: <code>{data.get('current_qty', 0) + data['quantity']} шт.</code>\n"
        f"🏭 Поставщик: <i>{html.escape(data.get('supplier') or 'Не указан')}</i>\n"
        f"📝 Примечания: <i>{html.escape(data.get('notes') or 'Нет')}</i>\n\n"
        f"Подтвердить внесение на склад?"
    )
    await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(StockInState.confirm)


@router.callback_query(StockInState.confirm, F.data == "sin_confirm")
async def sin_confirm_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    ok = await db.add_stock_movement(
        part_id=data["part_id"],
        quantity=data["quantity"],
        movement_type="incoming",
        supplier=data.get("supplier"),
        notes=data.get("notes"),
        user_id=callback.from_user.id,
    )

    if ok:
        new_qty = await db.get_part_quantity(data["part_id"])
        if new_qty <= LOW_STOCK_THRESHOLD:
            await db.create_low_stock_alert(data["part_id"], new_qty)
            await _notify_low_stock(bot, data["part_name"], new_qty)

        await callback.message.edit_text(
            f"✅ <b>Товар успешно оприходован!</b>\n\n"
            f"📦 Запчасть: <b>{html.escape(data['part_name'])}</b>\n"
            f"🔢 Добавлено: <code>+{data['quantity']} шт.</code>\n"
            f"📊 Текущий остаток: <b>{new_qty} шт.</b>\n"
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
        await _show_sout_parts(callback, category, "")
        await state.set_state(StockOutState.part)

    await callback.answer()


@router.callback_query(StockOutState.subcategory, F.data.startswith("sout_sub_"))
async def sout_select_subcategory(callback: types.CallbackQuery, state: FSMContext) -> None:
    sub = callback.data[9:]
    subcategory = "" if sub == "all" else sub
    data = await state.get_data()
    await state.update_data(subcategory=subcategory)
    await _show_sout_parts(callback, data["category"], subcategory)
    await state.set_state(StockOutState.part)
    await callback.answer()


async def _show_sout_parts(callback: types.CallbackQuery, category: str, subcategory: str) -> None:
    parts, _ = await db.get_parts_by_category(category, subcategory or None, page_size=60)
    in_stock = [p for p in parts if p[4] > 0]

    if not in_stock:
        builder = _cancel_builder("sout_cancel")
        await callback.message.edit_text(
            "⚠️ В этой категории нет запчастей в наличии для списания.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    for part_id, name, _, _, qty in in_stock:
        builder.button(text=f"{name[:30]} ({qty} шт.)", callback_data=f"sout_part_{part_id}")
    builder.button(text="❌ Отмена", callback_data="sout_cancel")
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

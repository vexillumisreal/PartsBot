"""handlers/catalog.py — каталог, поиск с полной пагинацией, карточки запчастей и интеграция с корзиной."""
import html
import logging
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import CATEGORIES, PAGE_SIZE, CURRENCY
from handlers.orders import add_to_cart

logger = logging.getLogger(__name__)
router = Router()


class SearchState(StatesGroup):
    query = State()


# ─────────────────── helpers ───────────────────

def _parts_text_html(
    parts: list[tuple],
    is_wholesale: bool,
    title: str,
    page: int,
    total: int,
    page_size: int,
) -> str:
    total_pages = max(1, -(-total // page_size))
    text = f"<b>{title}</b>\n"
    text += f"<i>Страница {page + 1} из {total_pages} (всего {total} поз.)</i>\n\n"
    for row in parts:
        part_id, name, ret_price, wh_price, qty = row[:5]
        price = wh_price if is_wholesale else ret_price
        icon = "✅" if qty > 0 else "❌"
        status_txt = f"{qty} шт." if qty > 0 else "нет в наличии"
        text += (
            f"{icon} <b>{html.escape(name)}</b>\n"
            f"   Цена: <b>{price:,.0f} {CURRENCY}</b> | Наличие: <code>{status_txt}</code>\n\n"
        )
    return text


def _pagination_builder(
    cb_prefix: str,
    page: int,
    total: int,
    page_size: int,
    back_cb: str,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, -(-total // page_size))

    nav_buttons = []
    if page > 0:
        nav_buttons.append((f"◀ Пред. ({page})", f"{cb_prefix}_p{page - 1}"))
    if (page + 1) * page_size < total:
        nav_buttons.append((f"След. ({page + 2}) ▶", f"{cb_prefix}_p{page + 1}"))

    for btn_text, btn_cb in nav_buttons:
        builder.button(text=btn_text, callback_data=btn_cb)

    if nav_buttons:
        builder.adjust(len(nav_buttons))

    bottom = InlineKeyboardBuilder()
    bottom.button(text="🔙 Назад", callback_data=back_cb)
    bottom.button(text="🛒 Корзина", callback_data="view_cart")
    bottom.adjust(2)

    builder.attach(bottom)
    return builder


# ─────────────────── КОДЫ БРЕНДОВ И КАТЕГОРИЙ (КОМПАКТНЫЕ CALLBACKS) ───────────────────

BRAND_CODES: dict[str, str] = {
    "iPhone": "iph",
    "Samsung": "sam",
    "Xiaomi": "xia",
    "Huawei / Honor": "hua",
    "Tecno": "tec",
    "Infinix": "inf",
    "Realme / Oppo": "rea",
    "iPad": "ipa",
    "Vivo": "viv",
    "Другие": "oth",
}
CODE_TO_BRAND: dict[str, str] = {v: k for k, v in BRAND_CODES.items()}

TYPE_CODES: dict[str, str] = {
    "Дисплеи": "disp",
    "Аккумуляторы": "bat",
    "Крышки": "cov",
    "Шлейфы": "flex",
    "Камеры": "cam",
    "Динамики": "spk",
    "Разное": "oth",
    "all": "all",
}
CODE_TO_TYPE: dict[str, str] = {v: k for k, v in TYPE_CODES.items()}


# ─────────────────── ГЛАВНОЕ МЕНЮ КАТАЛОГА (УРОВЕНЬ 1: БРЕНДЫ) ───────────────────

async def _send_category_menu(target: types.Message | types.CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    brands = await db.get_brands()
    for brand, cnt in brands:
        b_code = BRAND_CODES.get(brand, brand[:4].lower())
        builder.button(text=f"📱 {brand} ({cnt})", callback_data=f"cbr_{b_code}")
    builder.button(text="🔍 Поиск", callback_data="catalog_start_search")
    builder.button(text="🛒 Корзина", callback_data="view_cart")
    builder.adjust(2)

    text = "🛍️ <b>Каталог запчастей:</b>\nВыберите бренд устройства:"
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.message(F.text == "📦 Каталог запчастей")
async def show_catalog_menu(message: types.Message) -> None:
    await _send_category_menu(message)


@router.callback_query(F.data == "back_catalog")
async def back_to_catalog(callback: types.CallbackQuery) -> None:
    await _send_category_menu(callback)


# ─────────────────── УРОВЕНЬ 2: МОДЕЛИ ВЫБРАННОГО БРЕНДА ───────────────────

@router.callback_query(F.data.startswith("cbr_"))
async def show_brand_models(callback: types.CallbackQuery) -> None:
    raw = callback.data[4:]
    page = 0
    if "_p" in raw:
        raw, p_str = raw.rsplit("_p", 1)
        page = int(p_str) if p_str.isdigit() else 0

    b_code = raw
    brand = CODE_TO_BRAND.get(b_code, b_code)
    models = await db.get_models_by_brand(brand)

    if not models:
        await _show_parts_list(callback, brand, "", None, page=0)
        await callback.answer()
        return

    page_size = 8
    total = len(models)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    page_models = models[page * page_size : (page + 1) * page_size]

    builder = InlineKeyboardBuilder()
    for idx, (m_name, cnt) in enumerate(page_models, start=page * page_size):
        clean_btn = m_name[:26]
        builder.button(text=f"📂 {clean_btn} ({cnt})", callback_data=f"cmd_{b_code}_{idx}")
    builder.adjust(1)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="◀ Пред.", callback_data=f"cbr_{b_code}_p{page - 1}"))
    if total_pages > 1:
        nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * page_size < total:
        nav.append(types.InlineKeyboardButton(text="След. ▶", callback_data=f"cbr_{b_code}_p{page + 1}"))
    if nav:
        builder.row(*nav)

    builder.row(
        types.InlineKeyboardButton(text="🔙 К брендам", callback_data="back_catalog"),
        types.InlineKeyboardButton(text="🛒 Корзина", callback_data="view_cart"),
    )

    await callback.message.edit_text(
        f"📱 <b>{html.escape(brand)}</b> — выберите модель (всего {total}):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


# ─────────────────── УРОВЕНЬ 3: КАТЕГОРИИ ЗАПЧАСТЕЙ МОДЕЛИ ───────────────────

@router.callback_query(F.data.startswith("cmd_"))
async def show_model_part_types(callback: types.CallbackQuery) -> None:
    parts_tok = callback.data[4:].split("_")
    b_code = parts_tok[0]
    model_idx = int(parts_tok[1])

    brand = CODE_TO_BRAND.get(b_code, b_code)
    models = await db.get_models_by_brand(brand)
    if model_idx >= len(models):
        await callback.answer("Модель не найдена", show_alert=True)
        return
    model = models[model_idx][0]

    types_list = await db.get_part_types_by_model(brand, model)
    total_parts = sum(c for _, c in types_list)

    builder = InlineKeyboardBuilder()
    for ptype, cnt in types_list:
        t_code = TYPE_CODES.get(ptype, "oth")
        builder.button(text=f"⚙️ {ptype} ({cnt})", callback_data=f"ctp_{b_code}_{model_idx}_{t_code}")
    builder.button(text=f"📦 Все запчасти модели ({total_parts})", callback_data=f"ctp_{b_code}_{model_idx}_all")
    builder.button(text=f"🔙 К моделям {brand}", callback_data=f"cbr_{b_code}")
    builder.button(text="🛒 Корзина", callback_data="view_cart")
    builder.adjust(1)

    await callback.message.edit_text(
        f"📱 <b>{html.escape(brand)} → {html.escape(model)}</b>\n"
        f"Выберите категорию запчасти:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


# ─────────────────── УРОВЕНЬ 4: СПИСОК ЗАПЧАСТЕЙ ───────────────────

@router.callback_query(F.data.startswith("ctp_"))
async def show_parts_by_type(callback: types.CallbackQuery) -> None:
    raw = callback.data[4:]
    page = 0
    if "_p" in raw:
        raw, p_str = raw.rsplit("_p", 1)
        page = int(p_str) if p_str.isdigit() else 0

    tokens = raw.split("_")
    b_code = tokens[0]
    model_idx = int(tokens[1])
    t_code = tokens[2]

    brand = CODE_TO_BRAND.get(b_code, b_code)
    models = await db.get_models_by_brand(brand)
    if model_idx >= len(models):
        await callback.answer("Модель не найдена", show_alert=True)
        return
    model = models[model_idx][0]

    part_type = None if t_code == "all" else CODE_TO_TYPE.get(t_code, t_code)
    await _show_parts_list(
        callback,
        brand=brand,
        model=model,
        part_type=part_type,
        page=page,
        b_code=b_code,
        model_idx=model_idx,
        t_code=t_code,
    )
    await callback.answer()


async def _show_parts_list(
    callback: types.CallbackQuery,
    brand: str,
    model: str,
    part_type: str | None,
    page: int,
    b_code: str = "",
    model_idx: int = 0,
    t_code: str = "all",
) -> None:
    status = await db.get_user_status(callback.from_user.id)
    is_wholesale = status == "wholesale"

    parts, total = await db.get_parts_by_brand_model_type(brand, model, part_type, page, PAGE_SIZE)

    if not parts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data=f"cmd_{b_code}_{model_idx}" if model else "back_catalog")
        title_target = f"категории <b>{html.escape(part_type or 'Все')}</b>"
        await callback.message.edit_text(
            f"⚠️ В {title_target} пока нет запчастей.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    type_name = part_type if part_type else "Все запчасти"
    title = f"📦 <b>{html.escape(brand)} → {html.escape(model)} → {html.escape(type_name)}</b>"
    if is_wholesale:
        title += " <i>(Оптовые цены)</i>"

    cb_prefix = f"ctp_{b_code}_{model_idx}_{t_code}"
    back_cb = f"cmd_{b_code}_{model_idx}" if model else "back_catalog"

    total_pages = max(1, -(-total // PAGE_SIZE))
    text = f"{title}\n<i>Страница {page + 1} из {total_pages} (всего {total} поз.)</i>\n\n"
    for row in parts:
        part_id, name, ret_price, wh_price, qty = row[:5]
        price = wh_price if is_wholesale else ret_price
        stock_badge = "✅" if qty > 0 else "❌"
        status_txt = f"{qty} шт." if qty > 0 else "нет в наличии"
        text += (
            f"{stock_badge} <b>{html.escape(name)}</b>\n"
            f"   Цена: <b>{price:,.0f} {CURRENCY}</b> | Наличие: <code>{status_txt}</code>\n\n"
        )

    detail_builder = InlineKeyboardBuilder()
    for row in parts:
        part_id, name, ret_price, wh_price, qty = row[:5]
        price = wh_price if is_wholesale else ret_price
        stock_badge = "✅" if qty > 0 else "❌"
        btn_name = name[:26]
        detail_builder.button(
            text=f"{stock_badge} {btn_name} — {price:,.0f} {CURRENCY}",
            callback_data=f"part_detail_{part_id}",
        )
    detail_builder.adjust(1)

    pag_builder = _pagination_builder(cb_prefix, page, total, PAGE_SIZE, back_cb)
    detail_builder.attach(pag_builder)

    await callback.message.edit_text(text, reply_markup=detail_builder.as_markup(), parse_mode="HTML")


# ─────────────────── СОВМЕСТИМОСТЬ СО СТАРЫМИ CALLBACKS ───────────────────

@router.callback_query(F.data.startswith("cat_"))
async def show_category_fallback(callback: types.CallbackQuery) -> None:
    cat = callback.data[4:]
    b_code = BRAND_CODES.get(cat)
    if b_code:
        callback.data = f"cbr_{b_code}"
        await show_brand_models(callback)
    else:
        await back_to_catalog(callback)


# ─────────────────── КАРТОЧКА ЗАПЧАСТИ ───────────────────

@router.callback_query(F.data.startswith("part_detail_"))
async def show_part_detail(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[12:])
    part = await db.get_part(part_id)

    if not part:
        await callback.answer("❌ Запчасть не найдена или удалена", show_alert=True)
        return

    pid, name, cost_price, ret_price, wh_price, qty, category, subcategory, supplier, threshold = part
    status = await db.get_user_status(callback.from_user.id)
    role = await db.get_user_role(callback.from_user.id)
    is_wholesale = status == "wholesale"

    price = wh_price if is_wholesale else ret_price
    stock_badge = f"✅ В наличии (<b>{qty} шт.</b>)" if qty > 0 else "❌ <b>Нет в наличии</b>"

    text = (
        f"🔎 <b>{html.escape(name)}</b>\n\n"
        f"📁 Категория: <b>{html.escape(category)}</b>"
        + (f" → <i>{html.escape(subcategory)}</i>" if subcategory else "")
        + "\n"
        f"💰 Цена: <b>{price:,.0f} {CURRENCY}</b> "
        + ("<i>(Опт)</i>" if is_wholesale else "<i>(Розница)</i>")
        + "\n"
        f"📦 Статус: {stock_badge}\n"
    )

    if role in ("admin", "sales_manager", "warehouse_manager"):
        margin = ((ret_price - cost_price) / ret_price * 100) if ret_price > 0 else 0
        text += (
            f"\n🔒 <b>Для сотрудников:</b>\n"
            f"• ID детали: <code>{pid}</code>\n"
            f"• Себестоимость: <code>{cost_price:,.0f} {CURRENCY}</code>\n"
            f"• Оптовая цена: <code>{wh_price:,.0f} {CURRENCY}</code>\n"
            f"• Розничная цена: <code>{ret_price:,.0f} {CURRENCY}</code>\n"
            f"• Маржа: <code>{margin:.1f}%</code>\n"
            f"• Порог низкого остатка: <code>{threshold} шт.</code>\n"
        )
        if supplier:
            text += f"• Поставщик: <i>{html.escape(supplier)}</i>\n"

    builder = InlineKeyboardBuilder()

    # Кнопка добавления в корзину
    if qty > 0:
        builder.button(text="🛍️ Добавить в корзину", callback_data=f"cart_add_{pid}")
        builder.button(text="🛒 Перейти в корзину", callback_data="view_cart")
    else:
        builder.button(text="⚠️ Уведомить о поступлении", callback_data=f"notify_stock_{pid}")

    # Для администраторов — быстрое управление карточкой
    if role in ("admin", "warehouse_manager"):
        builder.button(text="✏️ Редактировать запчасть", callback_data=f"adm_edit_part_{pid}")

    b_code = BRAND_CODES.get(category, "")
    back_cb = f"cbr_{b_code}" if b_code else "back_catalog"
    builder.button(text="🔙 Назад к списку", callback_data=back_cb)
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cart_add_"))
async def add_part_to_cart_cb(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[9:])
    part = await db.get_part(part_id)
    if not part:
        await callback.answer("Запчасть не найдена", show_alert=True)
        return

    status = await db.get_user_status(callback.from_user.id)
    price = part[4] if status == "wholesale" else part[3]
    name = part[1]

    add_to_cart(callback.from_user.id, part_id, name, price, quantity=1)
    await callback.answer(f"✅ «{name[:25]}» добавлена в корзину!", show_alert=False)


@router.callback_query(F.data.startswith("notify_stock_"))
async def notify_stock_cb(callback: types.CallbackQuery) -> None:
    await callback.answer("✅ Мы уведомим вас, когда товар поступит на склад!", show_alert=True)


# ─────────────────── ПОИСК С ПАГИНАЦИЕЙ ───────────────────

@router.message(F.text == "🔍 Поиск")
@router.callback_query(F.data == "catalog_start_search")
async def start_search(target: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена поиска", callback_data="search_cancel")

    text = "🔍 <b>Поиск запчастей:</b>\n\nВведите название, модель или ключевые слова (например: <i>iPhone 13, дисплей Samsung</i>):"
    if isinstance(target, types.CallbackQuery):
        await target.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(SearchState.query)


@router.callback_query(F.data == "search_cancel")
async def cancel_search_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Поиск отменён.")
    await callback.answer()


@router.message(SearchState.query)
async def process_search_query(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    await state.clear()
    await render_search_results(message, query, page=0)


async def render_search_results(target: types.Message | types.CallbackQuery, query: str, page: int) -> None:
    user_id = target.from_user.id
    status = await db.get_user_status(user_id)
    is_wholesale = status == "wholesale"

    parts, total = await db.search_parts(query, page=page, page_size=PAGE_SIZE)

    if not parts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Искать снова", callback_data="catalog_start_search")
        builder.button(text="📦 В каталог", callback_data="back_catalog")
        builder.adjust(1)
        text = f"🔍 По запросу «<b>{html.escape(query)}</b>» ничего не найдено.\nПопробуйте изменить запрос."
        if isinstance(target, types.CallbackQuery):
            await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        return

    total_pages = max(1, -(-total // PAGE_SIZE))
    text = (
        f"🔍 <b>Результаты поиска по запросу:</b> «{html.escape(query)}»\n"
        f"<i>Страница {page + 1} из {total_pages} (найдено {total} поз.)</i>\n\n"
    )

    detail_builder = InlineKeyboardBuilder()
    for row in parts:
        part_id, name, ret_price, wh_price, qty = row[:5]
        price = wh_price if is_wholesale else ret_price
        stock_badge = "✅" if qty > 0 else "❌"
        status_txt = f"{qty} шт." if qty > 0 else "нет"
        text += (
            f"{stock_badge} <b>{html.escape(name)}</b>\n"
            f"   Цена: <b>{price:,.0f} {CURRENCY}</b> | Остаток: <code>{status_txt}</code>\n\n"
        )
        detail_builder.button(
            text=f"{stock_badge} {name[:28]} — {price:.0f} {CURRENCY}",
            callback_data=f"part_detail_{part_id}",
        )
    detail_builder.adjust(1)

    # Пагинация для поиска
    nav_buttons = []
    # Кодируем запрос без пробелов для безопасного callback_data (ограничение TG: 64 байта)
    # Используем короткий префикс `sp_{page}` и сохраняем query или передаем в callback
    safe_query = query[:20].replace(" ", "_")
    if page > 0:
        nav_buttons.append((f"◀ Пред. ({page})", f"srch_{safe_query}_p{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav_buttons.append((f"След. ({page + 2}) ▶", f"srch_{safe_query}_p{page + 1}"))

    pag_builder = InlineKeyboardBuilder()
    for btn_text, btn_cb in nav_buttons:
        pag_builder.button(text=btn_text, callback_data=btn_cb)
    if nav_buttons:
        pag_builder.adjust(len(nav_buttons))

    bottom = InlineKeyboardBuilder()
    bottom.button(text="🔍 Новый поиск", callback_data="catalog_start_search")
    bottom.button(text="📦 В каталог", callback_data="back_catalog")
    bottom.button(text="🛒 Корзина", callback_data="view_cart")
    bottom.adjust(2, 1)

    detail_builder.attach(pag_builder)
    detail_builder.attach(bottom)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=detail_builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=detail_builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("srch_"))
async def search_pagination_cb(callback: types.CallbackQuery) -> None:
    raw = callback.data[5:]  # убираем 'srch_'
    page = 0
    if "_p" in raw:
        raw_query, p_str = raw.rsplit("_p", 1)
        page = int(p_str) if p_str.isdigit() else 0
        query = raw_query.replace("_", " ")
    else:
        query = raw.replace("_", " ")

    await render_search_results(callback, query, page=page)
    await callback.answer()

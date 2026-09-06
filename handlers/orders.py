"""handlers/orders.py — Корзина, оформление заказов и управление заказами для менеджеров."""
import os
import html
import logging
import qrcode
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import (
    CURRENCY,
    WAREHOUSE_ADDRESS,
    WAREHOUSE_HOURS,
    WAREHOUSE_PHONE,
    WAREHOUSE_GEO_LINK,
    PAYMENT_REQUISITES,
    QR_DIR,
)

logger = logging.getLogger(__name__)
router = Router()

DELIVERY_NAMES: dict[str, str] = {
    "pickup": "🏬 Самовывоз со склада",
    "courier": "🚴 Курьер по городу",
    "cdek": "📦 СДЭК / Транспортная компания",
}

PAYMENT_NAMES: dict[str, str] = {
    "cash": "💵 При получении (наличные/терминал)",
    "sbp": "⚡ Перевод по СБП (с QR-кодом)",
    # "invoice": "🏢 Счёт для юрлиц (безнал)",  # Временно отключено по запросу (продолжим позже)
}

PAYMENT_STATUS_NAMES: dict[str, str] = {
    "unpaid": "⏳ Ожидает оплаты",
    "paid": "✅ Оплачен",
    "refunded": "↩️ Возврат",
}

# In-memory хранилище корзин: user_id -> {part_id: {'part_id': id, 'name': str, 'price': float, 'quantity': int}}
CARTS: dict[int, dict[int, dict]] = {}


def get_cart(user_id: int) -> dict[int, dict]:
    return CARTS.setdefault(user_id, {})


def add_to_cart(user_id: int, part_id: int, name: str, price: float, quantity: int = 1) -> None:
    cart = get_cart(user_id)
    if part_id in cart:
        cart[part_id]["quantity"] += quantity
    else:
        cart[part_id] = {
            "part_id": part_id,
            "name": name,
            "part_name": name,
            "price": price,
            "quantity": quantity,
        }


def remove_from_cart(user_id: int, part_id: int) -> None:
    cart = get_cart(user_id)
    cart.pop(part_id, None)


def clear_cart(user_id: int) -> None:
    CARTS[user_id] = {}


def get_cart_summary(user_id: int) -> tuple[float, int]:
    cart = get_cart(user_id)
    total_amount = sum(item["price"] * item["quantity"] for item in cart.values())
    total_items = sum(item["quantity"] for item in cart.values())
    return total_amount, total_items


class CheckoutState(StatesGroup):
    contact = State()
    delivery_method = State()
    delivery_address = State()
    payment_method = State()
    notes = State()


# ─────────────────── РЕНДЕР КОРЗИНЫ ───────────────────

def build_cart_message(user_id: int) -> tuple[str, InlineKeyboardBuilder]:
    cart = get_cart(user_id)
    builder = InlineKeyboardBuilder()

    if not cart:
        text = "🛒 <b>Ваша корзина пуста</b>\n\nВыберите нужные запчасти в каталоге и добавьте их в корзину!"
        builder.button(text="📦 Перейти в каталог", callback_data="back_catalog")
        return text, builder

    total_amount, total_count = get_cart_summary(user_id)
    text = "🛒 <b>Ваша корзина:</b>\n\n"

    for i, (part_id, item) in enumerate(cart.items(), 1):
        item_total = item["price"] * item["quantity"]
        text += (
            f"{i}. <b>{html.escape(item['name'])}</b>\n"
            f"   {item['quantity']} шт. × {item['price']:.0f} {CURRENCY} = <b>{item_total:.0f} {CURRENCY}</b>\n\n"
        )
        # Кнопки изменения количества
        builder.button(text="➖", callback_data=f"cart_dec_{part_id}")
        builder.button(text=f"{item['quantity']} шт", callback_data="cart_noop")
        builder.button(text="➕", callback_data=f"cart_inc_{part_id}")
        builder.button(text="🗑️", callback_data=f"cart_del_{part_id}")

    text += f"━━━━━━━━━━━━━━━━━━\n"
    text += f"💰 Итого к оплате: <b>{total_amount:,.0f} {CURRENCY}</b> ({total_count} шт.)\n"

    # Корзина: ряд по 4 кнопки на товар
    builder.adjust(4)

    # Управляющие кнопки
    bottom_builder = InlineKeyboardBuilder()
    bottom_builder.button(text="✅ Оформить заказ", callback_data="cart_checkout")
    bottom_builder.button(text="🗑️ Очистить", callback_data="cart_clear")
    bottom_builder.button(text="📦 В каталог", callback_data="back_catalog")
    bottom_builder.adjust(2, 1)

    builder.attach(bottom_builder)
    return text, builder


@router.message(F.text == "🛒 Корзина")
@router.message(Command("cart"))
async def show_cart(message: types.Message) -> None:
    text, builder = build_cart_message(message.from_user.id)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "view_cart")
async def show_cart_cb(callback: types.CallbackQuery) -> None:
    text, builder = build_cart_message(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "cart_noop")
async def cart_noop_cb(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("cart_inc_"))
async def cart_inc(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[9:])
    cart = get_cart(callback.from_user.id)
    if part_id in cart:
        # Проверим наличие на складе
        stock_qty = await db.get_part_quantity(part_id)
        if cart[part_id]["quantity"] < stock_qty:
            cart[part_id]["quantity"] += 1
        else:
            await callback.answer(f"⚠️ На складе всего {stock_qty} шт.", show_alert=True)
            return
    text, builder = build_cart_message(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cart_dec_"))
async def cart_dec(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[9:])
    cart = get_cart(callback.from_user.id)
    if part_id in cart:
        if cart[part_id]["quantity"] > 1:
            cart[part_id]["quantity"] -= 1
        else:
            remove_from_cart(callback.from_user.id, part_id)
    text, builder = build_cart_message(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cart_del_"))
async def cart_del(callback: types.CallbackQuery) -> None:
    part_id = int(callback.data[9:])
    remove_from_cart(callback.from_user.id, part_id)
    text, builder = build_cart_message(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("Товар удалён из корзины")


@router.callback_query(F.data == "cart_clear")
async def cart_clear_cb(callback: types.CallbackQuery) -> None:
    clear_cart(callback.from_user.id)
    text, builder = build_cart_message(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("Корзина очищена")


# ─────────────────── ОФОРМЛЕНИЕ ЗАКАЗА ───────────────────

@router.callback_query(F.data == "cart_checkout")
async def start_checkout(callback: types.CallbackQuery, state: FSMContext) -> None:
    cart = get_cart(callback.from_user.id)
    if not cart:
        await callback.answer("Ваша корзина пуста!", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_checkout")

    await callback.message.answer(
        "📞 <b>Шаг 1 из 4: Контактные данные</b>\n\n"
        "Введите ваш номер телефона для связи и подтверждения заказа (например, <code>+7 999 123-45-67</code>):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(CheckoutState.contact)
    await callback.answer()


@router.callback_query(F.data == "cancel_checkout")
async def cancel_checkout_cb(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Оформление заказа отменено.")
    await callback.answer()


@router.message(CheckoutState.contact)
async def checkout_contact_step(message: types.Message, state: FSMContext) -> None:
    contact = message.text.strip()
    if len(contact) < 3:
        await message.answer("❌ Слишком короткий контакт. Введите телефон или @username:")
        return
    await state.update_data(contact=contact)

    builder = InlineKeyboardBuilder()
    builder.button(text="🏬 Самовывоз со склада (бесплатно)", callback_data="chk_del_pickup")
    builder.button(text="🚴 Курьер по городу", callback_data="chk_del_courier")
    builder.button(text="📦 СДЭК / ТК (по РФ)", callback_data="chk_del_cdek")
    builder.button(text="❌ Отмена", callback_data="cancel_checkout")
    builder.adjust(1)

    await message.answer(
        "🚚 <b>Шаг 2 из 4: Способ получения заказа</b>\n\n"
        "Выберите удобный для вас вариант доставки или самовывоза:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(CheckoutState.delivery_method)


@router.callback_query(CheckoutState.delivery_method, F.data.startswith("chk_del_"))
async def select_delivery_method(callback: types.CallbackQuery, state: FSMContext) -> None:
    del_method = callback.data[8:]
    await state.update_data(delivery_method=del_method)

    if del_method == "pickup":
        await state.update_data(delivery_address=WAREHOUSE_ADDRESS)
        await render_payment_step(callback.message, state, is_edit=True)
    elif del_method == "courier":
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отмена", callback_data="cancel_checkout")
        await callback.message.edit_text(
            "🚴 <b>Курьерская доставка по городу:</b>\n\n"
            "Укажите точный адрес доставки (город, улица, дом, подъезд, этаж, кв./офис) и удобное время:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        await state.set_state(CheckoutState.delivery_address)
    elif del_method == "cdek":
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отмена", callback_data="cancel_checkout")
        await callback.message.edit_text(
            "📦 <b>Доставка СДЭК / Транспортной компанией:</b>\n\n"
            "Укажите город получения, ФИО получателя и адрес удобного пункта выдачи (ПВЗ СДЭК):",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        await state.set_state(CheckoutState.delivery_address)
    await callback.answer()


@router.message(CheckoutState.delivery_address)
async def checkout_address_step(message: types.Message, state: FSMContext) -> None:
    address = message.text.strip()
    if len(address) < 3:
        await message.answer("❌ Введите корректный адрес доставки:")
        return
    await state.update_data(delivery_address=address)
    await render_payment_step(message, state, is_edit=False)


def generate_sbp_qr(order_id: int, total_amount: float) -> str | None:
    """Генерирует QR-код для перевода по СБП и возвращает путь к PNG файлу."""
    try:
        static_qr = PAYMENT_REQUISITES.get("qr_image")
        if static_qr and os.path.isfile(static_qr):
            return static_qr

        sbp_link = PAYMENT_REQUISITES.get("sbp_link")
        if sbp_link:
            payload = sbp_link
        else:
            phone = PAYMENT_REQUISITES.get("phone", "")
            bank = PAYMENT_REQUISITES.get("bank", "")
            receiver = PAYMENT_REQUISITES.get("receiver", "")
            clean_phone = "".join(c for c in phone if c.isdigit())
            if any(k in bank.lower() for k in ("тинькофф", "т-банк", "t-bank", "tinkoff")):
                payload = f"https://www.tbank.ru/rm/{clean_phone}/"
            else:
                payload = f"СБП|Тел:{phone}|Банк:{bank}|Получатель:{receiver}|Заказ:#{order_id}|Сумма:{total_amount:.0f}RUB"

        qr_path = os.path.join(QR_DIR, f"sbp_order_{order_id}.png")
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=3,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
        return qr_path
    except Exception:
        logger.exception("Ошибка при генерации QR-кода СБП для заказа #%s", order_id)
        return None


async def render_payment_step(target: types.Message, state: FSMContext, is_edit: bool = False) -> None:
    await state.set_state(CheckoutState.payment_method)
    builder = InlineKeyboardBuilder()
    builder.button(text="💵 При получении (наличные/терминал)", callback_data="chk_pay_cash")
    builder.button(text="⚡ Перевод по СБП (с QR-кодом)", callback_data="chk_pay_sbp")
    # TODO: Оплата по счёту для юрлиц (безнал) — закомментировано по запросу, продолжим позже
    # builder.button(text="🏢 Счёт для юрлиц (безнал)", callback_data="chk_pay_invoice")
    builder.button(text="❌ Отмена", callback_data="cancel_checkout")
    builder.adjust(1)

    text = (
        "💳 <b>Шаг 3 из 4: Способ оплаты</b>\n\n"
        "Выберите предпочтительный вариант оплаты:"
    )
    if is_edit:
        await target.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(CheckoutState.payment_method, F.data.startswith("chk_pay_"))
async def select_payment_method(callback: types.CallbackQuery, state: FSMContext) -> None:
    pay_method = callback.data[8:]
    # if pay_method == "invoice":
    #     # TODO: Оплата по счёту для юрлиц (безнал) — продолжим позже
    #     await callback.answer("Оплата по счёту временно отключена", show_alert=True)
    #     return
    await state.update_data(payment_method=pay_method)

    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить ⏩", callback_data="skip_checkout_notes")
    builder.button(text="❌ Отмена", callback_data="cancel_checkout")
    builder.adjust(1)

    await callback.message.edit_text(
        "📝 <b>Шаг 4 из 4: Примечание к заказу</b>\n\n"
        "Укажите любые пожелания или комментарий (или нажмите <i>«Пропустить»</i>):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(CheckoutState.notes)
    await callback.answer()


@router.callback_query(CheckoutState.notes, F.data == "skip_checkout_notes")
async def skip_notes_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.update_data(notes="Без примечаний")
    await finalize_order(callback.message, callback.from_user, state, bot)
    await callback.answer()


@router.message(CheckoutState.notes)
async def checkout_notes_step(message: types.Message, state: FSMContext, bot: Bot) -> None:
    notes = message.text.strip()
    await state.update_data(notes=notes)
    await finalize_order(message, message.from_user, state, bot)


async def finalize_order(message: types.Message, user: types.User, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    cart = get_cart(user.id)

    if not cart:
        await message.answer("❌ Корзина оказалась пуста. Заказ не может быть создан.")
        await state.clear()
        return

    items = list(cart.values())
    total_amount, total_count = get_cart_summary(user.id)
    user_name = user.full_name or user.username or f"User_{user.id}"
    contact = str(data.get("contact") or "Не указан")
    notes = str(data.get("notes") or "Нет")
    delivery_method = str(data.get("delivery_method") or "pickup")
    delivery_address = str(data.get("delivery_address") or WAREHOUSE_ADDRESS)
    payment_method = str(data.get("payment_method") or "cash")
    payment_status = "unpaid"

    del_label = DELIVERY_NAMES.get(delivery_method, delivery_method)
    pay_label = PAYMENT_NAMES.get(payment_method, payment_method)

    try:
        order_id = await db.create_order(
            user_id=user.id,
            user_name=user_name,
            contact=contact,
            notes=notes,
            items=items,
            total_amount=total_amount,
            delivery_method=delivery_method,
            delivery_address=delivery_address,
            payment_method=payment_method,
            payment_status=payment_status,
        )
        clear_cart(user.id)
        await state.clear()

        # Детали доставки для клиента
        if delivery_method == "pickup":
            delivery_info = (
                f"🏬 <b>Способ получения:</b> {del_label}\n"
                f"📍 <b>Адрес склада:</b> <code>{html.escape(WAREHOUSE_ADDRESS)}</code>\n"
                f"🕐 <b>Режим работы:</b> <i>{html.escape(WAREHOUSE_HOURS)}</i>\n"
                f"📞 <b>Дежурный кладовщик:</b> <code>{html.escape(WAREHOUSE_PHONE)}</code>\n"
                f"🗺️ <a href=\"{html.escape(WAREHOUSE_GEO_LINK)}\">Открыть схему проезда на карте</a>\n"
            )
        else:
            delivery_info = (
                f"🚚 <b>Способ доставки:</b> {del_label}\n"
                f"📍 <b>Адрес назначения:</b> <code>{html.escape(delivery_address)}</code>\n"
            )

        # Детали оплаты для клиента
        if payment_method == "sbp":
            payment_info = (
                f"⚡ <b>Способ оплаты:</b> {pay_label}\n"
                f"🏦 <b>Банк:</b> <b>{html.escape(PAYMENT_REQUISITES['bank'])}</b>\n"
                f"📱 <b>Телефон СБП:</b> <code>{html.escape(PAYMENT_REQUISITES['phone'])}</code>\n"
                f"👤 <b>Получатель:</b> <b>{html.escape(PAYMENT_REQUISITES['receiver'])}</b>\n"
                f"💰 <b>Сумма к переводу:</b> <b>{total_amount:,.0f} {CURRENCY}</b>\n"
                f"<i>📸 После перевода отправьте снимок экрана (чек) ответным сообщением или менеджеру в чат.</i>\n"
            )
        # TODO: Оплата по счёту для юрлиц (безнал) — продолжим позже
        # elif payment_method == "invoice":
        #     payment_info = (
        #         f"🏢 <b>Способ оплаты:</b> {pay_label}\n"
        #         f"<i>Отправьте ИНН или карточку вашей организации в этот чат для формирования официального счёта на оплату.</i>\n"
        #     )
        else:
            payment_info = (
                f"💵 <b>Способ оплаты:</b> {pay_label}\n"
                f"<i>Оплата производится курьеру или кладовщику при проверке и получении товара.</i>\n"
            )

        # Итоговое сообщение клиенту
        success_text = (
            f"🎉 <b>Заказ #{order_id} успешно оформлен!</b>\n\n"
            f"📦 Количество позиций: <b>{total_count} шт.</b>\n"
            f"💰 Итоговая сумма: <b>{total_amount:,.0f} {CURRENCY}</b>\n"
            f"📞 Контакт: <code>{html.escape(contact)}</code>\n"
            f"⏳ Статус: <b>В обработке</b>\n\n"
            f"{delivery_info}\n"
            f"{payment_info}\n"
            f"📝 Примечание: <i>{html.escape(notes)}</i>\n\n"
            f"Менеджер уже обрабатывает ваш заказ и свяжется с вами при необходимости!"
        )
        await message.answer(success_text, parse_mode="HTML", disable_web_page_preview=True)

        # Отправка QR-кода при оплате СБП
        if payment_method == "sbp":
            qr_file_path = generate_sbp_qr(order_id, total_amount)
            if qr_file_path and os.path.exists(qr_file_path):
                try:
                    await message.answer_photo(
                        photo=FSInputFile(qr_file_path),
                        caption=(
                            f"⚡ <b>QR-код для быстрой оплаты заказа #{order_id}</b>\n\n"
                            f"Отсканируйте камерой смартфона или в приложении банка для перевода <b>{total_amount:,.0f} {CURRENCY}</b>."
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.warning("Не удалось отправить фото QR-кода для заказа %s", order_id)

        # Оповещение персонала
        staff_list = await db.get_sales_managers()
        items_summary = "\n".join(
            f"• {html.escape(str(i.get('name') or i.get('part_name') or 'Товар'))} — {i['quantity']} шт. × {i['price']:.0f} {CURRENCY}"
            for i in items
        )
        staff_text = (
            f"🛍️ <b>НОВЫЙ ЗАКАЗ #{order_id}!</b>\n\n"
            f"👤 Клиент: <b>{html.escape(user_name)}</b> (ID: <code>{user.id}</code>)\n"
            f"📞 Контакт: <code>{html.escape(contact)}</code>\n"
            f"🚚 Доставка: <b>{del_label}</b>\n"
            f"📍 Адрес/ПВЗ: <code>{html.escape(delivery_address)}</code>\n"
            f"💳 Оплата: <b>{pay_label}</b> ({PAYMENT_STATUS_NAMES.get(payment_status, payment_status)})\n"
            f"📝 Примечание: <i>{html.escape(notes)}</i>\n\n"
            f"📦 <b>Состав заказа:</b>\n{items_summary}\n\n"
            f"💰 Итого: <b>{total_amount:,.0f} {CURRENCY}</b>"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ В работу", callback_data=f"ord_status_{order_id}_confirmed")
        builder.button(text="💳 Оплачен", callback_data=f"ord_pay_{order_id}_paid")
        builder.button(text="📦 Выдан/Завершён", callback_data=f"ord_status_{order_id}_completed")
        builder.button(text="❌ Отменить", callback_data=f"ord_status_{order_id}_cancelled")
        builder.adjust(2, 2)

        for staff_id in staff_list:
            try:
                await bot.send_message(
                    staff_id, staff_text, reply_markup=builder.as_markup(), parse_mode="HTML"
                )
            except Exception:
                logger.warning("Не удалось отправить уведомление о заказе сотруднику %s", staff_id)

    except Exception:
        logger.exception("Ошибка при сохранении заказа")
        await message.answer("❌ Произошла ошибка при сохранении заказа. Пожалуйста, попробуйте позже.")
        await state.clear()


# ─────────────────── УПРАВЛЕНИЕ ЗАКАЗАМИ ДЛЯ ПЕРСОНАЛА ───────────────────

STATUS_NAMES = {
    "pending": "⏳ В обработке",
    "confirmed": "🛠️ В работе (подтверждён)",
    "completed": "✅ Завершён / Выдан",
    "cancelled": "❌ Отменён",
}


@router.callback_query(F.data.startswith("ord_status_"))
async def update_order_status_cb(callback: types.CallbackQuery, bot: Bot) -> None:
    role = await db.get_user_role(callback.from_user.id)
    if role not in ("admin", "sales_manager", "warehouse_manager"):
        await callback.answer("❌ Нет прав для изменения статуса заказа", show_alert=True)
        return

    # ord_status_{order_id}_{status}
    _, _, order_id_str, new_status = callback.data.split("_", 3)
    order_id = int(order_id_str)

    order = await db.get_order_details(order_id)
    if not order:
        await callback.answer("Заказ не найден!", show_alert=True)
        return

    # При подтверждении можно списать товар со склада
    deduct = (new_status == "confirmed" and order["status"] == "pending")
    ok = await db.update_order_status(
        order_id=order_id,
        new_status=new_status,
        deduct_stock=deduct,
        staff_id=callback.from_user.id,
    )

    if ok:
        status_label = STATUS_NAMES.get(new_status, new_status)
        await callback.answer(f"Статус обновлён: {status_label}")

        # Уведомим клиента
        client_id = order["user_id"]
        try:
            await bot.send_message(
                client_id,
                f"🔔 <b>Статус вашего заказа #{order_id} изменён:</b>\n"
                f"Текущий статус: <b>{status_label}</b>",
                parse_mode="HTML",
            )
        except Exception:
            logger.warning("Не удалось уведомить клиента %s о статусе заказа", client_id)

        # Обновим кнопки у менеджера
        builder = InlineKeyboardBuilder()
        if new_status == "pending":
            builder.button(text="✅ Принять в работу", callback_data=f"ord_status_{order_id}_confirmed")
            builder.button(text="💳 Оплачен", callback_data=f"ord_pay_{order_id}_paid")
            builder.button(text="❌ Отменить", callback_data=f"ord_status_{order_id}_cancelled")
        elif new_status == "confirmed":
            builder.button(text="💳 Оплачен", callback_data=f"ord_pay_{order_id}_paid")
            builder.button(text="📦 Завершить", callback_data=f"ord_status_{order_id}_completed")
            builder.button(text="❌ Отменить", callback_data=f"ord_status_{order_id}_cancelled")
        builder.adjust(2, 1)

        old_caption = callback.message.html_text if hasattr(callback.message, "html_text") else callback.message.text
        updated_text = (
            f"{callback.message.text}\n\n"
            f"<i>Обновлено: {status_label} (сотрудник: {html.escape(callback.from_user.full_name)})</i>"
        )
        try:
            await callback.message.edit_text(updated_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except Exception:
            pass
    else:
        await callback.answer("Ошибка при обновлении статуса", show_alert=True)


@router.callback_query(F.data.startswith("ord_pay_"))
async def update_order_payment_cb(callback: types.CallbackQuery, bot: Bot) -> None:
    role = await db.get_user_role(callback.from_user.id)
    if role not in ("admin", "sales_manager", "warehouse_manager"):
        await callback.answer("❌ Нет прав для изменения статуса оплаты", show_alert=True)
        return

    # ord_pay_{order_id}_{status}
    _, _, order_id_str, pay_status = callback.data.split("_", 3)
    order_id = int(order_id_str)
    order = await db.get_order_details(order_id)
    if not order:
        await callback.answer("Заказ не найден!", show_alert=True)
        return

    ok = await db.set_order_payment_status(order_id, pay_status)
    if ok:
        pay_label = PAYMENT_STATUS_NAMES.get(pay_status, pay_status)
        await callback.answer(f"Статус оплаты: {pay_label}")
        try:
            await bot.send_message(
                order["user_id"],
                f"💳 <b>Статус оплаты вашего заказа #{order_id} обновлён:</b>\n"
                f"Текущий статус оплаты: <b>{pay_label}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        updated_text = (
            f"{callback.message.text}\n\n"
            f"<i>💳 Оплата отмечена: {pay_label} (сотрудник: {html.escape(callback.from_user.full_name)})</i>"
        )
        try:
            await callback.message.edit_text(updated_text, reply_markup=callback.message.reply_markup, parse_mode="HTML")
        except Exception:
            pass
    else:
        await callback.answer("Ошибка обновления оплаты", show_alert=True)


# ─────────────────── ИСТОРИЯ ЗАКАЗОВ КЛИЕНТА ───────────────────

@router.message(Command("orders"))
@router.callback_query(F.data == "my_orders")
async def show_my_orders(target: types.Message | types.CallbackQuery) -> None:
    uid = target.from_user.id
    orders = await db.get_user_orders(uid, limit=10)

    if not orders:
        text = "📦 <b>У вас пока нет заказов.</b>\n\nПерейдите в каталог, добавьте товары в корзину и оформите заказ!"
        builder = InlineKeyboardBuilder()
        builder.button(text="📦 Перейти в каталог", callback_data="back_catalog")
    else:
        text = "📦 <b>История ваших заказов:</b>\n\n"
        for o in orders:
            status_text = STATUS_NAMES.get(o["status"], o["status"])
            del_text = DELIVERY_NAMES.get(o.get("delivery_method") or "pickup", "Самовывоз")
            pay_text = PAYMENT_NAMES.get(o.get("payment_method") or "cash", "При получении")
            pay_status = PAYMENT_STATUS_NAMES.get(o.get("payment_status") or "unpaid", "Ожидает оплаты")
            text += (
                f"• <b>Заказ #{o['id']}</b> от <code>{o['created_at'][:16]}</code>\n"
                f"   💰 Сумма: <b>{o['total_amount']:,.0f} {CURRENCY}</b>\n"
                f"   🚚 {del_text}\n"
                f"   💳 {pay_text} (<i>{pay_status}</i>)\n"
                f"   📊 Статус: <b>{status_text}</b>\n\n"
            )
        builder = InlineKeyboardBuilder()
        builder.button(text="🛒 В корзину", callback_data="view_cart")
        builder.button(text="📦 В каталог", callback_data="back_catalog")
        builder.adjust(2)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


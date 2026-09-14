import asyncio
import html
import logging
import os
from aiohttp import web
from aiogram import Bot
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
)

logger = logging.getLogger(__name__)

# ─── Middleware: CORS headers (useful for local dev / ngrok) ───
@web.middleware
async def cors_middleware(request: web.Request, handler):
    resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


async def options_handler(request: web.Request) -> web.Response:
    return web.Response(status=204, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    })


# ─── GET /api/catalog ───
async def get_catalog(request: web.Request) -> web.Response:
    try:
        query = request.query.get("q", "")
        page = max(0, int(request.query.get("page", 0)))
        page_size = min(500, max(1, int(request.query.get("limit", 100))))

        parts, total_count = await db.search_parts(query, page, page_size)

        # search_parts returns:
        # idx: 0=id, 1=name, 2=retail_price, 3=wholesale_price,
        #      4=quantity, 5=category, 6=subcategory, 7=part_type, 8=cost_price
        # NOTE: cost_price (idx 8) is intentionally NOT exposed to clients.
        catalog_list = [
            {
                "id": p[0],
                "name": p[1],
                "retail_price": p[2],
                "wholesale_price": p[3],
                "quantity": p[4],
                "category": p[5],
                "subcategory": p[6] or "",
                "part_type": p[7] or "",
            }
            for p in parts
        ]

        return web.json_response(
            {"ok": True, "parts": catalog_list, "total": total_count, "page": page, "limit": page_size}
        )
    except Exception:
        logger.exception("Error fetching catalog")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── GET /api/brands ───
async def get_api_brands(request: web.Request) -> web.Response:
    try:
        brands = await db.get_brands()
        return web.json_response({"ok": True, "brands": [{"name": b[0], "count": b[1]} for b in brands]})
    except Exception:
        logger.exception("Error fetching brands")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── GET /api/models ───
async def get_api_models(request: web.Request) -> web.Response:
    try:
        brand = request.query.get("brand", "")
        if not brand:
            return web.json_response({"ok": False, "error": "Brand is required"}, status=400)
        models = await db.get_models_by_brand(brand)
        return web.json_response({"ok": True, "models": [{"name": m[0], "count": m[1]} for m in models]})
    except Exception:
        logger.exception("Error fetching models")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── GET /api/parts ───
async def get_api_parts(request: web.Request) -> web.Response:
    try:
        brand = request.query.get("brand", "")
        model = request.query.get("model", "")
        if not brand or not model:
            return web.json_response({"ok": False, "error": "Brand and model are required"}, status=400)
        
        parts, total_count = await db.get_parts_by_brand_model_type(brand, model, None, 0, 1000)
        
        catalog_list = [
            {
                "id": p[0],
                "name": p[1],
                "retail_price": p[2],
                "wholesale_price": p[3],
                "quantity": p[4],
                "part_type": p[5] or "",
                "category": brand,
                "subcategory": model,
            }
            for p in parts
        ]
        return web.json_response({"ok": True, "parts": catalog_list, "total": total_count})
    except Exception:
        logger.exception("Error fetching parts")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── GET /api/user_status ───
async def get_user_status(request: web.Request) -> web.Response:
    """Returns wholesale/retail status for a given Telegram user ID."""
    try:
        user_id_str = request.query.get("user_id", "")
        if not user_id_str or not user_id_str.isdigit():
            return web.json_response({"ok": False, "error": "user_id required"}, status=400)
        user_id = int(user_id_str)
        status = await db.get_user_status(user_id)
        return web.json_response({"ok": True, "status": status or "retail"})
    except Exception:
        logger.exception("Error fetching user status")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── Order Notifications Helper ───
async def _notify_order_creation(
    bot: Bot,
    order_id: int,
    user_id: int,
    user_name: str,
    contact: str,
    delivery_method: str,
    delivery_address: str,
    payment_method: str,
    payment_status: str,
    notes: str,
    items: list[dict],
    total_amount: float,
) -> None:
    from handlers.orders import (
        DELIVERY_NAMES,
        PAYMENT_NAMES,
        PAYMENT_STATUS_NAMES,
        generate_sbp_qr,
    )

    del_label = DELIVERY_NAMES.get(delivery_method, delivery_method)
    pay_label = PAYMENT_NAMES.get(payment_method, payment_method)

    # 1. Оповещение персонала
    try:
        staff_list = await db.get_sales_managers()
        items_summary = "\n".join(
            f"• {html.escape(str(i.get('name') or i.get('part_name') or 'Товар'))} — {i.get('quantity', i.get('qty', 1))} шт. × {float(i.get('price', 0)):.0f} {CURRENCY}"
            for i in items
        )
        staff_text = (
            f"🛍️ <b>НОВЫЙ ЗАКАЗ #{order_id} (из Mini App)!</b>\n\n"
            f"👤 Клиент: <b>{html.escape(user_name)}</b> (ID: <code>{user_id}</code>)\n"
            f"📞 Контакт: <code>{html.escape(contact)}</code>\n"
            f"🚚 Доставка: <b>{del_label}</b>\n"
            f"📍 Адрес/ПВЗ: <code>{html.escape(delivery_address or '—')}</code>\n"
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
                await bot.send_message(staff_id, staff_text, reply_markup=builder.as_markup(), parse_mode="HTML")
            except Exception:
                logger.warning("Не удалось отправить уведомление о заказе сотруднику %s", staff_id)
    except Exception:
        logger.exception("Ошибка при отправке уведомлений сотрудникам о заказе #%s", order_id)

    # 2. Подтверждение клиенту в личный чат Telegram
    if user_id and user_id > 0:
        try:
            if delivery_method == "pickup":
                delivery_info = (
                    f"🏬 <b>Способ получения:</b> {del_label}\n"
                    f"📍 <b>Адрес склада:</b> <code>{html.escape(WAREHOUSE_ADDRESS)}</code>\n"
                    f"🕐 <b>Режим работы:</b> <i>{html.escape(WAREHOUSE_HOURS)}</i>\n"
                    f"📞 <b>Дежурный кладовщик:</b> <code>{html.escape(WAREHOUSE_PHONE)}</code>\n"
                    f'🗺️ <a href="{html.escape(WAREHOUSE_GEO_LINK)}">Открыть схему проезда на карте</a>\n'
                )
            else:
                delivery_info = (
                    f"🚚 <b>Способ доставки:</b> {del_label}\n"
                    f"📍 <b>Адрес назначения:</b> <code>{html.escape(delivery_address or '—')}</code>\n"
                )

            if payment_method == "sbp":
                payment_info = (
                    f"⚡ <b>Способ оплаты:</b> {pay_label}\n"
                    f"🏦 <b>Банк:</b> <b>{html.escape(PAYMENT_REQUISITES['bank'])}</b>\n"
                    f"📱 <b>Телефон СБП:</b> <code>{html.escape(PAYMENT_REQUISITES['phone'])}</code>\n"
                    f"👤 <b>Получатель:</b> <b>{html.escape(PAYMENT_REQUISITES['receiver'])}</b>\n"
                    f"💰 <b>Сумма к переводу:</b> <b>{total_amount:,.0f} {CURRENCY}</b>\n"
                    f"<i>📸 После перевода отправьте снимок экрана (чек) ответным сообщением менеджеру в чат.</i>\n"
                )
            else:
                payment_info = (
                    f"💵 <b>Способ оплаты:</b> {pay_label}\n"
                    f"<i>Оплата производится при проверке и получении товара.</i>\n"
                )

            total_items_count = sum(int(i.get('quantity', i.get('qty', 1))) for i in items)
            client_text = (
                f"🎉 <b>Заказ #{order_id} успешно оформлен через Mini App!</b>\n\n"
                f"📦 Позиций: <b>{total_items_count} шт.</b>\n"
                f"💰 Итоговая сумма: <b>{total_amount:,.0f} {CURRENCY}</b>\n"
                f"📞 Контакт: <code>{html.escape(contact)}</code>\n"
                f"⏳ Статус: <b>В обработке</b>\n\n"
                f"{delivery_info}\n"
                f"{payment_info}\n"
                f"Менеджер уже обрабатывает ваш заказ и свяжется с вами при необходимости!"
            )
            await bot.send_message(user_id, client_text, parse_mode="HTML", disable_web_page_preview=True)

            if payment_method == "sbp":
                qr_file = generate_sbp_qr(order_id, total_amount)
                if qr_file and os.path.exists(qr_file):
                    try:
                        await bot.send_photo(
                            user_id,
                            photo=FSInputFile(qr_file),
                            caption=(
                                f"⚡ <b>QR-код для перевода СБП к заказу #{order_id}</b>\n"
                                f"Отсканируйте код в приложении любого банка для оплаты."
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.warning("Не удалось отправить фото QR-кода клиенту %s", user_id)
        except Exception:
            logger.warning("Не удалось отправить сообщение клиенту %s в чат бота", user_id)


# ─── POST /api/order ───
async def create_order(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        user_id_raw = data.get("user_id")
        try:
            user_id = int(user_id_raw) if user_id_raw else 0
        except (ValueError, TypeError):
            user_id = 0

        user_name = (
            data.get("user_name")
            or data.get("first_name")
            or data.get("username")
            or (f"Пользователь {user_id}" if user_id else "Web Client")
        )
        contact = str(data.get("contact", "")).strip()
        notes = str(data.get("notes", "Заказ через Mini App")).strip()
        items = data.get("items", [])  # [{id, quantity/qty, price}]
        delivery_method = str(data.get("delivery_method", "pickup"))
        delivery_address = str(data.get("delivery_address", "")).strip()
        payment_method = str(data.get("payment_method", "cash"))

        if not items:
            return web.json_response(
                {"ok": False, "error": "Корзина пуста (items обязательны)"}, status=400
            )

        user_status = await db.get_user_status(user_id) if user_id else "retail"
        is_wholesale = user_status == "wholesale"
        calculated_total_sum = 0.0

        # ── Stock validation & Price calculation ────────────────────────────────
        for item in items:
            part_id = int(item.get("id", 0))
            requested_qty = int(item.get("quantity", item.get("qty", 0)))
            if requested_qty <= 0:
                return web.json_response(
                    {"ok": False, "error": f"Некорректное количество для товара {part_id}"}, status=400
                )
            
            part = await db.get_part(part_id)
            if not part:
                return web.json_response(
                    {"ok": False, "error": f"Товар {part_id} не найден"}, status=404
                )
                
            stock_qty = part[5]
            if stock_qty < requested_qty:
                name = part[1]
                return web.json_response(
                    {"ok": False, "error": f"Недостаточно на складе: «{name}» — {stock_qty} шт."},
                    status=409,
                )
                
            actual_price = part[4] if is_wholesale else part[3]
            item["quantity"] = requested_qty
            item["price"] = actual_price
            item["part_name"] = part[1]
            calculated_total_sum += float(actual_price) * requested_qty
        # ───────────────────────────────────────────────────────────────────

        total_sum = calculated_total_sum

        order_id = await db.create_order(
            user_id=user_id,
            user_name=user_name,
            contact=contact,
            notes=notes,
            items=items,
            total_amount=total_sum,
            delivery_method=delivery_method,
            delivery_address=delivery_address,
            payment_method=payment_method,
            payment_status="unpaid",
        )

        bot: Bot | None = request.app.get("bot")
        if bot:
            asyncio.create_task(
                _notify_order_creation(
                    bot=bot,
                    order_id=order_id,
                    user_id=user_id,
                    user_name=user_name,
                    contact=contact,
                    delivery_method=delivery_method,
                    delivery_address=delivery_address,
                    payment_method=payment_method,
                    payment_status="unpaid",
                    notes=notes,
                    items=items,
                    total_amount=total_sum,
                )
            )

        return web.json_response({"ok": True, "order_id": order_id})
    except Exception:
        logger.exception("Error creating order")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── Setup ───
async def setup_webapp(bot: Bot | None = None) -> web.AppRunner:
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot

    # OPTIONS pre-flight for CORS
    app.router.add_route("OPTIONS", "/api/{path_info:.*}", options_handler)

    app.router.add_get("/api/catalog", get_catalog)
    app.router.add_get("/api/brands", get_api_brands)
    app.router.add_get("/api/models", get_api_models)
    app.router.add_get("/api/parts", get_api_parts)
    app.router.add_get("/api/user_status", get_user_status)
    app.router.add_post("/api/order", create_order)

    # Static files (index.html, style.css, app.js)
    app.router.add_static("/", "./webapp", name="static", show_index=True)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 8888))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("WebApp server started on http://0.0.0.0:%s", port)

    return runner

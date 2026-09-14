import logging
import os
from aiohttp import web
import db

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
    except Exception as e:
        logger.exception("Error fetching catalog")
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


# ─── POST /api/order ───
async def create_order(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        user_id = data.get("user_id")
        user_name = data.get("user_name", "Web Client")
        contact = data.get("contact", "")
        notes = data.get("notes", "Заказ через Mini App")
        items = data.get("items", [])  # [{id, quantity, price}]
        delivery_method = data.get("delivery_method", "pickup")
        delivery_address = data.get("delivery_address", "")
        payment_method = data.get("payment_method", "cash")

        if not user_id or not items:
            return web.json_response(
                {"ok": False, "error": "user_id и items обязательны"}, status=400
            )

        user_status = await db.get_user_status(user_id)
        is_wholesale = user_status == "wholesale"
        calculated_total_sum = 0.0

        # ── Stock validation & Price calculation ────────────────────────────────
        for item in items:
            part_id = int(item.get("id", 0))
            requested_qty = int(item.get("quantity", 0))
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
            item["price"] = actual_price
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

        return web.json_response({"ok": True, "order_id": order_id})
    except Exception:
        logger.exception("Error creating order")
        return web.json_response({"ok": False, "error": "Internal server error"}, status=500)


# ─── Setup ───
async def setup_webapp() -> web.AppRunner:
    app = web.Application(middlewares=[cors_middleware])

    # OPTIONS pre-flight for CORS
    app.router.add_route("OPTIONS", "/api/{path_info:.*}", options_handler)

    app.router.add_get("/api/catalog", get_catalog)
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

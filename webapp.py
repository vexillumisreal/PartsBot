import json
import logging
from aiohttp import web
import db

logger = logging.getLogger(__name__)

async def get_catalog(request: web.Request) -> web.Response:
    try:
        # Paginating or searching
        query = request.query.get("q", "")
        page = int(request.query.get("page", 0))
        page_size = int(request.query.get("limit", 100)) # Allow larger limits for webapp
        
        parts, total_count = await db.search_parts(query, page, page_size)
        
        catalog_list = []
        for p in parts:
            catalog_list.append({
                "id": p[0],
                "category": p[1],
                "subcategory": p[2],
                "part_type": p[3],
                "name": p[4],
                "retail_price": p[5],
                "wholesale_price": p[6],
                "cost_price": p[7],
                "quantity": p[8]
            })
            
        return web.json_response({
            "ok": True, 
            "parts": catalog_list, 
            "total": total_count,
            "page": page,
            "limit": page_size
        })
    except Exception as e:
        logger.error(f"Error fetching catalog: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def create_order(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        user_id = data.get("user_id")
        user_name = data.get("user_name", "Web Client")
        contact = data.get("contact", "")
        notes = data.get("notes", "Заказ через Mini App")
        items = data.get("items", []) # [{"id": 1, "quantity": 2, "price": 100}]
        delivery_method = data.get("delivery_method", "pickup")
        delivery_address = data.get("delivery_address", "")
        payment_method = data.get("payment_method", "cash")
        
        if not user_id or not items:
            return web.json_response({"ok": False, "error": "Invalid data: user_id and items required"}, status=400)
            
        total_sum = sum(item.get("price", 0) * item.get("quantity", 1) for item in items)
        
        # In db.py:
        # db.create_order(user_id, user_name, contact, notes, items, total_amount, delivery_method, delivery_address, payment_method, payment_status)
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
            payment_status="unpaid"
        )
            
        return web.json_response({"ok": True, "order_id": order_id})
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def setup_webapp() -> web.AppRunner:
    app = web.Application()
    app.router.add_get('/api/catalog', get_catalog)
    app.router.add_post('/api/order', create_order)
    
    # Enable CORS for local testing if needed, or simply let it serve static.
    app.router.add_static('/', './webapp', name='static', show_index=True)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("WebApp server started on http://0.0.0.0:8080")
    
    return runner

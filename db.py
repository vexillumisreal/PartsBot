"""
db.py — асинхронный слой работы с базой данных (aiosqlite).

Все публичные функции — корутины (async def).
"""
import os
import csv
import logging
import aiosqlite
from config import ADMIN_ID, ADMIN_IDS, EXPORT_DIR

logger = logging.getLogger(__name__)

DB_NAME = "spare_parts.db"

# ─────────────────────────── РОЛИ ────────────────────────────
ROLES: dict[str, str] = {
    "user": "Пользователь",
    "warehouse_manager": "Менеджер склада",
    "sales_manager": "Менеджер продаж",
    "admin": "Администратор",
}

# Порог уведомления о низких остатках
LOW_STOCK_THRESHOLD = 3


# ─────────────────────────── INIT ────────────────────────────

async def init_db() -> None:
    """Создаёт все таблицы при первом запуске и накатывает миграции."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id               INTEGER PRIMARY KEY,
                username              TEXT    DEFAULT '',
                full_name             TEXT    DEFAULT '',
                status                TEXT    DEFAULT 'retail',
                role                  TEXT    DEFAULT 'user',
                notifications_enabled INTEGER DEFAULT 1,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS parts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                category            TEXT    NOT NULL,
                subcategory         TEXT    DEFAULT '',
                name                TEXT    NOT NULL,
                supplier            TEXT    DEFAULT '',
                cost_price          REAL    DEFAULT 0,
                retail_price        REAL    DEFAULT 0,
                wholesale_price     REAL    DEFAULT 0,
                quantity            INTEGER DEFAULT 0,
                low_stock_threshold INTEGER DEFAULT 3,
                is_active           INTEGER DEFAULT 1,
                last_updated        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stock_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id    INTEGER NOT NULL,
                quantity   INTEGER NOT NULL,
                supplier   TEXT,
                date       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes      TEXT,
                type       TEXT      DEFAULT 'incoming',
                user_id    INTEGER,
                FOREIGN KEY (part_id) REFERENCES parts(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS low_stock_alerts (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id  INTEGER NOT NULL,
                user_id  INTEGER,
                quantity INTEGER,
                date     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent     INTEGER DEFAULT 0,
                FOREIGN KEY (part_id) REFERENCES parts(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                type         TEXT,
                category     TEXT,
                data         TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wholesale_requests (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                user_name  TEXT    DEFAULT '',
                comment    TEXT    DEFAULT '',
                status     TEXT    DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                user_name        TEXT    DEFAULT '',
                contact          TEXT    DEFAULT '',
                notes            TEXT    DEFAULT '',
                delivery_method  TEXT    DEFAULT 'pickup',
                delivery_address TEXT    DEFAULT '',
                payment_method   TEXT    DEFAULT 'cash',
                payment_status   TEXT    DEFAULT 'unpaid',
                total_amount     REAL    DEFAULT 0,
                status           TEXT    DEFAULT 'pending',
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL,
                part_id    INTEGER NOT NULL,
                part_name  TEXT    NOT NULL,
                quantity   INTEGER NOT NULL,
                price      REAL    NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """)

        # Индексы базовых таблиц
        await db.execute("CREATE INDEX IF NOT EXISTS idx_parts_category ON parts(category, subcategory)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_stock_history_date ON stock_history(date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)")

        # Миграции колонок
        migrations = [
            ("users", "username", "TEXT DEFAULT ''"),
            ("users", "full_name", "TEXT DEFAULT ''"),
            ("users", "created_at", "TEXT DEFAULT ''"),
            ("users", "notifications_enabled", "INTEGER DEFAULT 1"),
            ("parts", "is_active", "INTEGER DEFAULT 1"),
            ("parts", "subcategory", "TEXT DEFAULT ''"),
            ("parts", "part_type", "TEXT DEFAULT ''"),
            ("parts", "low_stock_threshold", "INTEGER DEFAULT 3"),
            ("stock_history", "user_id", "INTEGER"),
            ("stock_history", "type", "TEXT DEFAULT 'incoming'"),
            ("stock_history", "price", "REAL DEFAULT 0"),
            ("orders", "delivery_method", "TEXT DEFAULT 'pickup'"),
            ("orders", "delivery_address", "TEXT DEFAULT ''"),
            ("orders", "payment_method", "TEXT DEFAULT 'cash'"),
            ("orders", "payment_status", "TEXT DEFAULT 'unpaid'"),
        ]
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            existing_tables = {r[0] for r in await cur.fetchall()}

        for table, column, col_def in migrations:
            if table not in existing_tables:
                continue
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                cols = {r[1] for r in await cur.fetchall()}
            if column not in cols:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                logger.info("Миграция: добавлена колонка %s.%s", table, column)

        # Индексы колонок после миграций
        await db.execute("CREATE INDEX IF NOT EXISTS idx_parts_hierarchy ON parts(category, subcategory, part_type)")

        # Синхронизация администраторов
        for aid in ADMIN_IDS:
            if aid and aid > 0:
                async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (aid,)) as cur:
                    if await cur.fetchone():
                        await db.execute("UPDATE users SET role = 'admin' WHERE user_id = ?", (aid,))
                    else:
                        await db.execute(
                            "INSERT INTO users (user_id, role, status) VALUES (?, 'admin', 'wholesale')",
                            (aid,),
                        )
                logger.info("ADMIN_ID=%s синхронизирован с ролью 'admin'", aid)

        await db.commit()
        logger.info("БД инициализирована: %s", DB_NAME)


# ─────────────────────────── USERS ───────────────────────────

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором по конфигу."""
    return user_id in ADMIN_IDS or (ADMIN_ID and user_id == ADMIN_ID)


async def add_user(user_id: int, username: str = "", full_name: str = "") -> None:
    role = "admin" if is_admin(user_id) else "user"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
        if exists:
            await db.execute(
                """
                UPDATE users
                SET username = COALESCE(NULLIF(?, ''), username),
                    full_name = COALESCE(NULLIF(?, ''), full_name)
                WHERE user_id = ?
                """,
                (username, full_name, user_id),
            )
            if is_admin(user_id):
                await db.execute("UPDATE users SET role = 'admin' WHERE user_id = ?", (user_id,))
        else:
            await db.execute(
                """
                INSERT INTO users (user_id, username, full_name, role)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, username, full_name, role),
            )
        await db.commit()


async def get_user_status(user_id: int) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT status FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else "retail"


async def set_user_status(user_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
        await db.commit()


async def toggle_user_status(user_id: int) -> str:
    current = await get_user_status(user_id)
    new_status = "wholesale" if current == "retail" else "retail"
    await set_user_status(user_id, new_status)
    return new_status


async def get_user_role(user_id: int) -> str:
    if is_admin(user_id):
        return "admin"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else "user"


async def set_user_role(user_id: int, role: str) -> bool:
    if role not in ROLES:
        return False
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
        await db.commit()
        return cur.rowcount > 0


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, full_name, status, role, created_at FROM users ORDER BY user_id"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_users_paged(page: int = 0, page_size: int = 8, search: str = "") -> tuple[list[dict], int]:
    offset = page * page_size
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if search:
            pat = f"%{search}%"
            async with db.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR full_name LIKE ?
                """,
                (pat, pat, pat),
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT user_id, username, full_name, status, role, created_at
                FROM users
                WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR full_name LIKE ?
                ORDER BY user_id DESC LIMIT ? OFFSET ?
                """,
                (pat, pat, pat, page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute("SELECT COUNT(*) FROM users") as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT user_id, username, full_name, status, role, created_at
                FROM users
                ORDER BY user_id DESC LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows], total


async def get_user_info(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, full_name, status, role, created_at FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_warehouse_managers() -> list[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE role IN ('admin', 'warehouse_manager')"
        ) as cur:
            rows = await cur.fetchall()
            res = [r[0] for r in rows]
            if ADMIN_ID and ADMIN_ID not in res:
                res.append(ADMIN_ID)
            return res


async def get_sales_managers() -> list[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE role IN ('admin', 'sales_manager')"
        ) as cur:
            rows = await cur.fetchall()
            res = [r[0] for r in rows]
            if ADMIN_ID and ADMIN_ID not in res:
                res.append(ADMIN_ID)
            return res


async def get_all_staff() -> list[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE role IN ('admin', 'warehouse_manager', 'sales_manager')"
        ) as cur:
            rows = await cur.fetchall()
            res = [r[0] for r in rows]
            if ADMIN_ID and ADMIN_ID not in res:
                res.append(ADMIN_ID)
            return res


async def user_exists(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None


# ─────────────────────────── WHOLESALE REQUESTS ───────────────────────────

async def create_wholesale_request(user_id: int, user_name: str, comment: str = "") -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            """
            INSERT INTO wholesale_requests (user_id, user_name, comment, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (user_id, user_name, comment),
        )
        await db.commit()
        return cur.lastrowid


async def has_pending_wholesale_request(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM wholesale_requests WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ) as cur:
            return await cur.fetchone() is not None


async def get_pending_wholesale_requests() -> list[dict]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, user_name, comment, created_at
            FROM wholesale_requests
            WHERE status = 'pending'
            ORDER BY created_at DESC
            """
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def resolve_wholesale_request(request_id: int, approved: bool) -> tuple[bool, int]:
    """Возвращает (success, user_id)."""
    status = "approved" if approved else "rejected"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id FROM wholesale_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False, 0
            user_id = row[0]

        await db.execute(
            "UPDATE wholesale_requests SET status = ? WHERE id = ?", (status, request_id)
        )
        if approved:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await db.execute("UPDATE users SET status = 'wholesale' WHERE user_id = ?", (user_id,))
        await db.commit()
        return True, user_id


# ─────────────────────────── PARTS ───────────────────────────

async def add_part(
    category: str,
    subcategory: str,
    name: str,
    cost_price: float,
    retail_price: float,
    wholesale_price: float,
    supplier: str = "",
    low_stock_threshold: int = 3,
) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            """
            INSERT INTO parts
                (category, subcategory, name, supplier, cost_price, retail_price, wholesale_price, low_stock_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (category, subcategory, name, supplier, cost_price, retail_price, wholesale_price, low_stock_threshold),
        )
        await db.commit()
        return cur.lastrowid


async def get_brands() -> list[tuple[str, int]]:
    """Возвращает список брендов (category) и количество активных запчастей."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT category, COUNT(*) FROM parts
            WHERE is_active = 1 AND category IS NOT NULL AND category != ''
            GROUP BY category
            ORDER BY COUNT(*) DESC, category ASC
            """
        ) as cur:
            rows = await cur.fetchall()
            return [(r[0], r[1]) for r in rows]


async def get_models_by_brand(brand: str) -> list[tuple[str, int]]:
    """Возвращает список моделей (subcategory) и количество запчастей для заданного бренда."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT subcategory, COUNT(*) FROM parts
            WHERE category = ? AND is_active = 1 AND subcategory IS NOT NULL AND subcategory != ''
            GROUP BY subcategory
            ORDER BY subcategory ASC
            """,
            (brand,),
        ) as cur:
            rows = await cur.fetchall()
            return [(r[0], r[1]) for r in rows]


async def get_part_types_by_model(brand: str, model: str) -> list[tuple[str, int]]:
    """Возвращает список типов запчастей (part_type) и их количество для заданного бренда и модели."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT COALESCE(NULLIF(part_type, ''), 'Разное'), COUNT(*) FROM parts
            WHERE category = ? AND subcategory = ? AND is_active = 1
            GROUP BY COALESCE(NULLIF(part_type, ''), 'Разное')
            ORDER BY COUNT(*) DESC
            """,
            (brand, model),
        ) as cur:
            rows = await cur.fetchall()
            return [(r[0], r[1]) for r in rows]


async def get_parts_by_brand_model_type(
    brand: str,
    model: str,
    part_type: str | None = None,
    page: int = 0,
    page_size: int = 8,
) -> tuple[list[tuple], int]:
    """Возвращает страницу запчастей для заданного бренда, модели и опционально типа запчасти."""
    offset = page * page_size
    async with aiosqlite.connect(DB_NAME) as db:
        if part_type and part_type != "Все":
            async with db.execute(
                """
                SELECT COUNT(*) FROM parts
                WHERE category = ? AND subcategory = ?
                  AND (part_type = ? OR (part_type = '' AND ? = 'Разное'))
                  AND is_active = 1
                """,
                (brand, model, part_type, part_type),
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT id, name, retail_price, wholesale_price, quantity, part_type
                FROM parts
                WHERE category = ? AND subcategory = ?
                  AND (part_type = ? OR (part_type = '' AND ? = 'Разное'))
                  AND is_active = 1
                ORDER BY name LIMIT ? OFFSET ?
                """,
                (brand, model, part_type, part_type, page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """
                SELECT COUNT(*) FROM parts
                WHERE category = ? AND subcategory = ? AND is_active = 1
                """,
                (brand, model),
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT id, name, retail_price, wholesale_price, quantity, part_type
                FROM parts
                WHERE category = ? AND subcategory = ? AND is_active = 1
                ORDER BY name LIMIT ? OFFSET ?
                """,
                (brand, model, page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        return list(rows), total


async def get_subcategories(category: str) -> list[str]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT DISTINCT subcategory FROM parts
            WHERE category = ? AND subcategory IS NOT NULL AND subcategory != '' AND is_active = 1
            ORDER BY subcategory
            """,
            (category,),
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows if r[0]]


async def get_parts_by_category(
    category: str,
    subcategory: str | None = None,
    page: int = 0,
    page_size: int = 8,
) -> tuple[list[tuple], int]:
    offset = page * page_size
    async with aiosqlite.connect(DB_NAME) as db:
        if subcategory:
            async with db.execute(
                "SELECT COUNT(*) FROM parts WHERE category=? AND subcategory=? AND is_active=1",
                (category, subcategory),
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT id, name, retail_price, wholesale_price, quantity
                FROM parts WHERE category=? AND subcategory=? AND is_active=1
                ORDER BY name LIMIT ? OFFSET ?
                """,
                (category, subcategory, page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT COUNT(*) FROM parts WHERE category=? AND is_active=1",
                (category,),
            ) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT id, name, retail_price, wholesale_price, quantity
                FROM parts WHERE category=? AND is_active=1
                ORDER BY name LIMIT ? OFFSET ?
                """,
                (category, page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        return list(rows), total


async def search_parts(query: str, page: int = 0, page_size: int = 8) -> tuple[list[tuple], int]:
    pattern = f"%{query}%"
    offset = page * page_size
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM parts WHERE name LIKE ? AND is_active=1", (pattern,)
        ) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            """
            SELECT id, name, retail_price, wholesale_price, quantity, category
            FROM parts WHERE name LIKE ? AND is_active=1
            ORDER BY name LIMIT ? OFFSET ?
            """,
            (pattern, page_size, offset),
        ) as cur:
            rows = await cur.fetchall()
        return list(rows), total


async def get_part(part_id: int) -> tuple | None:
    """Возвращает (id, name, cost_price, retail_price, wholesale_price, quantity, category, subcategory, supplier, low_stock_threshold)."""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                """
                SELECT id, name, cost_price, retail_price, wholesale_price,
                       quantity, category, subcategory, supplier, low_stock_threshold
                FROM parts WHERE id = ? AND is_active = 1
                """,
                (part_id,),
            ) as cur:
                row = await cur.fetchone()
                return tuple(row) if row else None
    except Exception:
        logger.exception("get_part(%s) failed", part_id)
        return None


async def get_part_quantity(part_id: int) -> int:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT quantity FROM parts WHERE id = ?", (part_id,)
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0
    except Exception:
        logger.exception("get_part_quantity(%s) failed", part_id)
        return 0


async def deactivate_part(part_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "UPDATE parts SET is_active = 0 WHERE id = ?", (part_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def update_part_prices(
    part_id: int,
    cost_price: float,
    retail_price: float,
    wholesale_price: float,
) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            """
            UPDATE parts
            SET cost_price=?, retail_price=?, wholesale_price=?,
                last_updated=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (cost_price, retail_price, wholesale_price, part_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_part_field(part_id: int, field: str, value) -> bool:
    allowed_fields = {
        "name", "cost_price", "retail_price", "wholesale_price",
        "quantity", "low_stock_threshold", "supplier", "subcategory"
    }
    if field not in allowed_fields:
        return False
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            f"UPDATE parts SET {field} = ?, last_updated=CURRENT_TIMESTAMP WHERE id = ?",
            (value, part_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_part_quantity(part_id: int, quantity: int, user_id: int | None = None, reason: str = "Ручная корректировка") -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT quantity FROM parts WHERE id = ?", (part_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return False
            old_qty = row[0]
        delta = quantity - old_qty
        mtype = "incoming" if delta >= 0 else "outgoing"
        await db.execute(
            "UPDATE parts SET quantity = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
            (quantity, part_id),
        )
        await db.execute(
            """
            INSERT INTO stock_history (part_id, quantity, supplier, notes, type, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (part_id, abs(delta), "", f"{reason} ({old_qty} -> {quantity})", mtype, user_id),
        )
        await db.commit()
        return True


# ─────────────────────────── STOCK ───────────────────────────

async def add_stock_movement(
    part_id: int,
    quantity: int,
    movement_type: str = "incoming",  # 'incoming' | 'outgoing'
    supplier: str | None = None,
    notes: str | None = None,
    user_id: int | None = None,
    cost_price: float = 0.0,
) -> bool:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            if movement_type == "outgoing":
                async with db.execute(
                    "SELECT quantity FROM parts WHERE id = ?", (part_id,)
                ) as cur:
                    row = await cur.fetchone()
                    if not row or row[0] < quantity:
                        return False
                delta = -quantity
            else:
                delta = quantity

            if cost_price > 0:
                await db.execute(
                    """
                    UPDATE parts
                    SET quantity = quantity + ?, cost_price = ?, last_updated = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (delta, cost_price, part_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE parts
                    SET quantity = quantity + ?, last_updated = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (delta, part_id),
                )
            await db.execute(
                """
                INSERT INTO stock_history (part_id, quantity, supplier, notes, type, user_id, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (part_id, quantity, supplier, notes, movement_type, user_id, cost_price),
            )
            await db.commit()
            return True
    except Exception:
        logger.exception("add_stock_movement(%s) failed", part_id)
        return False


async def create_low_stock_alert(part_id: int, current_qty: int) -> None:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO low_stock_alerts (part_id, quantity) VALUES (?, ?)",
                (part_id, current_qty),
            )
            await db.commit()
    except Exception:
        logger.exception("create_low_stock_alert(%s) failed", part_id)


async def get_low_stock_alerts(unread_only: bool = True) -> list[dict]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cond = "WHERE la.sent = 0" if unread_only else ""
        async with db.execute(
            f"""
            SELECT la.id AS alert_id, la.part_id, p.name AS part_name,
                   p.category, p.quantity, p.low_stock_threshold, la.date, la.sent
            FROM low_stock_alerts la
            JOIN parts p ON la.part_id = p.id
            {cond}
            ORDER BY la.date DESC LIMIT 50
            """
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def mark_alert_sent(alert_id: int) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE low_stock_alerts SET sent = 1 WHERE id = ?", (alert_id,))
        await db.commit()


async def mark_all_alerts_sent() -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE low_stock_alerts SET sent = 1")
        await db.commit()


async def clear_low_stock_alerts() -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM low_stock_alerts")
        await db.commit()


# ─────────────────────────── ORDERS ───────────────────────────

async def create_order(
    user_id: int,
    user_name: str,
    contact: str,
    notes: str,
    items: list[dict],
    total_amount: float,
    delivery_method: str = "pickup",
    delivery_address: str = "",
    payment_method: str = "cash",
    payment_status: str = "unpaid",
) -> int:
    """
    items: [{'part_id': int, 'part_name': str, 'quantity': int, 'price': float}]
    """
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, full_name) VALUES (?, ?)", (user_id, user_name))
        cur = await db.execute(
            """
            INSERT INTO orders (
                user_id, user_name, contact, notes,
                delivery_method, delivery_address, payment_method, payment_status,
                total_amount, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                user_id, user_name, contact, notes,
                delivery_method, delivery_address, payment_method, payment_status,
                total_amount,
            ),
        )
        order_id = cur.lastrowid
        for item in items:
            part_name = item.get("part_name") or item.get("name") or "Запчасть"
            await db.execute(
                """
                INSERT INTO order_items (order_id, part_id, part_name, quantity, price)
                VALUES (?, ?, ?, ?, ?)
                """,
                (order_id, item["part_id"], part_name, item["quantity"], item["price"]),
            )
        await db.commit()
        return order_id


async def get_user_orders(user_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, total_amount, status, created_at,
                   delivery_method, delivery_address, payment_method, payment_status
            FROM orders
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_all_orders(status: str | None = None, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if status:
            async with db.execute(
                """
                SELECT id, user_id, user_name, contact, total_amount, status, created_at
                FROM orders WHERE status = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (status, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """
                SELECT id, user_id, user_name, contact, total_amount, status, created_at
                FROM orders
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_all_orders_paged(
    page: int = 0,
    page_size: int = 8,
    status: str | None = None,
) -> tuple[list[dict], int]:
    """Возвращает страницу заказов с пагинацией и общее количество."""
    offset = page * page_size
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if status and status != "all":
            async with db.execute("SELECT COUNT(*) FROM orders WHERE status = ?", (status,)) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT id, user_id, user_name, contact, total_amount, status,
                       delivery_method, delivery_address, payment_method, payment_status, created_at
                FROM orders WHERE status = ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (status, page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute("SELECT COUNT(*) FROM orders") as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                """
                SELECT id, user_id, user_name, contact, total_amount, status,
                       delivery_method, delivery_address, payment_method, payment_status, created_at
                FROM orders
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows], total


async def get_order_details(order_id: int) -> dict | None:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            order = dict(row)

        async with db.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)) as cur:
            items = await cur.fetchall()
            order["items"] = [dict(i) for i in items]
        return order


async def update_order_status(order_id: int, new_status: str, deduct_stock: bool = False, staff_id: int | None = None) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        if deduct_stock:
            async with db.execute("SELECT part_id, quantity, part_name FROM order_items WHERE order_id = ?", (order_id,)) as cur:
                items = await cur.fetchall()
            for part_id, qty, pname in items:
                await db.execute(
                    "UPDATE parts SET quantity = MAX(0, quantity - ?), last_updated = CURRENT_TIMESTAMP WHERE id = ?",
                    (qty, part_id),
                )
                await db.execute(
                    """
                    INSERT INTO stock_history (part_id, quantity, supplier, notes, type, user_id)
                    VALUES (?, ?, '', ?, 'outgoing', ?)
                    """,
                    (part_id, qty, f"Заказ #{order_id}", staff_id),
                )

        cur = await db.execute(
            "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, order_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_order_payment_status(order_id: int, payment_status: str) -> bool:
    """Обновляет статус оплаты заказа (unpaid, paid, refunded)."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "UPDATE orders SET payment_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (payment_status, order_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ─────────────────────────── REPORTS & STATS ─────────────────────────

async def get_financial_summary() -> dict:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    COALESCE(SUM(retail_price * quantity), 0)                         AS total_revenue,
                    COALESCE(SUM(cost_price   * quantity), 0)                         AS total_cost,
                    COALESCE(SUM((retail_price - cost_price) * quantity), 0)          AS total_profit,
                    COALESCE(AVG(
                        CASE WHEN retail_price > 0
                             THEN (retail_price - cost_price) * 100.0 / retail_price
                             ELSE 0 END
                    ), 0) AS avg_margin
                FROM parts WHERE is_active = 1
                """
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else {}
    except Exception:
        logger.exception("get_financial_summary failed")
        return {}


async def get_financial_report() -> dict:
    return await get_financial_summary()


async def get_category_profitability(category: str | None = None) -> list[dict]:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            if category:
                async with db.execute(
                    """
                    SELECT name AS part_name, category,
                           (retail_price - cost_price) * quantity AS total_profit,
                           ROUND(CASE WHEN retail_price > 0
                                 THEN (retail_price - cost_price) * 100.0 / retail_price
                                 ELSE 0 END, 1) AS margin_percent
                    FROM parts WHERE category = ? AND is_active = 1
                    ORDER BY total_profit DESC
                    """,
                    (category,),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    """
                    SELECT name AS part_name, category,
                           (retail_price - cost_price) * quantity AS total_profit,
                           ROUND(CASE WHEN retail_price > 0
                                 THEN (retail_price - cost_price) * 100.0 / retail_price
                                 ELSE 0 END, 1) AS margin_percent
                    FROM parts WHERE is_active = 1
                    ORDER BY margin_percent DESC LIMIT 10
                    """
                ) as cur:
                    rows = await cur.fetchall()
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("get_category_profitability failed")
        return []


async def get_stock_movement_report(days: int = 30) -> list[dict]:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    p.name AS part_name,
                    p.category,
                    SUM(CASE WHEN sh.type = 'incoming' THEN sh.quantity ELSE 0 END) AS inflow,
                    SUM(CASE WHEN sh.type = 'outgoing' THEN sh.quantity ELSE 0 END) AS outflow,
                    sh.supplier,
                    MAX(sh.date) AS last_date
                FROM stock_history sh
                JOIN parts p ON sh.part_id = p.id
                WHERE sh.date >= datetime('now', '-' || ? || ' days')
                GROUP BY p.id, sh.supplier
                ORDER BY inflow DESC
                """,
                (days,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception:
        logger.exception("get_stock_movement_report failed")
        return []


async def get_supplier_analysis() -> list[dict]:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    supplier,
                    COUNT(*)               AS delivery_count,
                    SUM(quantity)          AS total_received,
                    ROUND(AVG(quantity),1) AS avg_delivery_qty,
                    MAX(date)              AS last_delivery
                FROM stock_history
                WHERE type = 'incoming' AND supplier IS NOT NULL AND supplier != ''
                GROUP BY supplier
                ORDER BY total_received DESC
                """
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception:
        logger.exception("get_supplier_analysis failed")
        return []


async def get_stats_summary() -> dict:
    """Полная сводка для главного экрана админ-панели."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        
        # Кол-во активных товаров и суммарный остаток
        async with db.execute(
            """
            SELECT COUNT(*) AS total_parts,
                   COALESCE(SUM(quantity), 0) AS total_stock,
                   COALESCE(SUM(retail_price * quantity), 0) AS total_retail_value,
                   COALESCE(SUM(cost_price * quantity), 0) AS total_cost_value
            FROM parts WHERE is_active = 1
            """
        ) as cur:
            p_stats = dict(await cur.fetchone())

        # Товары с низким остатком
        async with db.execute(
            "SELECT COUNT(*) AS low_stock_count FROM parts WHERE quantity <= low_stock_threshold AND is_active = 1"
        ) as cur:
            low_stock_count = (await cur.fetchone())[0]

        # Пользователи
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]

        # Заказы в статусе pending
        async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'") as cur:
            pending_orders = (await cur.fetchone())[0]

        # Оптовые заявки pending
        async with db.execute("SELECT COUNT(*) FROM wholesale_requests WHERE status = 'pending'") as cur:
            pending_wholesale = (await cur.fetchone())[0]

        return {
            **p_stats,
            "low_stock_count": low_stock_count,
            "total_users": total_users,
            "pending_orders": pending_orders,
            "pending_wholesale": pending_wholesale,
        }


# ─────────────────────────── CSV EXPORT ─────────────────────────

async def export_stock_csv(filename: str = "warehouse_stock.csv") -> str:
    filepath = os.path.join(EXPORT_DIR, filename)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, category, subcategory, name, supplier,
                   cost_price, retail_price, wholesale_price, quantity,
                   low_stock_threshold, last_updated
            FROM parts WHERE is_active = 1
            ORDER BY category, name
            """
        ) as cur:
            rows = await cur.fetchall()

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "ID", "Категория", "Подкатегория", "Название", "Поставщик",
            "Себестоимость (руб)", "Розница (руб)", "Опт (руб)", "Остаток (шт)",
            "Порог алерта", "Обновлено"
        ])
        for r in rows:
            writer.writerow([
                r["id"], r["category"], r["subcategory"], r["name"], r["supplier"],
                r["cost_price"], r["retail_price"], r["wholesale_price"], r["quantity"],
                r["low_stock_threshold"], r["last_updated"]
            ])
    return filepath


async def export_movements_csv(filename: str = "stock_movements.csv", days: int = 60) -> str:
    filepath = os.path.join(EXPORT_DIR, filename)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT sh.id, sh.date, sh.type, p.name AS part_name, p.category,
                   sh.quantity, sh.supplier, sh.notes, sh.user_id
            FROM stock_history sh
            JOIN parts p ON sh.part_id = p.id
            WHERE sh.date >= datetime('now', '-' || ? || ' days')
            ORDER BY sh.date DESC
            """,
            (days,),
        ) as cur:
            rows = await cur.fetchall()

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "ID операции", "Дата", "Тип", "Товар", "Категория",
            "Кол-во (шт)", "Поставщик", "Примечания", "User ID"
        ])
        for r in rows:
            mtype = "Приход" if r["type"] == "incoming" else "Расход/Списание"
            writer.writerow([
                r["id"], r["date"], mtype, r["part_name"], r["category"],
                r["quantity"], r["supplier"], r["notes"], r["user_id"]
            ])
    return filepath


# ─────────────────────────── РАСШИРЕННАЯ АНАЛИТИКА ─────────────────────────

async def get_sales_kpi() -> dict:
    """
    Возвращает финансовые и операционные KPI продаж:
    - Выручка, количество заказов и средний чек: Сегодня, за 7 дней, за 30 дней, за всё время.
    - Статусы заказов (pending, confirmed, completed, cancelled).
    - Способы доставки и оплаты.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row

            async def _period_metrics(condition: str, params: tuple = ()) -> dict:
                sql = f"""
                    SELECT
                        COUNT(*) AS orders_count,
                        COALESCE(SUM(total_amount), 0) AS revenue,
                        COALESCE(AVG(total_amount), 0) AS avg_check
                    FROM orders
                    WHERE status != 'cancelled' {condition}
                """
                async with db.execute(sql, params) as cur:
                    row = await cur.fetchone()
                    return dict(row) if row else {"orders_count": 0, "revenue": 0.0, "avg_check": 0.0}

            today = await _period_metrics("AND date(created_at) = date('now')")
            week = await _period_metrics("AND created_at >= datetime('now', '-7 days')")
            month = await _period_metrics("AND created_at >= datetime('now', '-30 days')")
            all_time = await _period_metrics("")

            # Распределение по статусам
            async with db.execute("SELECT status, COUNT(*) AS cnt FROM orders GROUP BY status") as cur:
                status_rows = await cur.fetchall()
                status_dist = {r["status"]: r["cnt"] for r in status_rows}

            # Распределение по доставке
            async with db.execute("SELECT delivery_method, COUNT(*) AS cnt FROM orders GROUP BY delivery_method") as cur:
                del_rows = await cur.fetchall()
                delivery_dist = {r["delivery_method"]: r["cnt"] for r in del_rows}

            # Распределение по оплате
            async with db.execute("SELECT payment_method, COUNT(*) AS cnt FROM orders GROUP BY payment_method") as cur:
                pay_rows = await cur.fetchall()
                payment_dist = {r["payment_method"]: r["cnt"] for r in pay_rows}

            return {
                "today": today,
                "week": week,
                "month": month,
                "all_time": all_time,
                "status_dist": status_dist,
                "delivery_dist": delivery_dist,
                "payment_dist": payment_dist,
            }
    except Exception:
        logger.exception("get_sales_kpi failed")
        return {}


async def get_abc_analysis() -> dict:
    """
    Классификация ассортимента по методу ABC (Правило Парето):
    A — до 80% накопленной выручки (ключевые ходовые позиции)
    B — следующие 15% выручки (стабильные товары со средним спросом)
    C — последние 5% выручки или товары без продаж (неликвид / редкий спрос)
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.category,
                    p.quantity AS stock_qty,
                    p.retail_price,
                    COALESCE(SUM(oi.quantity), 0) AS sold_qty,
                    COALESCE(SUM(oi.quantity * oi.price), 0) AS total_revenue
                FROM parts p
                LEFT JOIN order_items oi ON p.id = oi.part_id
                LEFT JOIN orders o ON oi.order_id = o.id AND o.status != 'cancelled'
                WHERE p.is_active = 1
                GROUP BY p.id
                ORDER BY total_revenue DESC, sold_qty DESC
                """
            ) as cur:
                items = [dict(r) for r in await cur.fetchall()]

            total_revenue = sum(it["total_revenue"] for it in items)

            group_a, group_b, group_c = [], [], []
            cum_rev = 0.0

            for it in items:
                rev = it["total_revenue"]
                if total_revenue > 0 and rev > 0:
                    cum_rev += rev
                    pct = (cum_rev / total_revenue) * 100.0
                    it["revenue_share"] = round((rev / total_revenue) * 100.0, 2)
                    it["cumulative_share"] = round(pct, 2)
                    if pct <= 80.0:
                        it["group"] = "A"
                        group_a.append(it)
                    elif pct <= 95.0:
                        it["group"] = "B"
                        group_b.append(it)
                    else:
                        it["group"] = "C"
                        group_c.append(it)
                else:
                    it["revenue_share"] = 0.0
                    it["cumulative_share"] = 100.0
                    it["group"] = "C"
                    group_c.append(it)

            return {
                "total_revenue": total_revenue,
                "total_items": len(items),
                "group_a": group_a,
                "group_b": group_b,
                "group_c": group_c,
                "counts": {
                    "A": len(group_a),
                    "B": len(group_b),
                    "C": len(group_c),
                },
                "revenue": {
                    "A": sum(i["total_revenue"] for i in group_a),
                    "B": sum(i["total_revenue"] for i in group_b),
                    "C": sum(i["total_revenue"] for i in group_c),
                },
            }
    except Exception:
        logger.exception("get_abc_analysis failed")
        return {}


async def get_procurement_forecast(days_window: int = 30) -> list[dict]:
    """
    Прогноз потребности в закупках (Smart Procurement):
    Рассчитывает скорость расхода (burn rate) за последние days_window дней,
    остаток дней запаса и рекомендованный объем дозаказа (на 30 дней запаса).
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.category,
                    p.quantity AS stock_qty,
                    p.low_stock_threshold,
                    p.cost_price,
                    p.supplier,
                    COALESCE(SUM(oi.quantity), 0) AS sold_period
                FROM parts p
                LEFT JOIN order_items oi ON p.id = oi.part_id
                LEFT JOIN orders o ON oi.order_id = o.id
                     AND o.status != 'cancelled'
                     AND o.created_at >= datetime('now', '-' || ? || ' days')
                WHERE p.is_active = 1
                GROUP BY p.id
                ORDER BY p.quantity ASC
                """,
                (days_window,),
            ) as cur:
                rows = await cur.fetchall()

            forecast = []
            for r in rows:
                item = dict(r)
                sold = item["sold_period"]
                stock = item["stock_qty"]
                daily_burn = sold / float(days_window)
                item["daily_burn"] = round(daily_burn, 2)

                if daily_burn > 0:
                    days_left = round(stock / daily_burn, 1)
                    item["days_left"] = days_left
                    target_stock = int(daily_burn * 30)
                    reorder_qty = max(0, target_stock - stock)
                    item["recommended_order"] = max(
                        reorder_qty,
                        (item["low_stock_threshold"] * 2 - stock) if stock <= item["low_stock_threshold"] else 0
                    )
                else:
                    item["days_left"] = 999.0 if stock > 0 else 0.0
                    item["recommended_order"] = item["low_stock_threshold"] * 2 if stock == 0 else 0

                if stock == 0:
                    item["urgency"] = "CRITICAL_EMPTY"
                    item["urgency_label"] = "🚨 Закончился"
                elif item["days_left"] <= 7 or stock <= item["low_stock_threshold"]:
                    item["urgency"] = "HIGH"
                    item["urgency_label"] = "⚠️ < 7 дней"
                elif item["days_left"] <= 14:
                    item["urgency"] = "MEDIUM"
                    item["urgency_label"] = "⏳ < 14 дней"
                else:
                    item["urgency"] = "OK"
                    item["urgency_label"] = "✅ В норме"

                forecast.append(item)

            urgency_weights = {"CRITICAL_EMPTY": 0, "HIGH": 1, "MEDIUM": 2, "OK": 3}
            forecast.sort(key=lambda x: (urgency_weights.get(x["urgency"], 4), x["days_left"]))
            return forecast
    except Exception:
        logger.exception("get_procurement_forecast failed")
        return []


async def get_top_clients(limit: int = 10) -> list[dict]:
    """
    Рейтинг ключевых клиентов по обороту и количеству заказов.
    """
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    u.user_id,
                    COALESCE(u.full_name, o.user_name, 'Клиент #' || u.user_id) AS full_name,
                    u.status AS client_type,
                    COUNT(o.id) AS orders_count,
                    COALESCE(SUM(o.total_amount), 0) AS total_spent,
                    COALESCE(AVG(o.total_amount), 0) AS avg_check,
                    MAX(o.created_at) AS last_order_date
                FROM orders o
                JOIN users u ON o.user_id = u.user_id
                WHERE o.status != 'cancelled'
                GROUP BY u.user_id
                ORDER BY total_spent DESC LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    except Exception:
        logger.exception("get_top_clients failed")
        return []


async def export_analytics_csv(filename: str = "sales_analytics.csv") -> str:
    """
    Выгружает комплексную аналитику: KPI, ABC-анализ, прогноз закупок и топ клиентов в один CSV.
    """
    filepath = os.path.join(EXPORT_DIR, filename)
    kpi = await get_sales_kpi()
    abc = await get_abc_analysis()
    forecast = await get_procurement_forecast()
    top_clients = await get_top_clients(limit=30)

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")

        # Блок 1: KPI
        writer.writerow(["=== ФИНАНСОВЫЕ KPI И ДИНАМИКА ПРОДАЖ ==="])
        writer.writerow(["Период", "Заказов (шт)", "Выручка (руб)", "Средний чек (руб)"])
        for period_key, label in [("today", "Сегодня"), ("week", "За 7 дней"), ("month", "За 30 дней"), ("all_time", "За всё время")]:
            p = kpi.get(period_key, {})
            writer.writerow([
                label,
                p.get("orders_count", 0),
                p.get("revenue", 0),
                round(p.get("avg_check", 0), 2),
            ])
        writer.writerow([])

        # Блок 2: Топ клиенты
        writer.writerow(["=== ТОП ПОКУПАТЕЛЕЙ (LTV) ==="])
        writer.writerow(["User ID", "Имя / Контакт", "Тип (розница/опт)", "Заказов", "Выручка (руб)", "Средний чек (руб)", "Последний заказ"])
        for c in top_clients:
            writer.writerow([
                c["user_id"], c["full_name"], c["client_type"], c["orders_count"],
                c["total_spent"], round(c["avg_check"], 2), c["last_order_date"]
            ])
        writer.writerow([])

        # Блок 3: ABC-анализ
        writer.writerow(["=== ABC-АНАЛИЗ АССОРТИМЕНТА (ПРАВИЛО ПАРЕТО) ==="])
        writer.writerow(["ID", "Товар", "Категория", "Группа ABC", "Остаток (шт)", "Продано (шт)", "Выручка (руб)", "Доля в выручке (%)", "Накопленная доля (%)"])
        all_abc = abc.get("group_a", []) + abc.get("group_b", []) + abc.get("group_c", [])
        for item in all_abc:
            writer.writerow([
                item["id"], item["name"], item["category"], item.get("group", "C"),
                item["stock_qty"], item["sold_qty"], item["total_revenue"],
                item.get("revenue_share", 0), item.get("cumulative_share", 0)
            ])
        writer.writerow([])

        # Блок 4: Прогноз закупок
        writer.writerow(["=== ПРОГНОЗ ЗАКУПОК И ДЕФИЦИТА (30 ДНЕЙ) ==="])
        writer.writerow(["ID", "Товар", "Категория", "Статус срочности", "Остаток (шт)", "Расход в день (шт)", "Хватит на (дней)", "Рекомендовано заказать (шт)", "Поставщик"])
        for fcast in forecast:
            writer.writerow([
                fcast["id"], fcast["name"], fcast["category"], fcast.get("urgency_label", ""),
                fcast["stock_qty"], fcast.get("daily_burn", 0), fcast.get("days_left", 0),
                fcast.get("recommended_order", 0), fcast.get("supplier", "")
            ])

    return filepath


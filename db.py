import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

DB_NAME = 'spare_parts.db'

# Роли доступа
ROLES = {
    'user': 'Пользователь',
    'warehouse_manager': 'Менеджер склада',
    'sales_manager': 'Менеджер продаж',
    'admin': 'Администратор'
}

# Пороги уведомлений
LOW_STOCK_THRESHOLD = 3  # Уведомлять если кол-во < 3

# Подкатегории по категориям
SUBCATEGORIES = {
    'Дисплеи': [
        'iPhone 15-14', 'iPhone 13-12', 'iPhone 11-SE',
        'Samsung Galaxy A', 'Samsung Galaxy S', 'Samsung Galaxy Note',
        'Xiaomi Redmi', 'Универсальные'
    ],
    'Аккумуляторы': [
        'iPhone 15-14', 'iPhone 13-12', 'iPhone 11-SE',
        'Samsung Galaxy', 'Xiaomi Redmi', 'Power Bank'
    ],
    'Крышки': [
        'iPhone', 'Samsung Galaxy', 'Xiaomi Redmi',
        'Google Pixel', 'Универсальные'
    ],
    'Камеры': [
        'Основная (Wide)', 'Фронтальная (Selfie)',
        'Ультраширокая (Ultra-wide)', 'Телефото (Zoom)'
    ],
    'Динамики': [
        'Встроенные динамики', 'Наушники', 
        'Bluetooth динамики', 'Другое'
    ]
}

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # Таблица пользователей с ролями
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'retail',
                role TEXT DEFAULT 'user',
                notifications_enabled INTEGER DEFAULT 1
            )
        ''')
        # Таблица запчастей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                subcategory TEXT,
                name TEXT,
                supplier TEXT,
                cost_price REAL,
                retail_price REAL,
                wholesale_price REAL,
                quantity INTEGER DEFAULT 0,
                low_stock_threshold INTEGER DEFAULT 3,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица истории поступлений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id INTEGER,
                quantity INTEGER,
                supplier TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                type TEXT DEFAULT 'incoming',
                user_id INTEGER,
                FOREIGN KEY (part_id) REFERENCES parts(id)
            )
        ''')
        # Таблица уведомлений о низких остатках
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS low_stock_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id INTEGER,
                user_id INTEGER,
                quantity INTEGER,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent INTEGER DEFAULT 0,
                FOREIGN KEY (part_id) REFERENCES parts(id)
            )
        ''')
        # Таблица отчетов (для кеша)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                category TEXT,
                data TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

def add_user(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        conn.commit()

def set_user_status(user_id, status):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute('UPDATE users SET status = ? WHERE user_id = ?', (status, user_id))
        conn.commit()

def set_user_role(user_id, role):
    """Установить роль пользователя"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute('UPDATE users SET role = ? WHERE user_id = ?', (role, user_id))
        conn.commit()

def get_user_status(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute('SELECT status FROM users WHERE user_id = ?', (user_id,)).fetchone()
        return res[0] if res else 'retail'

def get_user_role(user_id):
    """Получить роль пользователя"""
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute('SELECT role FROM users WHERE user_id = ?', (user_id,)).fetchone()
        return res[0] if res else 'user'

def has_admin_access(user_id):
    """Проверить админ доступ"""
    role = get_user_role(user_id)
    return role in ('admin', 'warehouse_manager', 'sales_manager')

def add_part(category, subcategory, name, supplier, cost_price, retail_price, wholesale_price):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute('''
            INSERT INTO parts (category, subcategory, name, supplier, cost_price, retail_price, wholesale_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (category, subcategory, name, supplier, cost_price, retail_price, wholesale_price))
        conn.commit()

def get_categories():
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute('SELECT DISTINCT category FROM parts ORDER BY category').fetchall()
        return [r[0] for r in res]

def get_subcategories(category):
    """Получить подкатегории для категории"""
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute(
            'SELECT DISTINCT subcategory FROM parts WHERE category = ? AND subcategory IS NOT NULL ORDER BY subcategory',
            (category,)
        ).fetchall()
        return [r[0] for r in res]

def get_parts_by_category(category, subcategory=None):
    with sqlite3.connect(DB_NAME) as conn:
        if subcategory:
            res = conn.cursor().execute('''
                SELECT id, name, retail_price, wholesale_price, quantity FROM parts 
                WHERE category = ? AND subcategory = ?
                ORDER BY name
            ''', (category, subcategory)).fetchall()
        else:
            res = conn.cursor().execute('''
                SELECT id, name, retail_price, wholesale_price, quantity FROM parts 
                WHERE category = ?
                ORDER BY name
            ''', (category,)).fetchall()
        return res

def get_part_by_id(part_id):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute(
            'SELECT id, name, category, subcategory, quantity, cost_price, retail_price, wholesale_price FROM parts WHERE id = ?',
            (part_id,)
        ).fetchone()
        return res

def update_part_quantity(part_id, quantity, user_id=None):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE parts SET quantity = quantity + ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?
        ''', (quantity, part_id))
        conn.commit()
        
        # Проверить низкие остатки
        check_low_stock(part_id, user_id)

def add_stock_history(part_id, quantity, supplier, notes='', user_id=None, stock_type='incoming'):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute('''
            INSERT INTO stock_history (part_id, quantity, supplier, notes, type, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (part_id, quantity, supplier, notes, stock_type, user_id))
        conn.commit()

def get_part_by_name(name, category):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute('''
            SELECT id FROM parts WHERE name = ? AND category = ?
        ''', (name, category)).fetchone()
        return res[0] if res else None

def check_low_stock(part_id, user_id=None):
    """Проверить и зафиксировать низкий уровень запасов"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        part = cursor.execute(
            'SELECT id, quantity, low_stock_threshold FROM parts WHERE id = ?',
            (part_id,)
        ).fetchone()
        
        if part and part[1] < part[2]:
            cursor.execute('''
                INSERT INTO low_stock_alerts (part_id, user_id, quantity)
                VALUES (?, ?, ?)
            ''', (part_id, user_id, part[1]))
            conn.commit()
            return True
        return False

def get_low_stock_alerts():
    """Получить все непрочитанные оповещения о низких остатках"""
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute('''
            SELECT la.id, p.name, p.category, p.quantity, la.date
            FROM low_stock_alerts la
            JOIN parts p ON la.part_id = p.id
            WHERE la.sent = 0
            ORDER BY la.date DESC
        ''').fetchall()
        return res

def mark_alert_sent(alert_id):
    """Отметить оповещение как отправленное"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute('UPDATE low_stock_alerts SET sent = 1 WHERE id = ?', (alert_id,))
        conn.commit()

def get_financial_report(category=None):
    """Получить финансовый отчет (рентабельность по категориям)"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if category:
                query = '''
                    SELECT 
                        category,
                        COUNT(*) as item_count,
                        SUM(quantity) as total_quantity,
                        SUM(cost_price * quantity) as total_cost,
                        SUM(retail_price * quantity) as total_retail_value,
                        SUM((retail_price - cost_price) * quantity) as total_profit,
                        ROUND(AVG((retail_price - cost_price) * 100.0 / retail_price), 2) as avg_margin_percent
                    FROM parts
                    WHERE category = ?
                    GROUP BY category
                '''
                res = cursor.execute(query, (category,)).fetchone()
                return dict(res) if res else None
            else:
                query = '''
                    SELECT 
                        category,
                        COUNT(*) as item_count,
                        SUM(quantity) as total_quantity,
                        SUM(cost_price * quantity) as total_cost,
                        SUM(retail_price * quantity) as total_retail_value,
                        SUM((retail_price - cost_price) * quantity) as total_profit,
                        ROUND(AVG((retail_price - cost_price) * 100.0 / retail_price), 2) as avg_margin_percent
                    FROM parts
                    GROUP BY category
                    ORDER BY total_profit DESC
                '''
                res = cursor.execute(query).fetchall()
                return [dict(row) for row in res]
    except Exception as e:
        print(f"❌ Ошибка при получении финансового отчета: {e}")
        return None

def get_stock_movement_report(days=30):
    """Получить отчет о движении товара за N дней"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            res = cursor.execute('''
                SELECT 
                    p.name,
                    p.category,
                    COUNT(*) as transaction_count,
                    SUM(CASE WHEN sh.type = 'incoming' THEN sh.quantity ELSE -sh.quantity END) as net_movement,
                    sh.supplier,
                    MAX(sh.date) as last_date
                FROM stock_history sh
                JOIN parts p ON sh.part_id = p.id
                WHERE sh.date >= datetime('now', '-' || ? || ' days')
                GROUP BY p.id, sh.supplier
                ORDER BY transaction_count DESC
            ''', (days,)).fetchall()
            return [dict(row) for row in res] if res else []
    except Exception as e:
        print(f"❌ Ошибка при получении отчета движения товара: {e}")
        return []

def get_supplier_analysis():
    """Анализ по поставщикам - поставки, надежность, частота доставок"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            res = cursor.execute('''
                SELECT 
                    p.supplier,
                    COUNT(DISTINCT p.id) as item_count,
                    SUM(CASE WHEN sh.type = 'incoming' THEN sh.quantity ELSE 0 END) as total_received,
                    COUNT(DISTINCT sh.id) as transaction_count,
                    MAX(sh.date) as last_delivery,
                    ROUND(AVG(CASE WHEN sh.type = 'incoming' THEN sh.quantity ELSE NULL END), 2) as avg_delivery_qty
                FROM parts p
                LEFT JOIN stock_history sh ON p.id = sh.part_id
                WHERE p.supplier IS NOT NULL
                GROUP BY p.supplier
                ORDER BY total_received DESC
            ''').fetchall()
            return [dict(row) for row in res] if res else []
    except Exception as e:
        print(f"❌ Ошибка при анализе поставщиков: {e}")
        return []

def get_category_profitability(category):
    """Получить прибыльность по категории"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            res = cursor.execute('''
                SELECT 
                    name,
                    quantity,
                    cost_price,
                    retail_price,
                    (retail_price - cost_price) as profit_per_unit,
                    (retail_price - cost_price) * quantity as total_profit,
                    ROUND((retail_price - cost_price) * 100.0 / retail_price, 1) as margin_percent
                FROM parts
                WHERE category = ?
                ORDER BY total_profit DESC
            ''', (category,)).fetchall()
            return [dict(row) for row in res] if res else []
    except Exception as e:
        print(f"❌ Ошибка при получении прибыльности: {e}")
        return []

def get_part(part_id):
    """Получить информацию о запчасти по ID"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT id, name, cost_price, retail_price, wholesale_price, quantity, category FROM parts WHERE id = ?',
                (part_id,)
            ).fetchone()
            return tuple(row) if row else None
    except Exception as e:
        print(f"❌ Ошибка при получении запчасти: {e}")
        return None

def get_part_quantity(part_id):
    """Получить количество запчасти на складе"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            qty = conn.execute(
                'SELECT quantity FROM parts WHERE id = ?',
                (part_id,)
            ).fetchone()
            return qty[0] if qty else 0
    except Exception as e:
        print(f"❌ Ошибка при получении количества: {e}")
        return 0

def add_stock_movement(part_id, quantity, supplier=None, notes=None):
    """Добавить приход товара и обновить остаток"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute(
                'UPDATE parts SET quantity = quantity + ? WHERE id = ?',
                (quantity, part_id)
            )
            conn.execute('''
                INSERT INTO stock_history (part_id, quantity, supplier, notes, stock_type, created_at)
                VALUES (?, ?, ?, ?, 'incoming', CURRENT_TIMESTAMP)
            ''', (part_id, quantity, supplier, notes))
            conn.commit()
    except Exception as e:
        print(f"❌ Ошибка при добавлении приходования: {e}")

def create_low_stock_alert(part_id, current_qty):
    """Создать уведомление о низком остатке"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute('''
                INSERT OR IGNORE INTO low_stock_alerts (part_id, quantity, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (part_id, current_qty))
            conn.commit()
    except Exception as e:
        print(f"❌ Ошибка при создании уведомления: {e}")

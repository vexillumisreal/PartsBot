"""
import_kazan_prices.py — импорт номенклатуры, 4-уровневой иерархии и цен из 'Цены Казань.xlsx' в spare_parts.db.

Иерархия:
  Бренд (category): iPhone, Samsung, Xiaomi, Huawei / Honor, Tecno, Infinix, Realme / Oppo, iPad, Другие
  Модель (subcategory): iPhone 13, Galaxy A51, Redmi Note 8...
  Категория запчасти (part_type): Дисплеи, Аккумуляторы, Крышки, Шлейфы, Камеры, Динамики, Разное
  Конкретная запчасть (name)

Цены:
  В файле 'Цены Казань.xlsx' колонка F содержит розничную цену продажи (retail_price).
  Оптовая цена (wholesale_price) = розница со скидкой 20% (округление до 10 руб).
  Закупочная цена (cost_price) = 0.0 (вводится вручную при приходовании товара на склад).
"""

import os
import sys
import re
import zipfile
import sqlite3
import logging
import xml.etree.ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("import_kazan")

DB_NAME = "spare_parts.db"
EXCEL_FILE = "Цены Казань.xlsx"


def extract_hierarchy(name: str, raw_model: str, raw_quality: str) -> tuple[str, str, str]:
    """
    Разбирает строку запчасти на 3 уровня: (Бренд, Модель, Категория запчасти).
    """
    name_lower = name.lower()
    m_lower = (raw_model or "").lower()
    full_text = f"{name_lower} {m_lower}"

    # 1. ТИП ЗАПЧАСТИ (part_type)
    if name_lower.startswith("дисплей") or "дисплейный" in name_lower or "тачскрин" in name_lower:
        part_type = "Дисплеи"
    elif name_lower.startswith("аккумулятор") or "акб" in name_lower:
        part_type = "Аккумуляторы"
    elif "крышка" in name_lower or "корпус" in name_lower:
        part_type = "Крышки"
    elif name_lower.startswith("камера") or "камеры" in name_lower:
        part_type = "Камеры"
    elif name_lower.startswith("динамик") or "динамики" in name_lower or "звонок" in name_lower:
        part_type = "Динамики"
    elif name_lower.startswith("шлейф") or "шлейфы" in name_lower:
        part_type = "Шлейфы"
    else:
        part_type = "Разное"

    # 2. БРЕНД (brand / category)
    if "iphone" in full_text:
        brand = "iPhone"
    elif "ipad" in full_text:
        brand = "iPad"
    elif "samsung" in full_text:
        brand = "Samsung"
    elif any(x in full_text for x in ["xiaomi", "redmi", "poco"]):
        brand = "Xiaomi"
    elif any(x in full_text for x in ["huawei", "honor"]):
        brand = "Huawei / Honor"
    elif "tecno" in full_text:
        brand = "Tecno"
    elif "infinix" in full_text:
        brand = "Infinix"
    elif any(x in full_text for x in ["realme", "oppo"]):
        brand = "Realme / Oppo"
    elif "vivo" in full_text:
        brand = "Vivo"
    else:
        brand = "Другие"

    # 3. МОДЕЛЬ (model / subcategory)
    if brand == "iPhone":
        patterns = [
            (r"16\s*pro\s*max", "iPhone 16 Pro Max"),
            (r"16\s*pro", "iPhone 16 Pro"),
            (r"16\s*plus", "iPhone 16 Plus"),
            (r"16\b", "iPhone 16"),
            (r"15\s*pro\s*max", "iPhone 15 Pro Max"),
            (r"15\s*pro", "iPhone 15 Pro"),
            (r"15\s*plus", "iPhone 15 Plus"),
            (r"15\b", "iPhone 15"),
            (r"14\s*pro\s*max", "iPhone 14 Pro Max"),
            (r"14\s*pro", "iPhone 14 Pro"),
            (r"14\s*plus", "iPhone 14 Plus"),
            (r"14\b", "iPhone 14"),
            (r"13\s*pro\s*max", "iPhone 13 Pro Max"),
            (r"13\s*pro", "iPhone 13 Pro"),
            (r"13\s*mini", "iPhone 13 mini"),
            (r"13\b", "iPhone 13"),
            (r"12\s*pro\s*max", "iPhone 12 Pro Max"),
            (r"12/12\s*pro", "iPhone 12 / 12 Pro"),
            (r"12\s*pro", "iPhone 12 Pro"),
            (r"12\s*mini", "iPhone 12 mini"),
            (r"12\b", "iPhone 12"),
            (r"11\s*pro\s*max", "iPhone 11 Pro Max"),
            (r"11\s*pro", "iPhone 11 Pro"),
            (r"11\b", "iPhone 11"),
            (r"xs\s*max", "iPhone XS Max"),
            (r"xs\b", "iPhone XS"),
            (r"xr\b", "iPhone XR"),
            (r"\bx\b", "iPhone X"),
            (r"se\s*2022|se\s*3", "iPhone SE 2022"),
            (r"se\s*2020|se\s*2", "iPhone SE 2020"),
            (r"\bse\b", "iPhone SE"),
            (r"8\s*plus", "iPhone 8 Plus"),
            (r"8\b", "iPhone 8"),
            (r"7\s*plus", "iPhone 7 Plus"),
            (r"7\b", "iPhone 7"),
            (r"6s\s*plus", "iPhone 6s Plus"),
            (r"6s\b", "iPhone 6s"),
            (r"6\s*plus", "iPhone 6 Plus"),
            (r"6\b", "iPhone 6"),
            (r"5s|5c|5\b", "iPhone 5/5s"),
        ]
        model = "iPhone Другие"
        for pat, norm in patterns:
            if re.search(pat, full_text):
                model = norm
                break
    elif brand == "iPad":
        if "air 2" in full_text:
            model = "iPad Air 2"
        elif "air" in full_text:
            model = "iPad Air"
        elif "mini" in full_text:
            model = "iPad mini"
        elif "pro" in full_text:
            model = "iPad Pro"
        else:
            model = "iPad"
    else:
        # Для Android брендов берем raw_model или очищаем
        if raw_model:
            # Убираем служебный мусор цветов и меток
            clean = raw_model
            clean = re.sub(r"^(Аккумулятор для |Дисплей для |Шлейф для |Задняя крышка для )", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*-\s*(OR|Ориг|Премиум|Copy|AAA|High Copy|HQ|JK|GX|ZY).*$", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*\((Black|White|Blue|Gold|Green|Purple|Red|Черный|Белый|Синий|Золотой|Фиолетовый|Зеленый|Красный|Grey|Gray|Silver|Yellow|Pink|Orange|orig|copy|sota|wide connector|2 flex)[^\)]*\)", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"!+.*?!+", "", clean).strip()
            # Ограничиваем длину названия модели для красивого отображения на кнопках
            if len(clean) > 36:
                clean = clean[:33] + "..."
            model = clean if clean else raw_model.strip()
        else:
            model = brand

    return brand, model, part_type


def parse_kazan_excel(file_path: str) -> list[dict]:
    """
    Парсит .xlsx напрямую через zipfile и ElementTree (без сторонних библиотек).
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл {file_path} не найден!")

    with zipfile.ZipFile(file_path) as z:
        wb_ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in tree.findall("ns:si", wb_ns):
                t_elems = si.findall(".//ns:t", wb_ns)
                text = "".join(t.text or "" for t in t_elems)
                shared_strings.append(text)

        sheet_tree = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = sheet_tree.findall(".//ns:row", wb_ns)

        items = []
        for row in rows:
            r_num = int(row.attrib.get("r", 0))
            if r_num <= 4:  # Пропускаем шапку таблицы
                continue

            row_data = {}
            for c in row.findall("ns:c", wb_ns):
                ref = c.attrib.get("r")
                col = "".join(filter(str.isalpha, ref))
                cell_type = c.attrib.get("t")
                v = c.find("ns:v", wb_ns)
                val = v.text if v is not None else None
                if cell_type == "s" and val is not None:
                    val = shared_strings[int(val)]
                row_data[col] = val

            name = (row_data.get("A") or "").strip()
            if not name:
                continue

            model_raw = (row_data.get("D") or "").strip()
            quality = (row_data.get("E") or "").strip()
            price_raw = row_data.get("F")

            try:
                # В файле колонка F — это розничная цена продажи!
                retail_price = float(price_raw) if price_raw else 0.0
            except ValueError:
                retail_price = 0.0

            # Оптовая цена: -20% от розничной, округление до 10 руб.
            if retail_price > 0:
                wholesale_price = float(round(retail_price * 0.80 / 10) * 10)
            else:
                wholesale_price = 0.0

            # Закупочная цена: 0.0 (заполняется при оприходовании товара)
            cost_price = 0.0

            brand, model, part_type = extract_hierarchy(name, model_raw, quality)

            items.append({
                "name": name,
                "category": brand,          # Уровень 1: Бренд
                "subcategory": model,        # Уровень 2: Модель
                "part_type": part_type,      # Уровень 3: Категория детали
                "retail_price": retail_price,
                "wholesale_price": wholesale_price,
                "cost_price": cost_price,
                "supplier": "Казань",
                "quantity": 0,
                "low_stock_threshold": 3,
                "is_active": 1,
            })

    return items


def import_kazan_prices(db_path: str = DB_NAME, excel_path: str = EXCEL_FILE) -> tuple[int, int]:
    """
    Выполняет импорт позиций в SQLite с сохранением существующих остатков.
    Возвращает (добавлено, обновлено).
    """
    logger.info("Чтение файла Excel: %s", excel_path)
    items = parse_kazan_excel(excel_path)
    logger.info("Прочитано позиций из Excel: %d", len(items))

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Проверяем наличие колонки part_type в таблице parts
    cursor.execute("PRAGMA table_info(parts)")
    cols = {r[1] for r in cursor.fetchall()}
    if "part_type" not in cols:
        cursor.execute("ALTER TABLE parts ADD COLUMN part_type TEXT DEFAULT ''")
        logger.info("Добавлена колонка parts.part_type")

    inserted = 0
    updated = 0

    for it in items:
        cursor.execute("SELECT id, cost_price, quantity FROM parts WHERE name = ?", (it["name"],))
        existing = cursor.fetchone()

        if existing:
            part_id, curr_cost, curr_qty = existing
            # Если реальная закупка уже была зафиксирована (были движения по складу), сохраняем её
            cursor.execute(
                "SELECT price FROM stock_history WHERE part_id = ? AND type = 'incoming' AND price > 0 ORDER BY date DESC LIMIT 1",
                (part_id,),
            )
            hist_cost = cursor.fetchone()
            real_cost = hist_cost[0] if hist_cost else 0.0

            cursor.execute(
                """
                UPDATE parts
                SET category = ?,
                    subcategory = ?,
                    part_type = ?,
                    retail_price = ?,
                    wholesale_price = ?,
                    cost_price = ?,
                    supplier = ?,
                    is_active = 1
                WHERE id = ?
                """,
                (
                    it["category"],
                    it["subcategory"],
                    it["part_type"],
                    it["retail_price"],
                    it["wholesale_price"],
                    real_cost,
                    it["supplier"],
                    part_id,
                ),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO parts (
                    category, subcategory, part_type, name, supplier,
                    cost_price, retail_price, wholesale_price,
                    quantity, low_stock_threshold, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    it["category"],
                    it["subcategory"],
                    it["part_type"],
                    it["name"],
                    it["supplier"],
                    it["cost_price"],
                    it["retail_price"],
                    it["wholesale_price"],
                    it["quantity"],
                    it["low_stock_threshold"],
                    it["is_active"],
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()

    logger.info("Импорт завершён! Добавлено: %d, обновлено: %d", inserted, updated)
    return inserted, updated


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    db_file = sys.argv[1] if len(sys.argv) > 1 else DB_NAME
    xlsx_file = sys.argv[2] if len(sys.argv) > 2 else EXCEL_FILE
    ins, upd = import_kazan_prices(db_file, xlsx_file)
    print(f"\n✅ Успешно импортировано: {ins} новых позиций, {upd} обновлено.")

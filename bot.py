import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import db

BOT_TOKEN = "8981400502:AAG8zDjlFAg4oWTF--32GZ8QVUEdV7s_keQ"
ADMIN_ID = 6139301544

CATEGORIES = {
    "📱 Дисплеи": "Дисплеи",
    "🔲 Крышки": "Крышки",
    "🔋 Аккумуляторы": "Аккумуляторы",
    "📞 Шлейфы iPhone": "Шлейфы iPhone",
    "🤖 Шлейфы Android": "Шлейфы Android",
    "📷 Камеры": "Камеры",
    "🔊 Динамики": "Динамики"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class PartState(StatesGroup):
    category = State()
    subcategory = State()
    name = State()
    supplier = State()
    cost_price = State()
    retail_price = State()
    wholesale_price = State()

class StockInState(StatesGroup):
    category = State()
    subcategory = State()
    part = State()
    quantity = State()
    supplier = State()
    notes = State()

class ReportState(StatesGroup):
    report_type = State()
    category = State()

class RoleState(StatesGroup):
    user_id = State()
    role = State()

def get_main_menu(user_id):
    builder = ReplyKeyboardBuilder()
    builder.button(text="📦 Каталог запчастей")
    builder.button(text="📥 Приходование")
    
    role = db.get_user_role(user_id)
    if role in ('admin', 'warehouse_manager', 'sales_manager'):
        builder.button(text="⚙️ Админ-панель")
    
    builder.adjust(2)
    return builder.as_markup()

def get_admin_menu(user_id):
    builder = ReplyKeyboardBuilder()
    role = db.get_user_role(user_id)
    
    builder.button(text="📦 Каталог запчастей")
    builder.button(text="📥 Приходование")
    
    if role in ('admin', 'warehouse_manager'):
        builder.button(text="🔔 Оповещения о низких остатках")
        builder.button(text="📊 Отчет о движении товара")
    
    if role in ('admin', 'sales_manager'):
        builder.button(text="💰 Финансовый анализ")
        builder.button(text="🏢 Анализ поставщиков")
    
    if role == 'admin':
        builder.button(text="➕ Добавить запчасть")
        builder.button(text="👥 Управление ролями")
    
    builder.button(text="🔙 Назад в меню")
    builder.adjust(1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.add_user(message.from_user.id)
    status = db.get_user_status(message.from_user.id)
    role = db.get_user_role(message.from_user.id)
    status_text = "ОПТ 📦" if status == 'wholesale' else "РОЗНИЦА 🛍️"
    role_text = db.ROLES.get(role, "Пользователь")
    
    welcome_text = (
        f"👋 **Добро пожаловать!**\n\n"
        f"📊 Ваш статус: {status_text}\n"
        f"👤 Роль: {role_text}\n\n"
        f"Выберите действие из меню ниже:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(message.from_user.id))

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🆘 **СПРАВКА**\n\n"
        "📦 **Каталог** - просмотр доступных запчастей\n"
        "📥 **Приходование** - добавление товара на склад\n"
        "⚙️ **Админ-панель** - управление (для менеджеров)\n\n"
        "**Роли:**\n"
        "👤 Пользователь - только каталог\n"
        "🏭 Менеджер склада - приход + уведомления + движение\n"
        "💼 Менеджер продаж - финансы + поставщики\n"
        "🔐 Администратор - полный доступ\n"
    )
    await message.answer(help_text)

# ====== КАТАЛОГ ЗАПЧАСТЕЙ ======
@dp.message(F.text == "📦 Каталог запчастей")
async def show_catalog_menu(message: types.Message):
    builder = InlineKeyboardBuilder()
    for emoji_name, category in CATEGORIES.items():
        builder.button(text=emoji_name, callback_data=f"cat_{category}")
    builder.adjust(2)
    
    await message.answer(
        "🛍️ **Выберите категорию запчастей:**",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("cat_"))
async def show_category(callback: types.CallbackQuery):
    category = callback.data.split("_", 1)[1]
    
    try:
        subcats = db.get_subcategories(category)
    except Exception as e:
        print(f"❌ Ошибка получения подкатегорий: {e}")
        subcats = []
    
    if subcats:
        builder = InlineKeyboardBuilder()
        for subcat in subcats:
            builder.button(text=subcat, callback_data=f"subcat_{category}_{subcat}")
        builder.button(text="🔙 Назад", callback_data="back_catalog")
        builder.adjust(1)
        
        await callback.message.edit_text(
            f"📦 **{category}** - Выберите подкатегорию:",
            reply_markup=builder.as_markup()
        )
    else:
        await show_parts_by_category(callback, category)
    
    await callback.answer()

@dp.callback_query(F.data.startswith("subcat_"))
async def show_by_subcategory(callback: types.CallbackQuery):
    parts = callback.data.split("_", 2)
    category = parts[1]
    subcategory = "_".join(parts[2:]) if len(parts) > 2 else ""
    
    try:
        parts_list = db.get_parts_by_category(category, subcategory)
    except Exception as e:
        print(f"❌ Ошибка получения запчастей: {e}")
        parts_list = []
    
    status = db.get_user_status(callback.from_user.id)
    
    if not parts_list:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data=f"cat_{category}")
        await callback.message.edit_text(
            f"⚠️ В подкатегории **{subcategory}** нет запчастей.",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
        return
    
    text = f"📦 **{category}** → **{subcategory}**\n\n"
    for part_id, name, ret_price, wh_price, qty in parts_list:
        price = wh_price if status == 'wholesale' else ret_price
        status_icon = "✅" if qty > 0 else "❌"
        text += f"{status_icon} **{name}**\n   Цена: `{price:.0f}₽` | Кол-во: `{qty} шт`\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=f"cat_{category}")
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

async def show_parts_by_category(callback: types.CallbackQuery, category: str):
    try:
        parts_list = db.get_parts_by_category(category)
    except Exception as e:
        print(f"❌ Ошибка получения запчастей: {e}")
        parts_list = []
    
    status = db.get_user_status(callback.from_user.id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="back_catalog")
    
    if not parts_list:
        await callback.message.edit_text(
            f"⚠️ В категории **{category}** пока нет запчастей.",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
        return
    
    text = f"📦 **{category}**\n\n"
    for part_id, name, ret_price, wh_price, qty in parts_list:
        price = wh_price if status == 'wholesale' else ret_price
        status_icon = "✅" if qty > 0 else "❌"
        text += f"{status_icon} **{name}**\n   Цена: `{price:.0f}₽` | Кол-во: `{qty} шт`\n\n"
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "back_catalog")
async def back_to_catalog(callback: types.CallbackQuery):
    await show_catalog_menu(callback.message)
    await callback.answer()

# ====== ПРИХОДОВАНИЕ ======
@dp.message(F.text == "📥 Приходование")
async def start_stock_in(message: types.Message, state: FSMContext):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager'):
        await message.answer("❌ У вас нет прав для приходования товара.\nТолько менеджер склада или администратор.")
        return
    
    builder = InlineKeyboardBuilder()
    for emoji_name, category in CATEGORIES.items():
        builder.button(text=emoji_name, callback_data=f"stock_cat_{category}")
    builder.adjust(2)
    
    await message.answer("📥 **Выберите категорию для приходования:**", reply_markup=builder.as_markup())
    await state.set_state(StockInState.category)

@dp.callback_query(F.data.startswith("stock_cat_"))
async def select_stock_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split("_", 2)[2]
    await state.update_data(category=category)
    
    try:
        subcats = db.get_subcategories(category)
    except Exception as e:
        print(f"❌ Ошибка получения подкатегорий: {e}")
        subcats = []
    
    if subcats:
        builder = InlineKeyboardBuilder()
        for subcat in subcats:
            builder.button(text=subcat, callback_data=f"stock_subcat_{category}_{subcat}")
        builder.adjust(1)
        await callback.message.edit_text(
            f"📦 **{category}** - Выберите подкатегорию:",
            reply_markup=builder.as_markup()
        )
    else:
        await show_stock_parts(callback, category, state)
    
    await callback.answer()

@dp.callback_query(F.data.startswith("stock_subcat_"))
async def select_stock_subcategory(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    category = parts[2]
    subcategory = "_".join(parts[3:]) if len(parts) > 3 else ""
    
    await state.update_data(category=category, subcategory=subcategory)
    
    try:
        parts_list = db.get_parts_by_category(category, subcategory)
    except Exception as e:
        print(f"❌ Ошибка получения запчастей: {e}")
        parts_list = []
    
    if not parts_list:
        await callback.message.edit_text(
            f"⚠️ В подкатегории **{subcategory}** нет запчастей.\n"
            f"❌ Нельзя приходовать товар, которого нет в базе."
        )
        await callback.answer()
        return
    
    builder = InlineKeyboardBuilder()
    for part_id, name, _, _, _ in parts_list:
        builder.button(text=name, callback_data=f"stock_part_{part_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"📦 **{category}** → **{subcategory}** - Выберите запчасть:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def show_stock_parts(callback: types.CallbackQuery, category: str, state: FSMContext):
    try:
        parts_list = db.get_parts_by_category(category)
    except Exception as e:
        print(f"❌ Ошибка получения запчастей: {e}")
        parts_list = []
    
    if not parts_list:
        await callback.message.edit_text(
            f"⚠️ В категории **{category}** нет запчастей.\n"
            f"❌ Нельзя приходовать товар, которого нет в базе."
        )
        await callback.answer()
        return
    
    builder = InlineKeyboardBuilder()
    for part_id, name, _, _, _ in parts_list:
        builder.button(text=name, callback_data=f"stock_part_{part_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"📦 **{category}** - Выберите запчасть:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("stock_part_"))
async def select_stock_part(callback: types.CallbackQuery, state: FSMContext):
    part_id = callback.data.split("_", 2)[2]
    await state.update_data(part=part_id)
    
    await callback.message.edit_text("📝 Введите количество запчастей:")
    await state.set_state(StockInState.quantity)
    await callback.answer()

@dp.message(StockInState.quantity)
async def input_stock_quantity(message: types.Message, state: FSMContext):
    try:
        quantity = int(message.text)
        if quantity <= 0:
            await message.answer("❌ Количество должно быть больше 0!")
            return
    except ValueError:
        await message.answer("❌ Пожалуйста, введите целое число!")
        return
    
    await state.update_data(quantity=quantity)
    await message.answer("📝 Введите поставщика (или напишите 'Пропустить'):")
    await state.set_state(StockInState.supplier)

@dp.message(StockInState.supplier)
async def input_stock_supplier(message: types.Message, state: FSMContext):
    supplier = message.text if message.text.lower() != 'пропустить' else None
    await state.update_data(supplier=supplier)
    await message.answer("📝 Введите примечания (или напишите 'Пропустить'):")
    await state.set_state(StockInState.notes)

@dp.message(StockInState.notes)
async def input_stock_notes(message: types.Message, state: FSMContext):
    notes = message.text if message.text.lower() != 'пропустить' else None
    data = await state.get_data()
    
    try:
        part_id = int(data['part'])
        quantity = data['quantity']
        supplier = data['supplier']
        
        db.add_stock_movement(part_id, quantity, supplier, notes)
        
        part = db.get_part(part_id)
        if part:
            _, name, _, _, _, _, _ = part
            
            current_qty = db.get_part_quantity(part_id)
            low_stock_threshold = db.LOW_STOCK_THRESHOLD
            if current_qty and current_qty < low_stock_threshold:
                db.create_low_stock_alert(part_id, current_qty)
                await bot.send_message(
                    ADMIN_ID,
                    f"🔔 **ВНИМАНИЕ: Низкий остаток!**\n\n"
                    f"Запчасть: **{name}**\n"
                    f"Остаток: `{current_qty} шт` (порог: `{low_stock_threshold} шт`)"
                )
            
            await message.answer(
                f"✅ Успешно приходовано!\n\n"
                f"Запчасть: **{name}**\n"
                f"Количество: **{quantity} шт**\n"
                f"Поставщик: **{supplier or 'Не указан'}**",
                reply_markup=get_main_menu(message.from_user.id)
            )
        else:
            await message.answer("❌ Ошибка: запчасть не найдена!")
    except Exception as e:
        print(f"❌ Ошибка приходования: {e}")
        await message.answer("❌ Ошибка при сохранении данных!")
    
    await state.clear()

# ====== АДМИН-ПАНЕЛЬ ======
@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager', 'sales_manager'):
        await message.answer("❌ У вас нет доступа к админ-панели.")
        return
    
    await message.answer("⚙️ **Админ-панель**", reply_markup=get_admin_menu(message.from_user.id))

@dp.message(F.text == "🔙 Назад в меню")
async def back_to_main(message: types.Message):
    await message.answer("📋 **Главное меню**", reply_markup=get_main_menu(message.from_user.id))

# ====== ОПОВЕЩЕНИЯ О НИЗКИХ ОСТАТКАХ ======
@dp.message(F.text == "🔔 Оповещения о низких остатках")
async def show_low_stock_alerts(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager'):
        await message.answer("❌ У вас нет прав для просмотра оповещений.")
        return
    
    try:
        alerts = db.get_low_stock_alerts()
    except Exception as e:
        print(f"❌ Ошибка получения оповещений: {e}")
        alerts = []
    
    if not alerts:
        await message.answer("✅ Все запчасти в норме! Нет оповещений о низких остатках.")
        return
    
    text = "🔔 **Оповещения о низких остатках:**\n\n"
    for alert_id, part_id, qty, created_at in alerts:
        part = db.get_part(part_id)
        if part:
            _, name, _, _, _, _, _ = part
            text += f"❗ **{name}**\n   Остаток: `{qty} шт`\n   Время: `{created_at}`\n\n"
    
    await message.answer(text)

# ====== ОТЧЕТ О ДВИЖЕНИИ ТОВАРА ======
@dp.message(F.text == "📊 Отчет о движении товара")
async def show_stock_movement(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager'):
        await message.answer("❌ У вас нет прав для просмотра отчетов.")
        return
    
    try:
        report = db.get_stock_movement_report()
    except Exception as e:
        print(f"❌ Ошибка получения отчета: {e}")
        report = []
    
    if not report:
        await message.answer("⚠️ Нет данных о движении товара.")
        return
    
    text = "📊 **Отчет о движении товара (последние 30 дней):**\n\n"
    for row in report:
        text += f"📦 **{row['part_name']}**\n   Приход: `{row['inflow']} шт` | Расход: `{row['outflow']} шт`\n   Поставщик: `{row['supplier'] or 'Не указан'}`\n\n"
    
    await message.answer(text)

# ====== ФИНАНСОВЫЙ АНАЛИЗ ======
@dp.message(F.text == "💰 Финансовый анализ")
async def show_financial_analysis(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'sales_manager'):
        await message.answer("❌ У вас нет прав для просмотра финансовых данных.")
        return
    
    try:
        report = db.get_financial_report()
    except Exception as e:
        print(f"❌ Ошибка получения финансовых данных: {e}")
        report = None
    
    if not report:
        await message.answer("⚠️ Нет финансовых данных.")
        return
    
    text = "💰 **Финансовый анализ:**\n\n"
    text += f"💵 **Общая выручка:** `{report['total_revenue']:.2f} ₽`\n"
    text += f"💸 **Общие затраты:** `{report['total_cost']:.2f} ₽`\n"
    text += f"📈 **Общая прибыль:** `{report['total_profit']:.2f} ₽`\n"
    text += f"📊 **Средняя маржа:** `{report['avg_margin']:.2f}%`\n\n"
    
    profitability = db.get_category_profitability()
    if profitability:
        text += "**Рентабельность по категориям:**\n"
        for row in profitability:
            text += f"📦 **{row['part_name']}** - Маржа: `{row['margin_percent']:.2f}%`\n"
    
    await message.answer(text)

# ====== АНАЛИЗ ПОСТАВЩИКОВ ======
@dp.message(F.text == "🏢 Анализ поставщиков")
async def show_supplier_analysis(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'sales_manager'):
        await message.answer("❌ У вас нет прав для просмотра анализа поставщиков.")
        return
    
    try:
        report = db.get_supplier_analysis()
    except Exception as e:
        print(f"❌ Ошибка получения данных поставщиков: {e}")
        report = []
    
    if not report:
        await message.answer("⚠️ Нет данных о поставщиках.")
        return
    
    text = "🏢 **Анализ поставщиков:**\n\n"
    for row in report:
        text += (
            f"🏭 **{row['supplier']}**\n"
            f"   Поставок: `{row['delivery_count']}`\n"
            f"   Средний объем: `{row['avg_delivery_qty']:.0f} шт`\n"
            f"   Последняя доставка: `{row['last_delivery']}`\n\n"
        )
    
    await message.answer(text)

# ====== УПРАВЛЕНИЕ РОЛЯМИ (ТОЛЬКО АДМИН) ======
@dp.message(F.text == "👥 Управление ролями")
async def manage_roles(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role != 'admin':
        await message.answer("❌ Только администратор может управлять ролями.")
        return
    
    await message.answer(
        "👥 **Управление ролями**\n\n"
        "Введите ID пользователя и новую роль в формате:\n"
        "`<user_id> <роль>`\n\n"
        "Доступные роли:\n"
        "- `user` - Пользователь\n"
        "- `warehouse_manager` - Менеджер склада\n"
        "- `sales_manager` - Менеджер продаж\n"
        "- `admin` - Администратор"
    )

@dp.message(F.text == "➕ Добавить запчасть")
async def add_new_part(message: types.Message, state: FSMContext):
    role = db.get_user_role(message.from_user.id)
    if role != 'admin':
        await message.answer("❌ Только администратор может добавлять запчасти.")
        return
    
    builder = InlineKeyboardBuilder()
    for emoji_name, category in CATEGORIES.items():
        builder.button(text=emoji_name, callback_data=f"add_cat_{category}")
    builder.adjust(2)
    
    await message.answer("➕ **Выберите категорию для новой запчасти:**", reply_markup=builder.as_markup())
    await state.set_state(PartState.category)

@dp.callback_query(F.data.startswith("add_cat_"))
async def select_add_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split("_", 2)[2]
    await state.update_data(category=category)
    
    try:
        subcats = db.get_subcategories(category)
    except Exception as e:
        print(f"❌ Ошибка получения подкатегорий: {e}")
        subcats = []
    
    if subcats:
        builder = InlineKeyboardBuilder()
        for subcat in subcats:
            builder.button(text=subcat, callback_data=f"add_subcat_{category}_{subcat}")
        builder.adjust(1)
        await callback.message.edit_text(
            f"➕ **{category}** - Выберите подкатегорию:",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.message.edit_text(f"➕ **{category}** - Введите название запчасти:")
        await state.set_state(PartState.name)
    
    await callback.answer()

@dp.callback_query(F.data.startswith("add_subcat_"))
async def select_add_subcategory(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    category = parts[2]
    subcategory = "_".join(parts[3:]) if len(parts) > 3 else ""
    
    await state.update_data(category=category, subcategory=subcategory)
    await callback.message.edit_text("➕ Введите название запчасти:")
    await state.set_state(PartState.name)
    await callback.answer()

@dp.message(PartState.name)
async def input_part_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("💰 Введите себестоимость (₽):")
    await state.set_state(PartState.cost_price)

@dp.message(PartState.cost_price)
async def input_cost_price(message: types.Message, state: FSMContext):
    try:
        cost_price = float(message.text)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    await state.update_data(cost_price=cost_price)
    await message.answer("💰 Введите розничную цену (₽):")
    await state.set_state(PartState.retail_price)

@dp.message(PartState.retail_price)
async def input_retail_price(message: types.Message, state: FSMContext):
    try:
        retail_price = float(message.text)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    await state.update_data(retail_price=retail_price)
    await message.answer("💰 Введите оптовую цену (₽):")
    await state.set_state(PartState.wholesale_price)

@dp.message(PartState.wholesale_price)
async def input_wholesale_price(message: types.Message, state: FSMContext):
    try:
        wholesale_price = float(message.text)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    data = await state.get_data()
    
    try:
        db.add_part(
            data['category'],
            data.get('subcategory', ''),
            data['name'],
            data['cost_price'],
            data['retail_price'],
            wholesale_price
        )
        
        await message.answer(
            f"✅ Запчасть успешно добавлена!\n\n"
            f"Название: **{data['name']}**\n"
            f"Категория: **{data['category']}**\n"
            f"Себестоимость: `{data['cost_price']:.0f}₽`\n"
            f"Розница: `{data['retail_price']:.0f}₽`\n"
            f"Опт: `{wholesale_price:.0f}₽`",
            reply_markup=get_admin_menu(message.from_user.id)
        )
    except Exception as e:
        print(f"❌ Ошибка добавления запчасти: {e}")
        await message.answer("❌ Ошибка при добавлении запчасти!")
    
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

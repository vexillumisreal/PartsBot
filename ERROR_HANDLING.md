# 🛡️ ERROR HANDLING - PartsBot v3.0

## Общая стратегия обработки ошибок

PartsBot использует многоуровневую стратегию обработки ошибок:

1. **Try-Except на уровне БД** (`db.py`) - все DB-операции обернуты в try-except с логированием
2. **Валидация на уровне Bot** (`bot.py`) - проверка входных данных перед обработкой
3. **Graceful Degradation** - если операция не удалась, показываем пользователю понятное сообщение
4. **Console Logging** - все ошибки логируются в консоль для отладки

---

## Категория 1: Ошибки каталога

### 1.1 Пустая категория

**Сценарий:** Пользователь выбирает категорию, в которой нет запчастей

**Место:** `show_category()` в bot.py

```python
@dp.callback_query(F.data.startswith("cat_"))
async def show_category(callback: types.CallbackQuery):
    category = callback.data.split("_", 1)[1]
    try:
        subcats = db.get_subcategories(category)
    except Exception as e:
        print(f"❌ Ошибка получения подкатегорий: {e}")
        subcats = []
    
    if subcats:
        # показываем подкатегории
    else:
        await show_parts_by_category(callback, category)
```

**Обработка:**

```python
async def show_parts_by_category(callback: types.CallbackQuery, category: str):
    try:
        parts_list = db.get_parts_by_category(category)
    except Exception as e:
        print(f"❌ Ошибка получения запчастей: {e}")
        parts_list = []
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="back_catalog")
    
    if not parts_list:
        await callback.message.edit_text(
            f"⚠️ В категории **{category}** пока нет запчастей.",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
        return
    # ... дальше выводим запчасти
```

**Результат:** ✅ Пользователь видит: "⚠️ В категории **Дисплеи** пока нет запчастей."

### 1.2 Пустая подкатегория

**Сценарий:** Пользователь выбирает подкатегорию, в которой нет запчастей

**Место:** `show_by_subcategory()` в bot.py

```python
@dp.callback_query(F.data.startswith("subcat_"))
async def show_by_subcategory(callback: types.CallbackQuery):
    # ... парсим category и subcategory
    
    try:
        parts_list = db.get_parts_by_category(category, subcategory)
    except Exception as e:
        print(f"❌ Ошибка получения запчастей: {e}")
        parts_list = []
    
    if not parts_list:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data=f"cat_{category}")
        await callback.message.edit_text(
            f"⚠️ В подкатегории **{subcategory}** нет запчастей.",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
        return
```

**Результат:** ✅ Пользователь видит: "⚠️ В подкатегории **iPhone 15-14** нет запчастей."

---

## Категория 2: Ошибки приходования

### 2.1 Попытка приходовать из пустой категории

**Сценарий:** Пользователь стартует приходование, выбирает категорию без запчастей

**Место:** `show_stock_parts()` в bot.py

```python
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
```

**Результат:** ❌ Пользователь видит: "⚠️ В категории **Дисплеи** нет запчастей. ❌ Нельзя приходовать товар, которого нет в базе."

### 2.2 Неправильное количество (не число)

**Сценарий:** Пользователь вводит "abc" вместо количества

**Место:** `input_stock_quantity()` в bot.py

```python
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
    # ... продолжаем процесс
```

**Результат:** ❌ Пользователь видит: "❌ Пожалуйста, введите целое число!"

**Поведение:** Пользователь остается в состоянии `StockInState.quantity`, может повторить попытку

### 2.3 Неправильное количество (отрицательное или ноль)

**Сценарий:** Пользователь вводит "0" или "-5"

**Место:** `input_stock_quantity()` в bot.py

```python
try:
    quantity = int(message.text)
    if quantity <= 0:
        await message.answer("❌ Количество должно быть больше 0!")
        return
except ValueError:
    # ...
```

**Результат:** ❌ Пользователь видит: "❌ Количество должно быть больше 0!"

**Поведение:** Пользователь остается в состоянии, может повторить попытку

### 2.4 Ошибка сохранения в БД

**Сценарий:** БД недоступна или ошибка при INSERT

**Место:** `input_stock_notes()` в bot.py

```python
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
            # ... проверяем низкие остатки
            await message.answer(f"✅ Успешно приходовано!", ...)
        else:
            await message.answer("❌ Ошибка: запчасть не найдена!")
    except Exception as e:
        print(f"❌ Ошибка приходования: {e}")
        await message.answer("❌ Ошибка при сохранении данных!")
    
    await state.clear()
```

**Результат:** ❌ Пользователь видит: "❌ Ошибка при сохранении данных!"

**Логирование:** Console: `❌ Ошибка приходования: <details>`

---

## Категория 3: Ошибки доступа

### 3.1 Попытка приходовать без прав

**Сценарий:** Пользователь с ролью "user" нажимает "📥 Приходование"

**Место:** `start_stock_in()` в bot.py

```python
@dp.message(F.text == "📥 Приходование")
async def start_stock_in(message: types.Message, state: FSMContext):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager'):
        await message.answer("❌ У вас нет прав для приходования товара.\nТолько менеджер склада или администратор.")
        return
    # ... продолжаем приходование
```

**Результат:** ❌ Пользователь видит: "❌ У вас нет прав для приходования товара. Только менеджер склада или администратор."

### 3.2 Попытка открыть админ-панель

**Сценарий:** Пользователь нажимает "⚙️ Админ-панель" без прав

**Место:** `admin_panel()` в bot.py

```python
@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager', 'sales_manager'):
        await message.answer("❌ У вас нет доступа к админ-панели.")
        return
    
    await message.answer("⚙️ **Админ-панель**", reply_markup=get_admin_menu(message.from_user.id))
```

**Результат:** ❌ Пользователь видит: "❌ У вас нет доступа к админ-панели."

### 3.3 Попытка управлять ролями (не админ)

**Сценарий:** Менеджер склада пытается нажать "👥 Управление ролями"

**Место:** `manage_roles()` в bot.py

```python
@dp.message(F.text == "👥 Управление ролями")
async def manage_roles(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role != 'admin':
        await message.answer("❌ Только администратор может управлять ролями.")
        return
    
    await message.answer("👥 **Управление ролями**\n...")
```

**Результат:** ❌ Пользователь видит: "❌ Только администратор может управлять ролями."

### 3.4 Попытка просмотреть оповещения (не менеджер склада)

**Сценарий:** Пользователь пытается нажать "🔔 Оповещения о низких остатках"

**Место:** `show_low_stock_alerts()` в bot.py

```python
@dp.message(F.text == "🔔 Оповещения о низких остатках")
async def show_low_stock_alerts(message: types.Message):
    role = db.get_user_role(message.from_user.id)
    if role not in ('admin', 'warehouse_manager'):
        await message.answer("❌ У вас нет прав для просмотра оповещений.")
        return
    # ... показываем оповещения
```

**Результат:** ❌ Пользователь видит: "❌ У вас нет прав для просмотра оповещений."

---

## Категория 4: Ошибки отчетов

### 4.1 Пустые финансовые данные

**Сценарий:** На складе нет движения, нечего выводить в финансовом отчете

**Место:** `show_financial_analysis()` в bot.py

```python
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
    
    # ... выводим отчет
```

**Результат:** ⚠️ Пользователь видит: "⚠️ Нет финансовых данных."

**Логирование:** Console (если была ошибка БД): `❌ Ошибка получения финансовых данных: ...`

### 4.2 Пустые данные о поставщиках

**Сценарий:** Нет никаких поставок в истории

**Место:** `show_supplier_analysis()` в bot.py

```python
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
    
    # ... выводим отчет
```

**Результат:** ⚠️ Пользователь видит: "⚠️ Нет данных о поставщиках."

### 4.3 Пустой отчет о движении

**Сценарий:** Нет движения товара за последние 30 дней

**Место:** `show_stock_movement()` в bot.py

```python
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
    
    # ... выводим отчет
```

**Результат:** ⚠️ Пользователь видит: "⚠️ Нет данных о движении товара."

---

## Категория 5: Ошибки БД

### 5.1 Ошибка при получении подкатегорий

**Место:** `db.get_subcategories()` в db.py

```python
def get_subcategories(category):
    """Получить подкатегории из hardcoded словаря или БД"""
    try:
        # Попробуем получить из БД (если будет реализовано)
        with sqlite3.connect(DB_NAME) as conn:
            # ...
            return [dict(row) for row in res] if res else []
    except Exception as e:
        print(f"❌ Ошибка получения подкатегорий: {e}")
        # Fallback на hardcoded словарь
        return SUBCATEGORIES.get(category, [])
```

**Обработка в bot.py:**

```python
try:
    subcats = db.get_subcategories(category)
except Exception as e:
    print(f"❌ Ошибка получения подкатегорий: {e}")
    subcats = []  # Graceful degradation

if subcats:
    # показываем подкатегории
else:
    # показываем запчасти напрямую (fallback)
    await show_parts_by_category(callback, category)
```

**Результат:** ✅ Пользователь видит подкатегории из hardcoded словаря или запчасти напрямую

**Логирование:** Console (если была ошибка): `❌ Ошибка получения подкатегорий: ...`

---

## Категория 6: Edge Cases

### 6.1 Попытка добавить запчасть с неправильной ценой

**Сценарий:** Администратор вводит "абвгд" вместо цены

**Место:** `input_cost_price()` в bot.py

```python
@dp.message(PartState.cost_price)
async def input_cost_price(message: types.Message, state: FSMContext):
    try:
        cost_price = float(message.text)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    await state.update_data(cost_price=cost_price)
    # ... продолжаем
```

**Результат:** ❌ Пользователь видит: "❌ Пожалуйста, введите число!"

### 6.2 Запчасть не найдена в БД

**Сценарий:** Попытка получить информацию о запчасти, которая была удалена

**Место:** `input_stock_notes()` в bot.py

```python
part = db.get_part(part_id)
if part:
    _, name, _, _, _, _, _ = part
    # ... продолжаем
else:
    await message.answer("❌ Ошибка: запчасть не найдена!")
```

**Результат:** ❌ Пользователь видит: "❌ Ошибка: запчасть не найдена!"

### 6.3 Низкий остаток при приходовании

**Сценарий:** Пользователь приходует 1 шт товара, текущий остаток становится 2 (< порога 3)

**Место:** `input_stock_notes()` в bot.py

```python
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
```

**Результат:** 
- ✅ Пользователю: "✅ Успешно приходовано!"
- 🔔 Администратору: Отдельное уведомление о низком остатке

---

## Таблица обработки ошибок

| Ошибка | Код | Сообщение | Действие |
|--------|-----|-----------|----------|
| Пустая категория | 1.1 | ⚠️ В категории нет запчастей | Показать кнопку назад |
| Пустая подкатегория | 1.2 | ⚠️ В подкатегории нет запчастей | Показать кнопку назад |
| Приход из пустой категории | 2.1 | ❌ Нельзя приходовать товар, которого нет в базе | Отменить приход |
| Неправильное количество | 2.2 | ❌ Пожалуйста, введите целое число! | Повторить запрос |
| Количество ≤ 0 | 2.3 | ❌ Количество должно быть больше 0 | Повторить запрос |
| Ошибка БД | 2.4 | ❌ Ошибка при сохранении данных | Log + отмена |
| Нет прав | 3.1-3.4 | ❌ У вас нет прав | Отклонить действие |
| Пустой отчет | 4.1-4.3 | ⚠️ Нет данных | Показать сообщение |
| Ошибка БД (получение) | 5.1 | Fallback на hardcoded + log | Graceful degradation |
| Неправильная цена | 6.1 | ❌ Пожалуйста, введите число! | Повторить запрос |
| Запчасть не найдена | 6.2 | ❌ Ошибка: запчасть не найдена! | Отменить действие |
| Низкий остаток | 6.3 | 🔔 Уведомление администратору | Auto-create alert |

---

## Логирование

Все ошибки логируются в консоль в формате:
```
❌ [Контекст]: [Сообщение об ошибке]
```

Примеры:
- `❌ Ошибка получения подкатегорий: [Errno 2] No such file or directory`
- `❌ Ошибка получения запчастей: list index out of range`
- `❌ Ошибка приходования: IntegrityError: UNIQUE constraint failed`

Это помогает быстро отладить проблемы при разработке.

---

## Тестирование ошибок

Для тестирования всех ошибок используйте следующие сценарии:

1. **Пустая категория**: Выберите категорию без запчастей в БД
2. **Неправильное количество**: При приходовании введите "abc" или "0"
3. **Нет прав**: Попробуйте приходовать с обычным пользователем
4. **Низкий остаток**: Приходуйте товар так, чтобы остаток был < 3
5. **Пустой отчет**: Откройте отчеты на чистой БД
6. **Ошибка БД**: Удалите файл БД во время работы бота (он пересоздастся)

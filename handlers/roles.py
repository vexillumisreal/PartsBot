"""handlers/roles.py — интерактивное управление пользователями, ролями и заявками на опт."""
import html
import logging
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from handlers.common import get_admin_menu

logger = logging.getLogger(__name__)
router = Router()


class RoleState(StatesGroup):
    user_id = State()
    role = State()


class UserSearchState(StatesGroup):
    query = State()


ROLE_BUTTONS = {
    "user": "👤 Пользователь",
    "warehouse_manager": "🏭 Менеджер склада",
    "sales_manager": "💼 Менеджер продаж",
    "admin": "🔐 Администратор",
}


# ─────────────────── СПИСОК ПОЛЬЗОВАТЕЛЕЙ И ПАГИНАЦИЯ ───────────────────

async def render_users_list(target: types.Message | types.CallbackQuery, page: int = 0, search: str = "") -> None:
    users, total = await db.get_users_paged(page=page, page_size=8, search=search)
    total_pages = max(1, -(-total // 8))

    text = f"👥 <b>Управление пользователями</b>\n"
    if search:
        text += f"<i>Поиск по запросу: «{html.escape(search)}»</i>\n"
    text += f"<i>Страница {page + 1} из {total_pages} (всего пользователей: {total})</i>\n\n"

    if not users:
        text += "⚠️ Пользователи не найдены.\n"

    builder = InlineKeyboardBuilder()
    for u in users:
        name = u["full_name"] or u["username"] or f"ID {u['user_id']}"
        role_label = db.ROLES.get(u["role"], u["role"])
        status_badge = "📦 Опт" if u["status"] == "wholesale" else "🛍️ Розница"
        btn_text = f"{name[:20]} | {role_label[:10]} | {status_badge}"
        builder.button(text=btn_text, callback_data=f"user_view_{u['user_id']}")
    builder.adjust(1)

    # Пагинация
    nav_buttons = []
    prefix = f"users_p_{page}"
    if page > 0:
        nav_buttons.append((f"◀ Пред. ({page})", f"users_page_{page - 1}"))
    if (page + 1) * 8 < total:
        nav_buttons.append((f"След. ({page + 2}) ▶", f"users_page_{page + 1}"))

    pag_builder = InlineKeyboardBuilder()
    for btn_t, btn_c in nav_buttons:
        pag_builder.button(text=btn_t, callback_data=btn_c)
    if nav_buttons:
        pag_builder.adjust(len(nav_buttons))

    bottom_builder = InlineKeyboardBuilder()
    bottom_builder.button(text="🔍 Поиск по ID/Имени", callback_data="user_search_start")
    bottom_builder.button(text="💼 Заявки на опт", callback_data="admin_wholesale_reqs")
    bottom_builder.button(text="➕ Ввести ID вручную", callback_data="role_manual_id")
    bottom_builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
    bottom_builder.adjust(2, 2)

    builder.attach(pag_builder)
    builder.attach(bottom_builder)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.message(F.text == "👥 Управление пользователями")
@router.callback_query(F.data == "admin_users_list")
async def show_users_menu(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role != "admin":
        if isinstance(target, types.CallbackQuery):
            await target.answer("❌ Только администратор имеет доступ к списку пользователей", show_alert=True)
        else:
            await target.answer("❌ Только администратор имеет доступ к списку пользователей.")
        return
    await render_users_list(target, page=0)


@router.callback_query(F.data.startswith("users_page_"))
async def users_page_cb(callback: types.CallbackQuery) -> None:
    page = int(callback.data[11:])
    await render_users_list(callback, page=page)
    await callback.answer()


# ─────────────────── КАРТОЧКА ПОЛЬЗОВАТЕЛЯ ───────────────────

@router.callback_query(F.data.startswith("user_view_"))
async def view_user_card(callback: types.CallbackQuery) -> None:
    user_id = int(callback.data[10:])
    user = await db.get_user_info(user_id)
    if not user:
        await callback.answer("Пользователь не найден!", show_alert=True)
        return

    status_text = "📦 ОПТ" if user["status"] == "wholesale" else "🛍️ РОЗНИЦА"
    role_text = db.ROLES.get(user["role"], user["role"])

    text = (
        f"👤 <b>Карточка пользователя:</b>\n\n"
        f"🆔 Telegram ID: <code>{user['user_id']}</code>\n"
        f"📛 Имя: <b>{html.escape(user['full_name'] or 'Не указано')}</b>\n"
        f"🌐 Username: @{html.escape(user['username'] or 'нет')}\n"
        f"💰 Статус цен: <b>{status_text}</b>\n"
        f"🔐 Роль: <b>{html.escape(role_text)}</b>\n"
        f"📅 Зарегистрирован: <code>{user['created_at']}</code>"
    )

    builder = InlineKeyboardBuilder()
    toggle_label = "🛍️ Переключить на Розницу" if user["status"] == "wholesale" else "📦 Переключить на ОПТ"
    builder.button(text=toggle_label, callback_data=f"user_toggle_status_{user_id}")
    builder.button(text="👑 Изменить роль", callback_data=f"user_change_role_{user_id}")
    builder.button(text="🔙 Назад к списку", callback_data="admin_users_list")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("user_toggle_status_"))
async def toggle_status_cb(callback: types.CallbackQuery, bot: Bot) -> None:
    user_id = int(callback.data[19:])
    new_status = await db.toggle_user_status(user_id)
    status_label = "ОПТ 📦" if new_status == "wholesale" else "РОЗНИЦА 🛍️"
    await callback.answer(f"Статус изменён: {status_label}")

    # Уведомляем пользователя
    try:
        await bot.send_message(
            user_id,
            f"🔔 <b>Ваш ценовой статус изменён администратором:</b>\n"
            f"Новый статус: <b>{status_label}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await view_user_card(callback)


@router.callback_query(F.data.startswith("user_change_role_"))
async def change_role_picker(callback: types.CallbackQuery, state: FSMContext) -> None:
    user_id = int(callback.data[17:])
    current_role = await db.get_user_role(user_id)

    builder = InlineKeyboardBuilder()
    for role_key, role_label in ROLE_BUTTONS.items():
        prefix = "✅ " if role_key == current_role else ""
        builder.button(text=f"{prefix}{role_label}", callback_data=f"setrole_{role_key}")
    builder.button(text="❌ Отмена", callback_data=f"user_view_{user_id}")
    builder.adjust(1)

    await state.update_data(target_id=user_id)
    await state.set_state(RoleState.role)

    await callback.message.edit_text(
        f"👑 <b>Назначение роли:</b>\n\n"
        f"Пользователь ID: <code>{user_id}</code>\n"
        f"Текущая роль: <b>{db.ROLES.get(current_role, current_role)}</b>\n\n"
        f"Выберите новую роль из списка:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(RoleState.role, F.data.startswith("setrole_"))
async def set_role_cb(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    new_role = callback.data[8:]
    data = await state.get_data()
    target_id = data["target_id"]

    success = await db.set_user_role(target_id, new_role)
    await state.clear()

    if success:
        role_name = db.ROLES.get(new_role, new_role)
        await callback.answer(f"Роль обновлена: {role_name}")
        try:
            await bot.send_message(
                target_id,
                f"🔔 <b>Ваша роль в PartsBot обновлена!</b>\n"
                f"Новая роль: <b>{role_name}</b>\n\n"
                f"Нажмите /start для обновления меню интерфейса.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await callback.answer("Ошибка при изменении роли", show_alert=True)

    callback.data = f"user_view_{target_id}"
    await view_user_card(callback)


# ─────────────────── ВВОД ID ВРУЧНУЮ ───────────────────

@router.callback_query(F.data == "role_manual_id")
@router.callback_query(F.data == "user_search_start")
async def manual_id_prompt(callback: types.CallbackQuery, state: FSMContext) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_users_list")
    await callback.message.edit_text(
        "👥 <b>Ввод ID пользователя вручную</b>\n\n"
        "Введите числовой Telegram ID пользователя:\n"
        "<i>(Пользователь может узнать свой ID с помощью команды /myid)</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(RoleState.user_id)
    await callback.answer()


@router.message(RoleState.user_id)
async def input_role_user_id(message: types.Message, state: FSMContext) -> None:
    try:
        target_id = int(message.text.strip())
    except ValueError:
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отмена", callback_data="admin_users_list")
        await message.answer("❌ ID должен содержать только цифры. Попробуйте снова:", reply_markup=builder.as_markup())
        return

    exists = await db.user_exists(target_id)
    if not exists:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 К списку пользователей", callback_data="admin_users_list")
        await message.answer(
            f"⚠️ Пользователь с ID <code>{target_id}</code> ещё не запускал бота.\n"
            f"Ему необходимо сначала нажать /start.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        await state.clear()
        return

    await state.clear()
    user = await db.get_user_info(target_id)
    status_text = "📦 ОПТ" if user["status"] == "wholesale" else "🛍️ РОЗНИЦА"
    role_text = db.ROLES.get(user["role"], user["role"])

    text = (
        f"👤 <b>Пользователь найден:</b>\n\n"
        f"🆔 Telegram ID: <code>{user['user_id']}</code>\n"
        f"📛 Имя: <b>{html.escape(user['full_name'] or 'Не указано')}</b>\n"
        f"🌐 Username: @{html.escape(user['username'] or 'нет')}\n"
        f"💰 Статус: <b>{status_text}</b>\n"
        f"🔐 Роль: <b>{role_text}</b>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="👑 Изменить роль", callback_data=f"user_change_role_{target_id}")
    builder.button(text="🔄 Сменить статус цен", callback_data=f"user_toggle_status_{target_id}")
    builder.button(text="🔙 К списку", callback_data="admin_users_list")
    builder.adjust(1)

    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ─────────────────── МОДЕРАЦИЯ ЗАЯВОК НА ОПТ ───────────────────

@router.message(F.text == "💼 Заявки на опт")
@router.callback_query(F.data == "admin_wholesale_reqs")
async def show_wholesale_requests(target: types.Message | types.CallbackQuery) -> None:
    role = await db.get_user_role(target.from_user.id)
    if role not in ("admin", "sales_manager"):
        msg = "❌ Нет прав для просмотра заявок на опт."
        if isinstance(target, types.CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    reqs = await db.get_pending_wholesale_requests()

    if not reqs:
        text = "✅ <b>Нет новых заявок на оптовые цены.</b>\nВсе заявки обработаны!"
        builder = InlineKeyboardBuilder()
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        builder.button(text="👥 К пользователям", callback_data="admin_users_list")
        builder.adjust(1)
    else:
        text = f"💼 <b>Необработанные заявки на ОПТ ({len(reqs)} шт.):</b>\n\n"
        builder = InlineKeyboardBuilder()
        for r in reqs:
            text += (
                f"• <b>Заявка #{r['id']}</b>\n"
                f"   👤 Клиент: <b>{html.escape(r['user_name'])}</b> (ID: <code>{r['user_id']}</code>)\n"
                f"   📝 Комментарий: <i>{html.escape(r['comment'] or 'Нет')}</i>\n"
                f"   📅 Дата: <code>{r['created_at'][:16]}</code>\n\n"
            )
            builder.button(text=f"✅ #{r['id']} Одобрить", callback_data=f"ws_req_{r['id']}_approve")
            builder.button(text=f"❌ #{r['id']} Отклонить", callback_data=f"ws_req_{r['id']}_reject")
        builder.button(text="⚙️ В админ-панель", callback_data="admin_dashboard")
        builder.adjust(2)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("ws_req_"))
async def resolve_wholesale_req_cb(callback: types.CallbackQuery, bot: Bot) -> None:
    role = await db.get_user_role(callback.from_user.id)
    if role not in ("admin", "sales_manager"):
        await callback.answer("❌ Недостаточно прав", show_alert=True)
        return

    parts = callback.data.split("_")
    # ws_req_{id}_{approve/reject}
    req_id = int(parts[2])
    action = parts[3]
    approved = (action == "approve")

    ok, user_id = await db.resolve_wholesale_request(req_id, approved)

    if ok:
        res_text = "одобрена" if approved else "отклонена"
        await callback.answer(f"Заявка #{req_id} {res_text}")

        # Уведомляем клиента
        try:
            if approved:
                await bot.send_message(
                    user_id,
                    "🎉 <b>Поздравляем! Ваша заявка на оптовые цены ОДОБРЕНА!</b>\n\n"
                    "Теперь в каталоге для вас действуют специальные оптовые цены.\n"
                    "Нажмите /start для обновления.",
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    user_id,
                    "ℹ️ <b>Ваша заявка на оптовые цены была отклонена.</b>\n"
                    "Для уточнения условий сотрудничества свяжитесь с отделом продаж.",
                    parse_mode="HTML",
                )
        except Exception:
            pass

        # Перезагружаем список заявок
        await show_wholesale_requests(callback)
    else:
        await callback.answer("Заявка не найдена или уже обработана", show_alert=True)

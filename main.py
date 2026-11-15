import asyncio
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
CURRENCY = os.getenv("CURRENCY", "")
# Необязательно: список Telegram ID через запятую, которым разрешён доступ
ALLOWED_USERS = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x}

WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))
WEB_SERVER_HOST = "0.0.0.0"

bot = Bot(token=BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dp = Dispatcher()


def is_allowed(event) -> bool:
    return not ALLOWED_USERS or (event.from_user is not None and event.from_user.id in ALLOWED_USERS)


dp.message.filter(is_allowed)
dp.callback_query.filter(is_allowed)

# ───────────────────────── Константы и клавиатуры ─────────────────────────

BTN_CLIENTS = "📋 Клиенты"
BTN_ADD_CLIENT = "➕ Добавить клиента"
BTN_ORDERS = "📦 Заказы"
BTN_ADD_ORDER = "➕ Добавить заказ"
BTN_COMPANIES = "🏢 Компании"

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_CLIENTS), KeyboardButton(text=BTN_ADD_CLIENT)],
        [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_ADD_ORDER)],
        [KeyboardButton(text=BTN_COMPANIES)],
    ],
    resize_keyboard=True,
)

PRODUCT_TYPES = ["game system", "vfx", "material"]
PT_LABEL = {"game system": "🎮 Game system", "vfx": "✨ VFX", "material": "🧱 Material"}

# сущность -> (таблица, название, {поле: подпись})
ENTITIES = {
    "client": ("clients", "Клиент", {
        "name": "Название", "personal_email": "Личная почта", "company_email": "Почта в компании"}),
    "company": ("companies", "Компания", {
        "name": "Название", "email": "Почта", "website": "Сайт"}),
    "rep": ("representatives", "Представитель", {
        "first_name": "Имя", "last_name": "Фамилия"}),
    "order": ("orders", "Заказ", {
        "deadline": "Дедлайн", "amount": "Сумма", "product_type": "Вид товара"}),
}

# ───────────────────────── Валидаторы ─────────────────────────

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def v_text(s: str) -> str:
    s = (s or "").strip()
    if not s:
        raise ValueError("Значение не может быть пустым")
    return s


def v_email(s: str) -> str:
    s = v_text(s)
    if not EMAIL_RE.match(s):
        raise ValueError("Некорректная почта")
    return s


def v_date(s: str) -> str:
    s = v_text(s)
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ")


def v_amount(s: str) -> float:
    s = v_text(s).replace(" ", "").replace(",", ".")
    try:
        value = Decimal(s)
    except InvalidOperation:
        raise ValueError("Сумма должна быть числом")
    if value < 0:
        raise ValueError("Сумма не может быть отрицательной")
    return float(round(value, 2))


VALIDATORS = {
    "email": v_email, "personal_email": v_email, "company_email": v_email,
    "deadline": v_date, "amount": v_amount,
}

# ───────────────────────── Вспомогательные функции ─────────────────────────


async def db(query):
    """Выполняет синхронный запрос supabase в потоке, чтобы не блокировать бота."""
    return (await asyncio.to_thread(query.execute)).data


def g(d, key) -> str:
    return (d or {}).get(key) or "—"


def fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y")


def fmt_money(x) -> str:
    return f"{Decimal(str(x)):,.2f}".replace(",", " ") + (f" {CURRENCY}" if CURRENCY else "")


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def card_kb(entity: str, item_id, extra=()) -> InlineKeyboardMarkup:
    rows = [[btn("✏️ Изменить", f"ed:{entity}:{item_id}"), btn("🗑 Удалить", f"dl:{entity}:{item_id}")]]
    rows += [[b] for b in extra]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def types_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(PT_LABEL[t], f"{prefix}:{i}")] for i, t in enumerate(PRODUCT_TYPES)
    ])


def client_card(c: dict) -> str:
    comp, rep = c.get("companies"), c.get("representatives")
    rep_name = f"{g(rep, 'first_name')} {g(rep, 'last_name')}" if rep else "—"
    return (
        f"👤 Клиент: {c['name']}\n"
        f"📧 Личная почта: {g(c, 'personal_email')}\n"
        f"💼 Почта в компании: {g(c, 'company_email')}\n"
        f"🏢 Компания: {g(comp, 'name')}\n"
        f"     ✉️ {g(comp, 'email')}   🌐 {g(comp, 'website')}\n"
        f"🤝 Представитель: {rep_name}"
    )


def order_card(o: dict) -> str:
    cl = o.get("clients") or {}
    overdue = " ⚠️ просрочен" if date.fromisoformat(o["deadline"]) < date.today() else ""
    return (
        f"📦 Заказ №{o['id']}\n"
        f"👤 Клиент: {g(cl, 'name')}\n"
        f"🏢 Компания: {g(cl.get('companies'), 'name')}\n"
        f"🏷 Вид товара: {PT_LABEL.get(o['product_type'], o['product_type'])}\n"
        f"💰 Сумма: {fmt_money(o['amount'])}\n"
        f"⏰ Дедлайн: {fmt_date(o['deadline'])}{overdue}"
    )


def company_card(c: dict) -> str:
    reps = ", ".join(f"{r['first_name']} {r['last_name']}" for r in c.get("representatives") or []) or "—"
    clients = ", ".join(x["name"] for x in c.get("clients") or []) or "—"
    return (
        f"🏢 Компания: {c['name']}\n"
        f"✉️ Почта: {g(c, 'email')}\n"
        f"🌐 Сайт: {g(c, 'website')}\n"
        f"🤝 Представители: {reps}\n"
        f"👥 Клиенты: {clients}"
    )


# ───────────────────────── Состояния FSM ─────────────────────────


class AddClient(StatesGroup):
    step = State()


class AddOrder(StatesGroup):
    deadline = State()
    amount = State()
    product_type = State()


class EditField(StatesGroup):
    value = State()


# Шаги добавления клиента: (ключ, вопрос, валидатор)
CLIENT_STEPS = [
    ("name", "Введите название клиента:", v_text),
    ("personal_email", "Введите личную почту клиента:", v_email),
    ("company_email", "Введите почту клиента в компании:", v_email),
    ("company_name", "Введите название компании:", v_text),
    ("c_email", "Введите общую почту компании:", v_email),
    ("c_website", "Введите сайт компании:", v_text),
    ("rep_first", "Введите имя представителя компании:", v_text),
    ("rep_last", "Введите фамилию представителя:", v_text),
]
STEP_INDEX = {key: i for i, (key, _, _) in enumerate(CLIENT_STEPS)}

# ───────────────────────── Старт и отмена ─────────────────────────


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Добро пожаловать! Используйте кнопки меню ниже.", reply_markup=main_menu)


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu)


# ───────────────────────── Списки ─────────────────────────


@dp.message(F.text == BTN_CLIENTS)
@dp.message(Command("list"))
async def list_clients(message: Message, state: FSMContext):
    await state.clear()
    rows = await db(
        supabase.table("clients")
        .select("*, companies(name,email,website), representatives(first_name,last_name)")
        .order("id")
    )
    if not rows:
        await message.answer("Клиентов пока нет", reply_markup=main_menu)
        return
    for c in rows:
        extra = [btn("📦 Новый заказ", f"oc:{c['id']}")]
        if c.get("representative_id"):
            extra.append(btn("✏️ Представитель", f"ed:rep:{c['representative_id']}"))
        if c.get("company_id"):
            extra.append(btn("✏️ Компания", f"ed:company:{c['company_id']}"))
        await message.answer(client_card(c), reply_markup=card_kb("client", c["id"], extra))


@dp.message(F.text == BTN_ORDERS)
async def list_orders(message: Message, state: FSMContext):
    await state.clear()
    rows = await db(
        supabase.table("orders").select("*, clients(name, companies(name))").order("deadline")
    )
    if not rows:
        await message.answer("Заказов пока нет", reply_markup=main_menu)
        return
    for o in rows:
        await message.answer(order_card(o), reply_markup=card_kb("order", o["id"]))


@dp.message(F.text == BTN_COMPANIES)
async def list_companies(message: Message, state: FSMContext):
    await state.clear()
    rows = await db(
        supabase.table("companies")
        .select("*, representatives(first_name,last_name), clients(name)")
        .order("id")
    )
    if not rows:
        await message.answer("Компаний пока нет", reply_markup=main_menu)
        return
    for c in rows:
        await message.answer(company_card(c), reply_markup=card_kb("company", c["id"]))


# ───────────────────────── Добавление клиента (мастер) ─────────────────────────


@dp.message(F.text == BTN_ADD_CLIENT)
@dp.message(Command("add"))
async def start_add_client(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddClient.step)
    await state.update_data(idx=0)
    await message.answer(CLIENT_STEPS[0][1] + "\n(отмена: /cancel)")


@dp.message(AddClient.step)
async def add_client_step(message: Message, state: FSMContext):
    data = await state.get_data()
    idx = data["idx"]
    key, _, validator = CLIENT_STEPS[idx]
    try:
        value = validator(message.text)
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Попробуйте ещё раз:")
        return
    await state.update_data(**{key: value})
    idx += 1

    if key == "company_name":
        found = await db(supabase.table("companies").select("id").ilike("name", value).limit(1))
        if found:
            await state.update_data(company_id=found[0]["id"])
            idx = STEP_INDEX["rep_first"]  # компания уже есть — почту и сайт не спрашиваем
            await message.answer("Компания уже есть в базе, использую её.")

    if idx < len(CLIENT_STEPS):
        await state.update_data(idx=idx)
        await message.answer(CLIENT_STEPS[idx][1])
        return

    data = await state.get_data()
    company_id = data.get("company_id")
    if not company_id:
        company_id = (await db(supabase.table("companies").insert({
            "name": data["company_name"], "email": data["c_email"], "website": data["c_website"],
        })))[0]["id"]
    rep_id = (await db(supabase.table("representatives").insert({
        "company_id": company_id, "first_name": data["rep_first"], "last_name": data["rep_last"],
    })))[0]["id"]
    await db(supabase.table("clients").insert({
        "name": data["name"], "personal_email": data["personal_email"],
        "company_email": data["company_email"], "company_id": company_id, "representative_id": rep_id,
    }))
    await state.clear()
    await message.answer("✅ Клиент сохранён", reply_markup=main_menu)


# ───────────────────────── Добавление заказа ─────────────────────────


@dp.message(F.text == BTN_ADD_ORDER)
async def start_add_order(message: Message, state: FSMContext):
    await state.clear()
    clients = await db(supabase.table("clients").select("id,name").order("name"))
    if not clients:
        await message.answer("Сначала добавьте клиента", reply_markup=main_menu)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn(c["name"], f"oc:{c['id']}")] for c in clients])
    await message.answer("Выберите клиента для заказа:", reply_markup=kb)


@dp.callback_query(F.data.startswith("oc:"))
async def order_pick_client(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(client_id=int(callback.data.split(":")[1]))
    await state.set_state(AddOrder.deadline)
    await callback.message.answer("Введите дедлайн (ДД.ММ.ГГГГ):\n(отмена: /cancel)")
    await callback.answer()


@dp.message(AddOrder.deadline)
async def order_deadline(message: Message, state: FSMContext):
    try:
        value = v_date(message.text)
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Попробуйте ещё раз:")
        return
    await state.update_data(deadline=value)
    await state.set_state(AddOrder.amount)
    await message.answer("Введите сумму заказа:")


@dp.message(AddOrder.amount)
async def order_amount(message: Message, state: FSMContext):
    try:
        value = v_amount(message.text)
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Попробуйте ещё раз:")
        return
    await state.update_data(amount=value)
    await state.set_state(AddOrder.product_type)
    await message.answer("Выберите вид товара:", reply_markup=types_kb("ot"))


@dp.callback_query(AddOrder.product_type, F.data.startswith("ot:"))
async def order_type(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await db(supabase.table("orders").insert({
        "client_id": data["client_id"], "deadline": data["deadline"],
        "amount": data["amount"], "product_type": PRODUCT_TYPES[int(callback.data.split(":")[1])],
    }))
    await state.clear()
    await callback.message.edit_text("✅ Заказ сохранён")
    await callback.message.answer("Готово.", reply_markup=main_menu)
    await callback.answer()


# ───────────────────────── Редактирование ─────────────────────────


@dp.callback_query(F.data.startswith("ed:"))
async def edit_choose_field(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _, entity, item_id = callback.data.split(":")
    _, title, fields = ENTITIES[entity]
    rows = [[btn(label, f"ef:{entity}:{item_id}:{field}")] for field, label in fields.items()]
    await callback.message.answer(f"{title} №{item_id}: что изменить?",
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("ef:"))
async def edit_field(callback: CallbackQuery, state: FSMContext):
    _, entity, item_id, field = callback.data.split(":")
    if field == "product_type":
        await callback.message.answer("Выберите вид товара:", reply_markup=types_kb(f"ep:{item_id}"))
    else:
        await state.set_state(EditField.value)
        await state.update_data(entity=entity, item_id=int(item_id), field=field)
        label = ENTITIES[entity][2][field]
        hint = " (ДД.ММ.ГГГГ)" if field == "deadline" else ""
        await callback.message.answer(f"Введите новое значение для «{label}»{hint}:\n(отмена: /cancel)")
    await callback.answer()


@dp.callback_query(F.data.startswith("ep:"))
async def edit_product_type(callback: CallbackQuery):
    _, item_id, idx = callback.data.split(":")
    await db(supabase.table("orders").update({"product_type": PRODUCT_TYPES[int(idx)]}).eq("id", int(item_id)))
    await callback.message.edit_text("✅ Вид товара обновлён")
    await callback.answer()


@dp.message(EditField.value)
async def edit_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["field"]
    try:
        value = VALIDATORS.get(field, v_text)(message.text)
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Попробуйте ещё раз:")
        return
    table = ENTITIES[data["entity"]][0]
    await db(supabase.table(table).update({field: value}).eq("id", data["item_id"]))
    await state.clear()
    await message.answer("✅ Данные обновлены", reply_markup=main_menu)


# ───────────────────────── Удаление ─────────────────────────


@dp.callback_query(F.data.startswith("dl:"))
async def delete_ask(callback: CallbackQuery):
    _, entity, item_id = callback.data.split(":")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        btn("✅ Да, удалить", f"dy:{entity}:{item_id}"), btn("↩️ Отмена", "dn"),
    ]])
    await callback.message.answer(f"Удалить {ENTITIES[entity][1].lower()} №{item_id}?", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("dy:"))
async def delete_confirm(callback: CallbackQuery):
    _, entity, item_id = callback.data.split(":")
    await db(supabase.table(ENTITIES[entity][0]).delete().eq("id", int(item_id)))
    await callback.message.edit_text(f"🗑 {ENTITIES[entity][1]} №{item_id} удалён(а)")
    await callback.answer("Удалено")


@dp.callback_query(F.data == "dn")
async def delete_cancel(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")


# Любая кнопка, не попавшая в обработчики выше (например, от старого сообщения)
@dp.callback_query()
async def unknown_callback(callback: CallbackQuery):
    logging.warning("Необработанный callback: %s", callback.data)
    await callback.answer("Кнопка устарела. Откройте список заново.", show_alert=True)


# ───────────────────────── Ошибки и запуск ─────────────────────────


@dp.error()
async def on_error(event: ErrorEvent):
    logging.exception("Ошибка в обработчике", exc_info=event.exception)
    update = event.update
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.answer(f"❌ Ошибка: {event.exception}")
    return True


async def on_startup(bot: Bot) -> None:
    if WEBHOOK_HOST:
        await bot.set_webhook(
            WEBHOOK_URL,
            allowed_updates=dp.resolve_used_update_types(),  # message + callback_query
            drop_pending_updates=True,
        )
        logging.info(f"Webhook установлен на {WEBHOOK_URL}")


def main():
    dp.startup.register(on_startup)
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host=WEB_SERVER_HOST, port=PORT)


if __name__ == "__main__":
    main()

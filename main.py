import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Message,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")

WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.getenv("PORT", 8080))
WEB_SERVER_HOST = "0.0.0.0"

bot = Bot(token=BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dp = Dispatcher()

# Создаем постоянную клавиатуру (UI кнопки под чатом)
main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Список клиентов"), KeyboardButton(text="➕ Добавить клиента")]
    ],
    resize_keyboard=True  # Делает кнопки аккуратными и компактными по размеру экрана
)


# Состояния FSM для добавления
class AddClient(StatesGroup):
    waiting_for_name = State()
    waiting_for_mail = State()


# Состояния FSM для редактирования
class EditClient(StatesGroup):
    waiting_for_name = State()
    waiting_for_mail = State()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Добро пожаловать! Используйте кнопки меню ниже или команды.",
        reply_markup=main_menu_keyboard
    )


# Обработка нажатия на UI-кнопку "📋 Список клиентов"
@dp.message(F.text == "📋 Список клиентов")
async def btn_list_clients(message: Message):
    try:
        response = supabase.table("clients").select("*").execute()
        clients = response.data
        if not clients:
            await message.answer("База пустая", reply_markup=main_menu_keyboard)
            return

        for client in clients:
            name = client.get("name") or "Не указано"
            mail = client.get("mail") or "Не указана"

            text = f"📋 Имя: {name}\n📧 Почта: {mail}"

            button = InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{client.get('id')}")
            edit_button = InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{client.get('id')}")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[button, edit_button]])

            await message.answer(text, reply_markup=keyboard)
    except Exception as e:
        await message.answer(f"Ошибка чтения: {e}")


# Также оставляем поддержку команды /list
@dp.message(Command("list"))
async def cmd_list(message: Message):
    await btn_list_clients(message)


async def on_startup(bot: Bot) -> None:
    if WEBHOOK_HOST:
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook установлен на {WEBHOOK_URL}")


# Обработка нажатия на UI-кнопку "➕ Добавить клиента"
@dp.message(F.text == "➕ Добавить клиента")
async def btn_add_client(message: Message, state: FSMContext):
    await message.answer("Введите имя клиента:")
    await state.set_state(AddClient.waiting_for_name)


# Также оставляем поддержку команды /add
@dp.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    await btn_add_client(message, state)


@dp.message(AddClient.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите mail клиента:")
    await state.set_state(AddClient.waiting_for_mail)


@dp.message(AddClient.waiting_for_mail)
async def process_mail(message: Message, state: FSMContext):
    await state.update_data(mail=message.text)
    data = await state.get_data()
    try:
        supabase.table("clients").insert({
            "name": data.get("name"),
            "mail": data.get("mail")
        }).execute()
        await message.answer("✅ Пользователь сохранен", reply_markup=main_menu_keyboard)
    except Exception as e:
        await message.answer(f"❌ Ошибка записи: {e}", reply_markup=main_menu_keyboard)
    await state.clear()


# Удаление клиентов (поставим его сразу после команд, чтобы бот точно его перехватывал)
@dp.callback_query(F.data.startswith("del_"))
async def delete_client(callback: CallbackQuery):
    try:
        # Достаем ID и преобразуем в целое число
        client_id = int(callback.data.split("_")[1])
        print(f"DEBUG: Пытаюсь удалить клиента ID: {client_id}")

        # Выполняем запрос на удаление в Supabase
        response = supabase.table("clients").delete().eq("id", client_id).execute()
        print(f"DEBUG: Ответ Supabase: {response}")

        # Меняем текст карточки в чате
        await callback.message.edit_text("❌ Запись удалена из базы")
        await callback.answer("Клиент успешно удален!", show_alert=True)
    except Exception as e:
        print(f"DEBUG ОШИБКА УДАЛЕНИЯ: {e}")
        await callback.answer(f"Ошибка: {e}", show_alert=True)


# Редактирование записей
@dp.callback_query(F.data.startswith("edit_"))
async def edit_client_start(callback: CallbackQuery, state: FSMContext):
    client_id = callback.data.split("_")[1]
    await state.update_data(client_id=client_id)
    await callback.message.answer("Введите новое имя клиента:")
    await state.set_state(EditClient.waiting_for_name)
    await callback.answer()


@dp.message(EditClient.waiting_for_name)
async def process_edit_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите новую почту клиента:")
    await state.set_state(EditClient.waiting_for_mail)


@dp.message(EditClient.waiting_for_mail)
async def process_edit_mail(message: Message, state: FSMContext):
    await state.update_data(mail=message.text)

    data = await state.get_data()
    client_id = data.get("client_id")
    new_name = data.get("name")
    new_mail = data.get("mail")

    try:
        supabase.table("clients").update({
            "name": new_name,
            "mail": new_mail
        }).eq("id", client_id).execute()

        await message.answer(f"✅ Данные клиента успешно обновлены!\n\nИмя: {new_name}\nПочта: {new_mail}",
                             reply_markup=main_menu_keyboard)
    except Exception as e:
        await message.answer(f"❌ Ошибка обновления: {e}", reply_markup=main_menu_keyboard)

    await state.clear()


def main():
    dp.startup.register(on_startup)
    app = web.Application()

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host=WEB_SERVER_HOST, port=PORT)


if __name__ == "__main__":
    main()
import asyncio
import logging
import os
from http.client import responses

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from supabase import Client, create_client
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram import F



load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")


WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Порт, который отдает Render
PORT = int(os.getenv("PORT", 8080))
WEB_SERVER_HOST = "0.0.0.0"

bot = Bot(token=BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dp = Dispatcher()

#Добавление записей
class AddClient(StatesGroup):
    waiting_for_name=State()
    waiting_for_mail=State()
#Добавление записей

# Редактирование записей
class EditClient(StatesGroup):
    waiting_for_name = State()
    waiting_for_mail = State()


# Редактирование записей

@dp.message(CommandStart())
async def cmd_start(message: Message):
  await message.answer(
      "Добро пожаловать!\nНажмите /list для просмотра клиентов."
  )

#Вывод клиентов
@dp.message(Command("list"))
async def cmd_list(message: Message):
  try:
    response = supabase.table("clients").select("*").execute()
    clients = response.data
    if not clients:
      await message.answer("База пустая")
      return

    text = "📋 Клиенты из базы:\n"
    for client in clients:
      text =f"Имя: {client.get('name')}\nПочта: {client.get('email')}"
      button = InlineKeyboardButton(text="Удалить", callback_data=f"del_{client.get('id')}")
      edit_button = InlineKeyboardButton(text="Редактировать", callback_data=f"edit_{client.get('id')}")
      keyboard = InlineKeyboardMarkup(inline_keyboard=[[button,edit_button]])

      await message.answer(text, reply_markup=keyboard)
  except Exception as e:
    await message.answer(f"Ошибка чтения: {e}")
#Вывод клиентов

async def on_startup(bot: Bot) -> None:
  if WEBHOOK_HOST:
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен на {WEBHOOK_URL}")


#Добавление клиентов
@dp.message(Command("add"))
async def cmd_add(message: Message, state:FSMContext):
    await message.answer("Введите имя клиента:")
    await state.set_state(AddClient.waiting_for_name)

@dp.message(AddClient.waiting_for_name)
async def process_name(message: Message, state:FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите mail клиента:")
    await state.set_state(AddClient.waiting_for_mail)

@dp.message(AddClient.waiting_for_mail)
async def process_mail(message: Message, state:FSMContext):
    await state.update_data(mail=message.text)
    data = await state.get_data()
    try:
      response= supabase.table("clients").insert({"name": data.get("name"), "mail": data.get("mail")}).execute()
      await message.answer("Пользователь сохранен")
    except Exception as e:
        await message.answer("Ошибка записи")
    await state.clear()
#Добавление клиентов

#Удаление клиентов
@dp.callback_query(F.data.startswith("del_"))
async def delete_client(callback:CallbackQuery):
    client_id=callback.data.split("_")[1]
    supabase.table("clients").delete().eq("id",client_id).execute()
    await callback.answer("Клиент удален",show_alert=True)
    await callback.message.delete()
#Удаление клиентов

#Редактирование записей
@dp.callback_query(F.data.startwith("edit_"))
async def edit_client_start(callback:CallbackQuery, state:FSMContext):
    client_id=callback.data.split("_")[1]
    await state.update_data(edititing_client_id=client_id)
    await callback.message.answer("Введите новое имя клиента")
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
        # Обновляем строку в базе данных
        supabase.table("clients").update({
            "name": new_name,
            "mail": new_mail
        }).eq("id", client_id).execute()

        await message.answer(f"✅ Данные клиента успешно обновлены!\n\nИмя: {new_name}\nПочта: {new_mail}")
    except Exception as e:
        await message.answer(f"Ошибка обновления: {e}")

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
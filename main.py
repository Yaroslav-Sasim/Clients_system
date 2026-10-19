import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
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

# Порт, который отдает Render
PORT = int(os.getenv("PORT", 8080))
WEB_SERVER_HOST = "0.0.0.0"

bot = Bot(token=BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
  await message.answer(
      "Добро пожаловать!\nНажмите /list для просмотра клиентов."
  )


@dp.message(Command("list"))
async def cmd_list(message: Message):
  try:
    response = supabase.table("clients").select("*").execute()
    clients = response.data
    if not clients:
      await message.answer("База пустая")
      return

    text = "📋 **Клиенты из базы:**\n"
    for client in clients:
      text += f"\nИмя: {client.get('name')}"
      text += f"\nПочта: {client.get('mail')}"
      text += "\n-------------------\n"
    await message.answer(text, parse_mode="Markdown")
  except Exception as e:
    await message.answer(f"Ошибка чтения: {e}")


async def on_startup(bot: Bot) -> None:
  if WEBHOOK_HOST:
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен на {WEBHOOK_URL}")


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
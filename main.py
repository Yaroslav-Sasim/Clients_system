import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")

bot = Bot(token=BOT_TOKEN)
supabase: Client=create_client(SUPABASE_URL,SUPABASE_KEY)
router=Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("Добро пожаловать!","Нажмите Список для просмотра клиентов")

@router.message(Command("list"))
async def cmd_list(message: Message):
    try:
        response=supabase.table("clients").select("*").execute()
        clients=response.data
        if not clients:
            await message.answer("База пустая")
            return
        text ="КЛиенты из базы"
        for client in clients:
            text += f"\n{client.get('Имя')}\n"

            text += f"\n{client.get('mail')}\n"

            text += f"-------------------\n"
        await message.answer(text,parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"Ошибка чтения {e}")

async def main():
    dp=Dispatcher()
    dp.include_router(router)
    print("Запущен бот")
    await dp.start_polling(bot)

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

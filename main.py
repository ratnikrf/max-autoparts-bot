import asyncio
import logging
import os
import json
import hmac
from datetime import datetime
from aiohttp import web
import aiohttp
from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, BotStarted

load_dotenv()

# Поддержка MAX_BOT_TOKEN (Bothost) и BOT_TOKEN (старый)
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN") or os.getenv("BOT_TOKEN")
MANAGER_CHAT_ID = int(os.getenv("MANAGER_CHAT_ID", 223956964))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise ValueError("Не задан ни MAX_BOT_TOKEN, ни BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher(bot)

# ========== ВСЕ ВАШИ ФУНКЦИИ (load_users, save_users, has_attachments, get_photo_url, get_photo_token,
# send_broadcast, is_manager, @dp.bot_started, @dp.message_created, finalize_order) ==========
# Они остаются **БЕЗ ИЗМЕНЕНИЙ**. Скопируйте их сюда из вашего текущего main.py.
# (Я не буду дублировать их здесь для краткости, но вы вставите свои.)

# ========== WEBHOOK ОБРАБОТЧИК ==========
async def handle_webhook(request):
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(got, WEBHOOK_SECRET):
            return web.Response(status=403, text="Forbidden")
    try:
        update_data = await request.json()
        await dp.feed_update(bot, update_data)
    except Exception as e:
        logger.exception("Ошибка обработки вебхука")
    return web.Response(status=200, text="OK")

async def health(request):
    return web.json_response({"status": "ok"})

async def main():
    await bot.delete_webhook()
    logger.info("Старый вебхук удалён")

    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/health", health)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("Бот запущен на порту %s", port)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

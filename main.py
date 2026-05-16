import asyncio
import logging
import os
import json
import hmac
from datetime import datetime
from aiohttp import web
from dotenv import load_dotenv
from maxapi import Bot

load_dotenv()

BOT_TOKEN = os.getenv("MAX_BOT_TOKEN") or os.getenv("BOT_TOKEN")
MANAGER_CHAT_ID = int(os.getenv("MANAGER_CHAT_ID", 223956964))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise ValueError("Не задан токен бота")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)

# ========== ХРАНИЛИЩА ==========
USERS_FILE = "users.json"
users = {}
user_states = {}
user_data = {}
broadcast_data = {}

STATE_START = 0
STATE_ASK_ARTICLE = 1
STATE_ASK_CAR_INFO = 2
STATE_ASK_PHOTO = 3
STATE_ASK_PHONE = 4
STATE_AWAITING_CAR_DETAILS = 5

WELCOME_TEXT = """Здравствуйте! Я помогу найти автозапчасти под заказ.

У Вас есть номер детали (артикул) или номер кузова (VIN)?

👉 Напишите ДА или НЕТ"""

# ========== РАБОТА С ФАЙЛАМИ ==========
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

users = load_users()

# ========== РАССЫЛКА ==========
async def send_broadcast(chat_id, text, photo_token=None):
    results = {"total": len(users), "success": 0, "failed": 0, "failed_users": []}
    logger.info(f"📢 НАЧАЛО РАССЫЛКИ, пользователей: {results['total']}")

    for user_id, user_info in users.items():
        try:
            user_chat_id = user_info.get('chat_id')
            if not user_chat_id:
                continue
            if photo_token:
                attachments = [{"type": "image", "payload": {"token": photo_token}}]
                await bot.send_message(
                    chat_id=int(user_chat_id),
                    text=f"📢 **РАССЫЛКА**\n\n{text}\n\n---\nЭто сообщение от менеджера бота.",
                    attachments=attachments
                )
            else:
                await bot.send_message(
                    chat_id=int(user_chat_id),
                    text=f"📢 **РАССЫЛКА**\n\n{text}\n\n---\nЭто сообщение от менеджера бота."
                )
            results["success"] += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            results["failed"] += 1
            results["failed_users"].append({"user_id": user_id, "name": user_info.get('user_name', 'Unknown'), "error": str(e)})

    report = f"📊 **ОТЧЁТ О РАССЫЛКЕ**\n\n👥 Всего: {results['total']}\n✅ Успешно: {results['success']}\n❌ Ошибок: {results['failed']}"
    await bot.send_message(chat_id=chat_id, text=report)
    return results

def is_manager(chat_id, user_id):
    return chat_id == MANAGER_CHAT_ID

# ========== ОСНОВНАЯ ЛОГИКА ==========
async def on_bot_started(chat_id, user_id, user_name):
    users[str(user_id)] = {
        'user_id': user_id,
        'chat_id': chat_id,
        'user_name': user_name,
        'first_seen': datetime.now().isoformat(),
        'last_activity': datetime.now().isoformat()
    }
    save_users(users)

    user_states[chat_id] = STATE_START
    user_data[chat_id] = {}
    user_data[chat_id]['user_name'] = user_name

    await bot.send_message(chat_id=chat_id, text=WELCOME_TEXT)
    logger.info(f"👋 Новый пользователь: {user_id} ({user_name})")

async def handle_message(chat_id, user_id, user_text, has_photo, photo_url, photo_token):
    # Обновляем активность
    if str(user_id) in users:
        users[str(user_id)]['last_activity'] = datetime.now().isoformat()
        save_users(users)

    # Команды менеджера
    if is_manager(chat_id, user_id):
        if user_text == '/broadcast':
            broadcast_data[chat_id] = {'step': 'awaiting_text'}
            await bot.send_message(chat_id=chat_id, text="📢 Режим рассылки. Отправьте текст или фото.")
            return
        if user_text == '/cancel':
            broadcast_data.pop(chat_id, None)
            await bot.send_message(chat_id=chat_id, text="❌ Рассылка отменена.")
            return
        if user_text == '/stats':
            stats_text = f"📊 Статистика\n👥 Всего пользователей: {len(users)}"
            await bot.send_message(chat_id=chat_id, text=stats_text)
            return
        if user_text == '/users':
            await bot.send_message(chat_id=chat_id, text=f"👥 Всего пользователей: {len(users)}")
            return

        # Логика рассылки
        if chat_id in broadcast_data:
            state = broadcast_data[chat_id].get('step')
            if has_photo and state == 'awaiting_text':
                broadcast_data[chat_id]['photo_token'] = photo_token
                broadcast_data[chat_id]['step'] = 'awaiting_text_with_photo'
                await bot.send_message(chat_id=chat_id, text="📸 Фото получено! Теперь напишите текст.")
                return

            if user_text and state in ['awaiting_text', 'awaiting_text_with_photo']:
                broadcast_data[chat_id]['text'] = user_text
                broadcast_data[chat_id]['step'] = 'awaiting_confirmation'
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 Подтвердите рассылку\n\nТекст:\n{user_text}\n\nОтправить? (ДА/НЕТ)"
                )
                return

            if state == 'awaiting_confirmation':
                if user_text in ['да', 'yes', '+', 'конечно']:
                    await send_broadcast(chat_id, broadcast_data[chat_id].get('text', ''), broadcast_data[chat_id].get('photo_token'))
                    broadcast_data.pop(chat_id, None)
                else:
                    await bot.send_message(chat_id=chat_id, text="❌ Рассылка отменена.")
                    broadcast_data.pop(chat_id, None)
                return

    # Обычный диалог
    state = user_states.get(chat_id, STATE_START)

    if user_text == '/start':
        user_states[chat_id] = STATE_START
        user_data[chat_id] = {}
        await bot.send_message(chat_id=chat_id, text="🔄 Диалог сброшен.\n\n" + WELCOME_TEXT)
        return

    if state == STATE_ASK_PHOTO:
        if has_photo:
            user_data[chat_id]['has_photo'] = True
            user_data[chat_id]['photo_url'] = photo_url
            user_data[chat_id]['photo_token'] = photo_token
            user_states[chat_id] = STATE_ASK_PHONE
            await bot.send_message(chat_id=chat_id, text="📸 Фото получено! Укажите ваш телефон.")
        else:
            await bot.send_message(chat_id=chat_id, text="📸 Отправьте фото детали или VIN.")
        return

    if state == STATE_START:
        if user_text in ['да', 'yes', '+', 'давай', 'ок', 'конечно']:
            user_states[chat_id] = STATE_ASK_ARTICLE
            await bot.send_message(chat_id=chat_id, text="📝 Напишите артикул или VIN.")
        elif user_text in ['нет', 'no', '-', 'не']:
            user_states[chat_id] = STATE_ASK_CAR_INFO
            await bot.send_message(chat_id=chat_id, text="🚗 Вы знаете марку и модель авто?")
        else:
            await bot.send_message(chat_id=chat_id, text="❓ Ответьте ДА или НЕТ.")
    elif state == STATE_ASK_ARTICLE:
        if user_text:
            user_data[chat_id]['article_or_vin'] = user_text
            user_states[chat_id] = STATE_ASK_PHONE
            await bot.send_message(chat_id=chat_id, text=f"✅ Принято: {user_text}\n\n📞 Укажите ваш телефон.")
    elif state == STATE_ASK_CAR_INFO:
        if user_text in ['да', 'yes']:
            user_states[chat_id] = STATE_AWAITING_CAR_DETAILS
            await bot.send_message(chat_id=chat_id, text="🚙 Напишите марку, модель и год выпуска.")
        elif user_text in ['нет', 'no']:
            user_states[chat_id] = STATE_ASK_PHOTO
            await bot.send_message(chat_id=chat_id, text="📸 Отправьте фото детали.")
        else:
            await bot.send_message(chat_id=chat_id, text="❓ Ответьте ДА или НЕТ.")
    elif state == STATE_AWAITING_CAR_DETAILS:
        if user_text:
            user_data[chat_id]['car_info'] = user_text
            user_states[chat_id] = STATE_ASK_PHONE
            await bot.send_message(chat_id=chat_id, text=f"✅ Принято: {user_text}\n\n📞 Укажите ваш телефон.")
    elif state == STATE_ASK_PHONE:
        if any(x in user_text for x in ['+', '7', '8', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']):
            user_data[chat_id]['phone'] = user_text
            await finalize_order(chat_id)
        else:
            await bot.send_message(chat_id=chat_id, text="📞 Укажите телефон для связи.")

async def finalize_order(chat_id):
    data = user_data.get(chat_id, {})
    user_name = data.get('user_name', 'Клиент')
    order_text = f"🆕 НОВАЯ ЗАЯВКА\n\n👤 {user_name}\n🆔 {chat_id}\n\n"
    if 'article_or_vin' in data:
        order_text += f"🔢 Артикул/VIN: {data['article_or_vin']}\n"
    if 'car_info' in data:
        order_text += f"🚗 Автомобиль: {data['car_info']}\n"
    if 'phone' in data:
        order_text += f"📞 Телефон: {data['phone']}\n"
    if data.get('has_photo'):
        order_text += f"📸 Фото: отправлено\n"
        if data.get('photo_url'):
            order_text += f"🔗 Ссылка: {data['photo_url']}\n"
    order_text += f"\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    await bot.send_message(chat_id=MANAGER_CHAT_ID, text=order_text)
    await bot.send_message(chat_id=chat_id, text="✅ Заявка отправлена! Менеджер свяжется с вами.")
    user_states[chat_id] = STATE_START
    user_data[chat_id] = {}

# ========== ОБРАБОТЧИК ВЕБХУКА ==========
async def webhook_handler(request):
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(got, WEBHOOK_SECRET):
            return web.Response(status=403, text="Forbidden")
    try:
        data = await request.json()
        update_type = data.get("update_type")

        if update_type == "bot_started":
            chat_id = data.get("chat_id")
            user_id = data.get("user_id")
            user_info = data.get("user", {})
            user_name = user_info.get("name", "Гость")
            await on_bot_started(chat_id, user_id, user_name)

        elif update_type == "message_created":
            msg = data.get("message", {})
            recipient = msg.get("recipient", {})
            sender = msg.get("sender", {})
            body = msg.get("body", {})
            chat_id = recipient.get("chat_id")
            user_id = sender.get("user_id")
            text = body.get("text", "")

            attachments = body.get("attachments", [])
            logger.info(f"📸 ATTACHMENTS RAW: {json.dumps(attachments, ensure_ascii=False)}")
            logger.info(f"📸 BODY: {json.dumps(body, ensure_ascii=False)}")
            has_photo = False
            photo_url = None
            photo_token = None
            if attachments:
                first_att = attachments[0]
                att_type = first_att.get("type")
                # Некоторые версии MAX могут использовать "photo" вместо "image"
                if att_type == "image" or att_type == "photo":
                    has_photo = True
                    payload = first_att.get("payload", {})
                    photo_url = payload.get("url")
                    photo_token = payload.get("token")

            await handle_message(chat_id, user_id, text, has_photo, photo_url, photo_token)

    except Exception as e:
        logger.exception("Ошибка обработки вебхука")
    return web.Response(status=200, text="OK")

async def health(request):
    return web.json_response({"status": "ok"})

# ========== ЗАПУСК ==========
async def main():
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/health", health)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("🚗 Бот запущен через Webhook на порту %s", port)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

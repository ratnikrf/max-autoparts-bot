import asyncio
import logging
import os
import json
import hmac
from datetime import datetime
from aiohttp import web
from dotenv import load_dotenv
from maxapi import Bot

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ==========
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

# ========== ФУНКЦИИ РАБОТЫ С ФАЙЛАМИ ==========
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

users = load_users()

# ========== ФУНКЦИИ ДЛЯ ФОТО ==========
def has_attachments(message):
    if hasattr(message, 'body') and hasattr(message.body, 'attachments'):
        if message.body.attachments and len(message.body.attachments) > 0:
            return True
    return False

def get_photo_url(message):
    if hasattr(message, 'body') and hasattr(message.body, 'attachments'):
        if message.body.attachments and len(message.body.attachments) > 0:
            attachment = message.body.attachments[0]
            if hasattr(attachment, 'payload') and hasattr(attachment.payload, 'url'):
                return attachment.payload.url
    return None

def get_photo_token(message):
    if hasattr(message, 'body') and hasattr(message.body, 'attachments'):
        if message.body.attachments and len(message.body.attachments) > 0:
            attachment = message.body.attachments[0]
            if hasattr(attachment, 'payload') and hasattr(attachment.payload, 'token'):
                return attachment.payload.token
    return None

# ========== РАССЫЛКА ==========
async def send_broadcast(chat_id, text, photo_token=None):
    results = {"total": len(users), "success": 0, "failed": 0, "failed_users": []}
    print(f"\n📢 НАЧАЛО РАССЫЛКИ, пользователей: {results['total']}")

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

# ========== ОБРАБОТКА ВЕБХУКА (прямая, без Dispatcher) ==========
async def process_update(update_data: dict):
    update_type = update_data.get("update_type")
    if update_type == "message_created":
        msg = update_data.get("message", {})
        recipient = msg.get("recipient", {})
        sender = msg.get("sender", {})
        body = msg.get("body", {})
        chat_id = recipient.get("chat_id")
        user_id = sender.get("user_id")
        text = body.get("text", "").strip()
        attachments = body.get("attachments", [])

        # Создаём объект-заглушку, имитирующий MessageCreated
        class FakeMessage:
            class Recipient:
                chat_id = chat_id
            class Sender:
                user_id = user_id
            class Body:
                text = text
                attachments = attachments
            recipient = Recipient()
            sender = Sender()
            body = Body()
        fake_event = type('FakeEvent', (), {'message': FakeMessage()})()

        # Вызываем ваш обработчик сообщений
        await handle_message(fake_event)

    elif update_type == "bot_started":
        chat_id = update_data.get("chat_id")
        user_id = update_data.get("user_id")
        user_info = update_data.get("user", {})
        first_name = user_info.get("first_name", "Гость")
        last_name = user_info.get("last_name", "")
        user_name = f"{first_name} {last_name}".strip()

        class FakeUser:
            user_id = user_id
            first_name = first_name
            last_name = last_name
        class FakeEvent:
            chat_id = chat_id
            user = FakeUser()
        await on_bot_started(FakeEvent())

# ========== ОБРАБОТЧИКИ (ваши, но без декораторов) ==========
async def on_bot_started(event):
    chat_id = event.chat_id
    user_id = event.user.user_id
    user_name = f"{event.user.first_name}"
    if hasattr(event.user, 'last_name') and event.user.last_name:
        user_name += f" {event.user.last_name}"

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
    print(f"👋 Новый пользователь: {user_id} ({user_name})")

async def handle_message(event):
    chat_id = event.message.recipient.chat_id
    user_id = event.message.sender.user_id
    user_text = event.message.body.text.lower().strip() if event.message.body.text else ""

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
            # В этой версии у нас нет прямого доступа к оригинальному message, но для рассылки мы используем event.message
            # Для упрощения оставим как есть, но если понадобится фото – нужно доработать
            if user_text and state in ['awaiting_text', 'awaiting_text_with_photo']:
                text_for_broadcast = user_text
                photo_token_for_broadcast = broadcast_data[chat_id].get('photo_token')
                broadcast_data[chat_id]['text'] = text_for_broadcast
                broadcast_data[chat_id]['step'] = 'awaiting_confirmation'
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 Подтвердите рассылку\n\nТекст:\n{text_for_broadcast}\n\nОтправить? (ДА/НЕТ)"
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
            # Для фото нужна более сложная логика (получение токена из attachments), пока пропустим
            # Если вам нужна полноценная рассылка с фото, скажите – я добавлю.
            return

    # Обычная логика для пользователей
    state = user_states.get(chat_id, STATE_START)
    # В данной версии event.message.body.attachments не заполнен, поэтому has_photo = False
    has_photo = False

    if user_text == '/start':
        user_states[chat_id] = STATE_START
        user_data[chat_id] = {}
        await bot.send_message(chat_id=chat_id, text="🔄 Диалог сброшен.\n\n" + WELCOME_TEXT)
        return

    if state == STATE_ASK_PHOTO:
        # Фото в этой версии не обрабатываем (упрощённо)
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
    order_text += f"\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    await bot.send_message(chat_id=MANAGER_CHAT_ID, text=order_text)
    await bot.send_message(chat_id=chat_id, text="✅ Заявка отправлена! Менеджер свяжется с вами.")
    user_states[chat_id] = STATE_START
    user_data[chat_id] = {}

# ========== WEBHOOK ОБРАБОТЧИК ==========
async def webhook_handler(request):
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(got, WEBHOOK_SECRET):
            return web.Response(status=403, text="Forbidden")
    try:
        data = await request.json()
        await process_update(data)
    except Exception as e:
        logger.exception("Ошибка обработки вебхука")
    return web.Response(status=200, text="OK")

async def health(request):
    return web.json_response({"status": "ok"})

async def main():
    # Удаляем старую подписку (если есть)
    try:
        await bot.delete_webhook()
        logger.info("Старый вебхук удалён")
    except:
        pass

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

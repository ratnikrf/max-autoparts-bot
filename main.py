import asyncio
import logging
import os
import json
from datetime import datetime
from aiohttp import web
import aiohttp  # Добавлен импорт
from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, BotStarted

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ==========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_CHAT_ID = int(os.getenv("MANAGER_CHAT_ID", 223956964))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

# ========== НАСТРОЙКА БОТА ==========
logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

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
            results["failed_users"].append(
                {"user_id": user_id, "name": user_info.get('user_name', 'Unknown'), "error": str(e)})

    report = f"📊 **ОТЧЁТ О РАССЫЛКЕ**\n\n👥 Всего: {results['total']}\n✅ Успешно: {results['success']}\n❌ Ошибок: {results['failed']}"
    await bot.send_message(chat_id=chat_id, text=report)
    return results


def is_manager(chat_id, user_id):
    return chat_id == MANAGER_CHAT_ID


# ========== ОБРАБОТЧИКИ ==========
@dp.bot_started()
async def on_bot_started(event: BotStarted):
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


@dp.message_created()
async def handle_message(event: MessageCreated):
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
            stats_text = f"📊 Статистика\n👥 Всего: {len(users)}\n👤 Активных сегодня: ..."
            await bot.send_message(chat_id=chat_id, text=stats_text)
            return
        if user_text == '/users':
            await bot.send_message(chat_id=chat_id, text=f"👥 Всего пользователей: {len(users)}")
            return

        # Логика рассылки
        if chat_id in broadcast_data:
            state = broadcast_data[chat_id].get('step')
            has_photo = has_attachments(event.message)
            photo_token = get_photo_token(event.message) if has_photo else None

            if has_photo and state == 'awaiting_text':
                broadcast_data[chat_id]['photo_token'] = photo_token
                broadcast_data[chat_id]['step'] = 'awaiting_text_with_photo'
                await bot.send_message(chat_id=chat_id, text="📸 Фото получено! Теперь напишите текст.")
                return

            if event.message.body.text and state in ['awaiting_text', 'awaiting_text_with_photo']:
                text_for_broadcast = event.message.body.text.strip()
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
                    await send_broadcast(chat_id, broadcast_data[chat_id].get('text', ''),
                                         broadcast_data[chat_id].get('photo_token'))
                    broadcast_data.pop(chat_id, None)
                else:
                    await bot.send_message(chat_id=chat_id, text="❌ Рассылка отменена.")
                    broadcast_data.pop(chat_id, None)
                return

    # Обычная логика для пользователей
    state = user_states.get(chat_id, STATE_START)
    has_photo = has_attachments(event.message)

    if user_text == '/start':
        user_states[chat_id] = STATE_START
        user_data[chat_id] = {}
        await bot.send_message(chat_id=chat_id, text="🔄 Диалог сброшен.\n\n" + WELCOME_TEXT)
        return

    if state == STATE_ASK_PHOTO:
        if has_photo:
            photo_url = get_photo_url(event.message)
            user_data[chat_id]['has_photo'] = True
            user_data[chat_id]['photo_url'] = photo_url
            user_states[chat_id] = STATE_ASK_PHONE
            await bot.send_message(chat_id=chat_id, text="📸 Фото получено! Укажите ваш телефон.")
        else:
            await bot.send_message(chat_id=chat_id, text="📸 Отправьте фото детали или VIN.")
        return

    # Остальные состояния
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


# ========== ЗАПУСК ЧЕРЕЗ WEBHOOK ==========
async def handle_webhook(request):
    try:
        update_data = await request.json()
        await dp.feed_update(bot, update_data)
        return web.Response(status=200)
    except Exception as e:
        print(f"Ошибка вебхука: {e}")
        return web.Response(status=500)


async def main():
    # Удаляем старый вебхук
    await bot.delete_webhook()

    # Устанавливаем вебхук через прямой API-запрос
    WEBHOOK_URL = "https://bot1777832006.bothost.tech"

    async with aiohttp.ClientSession() as session:
        headers = {
            "Authorization": BOT_TOKEN,
            "Content-Type": "application/json"
        }
        payload = {
            "url": WEBHOOK_URL,
            "update_types": ["message_created", "bot_started"]
        }
        async with session.post("https://botapi.max.ru/subscriptions",
                                headers=headers,
                                json=payload) as response:
            result = await response.json()
            print(f"📡 Результат установки вебхука: {result}")

    print(f"✅ Webhook установлен на {WEBHOOK_URL}")

    # Запускаем веб-сервер
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

    print("🚗 Бот запущен через Webhook!")
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())

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
    logger.info(f"\n📢 НАЧАЛО РАССЫЛКИ, пользователей: {results['total']}")
    logger.info(f"📝 Текст: {text[:100]}..." if text else "📝 Текст: (пусто)")
    logger.info(f"📸 Фото: {'есть' if photo_token else 'нет'}")

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
    if results['failed_users']:
        report += f"\n❌ **Неудачные отправки:**\n"
        for failed in results['failed_users'][:10]:
            report += f"• {failed['name']} (ID: {failed['user_id']}): {failed['error'][:50]}\n"
        if len(results['failed_users']) > 10:
            report += f"\n... и ещё {len(results['failed_users']) - 10} ошибок"
    await bot.send_message(chat_id=chat_id, text=report)
    logger.info(f"📊 ИТОГИ РАССЫЛКИ: ✅ {results['success']} ❌ {results['failed']}")
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
    logger.info(f"👋 Новый пользователь: {user_id} ({user_name}) - сохранён в базу")

async def handle_message(chat_id, user_id, user_text, has_photo, photo_url, photo_token):
    # Обновляем активность пользователя
    if str(user_id) in users:
        users[str(user_id)]['last_activity'] = datetime.now().isoformat()
        save_users(users)

    # === КОМАНДЫ ТОЛЬКО ДЛЯ МЕНЕДЖЕРА ===
    if is_manager(chat_id, user_id):
        if user_text == '/broadcast':
            broadcast_data[chat_id] = {'step': 'awaiting_text'}
            await bot.send_message(
                chat_id=chat_id,
                text="📢 **Режим рассылки активирован!**\n\n"
                     "Отправьте текст сообщения для рассылки.\n"
                     "Если хотите отправить фото, сначала отправьте фото, затем текст.\n\n"
                     "Чтобы отменить рассылку, напишите /cancel"
            )
            return
        if user_text == '/cancel':
            if chat_id in broadcast_data:
                del broadcast_data[chat_id]
            await bot.send_message(chat_id=chat_id, text="❌ Режим рассылки отменён.")
            return
        if user_text == '/stats':
            total_users = len(users)
            active_today = sum(1 for u in users.values()
                               if u.get('last_activity', '').startswith(datetime.now().strftime('%Y-%m-%d')))
            stats_text = f"📊 **СТАТИСТИКА БОТА**\n\n"
            stats_text += f"👥 Всего пользователей: {total_users}\n"
            stats_text += f"📅 Активных сегодня: {active_today}\n"
            stats_text += f"💾 База пользователей: {USERS_FILE}\n"
            await bot.send_message(chat_id=chat_id, text=stats_text)
            return
        if user_text == '/users':
            if not users:
                await bot.send_message(chat_id=chat_id, text="📭 Нет сохранённых пользователей.")
                return
            users_list = "📋 **СПИСОК ПОЛЬЗОВАТЕЛЕЙ**\n\n"
            for uid, info in list(users.items())[:20]:
                users_list += f"🆔 {uid} - {info.get('user_name', 'Unknown')}\n"
                users_list += f"   📅 Зарегистрирован: {info.get('first_seen', 'Unknown')[:10]}\n"
            if len(users) > 20:
                users_list += f"\n... и ещё {len(users) - 20} пользователей"
            await bot.send_message(chat_id=chat_id, text=users_list)
            return

        # Обработка рассылки с фото
        if chat_id in broadcast_data:
            state = broadcast_data[chat_id].get('step')
            if has_photo and state == 'awaiting_text':
                broadcast_data[chat_id]['photo_token'] = photo_token
                broadcast_data[chat_id]['step'] = 'awaiting_text_with_photo'
                await bot.send_message(
                    chat_id=chat_id,
                    text="📸 Фото получено! Теперь напишите текст сообщения для рассылки.\n\n"
                         "Чтобы отменить рассылку, напишите /cancel"
                )
                return

            if user_text and state in ['awaiting_text', 'awaiting_text_with_photo']:
                broadcast_data[chat_id]['text'] = user_text
                broadcast_data[chat_id]['step'] = 'awaiting_confirmation'
                confirm_text = f"📢 **ПОДТВЕРДИТЕ РАССЫЛКУ**\n\n"
                confirm_text += f"📝 Текст сообщения:\n\"{user_text}\"\n\n"
                if broadcast_data[chat_id].get('photo_token'):
                    confirm_text += f"📸 Будет отправлено с фото\n\n"
                confirm_text += f"👥 Будет отправлено {len(users)} пользователям.\n\n"
                confirm_text += f"Отправить? Напишите **ДА** или **НЕТ**"
                await bot.send_message(chat_id=chat_id, text=confirm_text)
                return

            if state == 'awaiting_confirmation':
                if user_text in ['да', 'yes', '+', 'lf', 'конечно', 'да.', 'yes.']:
                    await send_broadcast(chat_id, broadcast_data[chat_id].get('text', ''), broadcast_data[chat_id].get('photo_token'))
                    del broadcast_data[chat_id]
                else:
                    await bot.send_message(chat_id=chat_id, text="❌ Рассылка отменена.")
                    del broadcast_data[chat_id]
                return

    # === ОБЫЧНАЯ ЛОГИКА ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ===
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
            await bot.send_message(
                chat_id=chat_id,
                text="📸 Фото получено! Спасибо.\n\n📞 Укажите Ваш телефон для связи. Менеджер перезвонит, чтобы уточнить цену и наличие.\n\n👉 Пример: +7 123 456-78-90"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="📸 Пожалуйста, отправьте фото старой детали или VIN (под лобовым стеклом)."
            )
        return

    if has_photo and state != STATE_ASK_PHOTO:
        await bot.send_message(
            chat_id=chat_id,
            text="📸 Фото получено, но давайте сначала ответим на вопросы.\n\n" + WELCOME_TEXT
        )
        return

    if state == STATE_START:
        if user_text in ['да', 'yes', '+', 'lf', 'ну да', 'давай', 'ок', 'да.', 'yes.', 'конечно', 'есть', 'имеется', 'ага', 'угу']:
            user_states[chat_id] = STATE_ASK_ARTICLE
            await bot.send_message(
                chat_id=chat_id,
                text="📝 Напишите артикул запчасти или VIN кузова (17 символов).\n\n👉 Пример артикула: 12345-67890\n👉 Пример VIN: JTDBE32KX00345678"
            )
        elif user_text in ['нет', 'no', '-', 'ytn', 'не', 'неа', 'нет(', 'нету', 'нема', 'незнаю']:
            user_states[chat_id] = STATE_ASK_CAR_INFO
            await bot.send_message(
                chat_id=chat_id,
                text="🚗 Ничего страшного. Вы знаете марку, модель и год выпуска авто?\n\n👉 Напишите ДА или НЕТ"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❓ Пожалуйста, ответьте «ДА» или «НЕТ».\n\n" + WELCOME_TEXT
            )
    elif state == STATE_ASK_ARTICLE:
        if user_text:
            user_data[chat_id]['article_or_vin'] = user_text
            user_states[chat_id] = STATE_ASK_PHONE
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Принято: {user_text}\n\n📞 Укажите Ваш телефон для связи. Менеджер перезвонит, чтобы уточнить цену и наличие.\n\n👉 Пример: +7 123 456-78-90"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❓ Пожалуйста, напишите артикул или VIN номер."
            )
    elif state == STATE_ASK_CAR_INFO:
        if user_text in ['да', 'yes', '+', 'lf', 'ну да', 'да.', 'конечно', 'знаю', 'ага']:
            user_states[chat_id] = STATE_AWAITING_CAR_DETAILS
            await bot.send_message(
                chat_id=chat_id,
                text="🚙 Напишите марку, модель и год выпуска.\n\n👉 Пример: Toyota Camry 2020\n👉 Или: BMW X5 2018"
            )
        elif user_text in ['нет', 'no', '-', 'ytn', 'не', 'неа', 'нет(', 'не знаю']:
            user_states[chat_id] = STATE_ASK_PHOTO
            await bot.send_message(
                chat_id=chat_id,
                text="📸 Сфотографируйте старую деталь или VIN (под лобовым стеклом). Я передам фото менеджеру.\n\nОтправьте фото в этот чат."
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❓ Пожалуйста, ответьте «ДА» или «НЕТ».\n\nВы знаете марку, модель и год выпуска авто?"
            )
    elif state == STATE_AWAITING_CAR_DETAILS:
        if user_text:
            user_data[chat_id]['car_info'] = user_text
            user_states[chat_id] = STATE_ASK_PHONE
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Принято: {user_text}\n\n📞 Укажите Ваш телефон для связи. Менеджер перезвонит, чтобы уточнить цену и наличие.\n\n👉 Пример: +7 123 456-78-90"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❓ Напишите марку, модель и год выпуска.\n\n👉 Пример: Toyota Camry 2020"
            )
    elif state == STATE_ASK_PHONE:
        if any(x in user_text for x in ['+', '7', '8', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']):
            user_data[chat_id]['phone'] = user_text
            await finalize_order(chat_id)
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="📞 Пожалуйста, укажите Ваш телефон для связи.\n\n👉 Пример: +7 123 456-78-90"
            )

async def finalize_order(chat_id):
    data = user_data.get(chat_id, {})
    user_name = data.get('user_name', 'Клиент')
    order_text = f"🆕 НОВАЯ ЗАЯВКА\n\n"
    order_text += f"👤 Клиент: {user_name}\n"
    order_text += f"🆔 ID: {chat_id}\n"
    order_text += "━" * 30 + "\n\n"

    if 'article_or_vin' in data:
        order_text += f"🔢 Артикул/VIN: {data['article_or_vin']}\n\n"
    if 'car_info' in data:
        order_text += f"🚗 Автомобиль: {data['car_info']}\n\n"
    if 'phone' in data:
        order_text += f"📞 Телефон: {data['phone']}\n\n"
    if data.get('has_photo'):
        order_text += f"📸 Фото: отправлено\n"
        if data.get('photo_url'):
            order_text += f"🔗 Ссылка: {data['photo_url']}\n"

    order_text += "━" * 30 + "\n"
    order_text += f"📅 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    try:
        await bot.send_message(chat_id=MANAGER_CHAT_ID, text=order_text)
        logger.info(f"📨 Заявка отправлена менеджеру от {user_name}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки менеджеру: {e}")

    first_name = user_name.split()[0] if user_name.split() else "Клиент"
    await bot.send_message(
        chat_id=chat_id,
        text=f"✅ **Ваша заявка успешно отправлена!**\n\n"
             f"Спасибо за обращение, {first_name}!\n"
             f"Наш менеджер свяжется с вами в ближайшее время для уточнения цены и наличия.\n\n"
             f"🚗 Хорошего дня!\n\n"
             f"---\n"
             f"Чтобы начать новую заявку, напишите /start"
    )

    logger.info(order_text)
    logger.info("=" * 50)

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
            has_photo = False
            photo_url = None
            photo_token = None
            if attachments:
                first_att = attachments[0]
                att_type = first_att.get("type")
                if att_type in ("image", "photo"):
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

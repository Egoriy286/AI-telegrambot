# bot.py - Главный файл бота
import os
import requests
import logging
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timedelta
from utils import markdown_to_html, smart_split_message
from database import Database

load_dotenv()

# Настройка логирования
def setup_logging():
    """Настройка системы логирования"""
    # Создаем директорию для логов если её нет
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # Формат логов
    log_format = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Основной логгер
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Хендлер для файла (все логи)
    file_handler = RotatingFileHandler(
        'logs/bot.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_format)
    
    # Хендлер для ошибок
    error_handler = RotatingFileHandler(
        'logs/errors.log',
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(log_format)
    
    # Хендлер для консоли
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    
    # Добавляем хендлеры
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)
    
    # Отключаем избыточное логирование от aiogram
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    
    return logger

# Инициализируем логирование
logger = setup_logging()

# Константа для установки лимита
COOLDOWN_SECONDS = 7 
MAX_MESSAGE_LENGTH = 4096 
FREE_DAILY_LIMIT = os.getenv("FREE_DAILY_LIMIT")
PREMIUM_DAILY_LIMIT = os.getenv("PREMIUM_DAILY_LIMIT")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY_GPT")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))


logger.info("="*50)
logger.info("Инициализация бота...")
logger.info(f"Telegram Token: {'✓' if TELEGRAM_TOKEN else '✗'}")
logger.info(f"OpenRouter API Key: {'✓' if OPENROUTER_API_KEY else '✗'}")
logger.info(f"Количество администраторов: {len(ADMIN_IDS)}")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
db = Database()

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.5-flash-lite-preview-09-2025"

# Цены за токены
INPUT_TOKEN_PRICE = 0.10 / 1_000_000
OUTPUT_TOKEN_PRICE = 0.40 / 1_000_000

# Лимиты и цены подписок (в Telegram Stars)
FREE_DAILY_LIMIT = 10
SUBSCRIPTION_PRICES = {
    "week": {"price": 25, "days": 7, "title": "Неделя"},
    "month": {"price": 50, "days": 30, "title": "Месяц"},
    "year": {"price": 75, "days": 60, "title": "2 Месяца"}
}

# Состояния для FSM
class AdminStates(StatesGroup):
    waiting_broadcast = State()

def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Рассчитывает стоимость запроса"""
    return (input_tokens * INPUT_TOKEN_PRICE) + (output_tokens * OUTPUT_TOKEN_PRICE)

def get_adaptive_max_tokens(history_length: int) -> int:
    """Адаптивное определение max_tokens"""
    if history_length < 5:
        return 2000
    elif history_length < 15:
        return 1500
    elif history_length < 30:
        return 1000
    else:
        return 800

def is_admin(user_id: int) -> bool:
    """Проверка является ли пользователь админом"""
    return user_id in ADMIN_IDS

def get_subscription_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с вариантами подписки"""
    keyboard = [
        [InlineKeyboardButton(
            text=f"⭐️ {SUBSCRIPTION_PRICES['week']['title']} - {SUBSCRIPTION_PRICES['week']['price']} Stars",
            callback_data="subscribe_week"
        )],
        [InlineKeyboardButton(
            text=f"🌟 {SUBSCRIPTION_PRICES['month']['title']} - {SUBSCRIPTION_PRICES['month']['price']} Stars",
            callback_data="subscribe_month"
        )],
        [InlineKeyboardButton(
            text=f"✨ {SUBSCRIPTION_PRICES['year']['title']} - {SUBSCRIPTION_PRICES['year']['price']} Stars",
            callback_data="subscribe_year"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id

    logger.info(f"Команда /start от пользователя {user_id}")
    
    try:
        # Добавляем пользователя и очищаем историю
        it_new_user = not(db.check_user(user_id))
        if (it_new_user):
            logging.info(f"Add new user: {user_id}")
            username = message.from_user.username or "Нет username"
            full_name = message.from_user.full_name

            db.add_user(user_id, username, full_name)
            
        db.clear_history(user_id)
        logger.info(f"История очищена для пользователя {user_id}")
        
        # Проверяем статус подписки
        subscription_info = db.get_subscription_info(user_id)
        has_subscription = subscription_info['is_active']
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Моя статистика", callback_data="my_stats")],
            [InlineKeyboardButton(text="💎 Подписка", callback_data="subscription_info")]
        ])
        
        welcome_text = (
            "👋 <b>Привет! Я умный AI-ассистент</b>\n\n"
            "💬 Просто напиши мне сообщение, и я помогу!\n\n"
        )
        remaining = db.get_remaining_requests(user_id)
        if has_subscription:
            
            welcome_text += (
                f"✅ У вас активна подписка до {subscription_info['expires_at']}\n"
                f"💎 У вас доступно: {remaining} запросов сегодня!"
            )
            logger.info(f"Пользователь {user_id} имеет активную подписку до {subscription_info['expires_at']}")
        else:
            welcome_text += (
                f"🔍 У вас доступно: {remaining} запросов сегодня\n"
                "💎 Купите <b>подписку</b> для доступа и увеличения количества запросов."
            )
            logger.info(f"Пользователь {user_id} имеет {remaining}/{FREE_DAILY_LIMIT} бесплатных запросов")
        
        logger.info(f"Приветственное сообщение отправлено пользователю {user_id}")

        await message.answer(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Ошибка в start_handler для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")

@dp.message(Command("stats"))
async def stats_command(message: Message, user_id: int = None):
    """Статистика пользователя"""
    if (user_id == None):
        user_id = message.from_user.id
    logger.info(f'stat {user_id}')
    stats = db.get_user_stats(user_id)
    subscription = db.get_subscription_info(user_id)
    
    if stats:
        status = "✅ Активна" if subscription['is_active'] else "❌ Не активна"
        expires = subscription['expires_at'] if subscription['is_active'] else "—"
        
        text = (
            f"📊 <b>Ваша статистика:</b>\n\n"
            f"👤 <b>Профиль:</b>\n"
            f"├ ID: <code>{user_id}</code>\n"
            f"├ Имя: {stats['full_name']}\n"
            f"└ Регистрация: {stats['registration_date']}\n\n"
            f"💎 <b>Подписка:</b>\n"
            f"├ Статус: {status}\n"
            f"└ Действует до: {expires}\n\n"
            f"📈 <b>Активность:</b>\n"
            f"├ Всего сообщений: {stats['total_messages']}\n"
            f"├ Сегодня: {stats['today_requests']}/{FREE_DAILY_LIMIT if not subscription['is_active'] else PREMIUM_DAILY_LIMIT}\n"
            f"└ Последнее сообщение: {stats['last_activity']}\n\n"
            
        )

        # Добавляем блок затрат только для админов
        if user_id in ADMIN_IDS:
            text += (
                f"🔤 <b>Использование токенов:</b>\n"
                f"├ Входные: {stats['total_input_tokens']:,}\n"
                f"├ Выходные: {stats['total_output_tokens']:,}\n"
                f"└ Всего: {stats['total_input_tokens'] + stats['total_output_tokens']:,}\n\n"
                f"💰 <b>Затраты:</b> ${stats['total_cost']:.6f}\n"
                )

        # Отправляем сообщение
        await message.answer(text, parse_mode=ParseMode.HTML)
        
@dp.message(Command("subscribe"))
async def subscribe_command(message: Message, user_id: int = None):
    """Информация о подписке"""
    if (user_id == None):
        user_id = message.from_user.id
    logger.info(f"sub {user_id}")
    subscription = db.get_subscription_info(user_id)
    
    if subscription['is_active']:
        await message.answer(
            f"✅ <b>У вас активна подписка!</b>\n\n"
            f"Действует до: {subscription['expires_at']}\n"
            f"🚀 Дополнительные запросы",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "💎 <b>Выберите подходящий тариф:</b>\n\n"
            "⭐️ <b>Неделя</b> — 25 Stars\n"
            "└ +100 дополнительных запросов и доступ к Премиум ИИ-ботам на 7 дней\n\n"
            "🌟 <b>Месяц</b> — 50 Stars\n"
            "└ +100 запросов ежедневно и доступ к Премиум ИИ-ботам на 30 дней\n\n"
            "✨ <b>2 Месяца</b> — 75 Stars\n"
            "└ +100 запросов ежедневно и доступ к Премиум ИИ-ботам на 60 дней\n"
            "└ Выгода 34%!\n\n"
            "ℹ️ <i>1 Star ≈ 1.79₽</i>\n"
            "💰 <b>Купите Stars:</b> @PremiumBot",
            reply_markup=get_subscription_keyboard(),
            parse_mode=ParseMode.HTML
        )

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    """Админ-панель"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_general")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="admin_finance")],
        [InlineKeyboardButton(text="🔝 Топ пользователей", callback_data="admin_top")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])
    
    await message.answer(
        "🔐 <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "my_stats")
async def my_stats_callback(callback: types.CallbackQuery):
    await stats_command(callback.message, callback.from_user.id)
    await callback.answer()

@dp.callback_query(F.data == "subscription_info" or F.data == "subscribe")
async def subscription_info_callback(callback: types.CallbackQuery):
    await subscribe_command(callback.message, callback.from_user.id)
    await callback.answer()

@dp.callback_query(F.data.startswith("subscribe_"))
async def subscribe_callback(callback: types.CallbackQuery):
    period = callback.data.split("_")[1]
    price_info = SUBSCRIPTION_PRICES[period]
    
    # Создаем инвойс для Telegram Stars
    prices = [LabeledPrice(label=f"Подписка на {price_info['title']}", amount=price_info['price'])]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Подписка на {price_info['title']}",
        description=f"+100 запросов ежедневно к AI-ассистенту на {price_info['days']} дней",
        payload=f"{period}_{callback.from_user.id}",
        currency="XTR",  # XTR = Telegram Stars
        prices=prices
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Обработка предварительной проверки платежа"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    """Обработка успешного платежа"""
    try:
        payload = message.successful_payment.invoice_payload
        period, user_id = payload.split("_")
        user_id = int(user_id)
        
        days = SUBSCRIPTION_PRICES[period]['days']
        price = SUBSCRIPTION_PRICES[period]['price']
        
        logger.info(f"Успешный платеж от пользователя {user_id}: {price} Stars за {days} дней ({period})")
        
        db.add_subscription(user_id, days, price)
        
        await message.answer(
            f"✅ <b>Оплата прошла успешно!</b>\n\n"
            f"⭐️ Списано: {price} Stars\n"
            f"📅 Подписка активирована на {days} дней\n"
            f"🚀 Теперь у вас +100 запросов ежедневно!",
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Подписка успешно активирована для пользователя {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке платежа: {e}", exc_info=True)
        await message.answer("❌ Ошибка при активации подписки. Обратитесь в поддержку.")

@dp.callback_query(F.data == "admin_general")
async def admin_general_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    stats = db.get_general_stats()
    
    await callback.message.answer(
        f"📊 <b>Общая статистика бота:</b>\n\n"
        f"👥 <b>Пользователи:</b>\n"
        f"├ Всего: {stats['total_users']}\n"
        f"├ Активных сегодня: {stats['active_today']}\n"
        f"├ Новых за неделю: {stats['new_week']}\n"
        f"└ С подпиской: {stats['with_subscription']}\n\n"
        f"💬 <b>Сообщения:</b>\n"
        f"├ Всего: {stats['total_messages']:,}\n"
        f"└ Сегодня: {stats['today_messages']:,}\n\n"
        f"🔤 <b>Токены:</b>\n"
        f"├ Входные: {stats['total_input_tokens']:,}\n"
        f"├ Выходные: {stats['total_output_tokens']:,}\n"
        f"└ Всего: {stats['total_input_tokens'] + stats['total_output_tokens']:,}\n\n"
        f"💰 <b>Затраты на API:</b> ${stats['total_cost']:.4f}",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    recent_users = db.get_recent_users(limit=10)
    
    message_text = "👥 <b>Последние 10 пользователей:</b>\n\n"
    for i, user in enumerate(recent_users, 1):
        sub_status = "💎" if user['has_subscription'] else "🆓"
        message_text += (
            f"{i}. {sub_status} @{user['username']}\n"
            f"   └ {user['full_name']}\n"
            f"   └ Сообщений: {user['message_count']} | Рег: {user['registration_date']}\n\n"
        )
    
    await callback.message.answer(message_text, parse_mode=ParseMode.HTML)
    await callback.answer()

@dp.callback_query(F.data == "admin_finance")
async def admin_finance_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    finance = db.get_finance_stats()
    
    await callback.message.answer(
        f"💰 <b>Финансовая статистика:</b>\n\n"
        f"📈 <b>Доход от подписок:</b>\n"
        f"├ Всего: {finance['total_revenue']}₽\n"
        f"├ За месяц: {finance['month_revenue']}₽\n"
        f"└ За неделю: {finance['week_revenue']}₽\n\n"
        f"💸 <b>Расходы на API:</b>\n"
        f"├ Всего: ${finance['total_api_cost']:.4f}\n"
        f"├ За месяц: ${finance['month_api_cost']:.4f}\n"
        f"└ За неделю: ${finance['week_api_cost']:.4f}\n\n"
        f"📊 <b>Подписки:</b>\n"
        f"├ Активных: {finance['active_subscriptions']}\n"
        f"└ Всего продано: {finance['total_subscriptions']}",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_top")
async def admin_top_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    top_users = db.get_top_users(limit=10)
    
    message_text = "🔝 <b>Топ-10 пользователей:</b>\n\n"
    for i, user in enumerate(top_users, 1):
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
        message_text += (
            f"{medal} @{user['username']}\n"
            f"   └ {user['full_name']}\n"
            f"   └ Сообщений: {user['message_count']} | Затраты: ${user['total_cost']:.4f}\n\n"
        )
    
    await callback.message.answer(message_text, parse_mode=ParseMode.HTML)
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.answer("📢 Отправьте сообщение для рассылки:")
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.answer()

@dp.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    users = db.get_all_user_ids()
    success = 0
    failed = 0
    
    status_msg = await message.answer("📤 Начинаю рассылку...")
    
    for user_id in users:
        try:
            await bot.send_message(user_id, message.text, parse_mode=ParseMode.HTML)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    
    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {failed}"
    )
    await state.clear()

def split_message(text):
    return [text[i:i+MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]

@dp.message()
async def chat_handler(message: Message):
    user_id = message.from_user.id
    message_text = message.text[:100] + "..." if len(message.text) > 100 else message.text

    it_new_user = not(db.check_user(user_id))
    if it_new_user:
        logging.info(f"Add new user: {user_id} from message")
        username = message.from_user.username or "Нет username"
        full_name = message.from_user.full_name
        
        db.add_user(user_id, username, full_name)

    logger.info(f"Получено сообщение от пользователя {user_id}")
    
    try:
        # Проверяем лимиты
        subscription = db.get_subscription_info(user_id)

        is_premium = subscription and subscription.get('is_active', False)
        daily_limit = PREMIUM_DAILY_LIMIT if is_premium else FREE_DAILY_LIMIT
        if not is_premium:
            remaining = db.get_remaining_requests(user_id)
            logger.info(f"Пользователь {user_id} без подписки, осталось запросов: {remaining}/{daily_limit}")
            
            if remaining <= 0:
                logger.warning(f"Пользователь {user_id} исчерпал лимит бесплатных запросов")
                await message.answer(
                    "⚠️ <b>Вы исчерпали дневной лимит бесплатных запросов!</b>\n\n"
                    "💎 Оформите подписку, чтобы получить больше запросов.:",
                    reply_markup=get_subscription_keyboard(),
                    parse_mode=ParseMode.HTML
                )
                return
            
            # 1. Проверяем, когда пользователь делал запрос в последний раз
            delay_seconds = 0
             
            time_dict = db.get_user_last_act(user_id)
            last_time_str = time_dict.get('last_activity')
            current_time_str  = time_dict.get('current_time')
            if last_time_str:
                last_time = datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
                current_time = datetime.strptime(current_time_str, "%Y-%m-%d %H:%M:%S")
                time_since_last = current_time - last_time
                min_interval = timedelta(seconds=COOLDOWN_SECONDS)

                if time_since_last < min_interval:
                    remaining_time = min_interval - time_since_last
                    delay_seconds = int(remaining_time.total_seconds()) + 1
                    logging.info(
                        f"Пользователь {user_id} слишком рано сделал запрос. "
                        f"Последний запрос: {last_time}, текущее время: {current_time}, "
                        f"прошло: {time_since_last}, осталось секунд: {delay_seconds}"
                    )
                else:
                    logging.info(f"Пользователь {user_id} сделал запрос. Прошло времени с последнего запроса: {time_since_last}")
            else:
                logging.info(f"Пользователь {user_id} делает первый запрос.")
            if it_new_user:
                pass
            elif delay_seconds > 0:
                await message.answer(
                    f"⏳ Повторите запрос через <b>{delay_seconds}</b> секунд или купите подписку, чтобы получать ответы мгновенно 💎",
                    parse_mode=ParseMode.HTML
                )
            #elif delay_info.get('was_delayed', False): # Задержка только что закончилась    await message.answer(        "✅ Теперь вы можете отправить запрос!",        parse_mode=ParseMode.HTML    )
                return
        else:
            remaining = db.get_remaining_requests(user_id)
            logger.info(f"Пользователь {user_id}, осталось запросов: {remaining}/{daily_limit}")
            
            if remaining <= 0:
                logger.warning(f"Пользователь {user_id} исчерпал лимит запросов")
                await message.answer(
                    f"⚠️ <b>Вы исчерпали дневной лимит запросов!</b>\n\n"
                    f"🔄 Лимит обновится через <b>день</b>.\n\n",
                    parse_mode=ParseMode.HTML
                )
                return
        await bot.send_chat_action(message.chat.id, "typing")
        await asyncio.sleep(3)
        # Получаем историю
        history = db.get_history(user_id)
        logger.debug(f"Загружена история для пользователя {user_id}: {len(history)} сообщений")
        
        messages = [
            {"role": "system", "content": "Ты дружелюбный ассистент. Используй Markdown для форматирования: **жирный**, *курсив*, `код`, ```блоки кода```."}
        ]
        
        for msg in history:
            messages.append({"role": msg['role'], "content": msg['content']})
        
        messages.append({"role": "user", "content": message.text})
        
        max_tokens = get_adaptive_max_tokens(len(history))
        logger.debug(f"Используется max_tokens: {max_tokens}")
        
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": max_tokens
        }
        
        logger.info(f"Отправка запроса к OpenRouter API для пользователя {user_id}")
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        answer = data["choices"][0]["message"]["content"]
        
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = calculate_cost(input_tokens, output_tokens)
        
        logger.info(
            f"Ответ получен для пользователя {user_id}. "
            f"Токены: {input_tokens} вход, {output_tokens} выход. "
            f"Стоимость: ${cost:.6f}"
        )
        
        # Сохраняем в историю
        db.add_message(user_id, "user", message.text)
        db.add_message(user_id, "assistant", answer)
        db.update_stats(user_id, input_tokens, output_tokens, cost)
        
        formatted_answer = markdown_to_html(answer)
        
        # Разбиваем умным способом
        message_parts = smart_split_message(formatted_answer, max_length=4096)

        
        # Отправляем части
        if len(message_parts) > 1:
            for i, part in enumerate(message_parts, 1):
                await message.answer(part, parse_mode=ParseMode.HTML)
                logger.info(f"Отправлена часть {i}/{len(message_parts)} пользователю {user_id}")
        else:
            await message.answer(formatted_answer, parse_mode=ParseMode.HTML)
            logger.info(f"Ответ отправлен пользователю {user_id}")
        

    except requests.exceptions.Timeout:
        logger.error(f"Timeout при запросе к API для пользователя {user_id}")
        await message.answer("⏳ К сожалению, время ожидания ответа сервиса истекло. Пожалуйста, попробуйте повторить запрос чуть позже.")

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка запроса к API для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Возникла временная трудность при обращении к сервису. Мы уже работаем над её устранением. Попробуйте снова через некоторое время.")

    except Exception as e:
        logger.error(f"Неожиданная ошибка в chat_handler для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Произошла непредвиденная ошибка. Наша команда уже уведомлена, и мы постараемся всё исправить как можно скорее.")



async def main():
    logger.info("="*50)
    logger.info("🤖 Бот запущен и готов к работе!")
    logger.info(f"Модель: {MODEL}")
    logger.info(f"Бесплатный лимит: {FREE_DAILY_LIMIT} запросов/день")
    logger.info("="*50)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Неожиданная ошибка: {e}", exc_info=True)
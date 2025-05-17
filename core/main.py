import platform, os, sys, json
from typing import Dict, Any, List, Tuple
from subprocess import call

def install_libs():
    libs = [
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("telegram", "python-telegram-bot"),
        ("dotenv", "python-dotenv")
    ]

    for pkg, inst in libs:
        try: __import__(pkg)
        except ImportError:
            print("")
            print(f"Установка библиотек: {pkg} ({inst})")
            call([sys.executable, "-m", "pip", "install", inst])

install_libs()

from reactor import ThermalReactor, handle_temperatures, parse_temperatures
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

load_dotenv()

# Загрузка базы данных реакторов
with open('assets/reactors.json', 'r') as f:
    REACTORS_DB = json.load(f)

# Словарь для хранения активных выводов
active_outputs = {}  # {reactor_id: {"message": message_text, "temps": {"current": [...], "target": float}, "corrections": [...]}}

# Словарь для хранения пользовательских диапазонов
user_ranges = {}  # {user_id: {'B': (min, max), 'C': (min, max), 'D': (min, max)}}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("⚙️ Новый вывод")],
        [KeyboardButton("🖥️ Текущие выводы")],
        [KeyboardButton("🔧 Рабочие диапазоны зон")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '💡 Выберите действие:',
        reply_markup=reply_markup
    )

def parse_range(input_str: str) -> Tuple[float, float]:
    """Парсинг введенного диапазона"""
    try:
        values = input_str.strip().split()
        if len(values) != 2:
            raise ValueError
        
        start = float(values[0].replace(',', '.'))
        end = float(values[1].replace(',', '.'))
        
        return start, end
    except:
        raise ValueError("Неверный формат. Введите два числа, разделенных пробелом")

async def show_ranges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Если диапазоны не установлены, используем значения по умолчанию
    if user_id not in user_ranges:
        user_ranges[user_id] = {
            'B': (2, 0),
            'C': (1, -1),
            'D': (0, -1)
        }
    
    keyboard = [
        [
            InlineKeyboardButton("Б", callback_data="range_B"),
            InlineKeyboardButton("Ц", callback_data="range_C"),
            InlineKeyboardButton("Д", callback_data="range_D")
        ],
        [
        InlineKeyboardButton("Установить для всех зон сразу", callback_data="set_range_all")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    ranges = user_ranges[user_id]
    message = "Рабочие диапазоны зон установлены на значениях:\n\n"
    message += f"Б: от {'+' if ranges['B'][0] > 0 else ''}{ranges['B'][0]} до {'+' if ranges['B'][1] > 0 else ''}{ranges['B'][1]}.\n"
    message += f"Ц: от {'+' if ranges['C'][0] > 0 else ''}{ranges['C'][0]} до {'+' if ranges['C'][1] > 0 else ''}{ranges['C'][1]}.\n"
    message += f"Д: от {'+' if ranges['D'][0] > 0 else ''}{ranges['D'][0]} до {'+' if ranges['D'][1] > 0 else ''}{ranges['D'][1]}."
    
    await update.message.reply_text(message, reply_markup=reply_markup)

async def set_range_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает рабочие диапазоны для всех зон сразу"""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text(
        "Введите диапазоны для всех зон в формате:\n"
        "<code>макс_Б мин_Б макс_Ц мин_Ц макс_Д мин_Д</code>\n\n"
        "Пример: <code>+5 0 +1 -1 0 -2</code>\n"
        "или: <code>5 0 1 -1 0 -2</code>",
        parse_mode='HTML'
    )
    
    context.user_data['state'] = 'waiting_all_ranges'

async def process_all_ranges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод диапазонов для всех зон"""
    try:
        # Разбиваем ввод на числа
        values = update.message.text.strip().split()
        
        # Проверяем количество значений
        if len(values) != 6:
            raise ValueError("❌ Необходимо ввести ровно 6 чисел")
            
        # Парсим значения с учетом опционального +
        def parse_value(val: str) -> float:
            val = val.strip()
            if val.startswith('+'):
                val = val[1:]
            return float(val)
            
        try:
            ranges = [parse_value(v) for v in values]
        except ValueError:
            raise ValueError("❌ Неверный формат чисел")
            
        # Проверяем что максимумы больше минимумов
        if not all(ranges[i] >= ranges[i+1] for i in [0,2,4]):
            raise ValueError("❌ Максимальное значение должно быть больше или равно минимальному")
            
        # Создаем словарь диапазонов
        user_ranges = {
            'B': [ranges[0], ranges[1]],
            'C': [ranges[2], ranges[3]],
            'D': [ranges[4], ranges[5]]
        }
        
        # Сохраняем диапазоны
        user_id = update.effective_user.id
        if user_id not in user_ranges_dict:
            user_ranges_dict[user_id] = {}
        user_ranges_dict[user_id] = user_ranges
        
        await update.message.reply_text(
            "✅ Рабочие диапазоны установлены:\n\n"
            f"Б: от {ranges[1]:+.1f} до {ranges[0]:+.1f}\n"
            f"Ц: от {ranges[3]:+.1f} до {ranges[2]:+.1f}\n"
            f"Д: от {ranges[5]:+.1f} до {ranges[4]:+.1f}"
        )
        
        del context.user_data['state']
        
    except ValueError as e:
        await update.message.reply_text(str(e))

# В обработчик состояний добавить:
if state == 'waiting_all_ranges':
    await process_all_ranges(update, context)
    return

async def show_active_outputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_outputs:
        await update.message.reply_text("Нет активных выводов температуры.")
        return
    
    keyboard = []
    for reactor_id in active_outputs:
        keyboard.append([InlineKeyboardButton(f"Реактор {reactor_id}", callback_data=f"show_{reactor_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text("Активные выводы температуры:", reply_markup=reply_markup)

def validate_reactor_number(reactor_number: str) -> bool:
    # Проверяем, существует ли номер как ID или как альтернативное значение
    if reactor_number in REACTORS_DB['reactors']:  # Проверка как ID (1-1, 1-2, etc.)
        return True
    
    # Проверка как альтернативного значения (11, 12, etc.)
    for reactor_data in REACTORS_DB['reactors'].values():
        if reactor_data['alt'] == reactor_number:
            return True
    return False

def get_reactor_mode(reactor_number: str) -> str:
    # Проверяем сначала как ID, потом как альтернативное значение
    if reactor_number in REACTORS_DB['reactors']:
        return REACTORS_DB['reactors'][reactor_number]['mode']
    
    # Если не нашли как ID, ищем как альтернативное значение
    for reactor_data in REACTORS_DB['reactors'].values():
        if reactor_data['alt'] == reactor_number:
            return reactor_data['mode']
    return 'pc'  # default mode

def get_reactor_id(reactor_number: str) -> str:
    # Если это ID, возвращаем его
    if reactor_number in REACTORS_DB['reactors']:
        return reactor_number
    
    # Если это альтернативное значение, находим соответствующий ID
    for reactor_id, reactor_data in REACTORS_DB['reactors'].items():
        if reactor_data['alt'] == reactor_number:
            return reactor_id
    return reactor_number

async def handle_range_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    zone = query.data.split('_')[1]
    context.user_data['editing_range'] = zone
    
    await query.message.reply_text(
        f"Введите диапазон для зоны {zone} в формате:\n"
        f"<code>мин макс</code>\n\n"
        f"Например: <code>+2 0</code>",
        parse_mode='HTML'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("show_"):
        reactor_id = query.data.split("_")[1]
        if reactor_id in active_outputs:
            output_data = active_outputs[reactor_id]
            keyboard = [
                [
                    InlineKeyboardButton("Завершить вывод канала", callback_data=f"finish_{reactor_id}"),
                    InlineKeyboardButton("Отредактировать вводимые температуры", callback_data=f"edit_{reactor_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(output_data["message"], reply_markup=reply_markup, parse_mode='HTML')
    
    elif query.data.startswith("edit_"):
        reactor_id = query.data.split("_")[1]
        if reactor_id in active_outputs:
            context.user_data['editing_reactor'] = reactor_id
            current_temps = active_outputs[reactor_id]["temps"]["current"]
            target_temp = active_outputs[reactor_id]["temps"]["target"]
            
            await query.message.reply_text(
                f"Реактор: <code>{reactor_id}</code>\n\n"
                f"⌛️ Текущие температуры (Б Ц Д): <code>{' '.join(f'{temp:.1f}' for temp in current_temps)}</code>\n\n"
                f"🌡 Температура по заданию: <code>{target_temp:.1f}</code>\n\n"
                "Введите новые температуры в формате:\n"
                "[три значения температур]\n"
                "Например: <code>1008.5 1003.7 1001.2</code>\n"
                "или: <code>1008,5 1003,7 1001,2</code>",
                parse_mode='HTML'
            )
    
    elif query.data.startswith("finish_"):
        reactor_id = query.data.split("_")[1]
        if reactor_id in active_outputs:
            del active_outputs[reactor_id]
            await query.message.edit_text(f"Вывод для реактора {reactor_id} завершен.")
    
    elif query.data.startswith("range_"):
        await handle_range_callback(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "⚙️ Новый вывод":
        await update.message.reply_text(
            "✍️ Введите номер реактора:\n\n"
            "<blockquote>Например:\n\n"
            "• <code>1-1</code> или упрощённый вариант <code>11</code>\n"
            "• <code>1-2</code> или упрощённый вариант <code>12</code>\n"
            "• <code>1-3</code> или упрощённый вариант <code>13</code></blockquote>",
            parse_mode='HTML'
        )
        context.user_data['state'] = 'waiting_reactor_number'
    
    elif text == "🖥️ Текущие выводы":
        await show_active_outputs(update, context)
        
    elif text == "🔧 Рабочие диапазоны зон":
        await show_ranges(update, context)
    
    elif 'editing_range' in context.user_data:
        try:
            zone = context.user_data['editing_range']
            start, end = parse_range(text)
            
            user_id = update.effective_user.id
            if user_id not in user_ranges:
                user_ranges[user_id] = {}
            
            user_ranges[user_id][zone] = (start, end)
            del context.user_data['editing_range']
            
            await update.message.reply_text(f"✅ Диапазон для зоны {zone} установлен")
            await show_ranges(update, context)
            
        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}")
    
    elif context.user_data.get('state') == 'waiting_reactor_number':
        reactor_number = text.strip()
        
        if not validate_reactor_number(reactor_number):
            await update.message.reply_text(
                "❌ Неверный номер реактора.\n"
                "Используйте формат X-X (например: <code>1-1</code>) или XX (например: <code>11</code>).",
                parse_mode='HTML'
            )
            return
            
        mode = get_reactor_mode(reactor_number)
        reactor_id = get_reactor_id(reactor_number)
        context.user_data['current_reactor'] = reactor_id
        context.user_data['mode'] = mode
        await update.message.reply_text(
            f"Выбран реактор: <code>{reactor_id}</code>\n\n"
            "Введите температуры в формате:\n"
            "[три значения температур] [температура задания]\n"
            "Например: <code>1008.5 1003.7 1001.2 1000.0</code>\n"
            "или: <code>1008,5 1003,7 1001,2 1000,0</code>",
            parse_mode='HTML'
        )
        context.user_data['state'] = 'waiting_temperatures'
    
    elif context.user_data.get('state') == 'waiting_temperatures' or 'editing_reactor' in context.user_data:
        reactor_id = context.user_data.get('editing_reactor') or context.user_data.get('current_reactor')
        try:
            await handle_temperatures(update, context, reactor_id, active_outputs, user_ranges)
            if 'editing_reactor' in context.user_data:
                del context.user_data['editing_reactor']
        except ValueError as e:
            await update.message.reply_text(str(e), parse_mode='HTML')

def main():
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()
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

from reactor import ThermalReactor, handle_temperatures, parse_temperatures, DEFAULT_RANGES
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

load_dotenv()

# Загрузка базы данных реакторов
with open('assets/reactors.json', 'r') as f:
    REACTORS_DB = json.load(f)

# Словарь для хранения активных выводов
active_outputs = {}  # {reactor_id: {"message": message_text, "temps": {"current": [...], "target_temps": [...]}, "corrections": [...], "final_temps": [...]}}

# Словарь для хранения пользовательских диапазонов
user_ranges = {}  # {user_id: {'B': (min, max), 'C': (min, max), 'D': (min, max)}}

# Словарь для хранения пользовательских диапазонов для конкретных реакторов
reactor_specific_ranges = {}  # {user_id: {reactor_id: {'B': (min, max), 'C': (min, max), 'D': (min, max)}}}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сброс всех состояний при старте
    context.user_data.clear()
    
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

async def show_active_outputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_outputs:
        await update.message.reply_text("❌ Нет активных выводов")
        return
        
    keyboard = []
    for reactor_id in active_outputs:
        keyboard.append([InlineKeyboardButton(f"Реактор {reactor_id}", callback_data=f"show_{reactor_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите реактор:", reply_markup=reply_markup)

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
            'B': (DEFAULT_RANGES[0], DEFAULT_RANGES[1]),
            'C': (DEFAULT_RANGES[2], DEFAULT_RANGES[3]),
            'D': (DEFAULT_RANGES[4], DEFAULT_RANGES[5])
        }
    
    ranges = user_ranges[user_id]
    message = "🌐 Общие рабочие диапазоны:\n\n"
    message += f"Б: от {ranges['B'][0]:+.1f} до {ranges['B'][1]:+.1f}\n"
    message += f"Ц: от {ranges['C'][0]:+.1f} до {ranges['C'][1]:+.1f}\n"
    message += f"Д: от {ranges['D'][0]:+.1f} до {ranges['D'][1]:+.1f}"
    
    keyboard = [
        [
            InlineKeyboardButton("Б", callback_data="range_B"),
            InlineKeyboardButton("Ц", callback_data="range_C"),
            InlineKeyboardButton("Д", callback_data="range_D")
        ],
        [InlineKeyboardButton("Установить для всех зон сразу", callback_data="range_all")]
    ]
    
    # Добавляем кнопки для реакторов с особыми диапазонами
    if user_id in reactor_specific_ranges:
        message += "\n\n📍 Реакторы с особыми диапазонами:"
        for reactor_id in reactor_specific_ranges[user_id]:
            keyboard.append([
                InlineKeyboardButton(f"Реактор {reactor_id}", 
                                   callback_data=f"show_reactor_ranges_{reactor_id}")
            ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)

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
    """
    Получает режим работы реактора из базы данных
    Args:
        reactor_number: номер реактора в формате X-X или XX
    Returns:
        str: режим работы реактора из базы данных
    Raises:
        ValueError: если реактор не найден
    """
    # Проверяем сначала как ID
    if reactor_number in REACTORS_DB['reactors']:
        return REACTORS_DB['reactors'][reactor_number]['mode']
    
    # Если не нашли как ID, ищем как альтернативное значение
    for reactor_data in REACTORS_DB['reactors'].values():
        if reactor_data['alt'] == reactor_number:
            return reactor_data['mode']
            
    raise ValueError(f"Реактор {reactor_number} не найден в базе данных")

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

async def set_range_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['state'] = 'waiting_all_ranges'
    await query.message.reply_text(
        "Введите диапазоны для всех зон в формате:\n"
        "<code>Б_мин Б_макс Ц_мин Ц_макс Д_мин Д_макс</code>\n\n"
        "Например: <code>+2 0 +1 -1 0 -1</code>",
        parse_mode='HTML'
    )

async def process_all_ranges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        values = [float(x.replace(',', '.')) for x in update.message.text.strip().split()]
        if len(values) != 6:
            raise ValueError("❌ Необходимо ввести 6 чисел")
        
        user_id = update.effective_user.id
        
        # Определяем, устанавливаем ли диапазоны для конкретного реактора
        if 'setting_reactor_ranges' in context.user_data:
            reactor_id = context.user_data['setting_reactor_ranges']
            if user_id not in reactor_specific_ranges:
                reactor_specific_ranges[user_id] = {}
            
            reactor_specific_ranges[user_id][reactor_id] = {
                'B': (values[0], values[1]),
                'C': (values[2], values[3]),
                'D': (values[4], values[5])
            }
            del context.user_data['setting_reactor_ranges']
            await update.message.reply_text(f"✅ Диапазоны для реактора {reactor_id} установлены")
        else:
            # Устанавливаем общие диапазоны
            if user_id not in user_ranges:
                user_ranges[user_id] = {}
            
            user_ranges[user_id]['B'] = (values[0], values[1])
            user_ranges[user_id]['C'] = (values[2], values[3])
            user_ranges[user_id]['D'] = (values[4], values[5])
            
            if 'state' in context.user_data:
                del context.user_data['state']
            
            await update.message.reply_text("✅ Диапазоны установлены")
        
        await show_ranges(update, context)
        
    except ValueError as e:
        await update.message.reply_text(str(e))
        # При ошибке сбрасываем состояния
        context.user_data.clear()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if query.data.startswith("show_"):
            if query.data.startswith("show_reactor_ranges_"):
                reactor_id = query.data.split("_")[3]
                user_id = update.effective_user.id
                
                if user_id in reactor_specific_ranges and reactor_id in reactor_specific_ranges[user_id]:
                    ranges = reactor_specific_ranges[user_id][reactor_id]
                    message = f"📍 Особые диапазоны для реактора {reactor_id}:\n\n"
                    message += f"Б: от {ranges['B'][0]:+.1f} до {ranges['B'][1]:+.1f}\n"
                    message += f"Ц: от {ranges['C'][0]:+.1f} до {ranges['C'][1]:+.1f}\n"
                    message += f"Д: от {ranges['D'][0]:+.1f} до {ranges['D'][1]:+.1f}"
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("Изменить диапазоны", 
                                               callback_data=f"set_reactor_ranges_{reactor_id}"),
                            InlineKeyboardButton("Удалить особые диапазоны", 
                                               callback_data=f"delete_reactor_ranges_{reactor_id}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await query.message.reply_text(message, reply_markup=reply_markup)
            else:
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
                # Сохраняем mode перед очисткой
                current_mode = context.user_data.get('mode')
                
                context.user_data.clear()  # Сбрасываем все предыдущие состояния
                
                # Восстанавливаем mode и добавляем editing_reactor
                if current_mode:
                    context.user_data['mode'] = current_mode
                # Если mode не был в context.user_data, берем его из active_outputs
                elif 'mode' in active_outputs[reactor_id]:
                    context.user_data['mode'] = active_outputs[reactor_id]['mode']
                
                context.user_data['editing_reactor'] = reactor_id
                current_temps = active_outputs[reactor_id]["temps"]["current"]
                target_temps = active_outputs[reactor_id]["temps"]["target_temps"]
                
                await query.message.reply_text(
                    f"Реактор: <code>{reactor_id}</code>\n\n"
                    f"⌛️ Текущие температуры (Б Ц Д): <code>{' '.join(f'{temp:.1f}' for temp in current_temps)}</code>\n\n"
                    f"🌡 Температуры задания (Б Ц Д): <code>{' '.join(f'{temp:.1f}' for temp in target_temps)}</code>\n\n"
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
        
        elif query.data.startswith("set_reactor_ranges_"):
            reactor_id = query.data.split("_")[3]
            # Сохраняем mode перед очисткой
            current_mode = context.user_data.get('mode')
            context.user_data.clear()
            # Восстанавливаем mode если он был
            if current_mode:
                context.user_data['mode'] = current_mode
            context.user_data['setting_reactor_ranges'] = reactor_id
            await query.message.reply_text(
                f"Установка диапазонов для реактора {reactor_id}\n"
                "Введите диапазоны для всех зон в формате:\n"
                "<code>Б_мин Б_макс Ц_мин Ц_макс Д_мин Д_макс</code>\n\n"
                "Например: <code>+2 0 +1 -1 0 -1</code>",
                parse_mode='HTML'
            )
            
        elif query.data.startswith("delete_reactor_ranges_"):
            reactor_id = query.data.split("_")[3]
            user_id = update.effective_user.id
            
            if user_id in reactor_specific_ranges and reactor_id in reactor_specific_ranges[user_id]:
                del reactor_specific_ranges[user_id][reactor_id]
                if not reactor_specific_ranges[user_id]:  # Если это был последний реактор
                    del reactor_specific_ranges[user_id]
                
                await query.message.edit_text(
                    f"✅ Особые диапазоны для реактора {reactor_id} удалены"
                )
                # Показываем обновленный список диапазонов
                await show_ranges(update, context)
        
        elif query.data.startswith("range_"):
            # Сохраняем mode перед очисткой
            current_mode = context.user_data.get('mode')
            if query.data == "range_all":
                context.user_data.clear()  # Сбрасываем все предыдущие состояния
                # Восстанавливаем mode если он был
                if current_mode:
                    context.user_data['mode'] = current_mode
                await set_range_all(update, context)
            else:
                context.user_data.clear()  # Сбрасываем все предыдущие состояния
                # Восстанавливаем mode если он был
                if current_mode:
                    context.user_data['mode'] = current_mode
                await handle_range_callback(update, context)

    except Exception as e:
        # Общий обработчик ошибок
        await query.message.reply_text(f"❌ Произошла ошибка: {str(e)}", parse_mode='HTML')
        # При любой ошибке сбрасываем все состояния, кроме mode
        current_mode = context.user_data.get('mode')
        context.user_data.clear()
        if current_mode:
            context.user_data['mode'] = current_mode

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text

        # Если пользователь ввел одну из основных команд меню, сбрасываем все состояния
        if text in ["⚙️ Новый вывод", "🖥️ Текущие выводы", "🔧 Рабочие диапазоны зон"]:
            # Сброс всех состояний перед обработкой новой команды
            context.user_data.clear()
            
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
        
        elif context.user_data.get('state') == 'waiting_all_ranges':
            try:
                await process_all_ranges(update, context)
            except ValueError as e:
                await update.message.reply_text(f"❌ {str(e)}")
                # При ошибке сбрасываем состояние
                if 'state' in context.user_data:
                    del context.user_data['state']
        
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
                # При ошибке сбрасываем состояние редактирования
                if 'editing_range' in context.user_data:
                    del context.user_data['editing_range']
        
        elif context.user_data.get('state') == 'waiting_reactor_number':
            try:
                reactor_number = text.strip()
                
                if not validate_reactor_number(reactor_number):
                    await update.message.reply_text(
                        "❌ Неверный номер реактора.\n"
                        "Используйте формат X-X (например: <code>1-1</code>) или XX (например: <code>11</code>).",
                        parse_mode='HTML'
                    )
                    # При ошибке сбрасываем состояние
                    if 'state' in context.user_data:
                        del context.user_data['state']
                    return
                    
                reactor_id = get_reactor_id(reactor_number)
                if reactor_id in active_outputs:
                    await update.message.reply_text(
                        f"❌ Реактор <code>{reactor_id}</code> уже выводится!\n"
                        "Сначала завершите текущий вывод этого реактора.",
                        parse_mode='HTML'
                    )
                    # При ошибке сбрасываем состояние
                    if 'state' in context.user_data:
                        del context.user_data['state']
                    return
                    
                mode = get_reactor_mode(reactor_number)
                context.user_data['current_reactor'] = reactor_id
                context.user_data['mode'] = mode

                user_id = update.effective_user.id
                ranges_message = ""
                keyboard = []

                # Проверяем, установлены ли диапазоны для этого реактора
                if user_id in reactor_specific_ranges and reactor_id in reactor_specific_ranges[user_id]:
                    ranges = reactor_specific_ranges[user_id][reactor_id]
                    ranges_message = "\n📍 Для этого реактора установлены особые диапазоны:\n"
                    ranges_message += f"Б: от {ranges['B'][0]:+.1f} до {ranges['B'][1]:+.1f}\n"
                    ranges_message += f"Ц: от {ranges['C'][0]:+.1f} до {ranges['C'][1]:+.1f}\n"
                    ranges_message += f"Д: от {ranges['D'][0]:+.1f} до {ranges['D'][1]:+.1f}"
                else:
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "Установить рабочие диапазоны для этого реактора", 
                                callback_data=f"set_reactor_ranges_{reactor_id}"
                            )
                        ]
                    ]

                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                
                await update.message.reply_text(
                    f"Выбран реактор: <code>{reactor_id}</code>{ranges_message}\n\n"
                    "Введите температуры в формате:\n"
                    "[три значения температур] [температура задания]\n"
                    "Например: <code>1008.5 1003.7 1001.2 1000.0</code>\n"
                    "или: <code>1008,5 1003,7 1001,2 1000,0</code>\n"
                    "или: <code>1008.5 1003.7 1001.2 1040.0 1000.0 1000.0</code>\n"
                    "или: <code>1008,5 1003,7 1001,2 1040,0 1000,0 1000,0</code>",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                context.user_data['state'] = 'waiting_temperatures'
            except ValueError as e:
                await update.message.reply_text(f"❌ {str(e)}", parse_mode='HTML')
                # При ошибке сбрасываем состояние
                if 'state' in context.user_data:
                    del context.user_data['state']
        
        elif context.user_data.get('state') == 'waiting_temperatures' or 'editing_reactor' in context.user_data:
            try:
                reactor_id = context.user_data.get('editing_reactor') or context.user_data.get('current_reactor')
                await handle_temperatures(
                    update, 
                    context, 
                    reactor_id, 
                    active_outputs, 
                    user_ranges, 
                    reactor_specific_ranges
                )
                if 'editing_reactor' in context.user_data:
                    del context.user_data['editing_reactor']
            except ValueError as e:
                await update.message.reply_text(str(e), parse_mode='HTML')
                # При ошибке сбрасываем состояния
                if 'state' in context.user_data:
                    del context.user_data['state']
                if 'editing_reactor' in context.user_data:
                    del context.user_data['editing_reactor']
        
        elif 'setting_reactor_ranges' in context.user_data:
            try:
                values = [float(x.replace(',', '.')) for x in text.strip().split()]
                if len(values) != 6:
                    raise ValueError("❌ Необходимо ввести 6 чисел")
                
                user_id = update.effective_user.id
                reactor_id = context.user_data['setting_reactor_ranges']
                
                if user_id not in reactor_specific_ranges:
                    reactor_specific_ranges[user_id] = {}
                
                reactor_specific_ranges[user_id][reactor_id] = {
                    'B': (values[0], values[1]),
                    'C': (values[2], values[3]),
                    'D': (values[4], values[5])
                }
                
                # Получаем установленные диапазоны для отображения
                ranges = reactor_specific_ranges[user_id][reactor_id]
                ranges_message = "\n📍 Установлены следующие диапазоны:\n"
                ranges_message += f"Б: от {ranges['B'][0]:+.1f} до {ranges['B'][1]:+.1f}\n"
                ranges_message += f"Ц: от {ranges['C'][0]:+.1f} до {ranges['C'][1]:+.1f}\n"
                ranges_message += f"Д: от {ranges['D'][0]:+.1f} до {ranges['D'][1]:+.1f}"
                
                # Получаем и сохраняем режим работы реактора
                mode = get_reactor_mode(reactor_id)
                
                del context.user_data['setting_reactor_ranges']
                context.user_data['state'] = 'waiting_temperatures'
                context.user_data['current_reactor'] = reactor_id
                context.user_data['mode'] = mode  # Добавляем режим работы в context.user_data
                
                await update.message.reply_text(
                    f"✅ Диапазоны для реактора {reactor_id} установлены\n"
                    f"{ranges_message}\n\n"
                    "Введите температуры в формате:\n"
                    "[три значения температур] [температура задания]\n"
                    "Например: <code>1008.5 1003.7 1001.2 1000.0</code>\n"
                    "или: <code>1008,5 1003,7 1001,2 1000,0</code>\n"
                    "или: <code>1008.5 1003.7 1001.2 1040.0 1000.0 1000.0</code>\n"
                    "или: <code>1008,5 1003,7 1001,2 1040,0 1000,0 1000,0</code>",
                    parse_mode='HTML'
                )
                
            except ValueError as e:
                await update.message.reply_text(f"❌ {str(e)}")
                if 'setting_reactor_ranges' in context.user_data:
                    del context.user_data['setting_reactor_ranges']

    except Exception as e:
        # Общий обработчик ошибок
        await update.message.reply_text(f"❌ Произошла ошибка: {str(e)}", parse_mode='HTML')
        # При любой ошибке сбрасываем все состояния
        context.user_data.clear()

def main():
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()

import platform, os, sys, json, re
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

def load_reactors_db():
    """Загружает и проверяет базу данных реакторов"""
    global REACTORS_DB
    try:
        with open('assets/reactors.json', 'r', encoding='utf-8') as f:
            REACTORS_DB = json.load(f)
            
        # Проверяем и нормализуем данные
        for reactor_id, data in REACTORS_DB['reactors'].items():
            # Убеждаемся, что alt это список
            if isinstance(data['alt'], str):
                data['alt'] = [data['alt']]
            
            # Нормализуем строки (убираем невидимые символы)
            data['id'] = data['id'].strip()
            data['alt'] = [alt.strip() for alt in data['alt']]
            
    except Exception as e:
        raise

# Используем функцию для загрузки базы
load_reactors_db()

# Словарь для хранения активных выводов
active_outputs = {}  # {reactor_id: {"message": message_text, "temps": {"current": [...], "target_temps": [...]}, "corrections": [...], "final_temps": [...]}}

# Словарь для хранения пользовательских диапазонов
user_ranges = {}  # {user_id: {'B': (min, max), 'C': (min, max), 'D': (min, max)}}

# Словарь для хранения пользовательских диапазонов для конкретных реакторов
reactor_specific_ranges = {}  # {user_id: {reactor_id: {'B': (min, max), 'C': (min, max), 'D': (min, max)}}}

def is_valid_format(reactor_number: str) -> bool:
    """
    Проверяет, соответствует ли номер реактора одному из допустимых форматов.
    
    Допустимые форматы:
    - ТМ-Н, ТМ-В (заглавные с дефисом)
    - ТМН, ТМВ (заглавные без дефиса)
    - тм-н, тм-в (строчные с дефисом)
    - тмн, тмв (строчные без дефиса)
    - Тм-н, Тм-в (с заглавной первой буквой, с дефисом)
    - Тмн, Тмв (с заглавной первой буквой, без дефиса)
    - YY-Y (где Y - цифры)
    - YYY (где Y - цифры)
    - Y-Y (где Y - цифры)
    - YY (где Y - цифры)
    """
    if not reactor_number:
        return False
        
    # Буквенные специальные паттерны для ТМ-Н и ТМ-В
    special_patterns = [
        r'^ТМ-[НВ]$',      # ТМ-Н или ТМ-В
        r'^ТМ[НВ]$',       # ТМН или ТМВ
        r'^тм-[нв]$',      # тм-н или тм-в
        r'^тм[нв]$',       # тмн или тмв
        r'^Тм-[нв]$',      # Тм-н или Тм-в
        r'^Тм[нв]$'        # Тмн или Тмв
    ]
    
    # Числовые паттерны
    number_patterns = [
        r'^\d{2}-\d$',    # YY-Y (например, 11-1)
        r'^\d{3}$',       # YYY (например, 111)
        r'^\d-\d$',       # Y-Y (например, 1-1)
        r'^\d{2}$'        # YY (например, 11)
    ]
    
    # Проверяем специальные буквенные паттерны
    for pattern in special_patterns:
        if re.match(pattern, reactor_number):
            return True
            
    # Проверяем числовые паттерны
    for pattern in number_patterns:
        if re.match(pattern, reactor_number):
            return True
    
    return False

def get_reactor_id(reactor_number: str) -> str:
    """
    Получает ID реактора по введенному номеру.
    Args:
        reactor_number: номер реактора
    Returns:
        str: ID реактора из базы данных
    """
    reactor_number = reactor_number.strip()
    
    # Если это ID, возвращаем его
    if reactor_number in REACTORS_DB['reactors']:
        return reactor_number

    # Если это альтернативное значение, ищем соответствующий ID
    for reactor_id, reactor_data in REACTORS_DB['reactors'].items():
        alt_values = reactor_data['alt']
        if not isinstance(alt_values, list):
            alt_values = [alt_values]
            
        # Проверяем каждое альтернативное значение
        for alt in alt_values:
            if reactor_number == alt.strip():
                return reactor_id
                
    return reactor_number

def validate_reactor_number(reactor_number: str) -> bool:
    """
    Проверяет существование номера реактора в базе данных.
    """
    if not reactor_number:
        raise ValueError("❌ Номер реактора не может быть пустым")
        
    reactor_number = reactor_number.strip()
    
    # Проверяем формат
    if not is_valid_format(reactor_number):
        raise ValueError(
            "❌ Неверный формат номера реактора.\n"
            "Допустимые форматы:\n"
            "• Буквенные: ТМ-Н, ТМН, тм-н, тмн\n"
            "• Цифровые: Y-Y, YY-Y, YY или YYY (например: 1-1, 11-1, 11 или 111)"
        )
    
    # Пробуем найти в базе как есть
    if reactor_number in REACTORS_DB['reactors']:
        return True
        
    # Проверяем варианты из alt
    for reactor_id, reactor_data in REACTORS_DB['reactors'].items():
        alt = reactor_data['alt']
        if isinstance(alt, list) and reactor_number in alt:
            return True
        elif isinstance(alt, str) and reactor_number == alt:
            return True
    
    raise ValueError(
        "❌ Указанный номер реактора не найден в базе данных.\n"
        "Проверьте правильность ввода номера."
    )

def get_reactor_mode(reactor_number: str) -> str:
    """
    Получает режим работы реактора из базы данных
    Args:
        reactor_number: номер реактора
    Returns:
        str: режим работы реактора из базы данных
    """
    reactor_id = get_reactor_id(reactor_number)
    
    if reactor_id in REACTORS_DB['reactors']:
        return REACTORS_DB['reactors'][reactor_id]['mode']
        
    raise ValueError(
        "❌ Указанный номер реактора не найден в базе данных.\n"
        "Проверьте правильность ввода номера."
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сброс всех состояний при старте
    context.user_data.clear()
    
    keyboard = [
        [KeyboardButton("⚙️ Новый вывод")],
        [KeyboardButton("🖥️ Текущие выводы")],
        [KeyboardButton("🔧 Рабочие диапазоны зон")],
        [KeyboardButton("ℹ️ Инструкция по использованию")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '💡 Выберите действие:',
        reply_markup=reply_markup
    )

async def show_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⚙️ Новый вывод", callback_data="instruction_new_output")],
        [InlineKeyboardButton("🖥️ Текущие выводы", callback_data="instruction_current_outputs")],
        [InlineKeyboardButton("🔧 Рабочие диапазоны зон", callback_data="instruction_ranges")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🤖 <b>Выберите раздел инструкции:</b>"
    
    if update.callback_query:
        # Если функция вызвана через callback (кнопка "Назад")
        await update.callback_query.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        # Если функция вызвана напрямую
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def show_new_output_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instructions = """
<b>⚙️ Инструкция: Новый вывод</b>

<b>1️⃣ <u>Шаг 1: Ввод номера реактора</u></b>
🔻 Поддерживаемые форматы:
<blockquote>▪️ Буквенные: <code>Тмн</code>, <code>ТМН</code>, <code>тмн</code> (упрощённые варианты ввода канала <code>ТМ-Н</code>). Также можно ввести как <code>Тм-н</code> и <code>тм-н</code>
▪️ Цифровые: <code>52</code>, <code>132</code> (упрощённые варианты ввода каналов <code>5-2</code> и <code>13-2</code>)

▫️ Пояснение: Если нужно ввести канал <code>13-2</code>, то можно использовать упрощёенный вариант ввода: <code>132</code>. Если нужен канал <code>5-2</code>, то можно ввести <code>52</code> соответствующе.</blockquote>

<b>2️⃣ <u>Шаг 2: Установка рабочих диапазонов (опционально)</u></b>
🔻 После ввода номера реактора вы можете:
<blockquote>▪️ <b>Установить особые диапазоны для этого реактора</b> - нажмите кнопку "Установить рабочие диапазоны для этого реактора"
▪️ Использовать общие диапазоны - продолжите ввод температур без установки особых диапазонов</blockquote>

• При нажатии кнопки "Установить рабочие диапазоны для этого реактора":
<blockquote>1. Введите 6 чисел в формате:
   <code>Б_мин Б_макс Ц_мин Ц_макс Д_мин Д_макс</code>
2. Пример ввода: <code>+2 0 +1 -1 0 -1</code>
3. После ввода диапазонов бот покажет установленные значения
4. Затем вы сможете продолжить ввод температур</blockquote>

<b>3️⃣ <u>Шаг 3: Ввод температур</u></b>
🔻 Форматы ввода:
<blockquote>▪️ Базовый: <code>[три текущих температуры] [одна температура задания]</code>
▪️ Расширенный: <code>[три текущих температуры] [три температуры задания]</code>
🔹 Примеры:
▪️ <code>1008.5 1003.7 1001.2 1000.0</code>
▪️ <code>1008,5 1003,7 1001,2 1000,0</code>
▪️ <code>1008.5 1003.7 1001.2 1040.0 1000.0 1000.0</code></blockquote>

<b>4️⃣ <u>Шаг 4: После расчета корректировок</u></b>
🔻 Бот покажет:
<blockquote>▪️ Номер реактора
▪️ Текущие температуры по зонам (Б Ц Д)
▪️ Заданные температуры
▪️ Используемые диапазоны (общие или особые)
▪️ Необходимые корректировки для каждой зоны
▪️ Предполагаемые температуры после корректировок</blockquote>

• Доступные действия:
<blockquote>▪️ <b>"Завершить вывод канала"</b> - если работа с реактором закончена
▪️ <b>"Отредактировать вводимые температуры"</b> - если нужно ввести новые текущие температуры</blockquote>
"""
    keyboard = [[InlineKeyboardButton("◀️ Назад к разделам", callback_data="back_to_instructions")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(
        instructions,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def show_current_outputs_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instructions = """
<b>🖥️ Инструкция: Текущие выводы</b>

<b>1️⃣ <u>Шаг 1: Просмотр активных реакторов</u></b>
🔹 Показывает список всех активных реакторов

<b>2️⃣ <u>Шаг 2: Информация о реакторе</u></b>
🔹 При выборе реактора отображается:
<blockquote>▪️ Номер реактора
▪️ Текущие температуры по зонам (Б Ц Д)
▪️ Температуры задания
▪️ Используемые диапазоны (общие или особые)
▪️ Необходимые корректировки по зонам
▪️ Прогноз температур после корректировок</blockquote>

<b>3️⃣ <u>Шаг 3: Доступные действия</u></b>
🔹 Для каждого реактора можно:
<blockquote>▪️ <b>"Завершить вывод канала"</b> - удаляет реактор из активных выводов
▪️ <b>"Отредактировать вводимые температуры"</b> - позволяет ввести новые текущие температуры</blockquote>
"""
    keyboard = [[InlineKeyboardButton("◀️ Назад к разделам", callback_data="back_to_instructions")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(
        instructions,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def show_ranges_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instructions = """
<b>🔧 Инструкция: Рабочие диапазоны зон</b>

<b>1️⃣ <u>Шаг 1: Общие диапазоны</u></b>
🔹 Установка для отдельных зон:
<blockquote>▪️ Нажмите на кнопку зоны (<b>Б</b>, <b>Ц</b> или <b>Д</b>)
▪️ Введите два числа: <code>мин макс</code>

Пример: <code>+2 0</code></blockquote>

<b>2️⃣ <u>Шаг 2: Установка всех диапазонов</u></b>
🔹 Нажмите <b>"Установить для всех зон сразу"</b>
🔹 Введите 6 чисел в формате:
<blockquote><code>Б_мин Б_макс Ц_мин Ц_макс Д_мин Д_макс</code>
🔹 Пример: <code>+2 0 +1 -1 0 -1</code></blockquote>

<b>3️⃣ <u>Шаг 3: Особые диапазоны для реактора</u></b>
🔹 Просмотр и управление:
<blockquote>▪️ В списке диапазонов нажмите на реактор для просмотра его особых диапазонов
▪️ При просмотре доступны действия:
  🔸 <b>"Изменить диапазоны"</b> - установка новых особых диапазонов
  🔸 <b>"Удалить особые диапазоны"</b> - возврат к общим диапазонам</blockquote>

<b>4️⃣ <u>Шаг 4: Просмотр установленных диапазонов</u></b>
🔹 Отображаемая информация:
<blockquote>▪️ Текущие особые диапазоны для зон Б, Ц, Д
▪️ Минимальные и максимальные значения для каждой зоны</blockquote>
"""
    keyboard = [[InlineKeyboardButton("◀️ Назад к разделам", callback_data="back_to_instructions")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(
        instructions,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def handle_instruction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "instruction_new_output":
        await show_new_output_instruction(update, context)
    elif query.data == "instruction_current_outputs":
        await show_current_outputs_instruction(update, context)
    elif query.data == "instruction_ranges":
        await show_ranges_instruction(update, context)
    elif query.data == "back_to_instructions":
        await show_instructions(update, context)

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

async def handle_range_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    zone = query.data.split('_')[1]
    context.user_data['editing_range'] = zone
    
    await query.message.reply_text(
        f"Введите диапазон для зоны {zone} в формате:\n"
        f"<code>мин макс</code>\n\n"
        f"Например: <code>+2 0</code>",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_ranges")]])
    )

async def set_range_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['state'] = 'waiting_all_ranges'
    await query.message.reply_text(
        "Введите диапазоны для всех зон в формате:\n"
        "<code>Б_мин Б_макс Ц_мин Ц_макс Д_мин Д_макс</code>\n\n"
        "Например: <code>+2 0 +1 -1 0 -1</code>",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_ranges")]])
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
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_ranges")]])
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
                
        elif query.data == "back_to_ranges":
            # Сохраняем режим работы перед очисткой состояний
            mode = context.user_data.get('mode')
            context.user_data.clear()
            # Восстанавливаем режим работы если он был
            if mode:
                context.user_data['mode'] = mode
            # Показываем меню диапазонов
            await show_ranges(update, context)

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
        if text in ["⚙️ Новый вывод", "🖥️ Текущие выводы", "🔧 Рабочие диапазоны зон", "ℹ️ Инструкция по использованию"]:
            # Сохраняем важные данные перед сбросом
            mode = context.user_data.get('mode')
            current_reactor = context.user_data.get('current_reactor')
            # Сброс всех состояний перед обработкой новой команды
            context.user_data.clear()
            # Восстанавливаем режим работы если он был
            if mode:
                context.user_data['mode'] = mode
            if current_reactor:
                context.user_data['current_reactor'] = current_reactor
            
        if text == "⚙️ Новый вывод":
            await update.message.reply_text(
                "✍️ Введите номер реактора:\n\n"
                "<blockquote>Допустимые форматы:\n\n"
                "• Буквенные: <code>XX-X</code> или <code>XXX</code> (например: <code>ТМ-Н</code> или <code>ТМН</code>)\n"
                "• Цифровые: <code>Y-Y</code>, <code>YY-Y</code>, <code>YY</code> или <code>YYY</code>\n"
                "(например: <code>1-1</code>, <code>11-1</code>, <code>11</code> или <code>111</code>)</blockquote>",
                parse_mode='HTML'
            )
            context.user_data['state'] = 'waiting_reactor_number'
        
        elif text == "🖥️ Текущие выводы":
            await show_active_outputs(update, context)
            
        elif text == "🔧 Рабочие диапазоны зон":
            await show_ranges(update, context)
        
        elif text == "ℹ️ Инструкция по использованию":
            await show_instructions(update, context)
        
        elif context.user_data.get('state') == 'waiting_all_ranges':
            try:
                await process_all_ranges(update, context)
            except ValueError as e:
                await update.message.reply_text(f"❌ {str(e)}")
                # Сохраняем важные данные перед обработкой ошибки
                mode = context.user_data.get('mode')
                # Сохраняем состояние ожидания диапазонов
                context.user_data['state'] = 'waiting_all_ranges'
                if mode:
                    context.user_data['mode'] = mode
        
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
                # Сохраняем данные о редактируемой зоне
                zone = context.user_data.get('editing_range')
                mode = context.user_data.get('mode')
                if zone:
                    context.user_data['editing_range'] = zone
                if mode:
                    context.user_data['mode'] = mode
        
        elif context.user_data.get('state') == 'waiting_reactor_number':
            try:
                reactor_number = text.strip()
                
                if not validate_reactor_number(reactor_number):
                    await update.message.reply_text(
                        "❌ Неверный номер реактора.\n"
                        "Допустимые форматы:\n"
                        "• Буквенные: XX-X или XXX (например: ТМ-Н или ТМН)\n"
                        "• Цифровые: Y-Y, YY-Y, YY или YYY (например: 1-1, 11-1, 11 или 111)",
                        parse_mode='HTML'
                    )
                    # Сохраняем состояние ожидания номера реактора
                    context.user_data['state'] = 'waiting_reactor_number'
                    return
                    
                reactor_id = get_reactor_id(reactor_number)
                if reactor_id in active_outputs:
                    await update.message.reply_text(
                        f"❌ Реактор <code>{reactor_id}</code> уже выводится!\n"
                        "Сначала завершите текущий вывод этого реактора.",
                        parse_mode='HTML'
                    )
                    # Сохраняем состояние ожидания номера реактора
                    context.user_data['state'] = 'waiting_reactor_number'
                    return
                    
                mode = get_reactor_mode(reactor_number)
                context.user_data['current_reactor'] = reactor_id
                context.user_data['mode'] = mode

                user_id = update.effective_user.id
                ranges_message = ""
                keyboard = []

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
                # Сохраняем важные данные и состояние
                mode = context.user_data.get('mode')
                context.user_data['state'] = 'waiting_reactor_number'
                if mode:
                    context.user_data['mode'] = mode
        
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
                # Сохраняем важные данные перед обработкой ошибки
                mode = context.user_data.get('mode')
                current_reactor = context.user_data.get('current_reactor')
                editing_reactor = context.user_data.get('editing_reactor')
                
                # Сохраняем состояние ожидания температур
                context.user_data['state'] = 'waiting_temperatures'
                
                # Восстанавливаем необходимые данные
                if mode:
                    context.user_data['mode'] = mode
                if current_reactor:
                    context.user_data['current_reactor'] = current_reactor
                if editing_reactor:
                    context.user_data['editing_reactor'] = editing_reactor
        
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
                context.user_data['mode'] = mode
                
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
                # Сохраняем важные данные перед обработкой ошибки
                mode = context.user_data.get('mode')
                reactor_id = context.user_data.get('setting_reactor_ranges')
                if mode:
                    context.user_data['mode'] = mode
                if reactor_id:
                    context.user_data['setting_reactor_ranges'] = reactor_id

    except Exception as e:
        # Общий обработчик ошибок
        await update.message.reply_text(f"❌ Произошла ошибка: {str(e)}", parse_mode='HTML')
        # Сохраняем важные данные перед обработкой общей ошибки
        mode = context.user_data.get('mode')
        current_reactor = context.user_data.get('current_reactor')
        editing_reactor = context.user_data.get('editing_reactor')
        current_state = context.user_data.get('state')
        
        # Очищаем состояния но сохраняем критически важные данные
        context.user_data.clear()
        
        # Восстанавливаем необходимые данные
        if mode:
            context.user_data['mode'] = mode
        if current_reactor:
            context.user_data['current_reactor'] = current_reactor
        if editing_reactor:
            context.user_data['editing_reactor'] = editing_reactor
        if current_state:
            context.user_data['state'] = current_state

def main():
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_instruction_callback, 
                          pattern="^(instruction_new_output|instruction_current_outputs|instruction_ranges|back_to_instructions)$"))
    application.add_handler(CallbackQueryHandler(handle_callback))  # Общий обработчик для остальных колбэков
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()

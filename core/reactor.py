from typing import Tuple, List, Dict, Any
import os
import numpy as np
from scipy.optimize import minimize
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from dotenv import load_dotenv
import datetime

load_dotenv()

DISTANCE = float(os.getenv('DISTANCE'))
PC_MAX_DEVIATION = float(os.getenv('PC_MAX_DEVIATION'))
PC_CHARACTERISTIC_LENGTH = float(os.getenv('PC_CHARACTERISTIC_LENGTH'))
BPRT_MAX_DEVIATION = float(os.getenv('BPRT_MAX_DEVIATION'))
BPRT_CHARACTERISTIC_LENGTH = float(os.getenv('BPRT_CHARACTERISTIC_LENGTH'))

# Значения по умолчанию для диапазонов зон (формат: "Б_мин Б_макс Ц_мин Ц_макс Д_мин Д_макс")
DEFAULT_RANGES = [float(x) for x in os.getenv('DEFAULT_RANGES', '2 0 1 -1 0 -1').split()]

def parse_temperature(input_str: str) -> float:
    try:
        return float(input_str.replace(',', '.'))
    except ValueError:
        raise ValueError("❌ Неверный формат числа. Используйте точку или запятую для разделения десятичных знаков.")

def parse_temperatures(input_str: str, editing_mode: bool = False, target_temp: float = None) -> Tuple[List[float], List[float]]:
    """
    Парсинг введенных температур с учетом режима редактирования.
    
    Args:
        input_str (str): Строка с температурами
        editing_mode (bool): Режим редактирования (по умолчанию False)
        target_temp (float): Целевая температура для режима редактирования (по умолчанию None)
        
    Returns:
        Tuple[List[float], List[float]]: Кортеж из двух списков:
            - текущие температуры [Б, Ц, Д]
            - целевые температуры [целевая_Б, целевая_Ц, целевая_Д]
            
    Raises:
        ValueError: При неверном формате ввода или количестве значений
    """
    try:
        if editing_mode and target_temp is not None:
            try:
                # Парсинг только текущих температур в режиме редактирования
                current_temps = [parse_temperature(temp) for temp in input_str.strip().split()]
                if len(current_temps) != 3:
                    raise ValueError("❌ Необходимо ввести три значения текущих температур.")
                
                return current_temps, [target_temp] * 3
                
            except ValueError as e:
                raise ValueError("❌ Неверный формат температур в режиме редактирования.")
        else:
            try:
                # Парсинг всех температур (текущих и целевых)
                values = [parse_temperature(temp) for temp in input_str.strip().split()]
                
                # Проверка количества значений
                if len(values) not in [4, 6]:
                    raise ValueError(
                        "❌ Необходимо ввести или 4 значения (три текущих температуры и одну температуру задания), "
                        "или 6 значений (три текущих температуры и три температуры задания)."
                    )
                
                if len(values) == 4:
                    current_temps = values[:3]
                    target_temp = values[3]
                    return current_temps, [target_temp] * 3
                else:  # len(values) == 6
                    current_temps = values[:3]
                    target_temps = values[3:]
                    return current_temps, target_temps
                    
            except ValueError as e:
                if "Необходимо ввести" in str(e):
                    raise e
                raise ValueError("❌ Неверный формат температур.")
            
    except ValueError as e:
        # Формируем информативное сообщение об ошибке с примерами
        error_message = str(e)
        if editing_mode:
            error_message += "\nФормат ввода: [три значения текущих температур]\n"
            error_message += "Например: <code>1008.5 1003.7 1001.2</code>\n"
            error_message += "или: <code>1008,5 1003,7 1001,2</code>"
        else:
            error_message += "\nФормат ввода: [три значения текущих температур] [температура задания]\n"
            error_message += "Например:\n"
            error_message += "<code>1008.5 1003.7 1001.2 1000.0</code>\n"
            error_message += "или: <code>1008,5 1003,7 1001,2 1000,0</code>\n"
            error_message += "или: <code>1008.5 1003.7 1001.2 1040.0 1000.0 1000.0</code>\n"
            error_message += "или: <code>1008,5 1003,7 1001,2 1040,0 1000,0 1000,0</code>"
        raise ValueError(error_message)

def custom_round(value: float) -> str:
    if abs(value) < 0.25:
        return "не корректировать"
        
    integer_part = int(round(value))
    half_rounded = round(value * 2) / 2
    
    if abs(value - integer_part) < abs(value - half_rounded):
        return str(integer_part)
    else:
        return f"{half_rounded:.1f}" if half_rounded % 1 != 0 else str(int(half_rounded))

class ThermalReactor:
    def __init__(self, mode: str, user_ranges_dict=None):
        if mode not in ['pc', 'bprt']:
            raise ValueError("Некорректный режим работы")
            
        self.DISTANCE = DISTANCE
        self.mode = mode
        self.MAX_DEVIATION = PC_MAX_DEVIATION if mode == 'pc' else BPRT_MAX_DEVIATION
        self.initial_temps = None
        self.TARGET_TEMP = None
        self.TARGET_TEMPS = None
        self.user_ranges = None
        self.user_ranges_dict = user_ranges_dict
        self.input_state = {
            'waiting_for_correction': False,
            'last_error': None,
            'last_input': None,
            'retry_count': 0
        }
        
        # Разные коэффициенты теплопередачи для разных режимов
        if mode == 'pc':
            self.heat_transfer_coef = np.exp(-self.DISTANCE / PC_CHARACTERISTIC_LENGTH)
        else:  # bprt
            self.heat_transfer_coef = np.exp(-self.DISTANCE / BPRT_CHARACTERISTIC_LENGTH)

    def get_influence_matrix(self):
        """Возвращает матрицу влияния зон"""
        return np.array([
            [1.0, 1.0, self.heat_transfer_coef**5],
            [self.heat_transfer_coef, 1.0, self.heat_transfer_coef],
            [self.heat_transfer_coef**5, 1.0, 1.0]
        ])

    def set_temperatures(self, current_temps: List[float], target_temps: List[float], user_id: int = None):
        try:
            self.initial_temps = np.array(current_temps)
            self.TARGET_TEMP = target_temps[0]  # для совместимости используем первое значение
            self.TARGET_TEMPS = np.array(target_temps)
            
            if self.user_ranges_dict and user_id in self.user_ranges_dict:
                self.user_ranges = self.user_ranges_dict[user_id]
            else:
                # Используем значения по умолчанию из env
                self.user_ranges = {
                    'B': [DEFAULT_RANGES[0], DEFAULT_RANGES[1]],
                    'C': [DEFAULT_RANGES[2], DEFAULT_RANGES[3]],
                    'D': [DEFAULT_RANGES[4], DEFAULT_RANGES[5]]
                }
            
            # Сброс состояния ввода при успешной установке температур
            self.reset_input_state()
            
        except (ValueError, TypeError) as e:
            self.input_state['waiting_for_correction'] = True
            self.input_state['last_error'] = str(e)
            self.input_state['retry_count'] += 1
            raise ValueError(f"Ошибка при установке температур: {str(e)}\nПожалуйста, попробуйте ввести значения снова.")

    def reset_input_state(self):
        """Сброс состояния ввода"""
        self.input_state = {
            'waiting_for_correction': False,
            'last_error': None,
            'last_input': None,
            'retry_count': 0
        }

    def calculate_corrections(self, final_temps: np.ndarray) -> np.ndarray:
        try:
            corrections = np.zeros(3)
            current_temps = self.initial_temps

            if self.user_ranges:
                influence_matrix = self.get_influence_matrix()
                
                # 1. Центральная зона (Ц)
                current_c = current_temps[1]
                target_c = self.TARGET_TEMPS[1]
                c_upper = target_c + max(self.user_ranges['C'])
                c_lower = target_c + min(self.user_ranges['C'])
                
                if current_c > c_upper:
                    c_target = c_upper
                elif current_c < c_lower:
                    c_target = c_lower
                else:
                    c_steps = np.arange(c_lower, c_upper + 0.5, 0.5)
                    c_target = c_steps[np.abs(c_steps - current_c).argmin()]
                
                initial_c_correction = round((c_target - current_c) * 2) / 2
                
                # 2. Краевые зоны с учетом влияния центра
                targets = []
                for zone, idx in [('B', 0), ('C', 1), ('D', 2)]:
                    if idx == 1:  # Центральная зона
                        targets.append(c_target)
                        continue
                        
                    current = current_temps[idx]
                    target = self.TARGET_TEMPS[idx]
                    upper = target + max(self.user_ranges[zone])
                    lower = target + min(self.user_ranges[zone])
                    
                    if current > upper:
                        target = upper
                    elif current < lower:
                        target = lower
                    else:
                        steps = np.arange(lower, upper + 0.5, 0.5)
                        target = steps[np.abs(steps - current).argmin()]
                    targets.append(target)
                
                # 3. Решаем систему уравнений для получения корректировок
                targets = np.array(targets)
                corrections = np.linalg.solve(influence_matrix, targets - current_temps)
                
                # Округляем корректировки до ближайших 0.5
                corrections = np.round(corrections * 2) / 2

            return corrections
            
        except Exception as e:
            self.input_state['waiting_for_correction'] = True
            self.input_state['last_error'] = str(e)
            raise ValueError(f"Ошибка при расчете корректировок: {str(e)}")

    def calculate_temperature_changes(self, corrections: np.ndarray) -> np.ndarray:
        try:
            influence_matrix = self.get_influence_matrix()
            temperature_changes = np.dot(influence_matrix, corrections)
            final_temps = self.initial_temps + temperature_changes
            return final_temps
            
        except Exception as e:
            self.input_state['waiting_for_correction'] = True
            self.input_state['last_error'] = str(e)
            raise ValueError(f"Ошибка при расчете изменений температуры: {str(e)}")

    def objective_function(self, corrections: np.ndarray) -> float:
        final_temps = self.calculate_temperature_changes(corrections)
        return np.sum((final_temps - self.TARGET_TEMPS) ** 2)

    def optimize_temperatures(self) -> Tuple[np.ndarray, np.ndarray]:
        try:
            if self.user_ranges:
                corrections = self.calculate_corrections(self.initial_temps)
                final_temps = self.calculate_temperature_changes(corrections)
                return corrections, final_temps
            else:
                initial_guess = np.zeros(3)
                result = minimize(
                    self.objective_function,
                    initial_guess,
                    method='SLSQP',
                    options={'ftol': 1e-8, 'maxiter': 1000}
                )
                
                if not result.success:
                    raise ValueError("Оптимизация не сошлась!")
                
                corrections = result.x
                final_temps = self.calculate_temperature_changes(corrections)
                return corrections, final_temps
                
        except Exception as e:
            self.input_state['waiting_for_correction'] = True
            self.input_state['last_error'] = str(e)
            raise ValueError(f"Ошибка при оптимизации температур: {str(e)}")

    def get_input_state(self) -> dict:
        """Получение текущего состояния ввода"""
        return self.input_state.copy()

    def is_waiting_for_correction(self) -> bool:
        """Проверка, ожидается ли корректировка ввода"""
        return self.input_state['waiting_for_correction']

    def get_last_error(self) -> str:
        """Получение последней ошибки"""
        return self.input_state['last_error']

async def handle_temperatures(update: Update, context: ContextTypes.DEFAULT_TYPE, reactor_id: str, active_outputs: Dict[str, Any], user_ranges_dict: Dict, reactor_specific_ranges_dict: Dict):
    try:
        editing_mode = 'editing_reactor' in context.user_data
        target_temps = active_outputs.get(reactor_id, {}).get('temps', {}).get('target_temps') if editing_mode else None
        
        current_temps, target_temps = parse_temperatures(
            update.message.text, 
            editing_mode=editing_mode,
            target_temp=target_temps[0] if target_temps else None
        )
        
        # Если это режим редактирования, берем mode из активного вывода
        if editing_mode:
            # Сохраняем режим в context.user_data если его там нет
            if 'mode' not in context.user_data and reactor_id in active_outputs:
                context.user_data['mode'] = active_outputs[reactor_id].get('mode')
        
        if 'mode' not in context.user_data:
            raise ValueError("❌ Сначала выберите реактор")
            
        mode = context.user_data['mode']
        user_id = update.effective_user.id
        
        # Проверяем наличие особых диапазонов для реактора
        if (user_id in reactor_specific_ranges_dict and 
            reactor_id in reactor_specific_ranges_dict[user_id]):
            # Используем особые диапазоны для реактора
            ranges = reactor_specific_ranges_dict[user_id][reactor_id]
            ranges_info = "\n📍 Используются особые диапазоны реактора:\n"
        else:
            # Используем пользовательские диапазоны или DEFAULT_RANGES
            if user_id in user_ranges_dict and user_ranges_dict[user_id]:
                ranges = user_ranges_dict[user_id]
                ranges_info = "\n🌐 Используются общие диапазоны:\n"
            else:
                # Преобразуем DEFAULT_RANGES (список из 6 чисел) в словарь с диапазонами
                ranges = {
                    'B': (DEFAULT_RANGES[0], DEFAULT_RANGES[1]),  # Первая пара чисел для зоны Б
                    'C': (DEFAULT_RANGES[2], DEFAULT_RANGES[3]),  # Вторая пара чисел для зоны Ц
                    'D': (DEFAULT_RANGES[4], DEFAULT_RANGES[5])   # Третья пара чисел для зоны Д
                }
                ranges_info = "\n🌐 Используются стандартные диапазоны:\n"

        # Создаем словарь с диапазонами для пользователя
        ranges_dict = {user_id: ranges}

        # Создаем экземпляр реактора с выбранными диапазонами
        reactor = ThermalReactor(mode=mode, user_ranges_dict=ranges_dict)
        reactor.set_temperatures(current_temps, target_temps, user_id)
        
        corrections, final_temps = reactor.optimize_temperatures()
        
        keyboard = [
            [
                InlineKeyboardButton("Завершить вывод канала", callback_data=f"finish_{reactor_id}"),
                InlineKeyboardButton("Отредактировать вводимые температуры", callback_data=f"edit_{reactor_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Добавляем информацию об используемых диапазонах
        ranges_info += f"Б: от {ranges['B'][0]:+.1f} до {ranges['B'][1]:+.1f}\n"
        ranges_info += f"Ц: от {ranges['C'][0]:+.1f} до {ranges['C'][1]:+.1f}\n"
        ranges_info += f"Д: от {ranges['D'][0]:+.1f} до {ranges['D'][1]:+.1f}"
        
        message = (
            f"Реактор: <code>{reactor_id}</code>\n\n"
            f"⌛️ Текущие температуры (Б Ц Д): <code>{' '.join(f'{temp:.1f}' for temp in current_temps)}</code>\n\n"
            f"🌡 Температуры задания (Б Ц Д): <code>{' '.join(f'{temp:.1f}' for temp in target_temps)}</code>\n"
            f"{ranges_info}\n\n"
            f"🔧 Нужно откорректировать:\n\n"
        )
        
        for zone, corr in zip(["Б:", "Ц:", "Д:"], corrections):
            rounded_corr = custom_round(corr)
            if rounded_corr == "не корректировать":
                message += f"{zone} <code>{rounded_corr}</code>\n"
            else:
                sign = "+" if corr >= 0 else ""
                message += f"{zone} <code>{sign}{rounded_corr}°C</code>\n"

        message += f"\n🌡 Предположительная температура после корректировок: <code>{' '.join(f'{temp:.1f}°C' for temp in final_temps)}</code>"
        
        # Сохраняем режим в active_outputs
        active_outputs[reactor_id] = {
            "message": message,
            "temps": {
                "current": current_temps,
                "target_temps": target_temps
            },
            "corrections": corrections.tolist(),
            "final_temps": final_temps.tolist(),
            "mode": mode  # Добавляем режим в сохраняемые данные
        }
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
        # Не удаляем mode из context.user_data при редактировании
        if 'state' in context.user_data and not editing_mode:
            del context.user_data['state']
            
    except ValueError as e:
        raise ValueError(str(e))

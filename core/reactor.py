from typing import Tuple, List, Dict, Any
import os
import numpy as np
from scipy.optimize import minimize
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from dotenv import load_dotenv

load_dotenv()

DISTANCE = float(os.getenv('DISTANCE'))
PC_MAX_DEVIATION = float(os.getenv('PC_MAX_DEVIATION'))
PC_CHARACTERISTIC_LENGTH = float(os.getenv('PC_CHARACTERISTIC_LENGTH'))
BPRT_MAX_DEVIATION = float(os.getenv('BPRT_MAX_DEVIATION'))
BPRT_CHARACTERISTIC_LENGTH = float(os.getenv('BPRT_CHARACTERISTIC_LENGTH'))

USE_TEMP_OFFSETS = os.getenv('USE_TEMP_OFFSETS', 'false').lower() == 'true'
OFFSET_B = float(os.getenv('OFFSET_B', '2'))
OFFSET_C_POS = float(os.getenv('OFFSET_C_POS', '1'))
OFFSET_C_NEG = float(os.getenv('OFFSET_C_NEG', '-1'))
OFFSET_D = float(os.getenv('OFFSET_D', '-1'))

def parse_temperature(input_str: str) -> float:
    try:
        # Заменяем запятую на точку и преобразуем в float
        return float(input_str.replace(',', '.'))
    except ValueError:
        raise ValueError("❌ Неверный формат числа. Используйте точку или запятую для разделения десятичных знаков.")

def parse_temperatures(input_str: str, editing_mode: bool = False, target_temp: float = None) -> Tuple[List[float], float]:
    try:
        if editing_mode and target_temp is not None:
            # Режим редактирования - парсим только текущие температуры
            current_temps = [parse_temperature(temp) for temp in input_str.strip().split()]
            if len(current_temps) != 3:
                raise ValueError("❌ Необходимо ввести три значения текущих температур.")
            return current_temps, target_temp
        else:
            # Обычный режим - парсим и текущие температуры, и целевую
            try:
                values = [parse_temperature(temp) for temp in input_str.strip().split()]
                if len(values) != 4:
                    raise ValueError("❌ Необходимо ввести четыре значения: три текущих температуры и температуру задания.")
                    
                current_temps = values[:3]
                target_temp = values[3]
                
                return current_temps, target_temp
                
            except ValueError as e:
                if "четыре значения" in str(e):
                    raise e
                raise ValueError("❌ Неверный формат температур.")
            
    except ValueError as e:
        if editing_mode:
            raise ValueError(f"{str(e)}\nФормат ввода: [три значения температур]\nНапример: <code>1008.5 1003.7 1001.2</code>\nили: <code>1008,5 1003,7 1001,2</code>")
        else:
            raise ValueError(f"{str(e)}\nФормат ввода: [три значения температур] [температура задания]\nНапример: <code>1008.5 1003.7 1001.2 1000.0</code>\nили: <code>1008,5 1003,7 1001,2 1000,0</code>")

def parse_all_ranges(input_str: str) -> Dict[str, List[float]]:
    """Парсит строку с диапазонами для всех зон"""
    try:
        values = input_str.strip().split()
        if len(values) != 6:
            raise ValueError("Необходимо ввести 6 значений")
            
        # Проверяем формат каждого значения
        for val in values:
            if not (val.startswith('+') or val.startswith('-') or val == '0'):
                raise ValueError("Каждое значение должно начинаться с '+', '-' или быть равным '0'")
        
        ranges = {
            'B': [float(values[1]), float(values[0])],  # меняем порядок для правильного мин/макс
            'C': [float(values[3]), float(values[2])],
            'D': [float(values[5]), float(values[4])]
        }
        
        return ranges
        
    except ValueError as e:
        raise ValueError(f"❌ Неверный формат. {str(e)}.\nИспользуйте формат: \"+5 0 +1 -1 0 -2\"")

def custom_round(value: float) -> str:
    """
    Округляет число до целого или до 0.5, в зависимости от того, что ближе.
    Возвращает строку с результатом, убирая .0 для целых чисел.
    """
    if abs(value) < 0.25:
        return "не корректировать"
        
    integer_part = int(round(value))
    half_rounded = round(value * 2) / 2
    
    if abs(value - integer_part) < abs(value - half_rounded):
        return str(integer_part)  # Для целых чисел возвращаем просто число
    else:
        return f"{half_rounded:.1f}" if half_rounded % 1 != 0 else str(int(half_rounded))  # Для дробных оставляем .5, для целых убираем .0

class ThermalReactor:
    def __init__(self, mode='pc', user_ranges_dict=None):
        self.DISTANCE = DISTANCE
        self.mode = mode
        self.MAX_DEVIATION = PC_MAX_DEVIATION if mode == 'pc' else BPRT_MAX_DEVIATION
        self.initial_temps = None
        self.TARGET_TEMP = None
        self.TARGET_TEMPS = None
        self.user_ranges = None
        self.user_ranges_dict = user_ranges_dict
        characteristic_length = PC_CHARACTERISTIC_LENGTH if mode == 'pc' else BPRT_CHARACTERISTIC_LENGTH
        self.heat_transfer_coef = np.exp(-self.DISTANCE / characteristic_length)

    def set_temperatures(self, current_temps: List[float], target_temp: float, user_id: int = None):
        self.initial_temps = np.array(current_temps)
        self.TARGET_TEMP = target_temp
        
        if self.user_ranges_dict and user_id in self.user_ranges_dict:
            self.user_ranges = self.user_ranges_dict[user_id]
            self.TARGET_TEMPS = np.array([target_temp] * 3)
        else:
            if USE_TEMP_OFFSETS:
                self.TARGET_TEMPS = np.array([
                    target_temp + OFFSET_B,
                    target_temp,
                    target_temp + OFFSET_D
                ])
            else:
                self.TARGET_TEMPS = np.array([target_temp] * 3)

    def calculate_corrections(self, final_temps: np.ndarray) -> np.ndarray:
        """
        Рассчитывает корректировки с учетом рабочих диапазонов и взаимного влияния зон.
        Сначала рассчитывает зону Ц как ведущую, от которой зависят остальные зоны.
        """
        corrections = np.zeros(3)
        base_temp = self.TARGET_TEMP
        current_temps = self.initial_temps

        if self.user_ranges:
            # 1. Сначала зона Ц (ведущая)
            current_c = current_temps[1]
            c_upper = base_temp + max(self.user_ranges['C'])
            c_lower = base_temp + min(self.user_ranges['C'])
            
            # Определяем целевую температуру для Ц
            if current_c > c_upper:
                c_target = c_upper
            elif current_c < c_lower:
                c_target = c_lower
            else:
                c_steps = np.arange(c_lower, c_upper + 0.5, 0.5)
                c_target = c_steps[np.abs(c_steps - current_c).argmin()]
            
            # Рассчитываем корректировку для Ц
            corrections[1] = round((c_target - current_c) * 2) / 2
            
            # Матрица влияния зон друг на друга
            influence_matrix = np.array([
                [1.0, 1.0, self.heat_transfer_coef**5],
                [self.heat_transfer_coef, 1.0, self.heat_transfer_coef],
                [self.heat_transfer_coef**5, 1.0, 1.0]
            ])
            
            # 2. Теперь рассчитываем Б и Д с учетом влияния Ц и взаимных зависимостей
            for zone, idx in [('B', 0), ('D', 2)]:
                current = current_temps[idx]
                
                # Применяем влияние корректировки Ц и взаимные зависимости
                if idx == 0:  # Для зоны Б
                    affected = current + corrections[1] + (corrections[1] * self.heat_transfer_coef)
                else:  # Для зоны Д
                    affected = current + corrections[1] + (corrections[1] * self.heat_transfer_coef)
                
                upper = base_temp + max(self.user_ranges[zone])
                lower = base_temp + min(self.user_ranges[zone])
                
                # Определяем целевую температуру
                if affected > upper:
                    target = upper
                elif affected < lower:
                    target = lower
                else:
                    steps = np.arange(lower, upper + 0.5, 0.5)
                    target = steps[np.abs(steps - affected).argmin()]
                
                # Рассчитываем корректировку с учетом всех влияний
                corrections[idx] = round((target - current) * 2) / 2

            print("\nDebug info:")
            print(f"Current temps: {current_temps}")
            print(f"C correction: {corrections[1]}")
            print(f"B correction: {corrections[0]}")
            print(f"D correction: {corrections[2]}")
            print(f"Influence matrix:\n{influence_matrix}")

        return corrections

    def calculate_temperature_changes(self, corrections: np.ndarray) -> np.ndarray:
        """Рассчитывает финальные температуры с учетом взаимного влияния зон"""
        influence_matrix = np.array([
            [1.0, 1.0, self.heat_transfer_coef**5],
            [self.heat_transfer_coef, 1.0, self.heat_transfer_coef],
            [self.heat_transfer_coef**5, 1.0, 1.0]
        ])
        
        temperature_changes = np.dot(influence_matrix, corrections)
        final_temps = self.initial_temps + temperature_changes
        
        print("\nInfluence calculation:")
        print(f"Temperature changes: {temperature_changes}")
        print(f"Final temps: {final_temps}")
        
        return final_temps

    def objective_function(self, corrections: np.ndarray) -> float:
        final_temps = self.calculate_temperature_changes(corrections)
        
        if USE_TEMP_OFFSETS and not self.user_ranges:
            base_temp = self.TARGET_TEMP
            c_variants = [
                base_temp + OFFSET_C_POS,
                base_temp,
                base_temp + OFFSET_C_NEG
            ]
            deviation_b = (final_temps[0] - (base_temp + OFFSET_B)) ** 2
            deviation_c = min((final_temps[1] - variant) ** 2 for variant in c_variants)
            deviation_d = (final_temps[2] - (base_temp + OFFSET_D)) ** 2
            
            return deviation_b + deviation_c + deviation_d
        else:
            return np.sum((final_temps - self.TARGET_TEMPS) ** 2)

    def optimize_temperatures(self) -> Tuple[np.ndarray, np.ndarray]:
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

async def handle_temperatures(update: Update, context: ContextTypes.DEFAULT_TYPE, reactor_id: str, active_outputs: Dict[str, Any], user_ranges_dict: Dict):
    try:
        editing_mode = 'editing_reactor' in context.user_data
        target_temp = active_outputs.get(reactor_id, {}).get('temps', {}).get('target') if editing_mode else None
        
        current_temps, target_temp = parse_temperatures(
            update.message.text, 
            editing_mode=editing_mode,
            target_temp=target_temp
        )
        
        mode = context.user_data.get('mode', 'pc')
        reactor = ThermalReactor(mode=mode, user_ranges_dict=user_ranges_dict)
        reactor.set_temperatures(current_temps, target_temp, update.effective_user.id)
        
        corrections, final_temps = reactor.optimize_temperatures()
        
        keyboard = [
            [
                InlineKeyboardButton("Завершить вывод канала", callback_data=f"finish_{reactor_id}"),
                InlineKeyboardButton("Отредактировать вводимые температуры", callback_data=f"edit_{reactor_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"Реактор: <code>{reactor_id}</code>\n\n"
            f"⌛️ Текущие температуры (Б Ц Д): <code>{' '.join(f'{temp:.1f}' for temp in current_temps)}</code>\n\n"
            f"🌡 Температура по заданию: <code>{target_temp:.1f}</code>\n\n"
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
        
        active_outputs[reactor_id] = {
            "message": message,
            "temps": {
                "current": current_temps,
                "target": target_temp
            },
            "corrections": corrections.tolist(),
            "final_temps": final_temps.tolist()
        }
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
        if 'state' in context.user_data:
            del context.user_data['state']
            
    except ValueError as e:
        raise ValueError(str(e))
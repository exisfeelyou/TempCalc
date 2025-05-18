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

USE_TEMP_OFFSETS = os.getenv('USE_TEMP_OFFSETS', 'false').lower() == 'true'
OFFSET_B = float(os.getenv('OFFSET_B', '2'))
OFFSET_C_POS = float(os.getenv('OFFSET_C_POS', '1'))
OFFSET_C_NEG = float(os.getenv('OFFSET_C_NEG', '-1'))
OFFSET_D = float(os.getenv('OFFSET_D', '-1'))

def parse_temperature(input_str: str) -> float:
    try:
        return float(input_str.replace(',', '.'))
    except ValueError:
        raise ValueError("❌ Неверный формат числа. Используйте точку или запятую для разделения десятичных знаков.")

def parse_temperatures(input_str: str, editing_mode: bool = False, target_temp: float = None) -> Tuple[List[float], float]:
    try:
        if editing_mode and target_temp is not None:
            current_temps = [parse_temperature(temp) for temp in input_str.strip().split()]
            if len(current_temps) != 3:
                raise ValueError("❌ Необходимо ввести три значения текущих температур.")
            return current_temps, target_temp
        else:
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
            raise ValueError(f"{str(e)}\nФормат ввода: [три значения температур]\nНапример: <code>1008.5 1003.7 1001.2</code>")
        else:
            raise ValueError(f"{str(e)}\nФормат ввода: [три значения температур] [температура задания]\nНапример: <code>1008.5 1003.7 1001.2 1000.0</code>")

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
        corrections = np.zeros(3)
        base_temp = self.TARGET_TEMP
        current_temps = self.initial_temps

        if self.user_ranges:
            influence_matrix = self.get_influence_matrix()
            
            # 1. Центральная зона (Ц)
            current_c = current_temps[1]
            c_upper = base_temp + max(self.user_ranges['C'])
            c_lower = base_temp + min(self.user_ranges['C'])
            
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
                upper = base_temp + max(self.user_ranges[zone])
                lower = base_temp + min(self.user_ranges[zone])
                
                if current > upper:
                    target = upper
                elif current < lower:
                    target = lower
                else:
                    steps = np.arange(lower, upper + 0.5, 0.5)
                    target = steps[np.abs(steps - current).argmin()]
                targets.append(target)
            
            # 3. Решаем систему уравнений для получения корректировок
            # M * corrections = targets - current_temps
            # где M - матрица влияния
            targets = np.array(targets)
            corrections = np.linalg.solve(influence_matrix, targets - current_temps)
            
            # Округляем корректировки до ближайших 0.5
            corrections = np.round(corrections * 2) / 2

        return corrections

    def calculate_temperature_changes(self, corrections: np.ndarray) -> np.ndarray:
        # Используем общий метод для получения матрицы влияния
        influence_matrix = self.get_influence_matrix()
        
        temperature_changes = np.dot(influence_matrix, corrections)
        final_temps = self.initial_temps + temperature_changes
        
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
        
        if 'mode' not in context.user_data:
            raise ValueError("❌ Сначала выберите реактор")
            
        mode = context.user_data['mode']
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

from typing import Tuple, List
import os
import numpy as np
from scipy.optimize import minimize
from telegram import Update
from telegram.ext import ContextTypes
from dotenv import load_dotenv

load_dotenv()

DISTANCE = float(os.getenv('DISTANCE'))
PC_MAX_DEVIATION = float(os.getenv('PC_MAX_DEVIATION'))
PC_CHARACTERISTIC_LENGTH = float(os.getenv('PC_CHARACTERISTIC_LENGTH'))

def parse_temperature(input_str: str) -> float:
    try:
        return float(input_str.replace(',', '.'))
    except ValueError:
        raise ValueError("❌ Неверный формат числа. Используйте точку или запятую в качестве разделителя.")

def parse_temperatures(input_str: str) -> List[float]:
    try:
        temps = input_str.split()
        if len(temps) != 1 and len(temps) != 3:
            raise ValueError("❌ Введите либо одно значение (для одинаковых температур), либо три значения через пробел (для разных температур).")
        
        temps = [parse_temperature(temp) for temp in temps]
        
        if len(temps) == 1:
            temps = [temps[0]] * 3
            
        return temps
    except ValueError as e:
        raise ValueError(f"{str(e)}")

class ThermalReactor:
    def __init__(self):
        self.DISTANCE = DISTANCE
        self.MAX_DEVIATION = PC_MAX_DEVIATION
        self.initial_temps = None
        self.TARGET_TEMP = None
        self.TARGET_TEMPS = None
        self.heat_transfer_coef = np.exp(-self.DISTANCE / PC_CHARACTERISTIC_LENGTH)

    def set_initial_temps(self, temps: List[float]):
        self.initial_temps = np.array(temps)

    def set_target_temps(self, uniform: bool, temps: List[float] = None, single_temp: float = None):
        if uniform:
            self.TARGET_TEMP = single_temp
            self.TARGET_TEMPS = np.array([single_temp] * 3)
        else:
            self.TARGET_TEMP = None
            self.TARGET_TEMPS = np.array(temps)

    def calculate_temperature_changes(self, corrections: np.ndarray) -> np.ndarray:
        influence_matrix = np.array([
            [1.0, 1.0, self.heat_transfer_coef**5],
            [self.heat_transfer_coef, 1.0, self.heat_transfer_coef],
            [self.heat_transfer_coef**5, 1.0, 1.0]
        ])
        temperature_changes = np.dot(influence_matrix, corrections)
        return self.initial_temps + temperature_changes

    def objective_function(self, corrections: np.ndarray) -> float:
        final_temps = self.calculate_temperature_changes(corrections)
        temp_deviation = np.sum((final_temps - self.TARGET_TEMPS) ** 2)
        max_deviation_penalty = 1000 * np.sum(
            np.maximum(np.abs(final_temps - self.TARGET_TEMPS) - self.MAX_DEVIATION, 0)
        )
        return temp_deviation + max_deviation_penalty

    def optimize_temperatures(self) -> Tuple[np.ndarray, np.ndarray]:
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

async def start_pc_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reactor'] = ThermalReactor()
    context.user_data['step'] = 'initial_temps'
    await update.message.reply_text(
        "⌛️ Текущие температуры (Б Ц Д).\n\n"
        "<blockquote>Введите через пробел:</blockquote>",
        parse_mode='HTML'
    )

async def handle_pc_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        step = context.user_data.get('step', 'initial_temps')
        reactor = context.user_data.get('reactor')
        
        text = update.message.text

        if step == 'initial_temps':
            temps = parse_temperatures(text)
            reactor.set_initial_temps(temps)
            context.user_data['step'] = 'target_temps'
            
            await update.message.reply_text(
            "🌡️ Температуры по заданию (Б Ц Д).\n\n"
            "<blockquote>Введите:\n\n"
            "• Одно значение, если температуры по всем зонам одинаковые (например: 1050).\n"
            "• Три значения через пробел, если температуры по зонам разные (например: 1045 1040 1040).</blockquote>",
            parse_mode='HTML'
        )

        elif step == 'target_temps':
            temps = parse_temperatures(text)
            is_uniform = len(text.split()) == 1
            if is_uniform:
                reactor.set_target_temps(True, single_temp=temps[0])
            else:
                reactor.set_target_temps(False, temps=temps)
            await calculate_and_send_results(update, context)

    except ValueError as e:
        await update.message.reply_text(f"{str(e)}\n\nПожалуйста, попробуйте снова.")

async def calculate_and_send_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reactor = context.user_data.get('reactor')
    try:
        corrections, final_temps = reactor.optimize_temperatures()
        
        zones = ["Б:", "Ц:", "Д:"]
        result_message = "🔧 Нужно откорректировать:\n\n"
        for zone, corr in zip(zones, corrections):
            sign = "+" if corr >= 1 else ""
            result_message += f"{zone} {sign}{int(round(corr))}°C\n"
        
        await update.message.reply_text(result_message)
        
        context.user_data.clear()
        
    except Exception as e:
        await update.message.reply_text(f"Ошибка при расчетах: {str(e)}")
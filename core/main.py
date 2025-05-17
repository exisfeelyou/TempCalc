import platform, os, sys
from typing import Dict, Any, List
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

import bprt
import pc
from dotenv import load_dotenv
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

load_dotenv()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("⚙️ Канал управляемый от БПРТ и ИПП")],
        [KeyboardButton("🖥️ Канал управляемый от компьютера")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '💡 Выберите тип выводимого канала:',
        reply_markup=reply_markup
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get('mode', '')
    if mode == 'bprt':
        await bprt.handle_bprt_input(update, context)
    elif mode == 'pc':
        await pc.handle_pc_input(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "⚙️ Канал управляемый от БПРТ и ИПП":
        context.user_data['mode'] = 'bprt'
        await bprt.start_bprt_conversation(update, context)
    
    elif text == "🖥️ Канал управляемый от компьютера":
        context.user_data['mode'] = 'pc'
        await pc.start_pc_conversation(update, context)
    
    elif 'mode' in context.user_data:
        if context.user_data['mode'] == 'bprt':
            await bprt.handle_bprt_input(update, context)
        elif context.user_data['mode'] == 'pc':
            await pc.handle_pc_input(update, context)

def main():
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()
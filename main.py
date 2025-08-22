# main.py - Maxfiy bot - faqat admin ruxsati bilan kirish

import logging
import os
import json
from datetime import datetime
import pytz
import pandas as pd
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from api_handler import update_data_from_billz
from search import (
    search_customers_by_name,
    get_customer_debts,
    create_search_results_keyboard,
    format_search_results_message,
    format_customer_details,
    is_search_query,
    get_paginated_results,
    user_all_search_results,
    user_current_page
)

# --- ⚙️ ASOSIY SOZLAMALAR (.env faylidan o'qiladi) ⚙️ ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_IDS_STR = os.getenv("ADMIN_CHAT_ID")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("XATOLIK: .env faylida TELEGRAM_BOT_TOKEN topilmadi yoki bo'sh.")
if not ADMIN_CHAT_IDS_STR:
    raise ValueError("XATOLIK: .env faylida ADMIN_CHAT_ID topilmadi yoki bo'sh.")

# Admin ID larni ajratish va tekshirish
try:
    ADMIN_CHAT_IDS = [int(admin_id.strip()) for admin_id in ADMIN_CHAT_IDS_STR.split(",")]
except ValueError:
    raise ValueError("XATOLIK: .env faylidagi ADMIN_CHAT_ID da barcha qiymatlar raqam bo'lishi kerak.")

if not ADMIN_CHAT_IDS:
    raise ValueError("XATOLIK: Hech bo'lmaganda bitta admin ID kerak.")

# Birinchi admin ID ni asosiy admin sifatida belgilash (eski kod bilan moslashuv uchun)
ADMIN_CHAT_ID = ADMIN_CHAT_IDS[0]

DATA_FILE = "data.json"
SELLERS_FILE = "sellers.json"
WAITING_FOR_USER_ID_FILE = "waiting_for_user_id.json"  # Yangi fayl - admin user ID kutayotganda
TZ_UZB = pytz.timezone('Asia/Tashkent')
REPORT_LIMIT = 8 # Hisobotni matn yoki Excelda yuborish chegarasi

# LOGGING SOZLASH - USER ID'LARNI YASHIRISH UCHUN
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# User ID'larni xavfsiz ko'rsatish uchun helper funksiya
def safe_user_id(user_id):
    """User ID ni xavfsiz formatda ko'rsatish"""
    if isinstance(user_id, int):
        user_id_str = str(user_id)
        if len(user_id_str) > 6:
            return f"***{user_id_str[-3:]}"  # Faqat oxirgi 3 ta raqam
        return "***"
    return "***"

def safe_user_ids_list(user_ids):
    """User ID'lar ro'yxatini xavfsiz formatda ko'rsatish"""
    if isinstance(user_ids, list):
        return [safe_user_id(uid) for uid in user_ids]
    elif isinstance(user_ids, int):
        return safe_user_id(user_ids)
    return "***"

# --- YORDAMCHI FUNKSIYALAR ---
def is_admin(user_id):
    """Foydalanuvchi admin ekanligini tekshirish"""
    return user_id in ADMIN_CHAT_IDS

def is_seller(user_id):
    """Foydalanuvchi sotuvchi ekanligini tekshirish"""
    sellers = load_json(SELLERS_FILE)
    for seller_name, user_ids in sellers.items():
        if isinstance(user_ids, list):
            if user_id in user_ids:
                return True
        elif isinstance(user_ids, int):
            if user_id == user_ids:
                return True
    return False

def get_seller_name_by_user_id(user_id):
    """User ID bo'yicha sotuvchi nomini topish"""
    sellers = load_json(SELLERS_FILE)
    for seller_name, user_ids in sellers.items():
        if isinstance(user_ids, list):
            if user_id in user_ids:
                return seller_name
        elif isinstance(user_ids, int):
            if user_id == user_ids:
                return seller_name
    return None

def get_seller_user_ids(seller_name):
    """Sotuvchi nomiga tegishli barcha user ID larni olish"""
    sellers = load_json(SELLERS_FILE)
    user_ids = sellers.get(seller_name, [])
    if isinstance(user_ids, int):
        return [user_ids]
    elif isinstance(user_ids, list):
        return user_ids
    return []

def add_user_to_seller(seller_name, user_id):
    """Sotuvchiga yangi foydalanuvchi qo'shish"""
    sellers = load_json(SELLERS_FILE)

    if seller_name not in sellers:
        sellers[seller_name] = [user_id]
    else:
        current_ids = sellers[seller_name]

        # Agar hozirgi qiymat int bo'lsa, uni list ga aylantirish
        if isinstance(current_ids, int):
            if current_ids != user_id:
                sellers[seller_name] = [current_ids, user_id]
        # Agar list bo'lsa va user_id yo'q bo'lsa qo'shish
        elif isinstance(current_ids, list):
            if user_id not in current_ids:
                sellers[seller_name].append(user_id)
        else:
            sellers[seller_name] = [user_id]

    save_json(sellers, SELLERS_FILE)
    return True

def remove_user_from_all_sellers(user_id):
    """Foydalanuvchini barcha sotuvchilardan o'chirish (profil o'zgartirish uchun)"""
    sellers = load_json(SELLERS_FILE)
    old_seller_name = None

    for seller_name, user_ids in sellers.items():
        if isinstance(user_ids, list):
            if user_id in user_ids:
                old_seller_name = seller_name
                sellers[seller_name].remove(user_id)
                # Agar ro'yxat bo'sh qolsa, sotuvchini o'chirish
                if not sellers[seller_name]:
                    del sellers[seller_name]
                break
        elif isinstance(user_ids, int):
            if user_ids == user_id:
                old_seller_name = seller_name
                del sellers[seller_name]
                break

    save_json(sellers, SELLERS_FILE)
    return old_seller_name

def is_waiting_for_user_id(admin_id):
    """Admin user ID kutayotganini tekshirish"""
    waiting = load_json(WAITING_FOR_USER_ID_FILE)
    return str(admin_id) in waiting

def set_waiting_for_user_id(admin_id, seller_name):
    """Admin user ID kutish holatiga qo'yish"""
    waiting = load_json(WAITING_FOR_USER_ID_FILE)
    waiting[str(admin_id)] = seller_name
    save_json(waiting, WAITING_FOR_USER_ID_FILE)

def get_waiting_seller_name(admin_id):
    """Admin qaysi sotuvchi uchun user ID kutayotganini olish"""
    waiting = load_json(WAITING_FOR_USER_ID_FILE)
    return waiting.get(str(admin_id))

def clear_waiting_for_user_id(admin_id):
    """Admin user ID kutish holatini tozalash"""
    waiting = load_json(WAITING_FOR_USER_ID_FILE)
    if str(admin_id) in waiting:
        del waiting[str(admin_id)]
        save_json(waiting, WAITING_FOR_USER_ID_FILE)

async def send_message_to_all_admins(context: ContextTypes.DEFAULT_TYPE, message: str, parse_mode=None):
    """Barcha adminlarga xabar yuborish"""
    for admin_id in ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(admin_id, message, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Admin ***{str(admin_id)[-3:]} ga xabar yuborishda xatolik: {e}")

async def send_message_to_seller_users(context: ContextTypes.DEFAULT_TYPE, seller_name: str, message: str, parse_mode=None):
    """Sotuvchining barcha foydalanuvchilariga xabar yuborish"""
    user_ids = get_seller_user_ids(seller_name)
    success_count = 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(user_id, message, parse_mode=parse_mode)
            success_count += 1
        except Exception as e:
            logger.error(f"Sotuvchi '{seller_name}' ning foydalanuvchisiga xabar yuborishda xatolik: {e}")

    logger.info(f"Sotuvchi '{seller_name}' ga {success_count}/{len(user_ids)} ta foydalanuvchiga xabar yuborildi")

# --- JSON FAYL BILAN ISHLASH FUNKSIYALARI ---
def load_json(filename):
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {} # Agar fayl bo'sh yoki buzilgan bo'lsa

def save_json(data, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- YORDAMCHI FUNKSIYALAR ---
async def send_report(update_or_query, context: ContextTypes.DEFAULT_TYPE, report_data: list, title: str, filename_prefix: str):
    """Hisobotni matn yoki Excel fayli sifatida yuboradi"""

    def escape_markdown(text: str) -> str:
        import re
        escape_chars = r"_*[]()~`>#+-=|{}.!"
        return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", str(text))

    # Update yoki CallbackQuery dan chat_id olish
    if hasattr(update_or_query, 'effective_chat'):
        chat_id = update_or_query.effective_chat.id
    else:
        # CallbackQuery bo'lsa
        chat_id = update_or_query.message.chat.id

    if not report_data:
        await context.bot.send_message(chat_id, f"✅ '{title}' bo'yicha aktiv qarzdorliklar topilmadi\\.")
        return

    total_amount = sum(debt.get('Qolgan Summa', 0) for debt in report_data)

    # Agar qatorlar soni limitdan kam bo'lsa, matn sifatida yuborish
    if len(report_data) <= REPORT_LIMIT:
        message = (
            f"**{escape_markdown(title.upper())}**\n\n"
            f"🔢 **Jami:** {len(report_data)} ta\n"
            f"💵 **Umumiy summa:** {escape_markdown(f'{total_amount:,.0f}')} so'm\n\n"
        )
        for debt in report_data:
            payment_date = debt.get('To\'lov Muddati', 'N/A')
            deadline = debt.get('Muddati', 'N/A')
            customer_name = debt.get('Mijoz Ismi', 'N/A')
            check_number = debt.get('Chek Raqami', 'N/A')
            customer_phone = debt.get('Mijoz Telefoni', 'N/A')
            remaining_amount = debt.get('Qolgan Summa', 0)

            message += (
                f"👤 **{escape_markdown(customer_name)}** \\(Chek: {escape_markdown(check_number)}\\)\n"
                f"📞 {escape_markdown(customer_phone)}\n"
                f"💰 {escape_markdown(f'{remaining_amount:,.0f}')} so'm \\| "
                f"🗓️ {escape_markdown(payment_date)} \\({escape_markdown(deadline)}\\)\n\n"
            )
        await context.bot.send_message(chat_id, message, parse_mode='MarkdownV2')

    # Aks holda, Excel fayli sifatida yuborish
    else:
        await context.bot.send_message(chat_id, f"📄 Hisobotdagi qatorlar soni ({len(report_data)} ta) ko'p bo'lgani uchun Excel fayl shaklida yuborilmoqda...")

        df = pd.DataFrame(report_data)
        excel_columns = [
            'Chek Raqami', 'Sotuvchi Ismi', 'Mijoz Ismi', 'Mijoz Telefoni',
            'Yaratilgan Sana', 'Qarz Summasi', 'To\'langan Summa', 'Qolgan Summa',
            'Qarz Statusi', 'To\'lov Muddati', 'Muddati'
        ]
        df = df[excel_columns]

        filename = f"{filename_prefix}_{datetime.now(TZ_UZB).strftime('%Y%m%d_%H%M')}.xlsx"

        try:
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Hisobot', index=False)
                worksheet = writer.sheets['Hisobot']
                # Ustunlarni avtomatik kengaytirish
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = (max_length + 2) if max_length < 50 else 50
                    worksheet.column_dimensions[column_letter].width = adjusted_width

            with open(filename, 'rb') as doc:
                await context.bot.send_document(chat_id, document=doc)
            os.remove(filename)

        except Exception as e:
            logger.error(f"Excel faylni yaratish yoki yuborishda xatolik: {e}")
            await context.bot.send_message(chat_id, f"❌ Excel faylni yuborishda xatolik yuz berdi: {e}")

# --- KEYBOARD YARATISH FUNKSIYALARI ---
def create_admin_keyboard():
    keyboard = [
        [KeyboardButton("📊 Umumiy hisobot"), KeyboardButton("👥 Sotuvchilar ro'yxati")],
        [KeyboardButton("🔄 Ma'lumotlarni yangilash"), KeyboardButton("📈 Bot statistikasi")],
        [KeyboardButton("💰 Sotuvchi bo'yicha hisobot"), KeyboardButton("⚡ Muddati o'tganlar")],
        [KeyboardButton("🔍 Mijoz qidirish"), KeyboardButton("➕ Yangi odam qo'shish")]  # Yangi tugma
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_seller_keyboard():
    keyboard = [
        [KeyboardButton("📊 Mening hisobotim"), KeyboardButton("⏰ Muddati o'tganlar")],
        [KeyboardButton("📅 5 kun qolganlar"), KeyboardButton("📈 Barcha qarzdorliklar")],
        [KeyboardButton("🔍 Mijoz qidirish"), KeyboardButton("🔄 Profil o'zgartirish")]  # Yangi tugma qo'shildi
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_seller_selection_keyboard():
    all_debts = load_json(DATA_FILE)
    if not all_debts:
        return None
    keyboard = []
    sellers = sorted(list(all_debts.keys()))
    for i in range(0, len(sellers), 2):
        # Sotuvchi nomini 25 belgigacha qisqartirish
        seller1_name = sellers[i] if len(sellers[i]) <= 25 else sellers[i][:22] + "..."
        row = [InlineKeyboardButton(seller1_name, callback_data=f"admin_seller_{sellers[i]}")]

        if i + 1 < len(sellers):
            seller2_name = sellers[i + 1] if len(sellers[i + 1]) <= 25 else sellers[i + 1][:22] + "..."
            row.append(InlineKeyboardButton(seller2_name, callback_data=f"admin_seller_{sellers[i + 1]}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def create_add_user_keyboard():
    """Yangi foydalanuvchi qo'shish uchun sotuvchilar ro'yxatini yaratish"""
    all_debts = load_json(DATA_FILE)
    if not all_debts:
        return None

    keyboard = []
    sellers = sorted(list(all_debts.keys()))

    for i in range(0, len(sellers), 2):
        # Sotuvchi nomini 25 belgigacha qisqartirish
        seller1_name = sellers[i] if len(sellers[i]) <= 25 else sellers[i][:22] + "..."
        row = [InlineKeyboardButton(seller1_name, callback_data=f"add_user_to_{sellers[i]}")]

        if i + 1 < len(sellers):
            seller2_name = sellers[i + 1] if len(sellers[i + 1]) <= 25 else sellers[i + 1][:22] + "..."
            row.append(InlineKeyboardButton(seller2_name, callback_data=f"add_user_to_{sellers[i + 1]}"))
        keyboard.append(row)

    # Bekor qilish tugmasi
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_add_user")])

    return InlineKeyboardMarkup(keyboard)

def create_profile_change_keyboard():
    """Profil o'zgartirish uchun sotuvchilar ro'yxatini yaratish"""
    all_debts = load_json(DATA_FILE)
    if not all_debts:
        return None

    keyboard = []
    sellers = sorted(list(all_debts.keys()))

    for i in range(0, len(sellers), 2):
        # Sotuvchi nomini 25 belgigacha qisqartirish
        seller1_name = sellers[i] if len(sellers[i]) <= 25 else sellers[i][:22] + "..."
        row = [InlineKeyboardButton(seller1_name, callback_data=f"change_profile_{sellers[i]}")]

        if i + 1 < len(sellers):
            seller2_name = sellers[i + 1] if len(sellers[i + 1]) <= 25 else sellers[i + 1][:22] + "..."
            row.append(InlineKeyboardButton(seller2_name, callback_data=f"change_profile_{sellers[i + 1]}"))
        keyboard.append(row)

    # Bekor qilish tugmasi
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_profile_change")])

    return InlineKeyboardMarkup(keyboard)
# --- YANGI FOYDALANUVCHI QO'SHISH FUNKSIYALARI ---
async def handle_add_user_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yangi foydalanuvchi qo'shish so'rovini ishlab chiqish"""
    keyboard = create_add_user_keyboard()
    if not keyboard:
        await update.message.reply_text("❌ Ma'lumotlar bazasi bo'sh yoki sotuvchilar topilmadi.")
        return

    message = (
        f"➕ **Yangi foydalanuvchi qo'shish**\n\n"
        f"👇 Qaysi sotuvchi roliga odam qo'shmoqchisiz?"
    )

    await update.message.reply_text(message, reply_markup=keyboard, parse_mode='MarkdownV2')

async def handle_seller_selection_for_adding_user(query, context: ContextTypes.DEFAULT_TYPE, seller_name: str):
    """Sotuvchi tanlangandan keyin Telegram ID so'rash"""
    admin_id = query.from_user.id

    # Admin user ID kutish holatiga qo'yish
    set_waiting_for_user_id(admin_id, seller_name)

    message = (
        f"📱 **Telegram ID kiriting**\n\n"
        f"🔹 Tanlangan sotuvchi: **{escape_markdown(seller_name)}**\n\n"
        f"📋 Qo'shmoqchi bo'lgan odamning Telegram ID raqamini kiriting:\n\n"
        f"💡 **Masalan:** 123456789\n\n"
        f"❌ Bekor qilish uchun /cancel yozing"
    )

    await query.edit_message_text(message, parse_mode='MarkdownV2')
    await query.answer()

async def handle_telegram_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id_input: str):
    """Admin tomonidan kiritilgan Telegram ID ni qayta ishlash"""
    admin_id = update.effective_chat.id

    if not is_waiting_for_user_id(admin_id):
        return

    seller_name = get_waiting_seller_name(admin_id)
    if not seller_name:
        await update.message.reply_text("❌ Xatolik: Sotuvchi nomi topilmadi.")
        clear_waiting_for_user_id(admin_id)
        return

    # Telegram ID ni tekshirish
    try:
        new_user_id = int(user_id_input.strip())
        if new_user_id <= 0:
            raise ValueError("ID musbat bo'lishi kerak")
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri Telegram ID. Iltimos, to'g'ri raqam kiriting.")
        return

    # Foydalanuvchi allaqachon ro'yxatdan o'tgan-o'tmaganini tekshirish
    existing_seller = get_seller_name_by_user_id(new_user_id)
    if existing_seller:
        await update.message.reply_text(
            f"⚠️ Bu foydalanuvchi allaqachon **{escape_markdown(existing_seller)}** roliga qo'shilgan\\.\n\n"
            f"Yangi rol berishni xohlaysizmi? \\(Eski roldan avtomatik o'chiriladi\\)"
        )

    # Foydalanuvchini sotuvchiga qo'shish
    if existing_seller:
        remove_user_from_all_sellers(new_user_id)

    add_user_to_seller(seller_name, new_user_id)

    # Muvaffaqiyat xabari
    admin_name = update.effective_user.first_name or "Admin"
    success_message = (
    f"✅ **Foydalanuvchi muvaffaqiyatli qo'shildi\\!**\n\n"
    f"👤 **Telegram ID:** {new_user_id}\n"
    f"🏷️ **Sotuvchi roli:** {escape_markdown(seller_name)}\n"
    f"👑 **Admin:** {escape_markdown(admin_name)}"
    )

    if existing_seller:
        success_message += f"\n🔄 **Eski rol:** {escape_markdown(existing_seller)} \\(o'chirildi\\)"

    await update.message.reply_text(success_message, parse_mode='MarkdownV2')

    # Yangi foydalanuvchiga xabar yuborish
    try:
        welcome_message = (
        f"🎉 **Tabriklaymiz\\!**\n\n"
        f"Siz **{escape_markdown(seller_name)}** roliga qo'shildingiz\\.\n\n"
        f"🤖 Botdan foydalanishni boshlash uchun /start tugmasini bosing\\."
        )
        await context.bot.send_message(new_user_id, welcome_message, parse_mode='MarkdownV2')

        await update.message.reply_text("📨 Foydalanuvchiga xush kelibsiz xabari yuborildi.")

    except Exception as e:
        logger.error(f"Yangi foydalanuvchiga xabar yuborishda xatolik: {e}")
        await update.message.reply_text("⚠️ Foydalanuvchi qo'shildi, lekin unga xabar yuborib bo'lmadi. (Ehtimol u botni hali ishga tushirmagan)")

    # Boshqa adminlarga xabar yuborish
    notification_message = (
        f"➕ **Yangi foydalanuvchi qo'shildi**\n\n"
        f"👤 **ID:** {escape_markdown(safe_user_id(new_user_id))}\n"
        f"🏷️ **Rol:** {escape_markdown(seller_name)}\n"
        f"👑 **Qo'shgan admin:** {escape_markdown(admin_name)}"
    )

    for other_admin_id in ADMIN_CHAT_IDS:
        if other_admin_id != admin_id:
            try:
                await context.bot.send_message(other_admin_id, notification_message, parse_mode='MarkdownV2')
            except Exception as e:
                logger.error(f"Boshqa adminlarga xabar yuborishda xatolik: {e}")

    # Kutish holatini tozalash
    clear_waiting_for_user_id(admin_id)

# --- SEARCH FUNKSIYALAR ---
async def handle_search_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz qidirish so'rovini ishlab chiqish"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🔍 **Mijoz qidirish**\n\n"
        "Mijoz ismini yozing \\(kamida 2 ta harf\\):\n"
        "Masalan: *Ahad*, *Olim*, *Shohida* va h\\.k\\.\n\n"
        "❌ Bekor qilish uchun /cancel yozing",
        parse_mode='MarkdownV2'
    )

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE, search_query: str):
    """Qidiruv so'zini ishlab chiqish"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Qidiruv natijalarini olish
    search_results = search_customers_by_name(search_query, DATA_FILE, limit=50)  # Ko'proq natija olish

    if not search_results:
        await update.message.reply_text(f"❌ '{search_query}' bo'yicha mijozlar topilmadi.")
        return

    # Global o'zgaruvchilarda saqlash
    user_all_search_results[user_id] = search_results
    user_current_page[user_id] = 0

    # Birinchi sahifani olish
    page_results, has_more = get_paginated_results(user_id, 0, 5)

    # Natijalarni formatlash va yuborish
    message = format_search_results_message(page_results, search_query, 0, len(search_results))
    keyboard = create_search_results_keyboard(page_results, user_id, 0, has_more)

    if keyboard:
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='MarkdownV2')
    else:
        await update.message.reply_text(message, parse_mode='MarkdownV2')

async def handle_customer_selection(query, context: ContextTypes.DEFAULT_TYPE, selection_index: str):
    """Tanlangan mijoz haqida batafsil ma'lumot ko'rsatish"""
    user_id = query.from_user.id

    # Foydalanuvchining search natijalarini olish
    search_results = user_all_search_results.get(user_id, [])

    try:
        index = int(selection_index)
        if 0 <= index < len(search_results):
            customer_data = search_results[index]
            customer_name = customer_data['customer_name']
            customer_phone = customer_data['customer_phone']

            # Mijozning barcha qarzdorliklarini olish
            customer_debts = get_customer_debts(customer_name, customer_phone, DATA_FILE)

            # Batafsil ma'lumotni formatlash (bo'laklarga ajratilgan)
            messages = format_customer_details(customer_debts, customer_name)

            # Birinchi xabarni inline xabarni o'zgartirish orqali yuborish
            await query.edit_message_text(messages[0], parse_mode='MarkdownV2')

            # Qolgan xabarlarni oddiy xabar sifatida yuborish
            for message in messages[1:]:
                await context.bot.send_message(query.message.chat.id, message, parse_mode='MarkdownV2')

            # Search natijalarini tozalash
            if user_id in user_all_search_results:
                del user_all_search_results[user_id]
            if user_id in user_current_page:
                del user_current_page[user_id]

        else:
            await query.answer("❌ Noto'g'ri tanlov")

    except (ValueError, IndexError):
        await query.answer("❌ Xatolik yuz berdi")

async def handle_search_navigation(query, context: ContextTypes.DEFAULT_TYPE, action: str):
    """Qidiruv sahifalarini navigatsiya qilish"""
    user_id = query.from_user.id

    if user_id not in user_all_search_results:
        await query.answer("❌ Qidiruv natijalari topilmadi")
        return

    current_page = user_current_page.get(user_id, 0)

    if action.startswith("search_next_"):
        new_page = current_page + 1
    elif action.startswith("search_prev_"):
        new_page = current_page - 1
    else:
        await query.answer("❌ Noto'g'ri harakat")
        return

    # Yangi sahifani olish
    page_results, has_more = get_paginated_results(user_id, new_page, 5)

    if not page_results:
        await query.answer("❌ Bu sahifada natijalar yo'q")
        return

    # Sahifa raqamini yangilash
    user_current_page[user_id] = new_page

    # Xabar va klaviaturani yangilash
    search_query = "qidiruv"  # Bu qiymatni saqlab turish kerak bo'ladi
    total_results = len(user_all_search_results[user_id])

    message = format_search_results_message(page_results, search_query, new_page, total_results)
    keyboard = create_search_results_keyboard(page_results, user_id, new_page, has_more)

    try:
        await query.edit_message_text(message, reply_markup=keyboard, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Xabarni yangilashda xatolik: {e}")
        await query.answer("❌ Xabarni yangilashda xatolik")

# --- PROFIL O'ZGARTIRISH FUNKSIYALARI ---
async def handle_profile_change_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Profil o'zgartirish so'rovini ishlab chiqish"""
    user_id = update.effective_chat.id
    current_seller = get_seller_name_by_user_id(user_id)

    if not current_seller:
        await update.message.reply_text("❌ Siz hali ro'yxatdan o'tmagansiz. /start buyrug'ini bosing.")
        return

    keyboard = create_profile_change_keyboard()
    if not keyboard:
        await update.message.reply_text("❌ Ma'lumotlar bazasi bo'sh yoki sotuvchilar topilmadi.")
        return

    message = (
        f"🔄 **Profil o'zgartirish**\n\n"
        f"🔹 Hozirgi profilingiz: **{escape_markdown(current_seller)}**\n\n"
        f"👇 Yangi profil tanlang:"
    )

    await update.message.reply_text(message, reply_markup=keyboard, parse_mode='MarkdownV2')

async def handle_profile_change_selection(query, context: ContextTypes.DEFAULT_TYPE, new_seller_name: str):
    """Yangi profil tanlanganida ishlov berish"""
    user_id = query.from_user.id
    user_name = query.from_user.first_name or "Foydalanuvchi"

    # Eski profilni topish
    old_seller_name = get_seller_name_by_user_id(user_id)

    if old_seller_name == new_seller_name:
        await query.edit_message_text(f"ℹ️ Siz allaqachon **{new_seller_name}** profilida turibsiz.")
        await query.answer()
        return

    # Eski profildan o'chirish
    removed_from = remove_user_from_all_sellers(user_id)

    # Yangi profilga qo'shish
    add_user_to_seller(new_seller_name, user_id)

    # Foydalanuvchiga xabar
    unknown_text = "Noma'lum"
    message = (
        f"✅ **Profil muvaffaqiyatli o'zgartirildi\\!**\n\n"
        f"🔸 Eski profil: {escape_markdown(removed_from or unknown_text)}\n"
        f"🔸 Yangi profil: **{escape_markdown(new_seller_name)}**\n\n"
        f"🎯 Yangi panel tayyor\\!"
    )

    await query.edit_message_text(message, parse_mode='MarkdownV2')

    # Yangi klaviaturani yuborish
    await context.bot.send_message(user_id, f"👋 Xush kelibsiz, {new_seller_name}!", reply_markup=create_seller_keyboard())

    # Adminlarga xabar yuborish (USER ID ni yashirish)
    unknown_text = "Noma'lum"
    admin_message = (
        f"🔄 **Profil o'zgarishi:**\n\n"
        f"👤 Foydalanuvchi: {escape_markdown(user_name)} \\({escape_markdown(safe_user_id(user_id))}\\)\n"
        f"🔸 Eski: {escape_markdown(removed_from or unknown_text)}\n"
        f"🔸 Yangi: {escape_markdown(new_seller_name)}"
    )
    await send_message_to_all_admins(context, admin_message, parse_mode='MarkdownV2')

    await query.answer()

async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Kunlik eslatmalarni yuborish boshlandi.")
    all_debts = load_json(DATA_FILE)
    sellers = load_json(SELLERS_FILE)

    total_sent = 0
    for seller_name, user_ids_data in sellers.items():
        seller_debts = all_debts.get(seller_name, [])

        # Muddati o'tgan qarzdorliklar
        overdue_debts = [debt for debt in seller_debts if "o'tdi" in debt.get('Muddati', '')]

        # 5 kun qolganlar
        upcoming_debts = []
        for debt in seller_debts:
            muddati = debt.get('Muddati', '')
            if "qoldi" in muddati:
                try:
                    kun = int(muddati.split()[0])
                    if 0 < kun <= 5:
                        upcoming_debts.append(debt)
                except (ValueError, IndexError):
                    continue
            elif "Bugun" in muddati:
                upcoming_debts.append(debt)

        # Agar ikkalasi ham bo'sh bo'lsa, keyingisiga o'tish
        if not overdue_debts and not upcoming_debts:
            continue

        # User IDs ni olish
        if isinstance(user_ids_data, list):
            user_ids = user_ids_data
        elif isinstance(user_ids_data, int):
            user_ids = [user_ids_data]
        else:
            continue

        # Har bir foydalanuvchiga yuborish
        for user_id in user_ids:
            try:
                # Muddati o'tganlar
                if overdue_debts:
                    fake_update = type('Update', (), {'effective_chat': type('Chat', (), {'id': user_id})()})()
                    await send_report(
                        fake_update,
                        context,
                        overdue_debts,
                        "🔔 Muddati o'tgan qarzdorliklar (Kunlik eslatma)",
                        f"kunlik_muddati_otgan_{seller_name}"
                    )
                    total_sent += 1

                # 5 kun qolganlar
                if upcoming_debts:
                    fake_update = type('Update', (), {'effective_chat': type('Chat', (), {'id': user_id})()})()
                    await send_report(
                        fake_update,
                        context,
                        upcoming_debts,
                        "⏰ Yaqinlashayotgan to'lov mudatlari (5 kun ichida)",
                        f"kunlik_5kun_qolgan_{seller_name}"
                    )
                    total_sent += 1

            except Exception as e:
                logger.error(f"'{seller_name}' sotuvchisiga eslatma yuborishda xatolik: {e}")

    logger.info(f"Kunlik eslatmalar yuborish yakunlandi. Jami {total_sent} ta xabar yuborildi.")

async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
    """Rejalashtirilgan vazifa - ma'lumotlarni yangilash va eslatmalar yuborish"""
    logger.info("Rejalashtirilgan vazifa boshlandi: ma'lumotlarni yangilash")
    success = await update_data_from_billz()
    if success:
        logger.info("Ma'lumotlar muvaffaqiyatli yangilandi, eslatmalar yuborilmoqda")
        await send_daily_reminders(context)
    else:
        await send_message_to_all_admins(context, "❌ Reja bo'yicha ma'lumotlarni yangilashda xatolik yuz berdi.")

# --- BOT BUYRUQLARI VA HANDLERLAR ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "Foydalanuvchi"

    logger.info(f"Yangi /start buyruq: {user_name} ({safe_user_id(user_id)})")

    if is_admin(user_id):
        await update.message.reply_text(f"👋 Salom, {user_name}! Siz administratorsiz.", reply_markup=create_admin_keyboard())
        return

    # Sotuvchi ekanligini tekshirish
    seller_name = get_seller_name_by_user_id(user_id)
    if seller_name:
        await update.message.reply_text(f"👋 Xush kelibsiz, {seller_name}!", reply_markup=create_seller_keyboard())
        return

    # Agar sotuvchi emas va admin ham emas - ruxsati yo'q
    await update.message.reply_text(
        "🔐 **Kirishga ruxsat yo'q**\n\n"
        "Siz ushbu botdan foydalanish uchun ro'yxatdan o'tmagansiz.\n\n"
        "📞 Admin bilan bog'laning va o'zingizni qo'shishni so'rang.\n\n"
        "⚠️ Faqat admin tomonidan ruxsat berilgan foydalanuvchilar botdan foydalana oladi.",
        parse_mode='MarkdownV2'
    )

    # Adminlarga xabar yuborish
    admin_notification = (
    f"🔴 **Ruxsatsiz kirish urinishi**\n\n"
    f"👤 Foydalanuvchi: {escape_markdown(user_name)}\n"
    f"🆔 Telegram ID: {escape_markdown(safe_user_id(user_id))}\n"
    f"📅 Vaqt: {escape_markdown(datetime.now(TZ_UZB).strftime('%Y-%m-%d %H:%M:%S'))}"
    )
    await send_message_to_all_admins(context, admin_notification, parse_mode='MarkdownV2')

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday jarayonni bekor qilish"""
    user_id = update.effective_user.id

    # Search natijalarini tozalash
    if user_id in user_all_search_results:
        del user_all_search_results[user_id]
    if user_id in user_current_page:
        del user_current_page[user_id]

    # Admin user ID kutish holatini tozalash
    if is_admin(user_id) and is_waiting_for_user_id(user_id):
        clear_waiting_for_user_id(user_id)
        await update.message.reply_text("❌ Foydalanuvchi qo'shish bekor qilindi.")
    else:
        await update.message.reply_text("❌ Jarayon bekor qilindi.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    message_text = update.message.text

    # Admin user ID kutayotgan holatni tekshirish
    if is_admin(user_id) and is_waiting_for_user_id(user_id):
        await handle_telegram_id_input(update, context, message_text)
        return

    # Qidiruv so'zi ekanligini tekshirish
    if is_search_query(message_text):
        await handle_search_query(update, context, message_text)
        return

    if is_admin(user_id):
        await handle_admin_message(update, context, message_text)
    elif is_seller(user_id):
        await handle_seller_message(update, context, message_text)
    else:
        # Ruxsatsiz foydalanuvchi
        await update.message.reply_text(
            "🔐 Sizga botdan foydalanishga ruxsat berilmagan.\n\n"
            "📞 Admin bilan bog'laning."
        )

# --- ADMIN FUNKSIYALARI ---
async def admin_general_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_debts = load_json(DATA_FILE)
    if not all_debts:
        await update.message.reply_text("❌ Ma'lumotlar bazasi bo'sh.")
        return

    all_data = []
    for seller_name, debts in all_debts.items():
        all_data.extend(debts)

    total_amount = sum(d.get('Qolgan Summa', 0) for d in all_data)
    overdue_count = len([d for d in all_data if "o'tdi" in d.get('Muddati', '')])

    message = (
    "📊 **UMUMIY HISOBOT**\n\n"
    f"👥 **Sotuvchilar soni:** {len(all_debts)}\n"
    f"💰 **Jami qarzdorliklar:** {len(all_data)} ta\n"
    f"💵 **Umumiy summa:** {escape_markdown(f'{total_amount:,.0f}')} so'm\n"
    f"⚡ **Muddati o'tganlar:** {overdue_count} ta\n"
    )
    await update.message.reply_text(message, parse_mode='MarkdownV2')

async def admin_sellers_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sellers = load_json(SELLERS_FILE)
    if not sellers:
        await update.message.reply_text("❌ Hech qanday sotuvchi ro'yxatdan o'tmagan.")
        return

    message = "👥 **RO'YXATDAN O'TGAN SOTUVCHILAR:**\n\n"
    for i, (seller_name, user_ids_data) in enumerate(sellers.items(), 1):
        if isinstance(user_ids_data, list):
            user_count = len(user_ids_data)
            user_ids_str = ", ".join(safe_user_id(uid) for uid in user_ids_data)
        elif isinstance(user_ids_data, int):
            user_count = 1
            user_ids_str = safe_user_id(user_ids_data)
        else:
            user_count = 0
            user_ids_str = "N/A"

        # Seller nomini escape qilish
        escaped_seller_name = escape_markdown(seller_name)
        escaped_user_ids = escape_markdown(user_ids_str)

        message += f"{i}\\. **{escaped_seller_name}** \\({user_count} ta foydalanuvchi\\)\n"
        message += f"   📱 ID: {escaped_user_ids}\n\n"

    await update.message.reply_text(message, parse_mode='MarkdownV2')

async def admin_select_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = create_seller_selection_keyboard()
    if not keyboard:
        await update.message.reply_text("❌ Ma'lumotlar bazasi bo'sh yoki sotuvchilar topilmadi.")
        return
    await update.message.reply_text("👥 **Sotuvchi tanlang:**", reply_markup=keyboard)

async def admin_seller_report(query, context: ContextTypes.DEFAULT_TYPE, seller_name: str):
    all_debts = load_json(DATA_FILE)
    seller_debts = all_debts.get(seller_name, [])

    # query orqali hisobot yuborish
    await send_report(query, context, seller_debts, f"{seller_name} hisoboti", f"hisobot_{seller_name}")
    await query.answer() # Inline tugma bosilganini bildirish

async def admin_overdue_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_debts = load_json(DATA_FILE)
    if not all_debts:
        await update.message.reply_text("❌ Ma'lumotlar bazasi bo'sh.")
        return

    overdue_debts = []
    for debts in all_debts.values():
        overdue_debts.extend([d for d in debts if "o'tdi" in d.get('Muddati', '')])

    overdue_debts.sort(key=lambda x: int(x.get('Muddati', '0').split()[0]) if "o'tdi" in x.get('Muddati', '') else 0, reverse=True)

    await send_report(update, context, overdue_debts, "Barcha muddati o'tganlar", "muddati_otganlar")

async def seller_report(update: Update, context: ContextTypes.DEFAULT_TYPE, seller_name: str, filter_type):
    all_debts = load_json(DATA_FILE)
    seller_debts = all_debts.get(seller_name, [])

    if not seller_debts:
        await update.message.reply_text("❌ Sizga biriktirilgan aktiv qarzdorliklar yo'q.")
        return

    filtered_debts = []
    title = ""
    filename = ""

    if filter_type == "overdue":
        title = "Muddati o'tganlar"
        filename = f"{seller_name}_muddati_otgan"
        filtered_debts = [d for d in seller_debts if "o'tdi" in d.get('Muddati', '')]
        filtered_debts.sort(key=lambda x: int(x.get('Muddati', '0').split()[0]), reverse=True)

    elif filter_type == "all":
        title = "Barcha qarzdorliklar"
        filename = f"{seller_name}_barchasi"
        filtered_debts = seller_debts

    elif filter_type == 5:
        title = "5 kun qolganlar"
        filename = f"{seller_name}_5_kun"
        for d in seller_debts:
            muddati = d.get('Muddati', '')
            if "qoldi" in muddati:
                try:
                    kun = int(muddati.split()[0])
                    if 0 < kun <= 5:
                        filtered_debts.append(d)
                except (ValueError, IndexError):
                    continue
            elif "Bugun" in muddati:
                filtered_debts.append(d)
        filtered_debts.sort(key=lambda x: int(x.get('Muddati', '999').split()[0]) if "qoldi" in x.get('Muddati', '') else 0)

    else: # "Mening hisobotim"
        title = "Mening hisobotim"
        filename = f"{seller_name}_hisobot"
        filtered_debts = seller_debts

    await send_report(update, context, filtered_debts, title, filename)

async def force_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id): return
    await update.message.reply_text("⏳ Ma'lumotlarni majburiy yangilash boshlandi...")
    await scheduled_job(context)


import re

# Markdown belgilarini qochirish funksiyasi
def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", str(text))


async def bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        return
    try:
        last_update = datetime.fromtimestamp(
            os.path.getmtime(DATA_FILE)
        ).astimezone(TZ_UZB).strftime('%Y-%m-%d %H:%M:%S')
    except FileNotFoundError:
        last_update = "Hali yangilanmagan"

    sellers, all_debts = load_json(SELLERS_FILE), load_json(DATA_FILE)

    # Umumiy foydalanuvchilar sonini hisoblash
    total_users = 0
    for user_ids_data in sellers.values():
        if isinstance(user_ids_data, list):
            total_users += len(user_ids_data)
        elif isinstance(user_ids_data, int):
            total_users += 1

    total_debts = sum(len(d) for d in all_debts.values())

    # Admin IDs xavfsiz ko'rinishi
    admin_list = ", ".join([escape_markdown(safe_user_id(admin_id)) for admin_id in ADMIN_CHAT_IDS])

    # Xabar matni
    message = (
        f"📈 **BOT STATISTIKASI**\n\n"
        f"📊 **Oxirgi yangilanish:** {escape_markdown(last_update)}\n"
        f"👥 **Sotuvchilar soni:** {len(sellers)} ta\n"
        f"👤 **Jami foydalanuvchilar:** {total_users} ta\n"
        f"💰 **Jami aktiv qarzdorliklar:** {total_debts} ta\n"
        f"🔐 **Adminlar:** {admin_list}"
    )

    await update.message.reply_text(message, parse_mode='MarkdownV2')

async def post_init(application: Application):
    scheduler = AsyncIOScheduler(timezone=TZ_UZB)  # Toshkent vaqti


    reminder_times = [10, 14, 16, 20]  # 10:00, 14:00, 16:00, 20:00

    for hour in reminder_times:
        scheduler.add_job(
            scheduled_job_wrapper,
            'cron',
            hour=hour,
            minute=0,
            args=[application.bot]
        )
        logger.info(f"Rejalashtiruvchi qo'shildi: har kuni soat {hour:02d}:00")

    scheduler.start()
    logger.info("Barcha rejalashtiruvchilar muvaffaqiyatli ishga tushdi.")

    # Bot ishga tushganda bir marta ma'lumotlarni yangilash
    context_like = type('Context', (), {'bot': application.bot})()
    await send_message_to_all_admins(context_like, "🤖 Bot qayta ishga tushdi. Ma'lumotlar yangilanmoqda...")
    await update_data_from_billz()
    await send_message_to_all_admins(context_like, "✅ Bot tayyor!")

async def scheduled_job_wrapper(bot):
    """Scheduler uchun wrapper funksiya"""
    # Context yaratish
    context_like = type('Context', (), {'bot': bot})()
    await scheduled_job(context_like)

# Asosiy funksiyalar
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("admin_seller_"):
        seller_name = query.data.replace("admin_seller_", "")
        await query.message.delete() # Inline tugmalarni o'chirish
        await admin_seller_report(query, context, seller_name)

    elif query.data.startswith("customer_select_"):
        selection_index = query.data.replace("customer_select_", "")
        await handle_customer_selection(query, context, selection_index)

    elif query.data.startswith("search_next_") or query.data.startswith("search_prev_"):
        await handle_search_navigation(query, context, query.data)

    elif query.data == "search_cancel":
        user_id = query.from_user.id
        if user_id in user_all_search_results:
            del user_all_search_results[user_id]
        if user_id in user_current_page:
            del user_current_page[user_id]
        await query.edit_message_text("❌ Qidiruv bekor qilindi.")

    elif query.data == "search_info":
        await query.answer("ℹ️ Sahifa ma'lumoti", show_alert=False)

    # Yangi foydalanuvchi qo'shish callback'lari
    elif query.data.startswith("add_user_to_"):
        seller_name = query.data.replace("add_user_to_", "")
        await handle_seller_selection_for_adding_user(query, context, seller_name)

    elif query.data == "cancel_add_user":
        await query.edit_message_text("❌ Foydalanuvchi qo'shish bekor qilindi.")

    # Profil o'zgartirish callback'lari
    elif query.data.startswith("change_profile_"):
        new_seller_name = query.data.replace("change_profile_", "")
        await handle_profile_change_selection(query, context, new_seller_name)

    elif query.data == "cancel_profile_change":
        await query.edit_message_text("❌ Profil o'zgartirish bekor qilindi.")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    if message_text == "📊 Umumiy hisobot":
        await admin_general_report(update, context)
    elif message_text == "👥 Sotuvchilar ro'yxati":
        await admin_sellers_list(update, context)
    elif message_text == "🔄 Ma'lumotlarni yangilash":
        await force_update(update, context)
    elif message_text == "📈 Bot statistikasi":
        await bot_status(update, context)
    elif message_text == "💰 Sotuvchi bo'yicha hisobot":
        await admin_select_seller(update, context)
    elif message_text == "⚡ Muddati o'tganlar":
        await admin_overdue_report(update, context)
    elif message_text == "🔍 Mijoz qidirish":
        await handle_search_request(update, context)
    elif message_text == "➕ Yangi odam qo'shish":  # Yangi tugma
        await handle_add_user_request(update, context)

async def handle_seller_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    user_id = update.effective_chat.id
    seller_name = get_seller_name_by_user_id(user_id)

    if not seller_name:
        await update.message.reply_text("❌ Siz ro'yxatdan o'tmagansiz. /start buyrug'ini bosing.")
        return

    if message_text == "📊 Mening hisobotim":
        await seller_report(update, context, seller_name, None)
    elif message_text == "⏰ Muddati o'tganlar":
        await seller_report(update, context, seller_name, "overdue")
    elif message_text == "📅 5 kun qolganlar":
        await seller_report(update, context, seller_name, 5)
    elif message_text == "📈 Barcha qarzdorliklar":
        await seller_report(update, context, seller_name, "all")
    elif message_text == "🔍 Mijoz qidirish":
        await handle_search_request(update, context)
    elif message_text == "🔄 Profil o'zgartirish":  # Yangi tugma
        await handle_profile_change_request(update, context)

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))

    admin_count = len(ADMIN_CHAT_IDS)
    print(f"🤖 Bot ishga tushdi...")
    print(f"👑 Adminlar soni: {admin_count} ta")
    print(f"🔐 Maxfiy rejim yoqilgan - faqat ruxsat berilgan foydalanuvchilar kirishi mumkin")
    logger.info(f"Bot ishga tushdi. {admin_count} ta admin mavjud. Maxfiy rejim yoqilgan.")

    application.run_polling()

if __name__ == "__main__":
    main()

# main.py (bot)

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
TZ_UZB = pytz.timezone('Asia/Tashkent')
REPORT_LIMIT = 8 # Hisobotni matn yoki Excelda yuborish chegarasi



logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- YORDAMCHI FUNKSIYALAR ---
def is_admin(user_id):
    """Foydalanuvchi admin ekanligini tekshirish"""
    return user_id in ADMIN_CHAT_IDS

async def send_message_to_all_admins(context: ContextTypes.DEFAULT_TYPE, message: str, parse_mode=None):
    """Barcha adminlarga xabar yuborish"""
    for admin_id in ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(admin_id, message, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Admin {admin_id} ga xabar yuborishda xatolik: {e}")

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
    # Update yoki CallbackQuery dan chat_id olish
    if hasattr(update_or_query, 'effective_chat'):
        chat_id = update_or_query.effective_chat.id
    else:
        # CallbackQuery bo'lsa
        chat_id = update_or_query.message.chat.id

    if not report_data:
        await context.bot.send_message(chat_id, f"✅ '{title}' bo'yicha aktiv qarzdorliklar topilmadi.")
        return

    total_amount = sum(debt.get('Qolgan Summa', 0) for debt in report_data)

    # Agar qatorlar soni limitdan kam bo'lsa, matn sifatida yuborish
    if len(report_data) <= REPORT_LIMIT:
        message = (
            f"**{title.upper()}**\n\n"
            f"🔢 **Jami:** {len(report_data)} ta\n"
            f"💵 **Umumiy summa:** {total_amount:,.0f} so'm\n\n"
        )
        for debt in report_data:
            payment_date = debt.get('To\'lov Muddati', 'N/A')
            deadline = debt.get('Muddati', 'N/A')
            message += (
                f"👤 **{debt.get('Mijoz Ismi', 'N/A')}** (Chek: {debt.get('Chek Raqami', 'N/A')})\n"
                f"📞 {debt.get('Mijoz Telefoni', 'N/A')}\n"
                f"💰 {debt.get('Qolgan Summa', 0):,.0f} so'm | "
                f"🗓️ {payment_date} ({deadline})\n\n"
            )
        await context.bot.send_message(chat_id, message, parse_mode='Markdown')

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
        [KeyboardButton("🔍 Mijoz qidirish")]  # Yangi tugma
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_seller_keyboard():
    keyboard = [
        [KeyboardButton("📊 Mening hisobotim"), KeyboardButton("⏰ Muddati o'tganlar")],
        [KeyboardButton("📅 5 kun qolganlar"), KeyboardButton("📈 Barcha qarzdorliklar")],
        [KeyboardButton("🔍 Mijoz qidirish")]  # Yangi tugma
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_seller_selection_keyboard():
    all_debts = load_json(DATA_FILE)
    if not all_debts:
        return None
    keyboard = []
    sellers = sorted(list(all_debts.keys()))
    for i in range(0, len(sellers), 2):
        row = [InlineKeyboardButton(sellers[i], callback_data=f"admin_seller_{sellers[i]}")]
        if i + 1 < len(sellers):
            row.append(InlineKeyboardButton(sellers[i + 1], callback_data=f"admin_seller_{sellers[i + 1]}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# --- SEARCH FUNKSIYALAR ---
async def handle_search_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz qidirish so'rovini ishlab chiqish"""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🔍 **Mijoz qidirish**\n\n"
        "Mijoz ismini yozing (kamida 2 ta harf):\n"
        "Masalan: *Ahad*, *Olim*, *Shohida* va h.k.\n\n"
        "❌ Bekor qilish uchun /cancel yozing",
        parse_mode='Markdown'
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
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='Markdown')
    else:
        await update.message.reply_text(message, parse_mode='Markdown')

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
            await query.edit_message_text(messages[0], parse_mode='Markdown')

            # Qolgan xabarlarni oddiy xabar sifatida yuborish
            for message in messages[1:]:
                await context.bot.send_message(query.message.chat.id, message, parse_mode='Markdown')

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
        await query.edit_message_text(message, reply_markup=keyboard, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Xabarni yangilashda xatolik: {e}")
        await query.answer("❌ Xabarni yangilashda xatolik")

# --- ESLATMALAR VA REJALASHTIRISH ---
async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Kunlik eslatmalarni yuborish boshlandi.")
    all_debts = load_json(DATA_FILE)
    sellers = load_json(SELLERS_FILE)

    for seller_name, chat_id in sellers.items():
        seller_debts = all_debts.get(seller_name, [])
        overdue_debts = [debt for debt in seller_debts if "o'tdi" in debt.get('Muddati', '')]

        if overdue_debts:
            message = "🔔 **Muddati o'tgan qarzdorliklar bo'yicha eslatma:**\n\n"
            for debt in overdue_debts:
                customer_name = debt.get('Mijoz Ismi', 'N/A')
                customer_phone = debt.get('Mijoz Telefoni', 'N/A')
                remaining_amount = debt.get('Qolgan Summa', 0)
                payment_date = debt.get('To\'lov Muddati', 'N/A')
                deadline = debt.get('Muddati', 'N/A')

                message += (
                    f"👤 **Mijoz:** {customer_name}\n"
                    f"📞 **Telefon:** {customer_phone}\n"
                    f"💰 **Qolgan qarz:** {remaining_amount:,.0f} so'm\n"
                    f"🗓️ **To'lov sanasi:** {payment_date} ({deadline})\n\n"
                )
            try:
                await context.bot.send_message(chat_id, message, parse_mode='Markdown')
                logger.info(f"'{seller_name}' ga eslatma yuborildi.")
            except Exception as e:
                logger.error(f"'{seller_name}' ga eslatma yuborishda xatolik: {e}")

async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
    """Rejalashtirilgan vazifa - ma'lumotlarni yangilash va eslatmalar yuborish"""
    await send_message_to_all_admins(context, "⏳ Reja bo'yicha ma'lumotlarni yangilash boshlandi...")
    success = await update_data_from_billz()
    if success:
        await send_daily_reminders(context)
        await send_message_to_all_admins(
            context, "✅ Ma'lumotlar muvaffaqiyatli yangilandi va eslatmalar yuborildi."
        )
    else:
        await send_message_to_all_admins(context, "❌ Reja bo'yicha ma'lumotlarni yangilashda xatolik yuz berdi.")

# --- BOT BUYRUQLARI VA HANDLERLAR ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "Foydalanuvchi"

    if is_admin(user_id):
        await update.message.reply_text(f"👋 Salom, {user_name}! Siz administratorsiz.", reply_markup=create_admin_keyboard())
        return

    sellers = load_json(SELLERS_FILE)
    seller_name = next((name for name, chat_id in sellers.items() if chat_id == user_id), None)

    if seller_name:
        await update.message.reply_text(f"👋 Xush kelibsiz, {seller_name}!", reply_markup=create_seller_keyboard())
        return

    all_debts = load_json(DATA_FILE)
    if not all_debts:
        await update.message.reply_text("❌ Hozircha ma'lumotlar bazasi bo'sh. Administrator yangilashini kuting.")
        return

    all_seller_names = list(all_debts.keys())
    registered_seller_names = list(sellers.keys())
    unregistered_sellers = sorted([name for name in all_seller_names if name not in registered_seller_names])

    if not unregistered_sellers:
        await update.message.reply_text("✅ Barcha sotuvchilar ro'yxatdan o'tgan.")
        return

    keyboard = [[InlineKeyboardButton(name, callback_data=f"register_{name}")] for name in unregistered_sellers]
    await update.message.reply_text("🔐 Tizimga kirish uchun o'zingizni tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qidiruv jarayonini bekor qilish"""
    user_id = update.effective_user.id

    # Search natijalarini tozalash
    if user_id in user_all_search_results:
        del user_all_search_results[user_id]
    if user_id in user_current_page:
        del user_current_page[user_id]

    await update.message.reply_text("❌ Qidiruv bekor qilindi.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    message_text = update.message.text

    # Qidiruv so'zi ekanligini tekshirish
    if is_search_query(message_text):
        await handle_search_query(update, context, message_text)
        return

    if is_admin(user_id):
        await handle_admin_message(update, context, message_text)
    else:
        await handle_seller_message(update, context, message_text)

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
        f"💵 **Umumiy summa:** {total_amount:,.0f} so'm\n"
        f"⚡ **Muddati o'tganlar:** {overdue_count} ta\n"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def admin_sellers_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sellers = load_json(SELLERS_FILE)
    if not sellers:
        await update.message.reply_text("❌ Hech qanday sotuvchi ro'yxatdan o'tmagan.")
        return
    message = "👥 **RO'YXATDAN O'TGAN SOTUVCHILAR:**\n\n"
    for i, (seller_name, chat_id) in enumerate(sellers.items(), 1):
        message += f"{i}. {seller_name} (ID: {chat_id})\n"
    await update.message.reply_text(message)

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

async def bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id): return
    try:
        last_update = datetime.fromtimestamp(os.path.getmtime(DATA_FILE)).astimezone(TZ_UZB).strftime('%Y-%m-%d %H:%M:%S')
    except FileNotFoundError:
        last_update = "Hali yangilanmagan"
    sellers, all_debts = load_json(SELLERS_FILE), load_json(DATA_FILE)
    total_debts = sum(len(d) for d in all_debts.values())

    # Admin IDs ro'yxatini ko'rsatish
    admin_list = ", ".join(str(admin_id) for admin_id in ADMIN_CHAT_IDS)

    message = (
        f"📈 **BOT STATISTIKASI**\n\n"
        f"📊 **Oxirgi yangilanish:** {last_update}\n"
        f"👥 **Ro'yxatdan o'tgan sotuvchilar:** {len(sellers)} ta\n"
        f"💰 **Jami aktiv qarzdorliklar:** {total_debts} ta\n"
        f"🔐 **Adminlar:** {admin_list}"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def post_init(application: Application):
    scheduler = AsyncIOScheduler(timezone=TZ_UZB)
    scheduler.add_job(scheduled_job, 'cron', hour=6, minute=0, args=[application])
    scheduler.start()
    logger.info("Rejalashtiruvchi muvaffaqiyatli ishga tushdi (Har kuni soat 06:00).")
    # Bot ishga tushganda bir marta ma'lumotlarni yangilash
    await send_message_to_all_admins(application, "🤖 Bot qayta ishga tushdi. Ma'lumotlar birinchi marta yangilanmoqda...")
    await update_data_from_billz()
    await send_message_to_all_admins(application, "✅ Ma'lumotlar yangilandi.")

# Asosiy funksiyalar
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("register_"):
        seller_name = query.data.replace("register_", "")
        user_id = query.from_user.id
        sellers = load_json(SELLERS_FILE)
        sellers[seller_name] = user_id
        save_json(sellers, SELLERS_FILE)
        await query.edit_message_text(text=f"✅ Rahmat, {seller_name}! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.")
        await send_message_to_all_admins(context, f"📢 Yangi sotuvchi ro'yxatdan o'tdi: {seller_name}")
        await context.bot.send_message(user_id, "🎯 **Sizning panel** tayyor!", reply_markup=create_seller_keyboard())

    elif query.data.startswith("admin_seller_"):
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

async def handle_seller_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    user_id = update.effective_chat.id
    sellers = load_json(SELLERS_FILE)
    seller_name = next((name for name, chat_id in sellers.items() if chat_id == user_id), None)

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

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    print(f"🤖 Bot ishga tushdi...")
    print(f"👑 Adminlar: {', '.join(str(admin_id) for admin_id in ADMIN_CHAT_IDS)}")
    application.run_polling()

if __name__ == "__main__":
    main()

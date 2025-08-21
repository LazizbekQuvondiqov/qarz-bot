# api_handler.py

import logging
import os
import json
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv

# --- ⚙️ API SOZLAMALARI ⚙️ ---
load_dotenv()
BILLZ_SECRET_TOKEN = os.getenv("BILLZ_SECRET_TOKEN")

if not BILLZ_SECRET_TOKEN:
    raise ValueError("XATOLIK: .env faylida BILLZ_SECRET_TOKEN topilmadi yoki bo'sh.")

BASE_URL = "https://api-admin.billz.ai/v1"
DATA_FILE = "data.json"
TZ_UZB = pytz.timezone('Asia/Tashkent')

# Logging sozlash
logger = logging.getLogger(__name__)

# --- JSON FAYL BILAN ISHLASH FUNKSIYALARI ---
def save_json(data, filename):
    """JSON ma'lumotlarni faylga saqlash"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- BILLZ API BILAN ISHLASH FUNKSIYALARI ---
async def get_access_token():
    """BILLZ API dan access token olish"""
    url = f"{BASE_URL}/auth/login"
    payload = {"secret_token": BILLZ_SECRET_TOKEN}
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        logger.info("Access token muvaffaqiyatli olindi.")
        return response.json()['data']['access_token']
    except requests.exceptions.RequestException as e:
        logger.error(f"Access token olishda xatolik: {e}")
        return None

async def fetch_all_debts(access_token):
    """Barcha qarzdorliklarni olish"""
    headers = {"Authorization": f"Bearer {access_token}"}
    all_debts_data, page, url = [], 1, f"{BASE_URL}/debt"
    logger.info("Qarzdorliklarni olish jarayoni boshlandi...")
    while True:
        try:
            response = requests.get(f"{url}?page={page}&limit=100", headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json().get('data', [])
            if not data:
                break
            all_debts_data.extend(data)
            logger.info(f"Sahifa {page}: {len(data)} ta qarz olindi. Jami: {len(all_debts_data)}")
            page += 1
        except requests.exceptions.RequestException as e:
            logger.error(f"Qarzdorliklarni olishda xatolik: {e}")
            break
    logger.info(f"Jami {len(all_debts_data)} ta qarzdorlik olindi.")
    return all_debts_data

def process_debt_data(all_debts_data):
    """Qarzdorlik ma'lumotlarini qayta ishlash va Excel formatiga tayyorlash"""
    logger.info("Ma'lumotlarni qayta ishlash boshlandi...")
    processed_data = {}
    today = datetime.now(TZ_UZB).date()

    status_translation = {
        'partial_paid': 'Частично оплачен',
        'unpaid': 'Не оплачен',
        'overdue': 'Просрочен'
    }

    for debt in all_debts_data:
        debt_status = debt.get('status')
        if debt_status == 'fully_paid':
            continue

        created_by = debt.get('created_by', {})
        seller_name = f"{created_by.get('first_name', '')} {created_by.get('last_name', '')}".strip() or "Noma'lum"

        # Sotuvchi bo'yicha guruhlash uchun
        if not seller_name or seller_name == "Noma'lum":
            continue

        debt_amount = debt.get('amount', 0)
        paid_amount = debt.get('paid_amount', 0)
        unpaid_amount = debt_amount - paid_amount

        created_at_str = debt.get('created_at', '')
        repayment_date_str = debt.get('repayment_date', '')
        days_diff_text = "N/A"

        # Mijoz ismini ham olamiz, botdagi matnli xabarlar uchun kerak bo'ladi
        customer = debt.get('customer', {})
        client_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip() or "Noma'lum mijoz"

        try:
            created_at = datetime.fromisoformat(created_at_str.replace('Z', '')).astimezone(TZ_UZB).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            created_at = created_at_str.split('T')[0] if 'T' in str(created_at_str) else created_at_str

        try:
            repayment_date_obj = datetime.fromisoformat(repayment_date_str.replace('Z', '')).date()
            repayment_date = repayment_date_obj.strftime('%Y-%m-%d')
            days_diff = (repayment_date_obj - today).days

            if days_diff < 0:
                days_diff_text = f"{abs(days_diff)} kun o'tdi"
            elif days_diff == 0:
                days_diff_text = "Bugun"
            else:
                days_diff_text = f"{days_diff} kun qoldi"
        except (ValueError, TypeError):
            repayment_date = repayment_date_str.split('T')[0] if 'T' in str(repayment_date_str) else repayment_date_str

        debt_info = {
            # Excel uchun ustunlar
            'Chek Raqami': debt.get('order_number', 'N/A'),
            'Sotuvchi Ismi': seller_name,
            'Yaratilgan Sana': created_at,
            'Qarz Summasi': debt_amount,
            'To\'langan Summa': paid_amount,
            'Qolgan Summa': unpaid_amount,
            'Qarz Statusi': status_translation.get(debt_status, debt_status),
            'To\'lov Muddati': repayment_date,
            'Muddati': days_diff_text,
            'Mijoz Telefoni': ", ".join(debt.get('contact_phones', []) or ["N/A"]),
            # Botda matnli xabar uchun qo'shimcha ma'lumot
            'Mijoz Ismi': client_name,
        }

        if seller_name not in processed_data:
            processed_data[seller_name] = []
        processed_data[seller_name].append(debt_info)

    logger.info(f"Ma'lumotlarni qayta ishlash yakunlandi. Jami sotuvchilar: {len(processed_data)}")
    return processed_data

async def update_data_from_billz():
    """BILLZ API dan ma'lumotlarni yangilash - asosiy funksiya"""
    logger.info("🔄 Ma'lumotlarni yangilash jarayoni boshlandi...")
    try:
        access_token = await get_access_token()
        if not access_token:
            logger.error("❌ Access token olinmadi - jarayon to'xtatildi.")
            return False

        all_debts_data = await fetch_all_debts(access_token)
        if not all_debts_data:
            logger.warning("⚠️ Hech qanday qarzdorlik ma'lumoti olinmadi.")
            save_json({}, DATA_FILE)
            return True

        processed_data = process_debt_data(all_debts_data)
        save_json(processed_data, DATA_FILE)

        logger.info(f"✅ Ma'lumotlar muvaffaqiyatli yangilandi! ({len(processed_data)} ta sotuvchi)")
        return True
    except Exception as e:
        logger.error(f"❌ Ma'lumotlarni yangilashda kutilmagan xatolik: {e}")
        return False

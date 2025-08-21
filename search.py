# search.py

import json
import logging
from typing import List, Dict, Any
from difflib import SequenceMatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Global o'zgaruvchi - har bir foydalanuvchi uchun search natijalarini saqlash
user_all_search_results = {}
user_current_page = {}

def load_json(filename: str) -> dict:
    """JSON faylni yuklash"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def normalize_name(name: str) -> str:
    """Ismni normallashtirich (kichik harf, probellarsiz)"""
    return name.lower().strip().replace("  ", " ")

def similarity_score(a: str, b: str) -> float:
    """Ikki matn orasidagi o'xshashlik darajasini hisoblash"""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()

def search_customers_by_name(search_query: str, data_file: str = "data.json", limit: int = 5, min_similarity: float = 0.4) -> List[Dict[str, Any]]:
    """
    Mijoz ismini qidirish funksiyasi

    Args:
        search_query: Qidiruv so'zi
        data_file: Ma'lumotlar fayli
        limit: Har bir sahifadagi natijalar soni
        min_similarity: Minimal o'xshashlik darajasi (0.4 = 40%)

    Returns:
        Topilgan mijozlar ro'yxati
    """
    all_debts = load_json(data_file)
    if not all_debts:
        return []

    search_query_normalized = normalize_name(search_query)
    if len(search_query_normalized) < 2:  # Juda qisqa qidiruv so'zlari uchun
        return []

    results = []
    seen_customers = set()  # Takroriy mijozlarni oldini olish uchun

    # Barcha sotuvchilar va ularning qarzdorliklari bo'ylab qidirish
    for seller_name, debts in all_debts.items():
        for debt in debts:
            customer_name = debt.get('Mijoz Ismi', '').strip()
            customer_phone = debt.get('Mijoz Telefoni', 'N/A')

            if not customer_name or customer_name == 'Noma\'lum mijoz':
                continue

            # Takroriy mijozlarni tekshirish
            customer_key = f"{customer_name}_{customer_phone}"
            if customer_key in seen_customers:
                continue

            # O'xshashlik darajasini hisoblash
            similarity = similarity_score(search_query_normalized, customer_name)

            # Qisman moslik ham tekshirish (ismning bir qismi boshlanishi)
            partial_match = normalize_name(customer_name).startswith(search_query_normalized)

            if similarity >= min_similarity or partial_match:
                results.append({
                    'customer_name': customer_name,
                    'customer_phone': customer_phone,
                    'seller_name': seller_name,
                    'similarity': similarity,
                    'remaining_amount': debt.get('Qolgan Summa', 0),
                    'payment_date': debt.get('To\'lov Muddati', 'N/A'),
                    'deadline': debt.get('Muddati', 'N/A'),
                    'check_number': debt.get('Chek Raqami', 'N/A'),
                    'debt_status': debt.get('Qarz Statusi', 'N/A')
                })
                seen_customers.add(customer_key)

    # Natijalarni o'xshashlik darajasi bo'yicha saralash
    results.sort(key=lambda x: x['similarity'], reverse=True)

    logger.info(f"'{search_query}' uchun jami {len(results)} ta mijoz topildi")
    return results

def get_paginated_results(user_id: int, page: int = 0, per_page: int = 5) -> tuple[List[Dict[str, Any]], bool]:
    """
    Sahifalangan natijalarni olish

    Args:
        user_id: Foydalanuvchi ID
        page: Sahifa raqami (0 dan boshlab)
        per_page: Har sahifadagi natijalar soni

    Returns:
        tuple: (sahifa_natijalari, keyingi_sahifa_bormi)
    """
    all_results = user_all_search_results.get(user_id, [])
    start_index = page * per_page
    end_index = start_index + per_page

    page_results = all_results[start_index:end_index]
    has_more = end_index < len(all_results)

    return page_results, has_more

def get_customer_debts(customer_name: str, customer_phone: str, data_file: str = "data.json") -> List[Dict[str, Any]]:
    """
    Tanlangan mijozning barcha qarzdorliklarini olish

    Args:
        customer_name: Mijoz ismi
        customer_phone: Mijoz telefoni
        data_file: Ma'lumotlar fayli

    Returns:
        Mijozning barcha qarzdorliklari
    """
    all_debts = load_json(data_file)
    if not all_debts:
        return []

    customer_debts = []

    for seller_name, debts in all_debts.items():
        for debt in debts:
            if (debt.get('Mijoz Ismi', '') == customer_name and
                debt.get('Mijoz Telefoni', '') == customer_phone):
                customer_debts.append(debt)

    return customer_debts

def create_search_results_keyboard(page_results: List[Dict[str, Any]], user_id: int, current_page: int, has_more: bool) -> InlineKeyboardMarkup:
    """
    Qidiruv natijalar uchun inline keyboard yaratish (sahifalash bilan)

    Args:
        page_results: Joriy sahifa natijalari
        user_id: Foydalanuvchi ID
        current_page: Joriy sahifa raqami
        has_more: Keyingi sahifa bormi

    Returns:
        InlineKeyboardMarkup
    """
    if not page_results:
        return None

    keyboard = []

    start_index = current_page * 5
    for i, result in enumerate(page_results):
        customer_name = result['customer_name']
        remaining_amount = result['remaining_amount']

        button_text = f"👤 {customer_name}"
        if len(customer_name) > 18:
            button_text = f"👤 {customer_name[:15]}..."

        button_text += f" ({remaining_amount:,.0f})"

        actual_index = start_index + i
        callback_data = f"customer_select_{actual_index}"

        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=callback_data
            )
        ])

    navigation_row = []

    if current_page > 0:
        navigation_row.append(
            InlineKeyboardButton("⬅️ Oldingi", callback_data=f"search_prev_{current_page}")
        )

    if has_more:
        navigation_row.append(
            InlineKeyboardButton("➡️ Keyingi", callback_data=f"search_next_{current_page}")
        )

    if navigation_row:
        keyboard.append(navigation_row)

    total_results = len(user_all_search_results.get(user_id, []))
    total_pages = (total_results + 4) // 5
    info_row = [
        InlineKeyboardButton(
            f"📄 {current_page + 1}/{total_pages} ({total_results} ta)",
            callback_data="search_info"
        )
    ]
    keyboard.append(info_row)

    # Bekor qilish tugmasi
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="search_cancel")])

    return InlineKeyboardMarkup(keyboard)

def format_search_results_message(page_results: List[Dict[str, Any]], search_query: str, current_page: int, total_results: int) -> str:
    """
    Qidiruv natijalar uchun xabar formatini yaratish

    Args:
        page_results: Joriy sahifa natijalari
        search_query: Qidiruv so'zi
        current_page: Joriy sahifa raqami
        total_results: Jami natijalar soni

    Returns:
        Formatlangan xabar matni
    """
    if not page_results:
        return f"❌ '{search_query}' bo'yicha mijozlar topilmadi."

    total_pages = (total_results + 4) // 5
    message = (
        f"🔍 **'{search_query}'** bo'yicha natijalar\n"
        f"📄 Sahifa {current_page + 1}/{total_pages} (Jami: {total_results} ta)\n\n"
        "👇 Batafsil ma'lumot uchun mijozni tanlang:"
    )

    return message

def format_customer_details(customer_debts: List[Dict[str, Any]], customer_name: str) -> List[str]:
    """
    Mijozning batafsil ma'lumotlarini formatlash (bo'laklarga ajratib)

    Args:
        customer_debts: Mijozning qarzdorliklari
        customer_name: Mijoz ismi

    Returns:
        Formatlangan xabarlar ro'yxati
    """
    if not customer_debts:
        return [f"❌ {customer_name} uchun qarzdorliklar topilmadi."]

    total_debt = sum(debt.get('Qolgan Summa', 0) for debt in customer_debts)
    total_original = sum(debt.get('Qarz Summasi', 0) for debt in customer_debts)
    total_paid = sum(debt.get('To\'langan Summa', 0) for debt in customer_debts)

    # Asosiy ma'lumotlar
    header_message = (
        f"👤 **{customer_name.upper()}**\n\n"
        f"📞 **Telefon:** {customer_debts[0].get('Mijoz Telefoni', 'N/A')}\n"
        f"💸 **Umumiy qarz:** {total_original:,.0f} so'm\n"
        f"✅ **To'langan:** {total_paid:,.0f} so'm\n"
        f"💰 **Qolgan:** {total_debt:,.0f} so'm\n"
        f"🔢 **Qarzdorliklar soni:** {len(customer_debts)} ta\n\n"
        "**📋 BATAFSIL MA'LUMOTLAR:**"
    )

    messages = [header_message]
    current_message = ""

    for i, debt in enumerate(customer_debts, 1):
        check_number = debt.get('Chek Raqami', 'N/A')
        original_amount = debt.get('Qarz Summasi', 0)
        paid_amount = debt.get('To\'langan Summa', 0)
        remaining_amount = debt.get('Qolgan Summa', 0)
        payment_date = debt.get('To\'lov Muddati', 'N/A')
        deadline = debt.get('Muddati', 'N/A')
        seller_name = debt.get('Sotuvchi Ismi', 'N/A')
        debt_status = debt.get('Qarz Statusi', 'N/A')
        created_date = debt.get('Yaratilgan Sana', 'N/A')

        debt_info = (
            f"\n{i}. **Chek:** {check_number} ({created_date})\n"
            f"   💸 Umumiy: {original_amount:,.0f} so'm\n"
            f"   ✅ To'langan: {paid_amount:,.0f} so'm\n"
            f"   💰 Qolgan: {remaining_amount:,.0f} so'm\n"
            f"   🗓️ Muddat: {payment_date} ({deadline})\n"
            f"   👨‍💼 Sotuvchi: {seller_name}\n"
            f"   📊 Status: {debt_status}\n"
        )

        # Agar xabar juda uzun bo'lsa, yangi xabar boshlash
        if len(current_message + debt_info) > 4000:  # Telegram limiti 4096
            messages.append(current_message)
            current_message = debt_info
        else:
            current_message += debt_info

    # Oxirgi xabarni qo'shish
    if current_message:
        messages.append(current_message)

    return messages

def is_search_query(text: str) -> bool:
    """
    Matn qidiruv so'zi ekanligini aniqlash

    Args:
        text: Tekshiriladigan matn

    Returns:
        True agar qidiruv so'zi bo'lsa
    """
    if not text or len(text.strip()) < 2:
        return False

    button_texts = [
        "📊 Mening hisobotim", "⏰ Muddati o'tganlar", "📅 5 kun qolganlar",
        "📈 Barcha qarzdorliklar", "📊 Umumiy hisobot", "👥 Sotuvchilar ro'yxati",
        "🔄 Ma'lumotlarni yangilash", "📈 Bot statistikasi", "💰 Sotuvchi bo'yicha hisobot",
        "⚡ Muddati o'tganlar", "🔍 Mijoz qidirish"
    ]

    if text in button_texts:
        return False

    if text.startswith('/'):
        return False

    if text.replace(' ', '').isalpha():
        return True

    return False

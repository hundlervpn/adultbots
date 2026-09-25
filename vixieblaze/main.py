"""Telegram subscription bot: Telegram Stars + Crypto Bot (Crypto Pay).

All settings are in the CONFIG block below — no .env file needed.
The bot must be an administrator of the private channel with permission to
invite users.
"""

import asyncio
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.types import FSInputFile


BOT_DIR = Path(__file__).resolve().parent

# ================== НАСТРОЙКИ БОТА (всё здесь, .env не нужен) ==================
BOT_TOKEN = ""  # <-- ВСТАВЬ токен от @BotFather (или в local_config.py)
CHANNEL_ID = -1001234567890  # ID закрытого канала
CRYPTO_PAY_TOKEN = ""  # <-- ВСТАВЬ токен Crypto Pay (@CryptoBot -> Crypto Pay -> Create App)
CRYPTO_ASSET = "USDT"  # валюта крипто-счетов
CRYSTALPAY_AUTH_LOGIN = ""  # логин кассы Crystal Pay
CRYSTALPAY_AUTH_SECRET = ""  # секрет кассы Crystal Pay
DB_PATH = BOT_DIR / "payments.sqlite3"  # файл базы оплат
WELCOME_IMAGE = BOT_DIR / "welcome.png"  # картинка на старте (нет файла — будет текст)

# Секреты можно вынести в local_config.py рядом с main.py (в git не попадает):
# достаточно переопределить нужные переменные, например BOT_TOKEN = "...".
try:
    from local_config import *  # noqa: F401,F403
except ImportError:
    pass
# ===============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass(frozen=True)
class Plan:
    key: str
    title: str
    days: int | None
    stars: int
    crypto_amount: str
    rub_amount: str

    @property
    def period(self) -> str:
        return "без ограничения по сроку" if self.days is None else f"{self.days} дней"


# Цены: Stars — целые числа, crypto — в CRYPTO_ASSET, rub — рубли.
PLANS = {
    "1m": Plan("1m", "🔥 1 месяц", 30, 100, "1", "199"),
    "6m": Plan("6m", "💎 6 месяцев", 180, 500, "5", "799"),
    "life": Plan("life", "♾️ Навсегда", None, 1000, "10", "2999"),
}

router = Router()

# Stars invoices must be sent as separate messages (Telegram limitation), so we
# remember the user's last bot screen and later turn it into the access screen —
# one message keeps morphing, like in the crypto flow.
_last_screen: dict[int, Message] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with closing(db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                plan_key TEXT NOT NULL,
                amount TEXT NOT NULL,
                method TEXT NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                paid_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                language TEXT NOT NULL DEFAULT 'ru'
            );
            """
        )
        conn.commit()


TEXT = {
    "ru": {
        "welcome": """Привет 😏\n\nНу что, выбирай, насколько надолго хочешь остаться со мной\n\n🔥 <b>На месяц</b>\nЗайти, посмотреть всё самое интересное и кайфануть целый месяц 😉\n\n💎 <b>На полгода</b>\nЕсли одного месяца мало 😏 Получишь доступ сразу на 6 месяцев.\n\n♾️ <b>Навсегда</b>\nЗаплатил один раз — и остаёшься со мной без ограничений""",
        "plans": {"1m": "🔥 1 месяц", "6m": "💎 6 месяцев", "life": "♾️ Навсегда"},
        "periods": {"1m": "30 дней", "6m": "6 месяцев", "life": "Навсегда"},
        "choose_method": "Выберите способ оплаты:", "crypto": "₿ Криптовалюта", "rub": "💳 Оплата в рублях", "back": "← Назад",
        "language": "🇬🇧 English", "invoice": "Счёт создан. После оплаты нажмите «Проверить оплату».",
        "stars_invoice": "⭐ Счёт на {amount} Stars создан. Оплатите его в сообщении ниже ⬇",
        "pay": "💳 Оплатить криптовалютой", "check": "Проверить оплату", "paid": "✅ <b>Оплата получена!</b>",
        "access": "🔓 Получить доступ", "expires": "Срок доступа", "rub_invoice": "Счёт в рублях создан. После оплаты нажмите «Проверить оплату».",
        "channel_access_description": "Доступ к закрытому каналу: {period}",
        "crypto_description": "{plan}: доступ к закрытому каналу",
        "plan_not_found": "Тариф не найден",
        "invoice_not_found": "Счёт не найден",
        "invoice_create_failed": "Не удалось создать счёт. Попробуйте позже.",
        "invoice_check_failed": "Не удалось проверить оплату. Попробуйте позже.",
        "pre_checkout_invalid": "Счёт больше недействителен.",
        "payment_confirmed": "Оплата подтверждена",
        "invoice_closed": "Счёт отменён или истёк. Создайте новый.",
        "crypto_pending": "Оплата пока в ожидании. Подождите подтверждения Crypto Bot.",
        "payment_pending": "Оплата пока в ожидании.",
        "rub_temporarily_disabled": "Оплата в рублях временно отключена.",
        "access_message": "✅ <b>Оплата получена!</b>\n\nТариф: {plan}\nСрок доступа: {expiry}\n\nНажми кнопку ниже, чтобы получить доступ к закрытому каналу.",
        "expires_until": "до %d.%m.%Y %H:%M",
        "rub_unavailable": "Рублёвые способы оплаты сейчас недоступны. Попробуйте позже.",
        "payment_failed": "Платёж отклонён или завершился ошибкой. Подробнее смотрите на странице оплаты.",
        "payment_unavailable": "Выбранный способ оплаты сейчас недоступен. Выберите другой или попробуйте позже.",
        "payment_wrong_amount": "Для оплаты счёта требуется доплата. Проверьте сумму на странице оплаты.",
    },
    "en": {
        "welcome": """Welcome! Choose a plan:\n\n🔥 <b>1 month</b>\nPrivate-channel access for 30 days.\n\n💎 <b>6 months</b>\nPrivate-channel access for 6 months.\n\n♾️ <b>Lifetime</b>\nOne payment — unlimited access.\n\n💳 Pay with <b>Telegram Stars</b> or <b>cryptocurrency</b>.""",
        "plans": {"1m": "🔥 1 month", "6m": "💎 6 months", "life": "♾️ Lifetime"},
        "periods": {"1m": "30 days", "6m": "6 months", "life": "Lifetime"},
        "choose_method": "Choose a payment method:", "crypto": "₿ Cryptocurrency", "rub": "💳 Pay in RUB", "back": "← Back",
        "language": "🇷🇺 Русский", "invoice": "Invoice created. After payment, press “Check payment” below.",
        "stars_invoice": "⭐ Invoice for {amount} Stars created. Pay it in the message below ⬇",
        "pay": "💳 Pay with cryptocurrency", "check": "Check payment", "paid": "✅ <b>Payment received!</b>",
        "access": "🔓 Get access", "expires": "Access period", "rub_invoice": "RUB invoice created. After payment, press “Check payment”.",
        "channel_access_description": "Private-channel access: {period}",
        "crypto_description": "{plan}: private-channel access",
        "plan_not_found": "Plan not found.",
        "invoice_not_found": "Invoice not found.",
        "invoice_create_failed": "Could not create the invoice. Please try again later.",
        "invoice_check_failed": "Could not check the payment. Please try again later.",
        "pre_checkout_invalid": "This invoice is no longer valid.",
        "payment_confirmed": "Payment confirmed.",
        "invoice_closed": "The invoice was cancelled or expired. Create a new one.",
        "crypto_pending": "Payment is pending. Wait for Crypto Bot confirmation.",
        "payment_pending": "Payment is still pending.",
        "rub_temporarily_disabled": "RUB payments are temporarily disabled.",
        "access_message": "✅ <b>Payment received!</b>\n\nPlan: {plan}\nAccess period: {expiry}\n\nTap the button below to access the private channel.",
        "expires_until": "until %b %d, %Y at %H:%M",
        "rub_unavailable": "RUB payment methods are unavailable right now. Please try again later.",
        "payment_failed": "The payment was declined or failed. Check the payment page for details.",
        "payment_unavailable": "The selected payment method is unavailable. Choose another one or try later.",
        "payment_wrong_amount": "An additional payment is required. Check the amount on the payment page.",
    },
}


def language(user_id: int) -> str:
    with closing(db()) as conn:
        row = conn.execute("SELECT language FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    return row["language"] if row and row["language"] in TEXT else "ru"


def menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=TEXT[lang]["plans"][plan.key], callback_data=f"plan:{plan.key}")] for plan in PLANS.values()],
        [InlineKeyboardButton(text=TEXT[lang]["language"], callback_data=f"lang:{'en' if lang == 'ru' else 'ru'}:menu")],
    ])


def methods(plan_key: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"stars:{plan_key}")],
        [InlineKeyboardButton(text=TEXT[lang]["crypto"], callback_data=f"crypto:{plan_key}")],
        [InlineKeyboardButton(text=TEXT[lang]["back"], callback_data="menu")],
        [InlineKeyboardButton(text=TEXT[lang]["language"], callback_data=f"lang:{'en' if lang == 'ru' else 'ru'}:plan:{plan_key}")],
    ])


def plan_text(plan: Plan, lang: str) -> str:
    return f"<b>{TEXT[lang]['plans'][plan.key]}</b>\n{TEXT[lang]['expires']}: {TEXT[lang]['periods'][plan.key]}\n\n{TEXT[lang]['choose_method']}"


def valid_rub_price(amount: str) -> bool:
    try:
        return float(amount) > 0
    except ValueError:
        return False


@router.message(CommandStart())
async def start(message: Message) -> None:
    lang = language(message.from_user.id)
    if WELCOME_IMAGE.is_file():
        sent = await message.answer_photo(FSInputFile(WELCOME_IMAGE), caption=TEXT[lang]["welcome"], reply_markup=menu(lang))
    else:
        sent = await message.answer(TEXT[lang]["welcome"], reply_markup=menu(lang))
    _last_screen[message.from_user.id] = sent


async def edit_screen(message: Message, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    """Edit either a text message or the caption of the welcome photo."""
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await message.edit_text(text, reply_markup=reply_markup)


async def edit_menu_message(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    """Edit the current bot screen and remember it as the user's last screen."""
    await edit_screen(callback.message, text, reply_markup)
    _last_screen[callback.from_user.id] = callback.message


@router.callback_query(F.data == "menu")
async def show_menu(callback: CallbackQuery) -> None:
    lang = language(callback.from_user.id)
    await edit_menu_message(callback, TEXT[lang]["welcome"], menu(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def change_language(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    lang = parts[1] if len(parts) > 1 else ""
    if lang not in TEXT:
        await callback.answer()
        return
    with closing(db()) as conn:
        conn.execute("INSERT INTO user_settings(user_id, language) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET language=excluded.language", (callback.from_user.id, lang))
        conn.commit()
    if len(parts) == 4 and parts[2] == "plan" and parts[3] in PLANS:
        plan = PLANS[parts[3]]
        await edit_menu_message(callback, plan_text(plan, lang), methods(plan.key, lang))
    else:
        await edit_menu_message(callback, TEXT[lang]["welcome"], menu(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def choose_plan(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    lang = language(callback.from_user.id)
    plan = PLANS.get(key)
    if not plan:
        await callback.answer(TEXT[lang]["plan_not_found"], show_alert=True)
        return
    await edit_menu_message(callback, plan_text(plan, lang), methods(key, lang))
    await callback.answer()


@router.callback_query(F.data.startswith("stars:"))
async def buy_stars(callback: CallbackQuery, bot: Bot) -> None:
    key = callback.data.split(":", 1)[1]
    lang = language(callback.from_user.id)
    plan = PLANS.get(key)
    if not plan:
        await callback.answer(TEXT[lang]["plan_not_found"], show_alert=True)
        return
    plan_title = TEXT[lang]["plans"][key]
    payload = f"stars:{key}:{callback.from_user.id}"
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=plan_title,
        description=TEXT[lang]["channel_access_description"].format(period=TEXT[lang]["periods"][key]),
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=plan_title, amount=plan.stars)],
        provider_token="",
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXT[lang]["back"], callback_data="menu")],
        [InlineKeyboardButton(text=TEXT[lang]["language"], callback_data=f"lang:{'en' if lang == 'ru' else 'ru'}:menu")],
    ])
    await edit_menu_message(callback, TEXT[lang]["stars_invoice"].format(amount=plan.stars), keyboard)
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, bot: Bot) -> None:
    # Telegram asks this before it charges Stars. Validate our payload only.
    parts = query.invoice_payload.split(":")
    valid = len(parts) == 3 and parts[0] == "stars" and parts[1] in PLANS and parts[2] == str(query.from_user.id)
    lang = language(query.from_user.id)
    await bot.answer_pre_checkout_query(query.id, ok=valid, error_message=None if valid else TEXT[lang]["pre_checkout_invalid"])


def current_access(user_id: int) -> tuple[bool, datetime | None]:
    """Return (has_lifetime_access, latest_finite_expiry)."""
    with closing(db()) as conn:
        lifetime = conn.execute(
            "SELECT 1 FROM payments WHERE user_id=? AND status='paid' AND expires_at IS NULL LIMIT 1",
            (user_id,),
        ).fetchone()
        if lifetime:
            return True, None
        row = conn.execute(
            "SELECT expires_at FROM payments WHERE user_id=? AND status='paid' AND expires_at IS NOT NULL "
            "ORDER BY expires_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return False, datetime.fromisoformat(row["expires_at"]) if row else None


def grant_purchase(user_id: int, plan: Plan, amount: str, method: str, payment_id: str) -> datetime | None:
    """Stores a confirmed payment once and returns the effective expiration."""
    now = utcnow()
    with closing(db()) as conn:
        if conn.execute("SELECT 1 FROM payments WHERE payment_id=?", (payment_id,)).fetchone():
            row = conn.execute("SELECT expires_at FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
            return datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
        has_lifetime, current = current_access(user_id)
        if plan.days is None or has_lifetime:
            expiration = None
        else:
            expiration = max(now, current or now) + timedelta(days=plan.days)
        conn.execute(
            "INSERT INTO payments(user_id,plan_key,amount,method,payment_id,status,expires_at,created_at,paid_at) VALUES(?,?,?,?,?,'paid',?,?,?)",
            (user_id, plan.key, amount, method, payment_id, expiration.isoformat() if expiration else None, now.isoformat(), now.isoformat()),
        )
        conn.commit()
    return expiration


async def send_access(message: Message, bot: Bot, user_id: int, plan: Plan, expiration: datetime | None) -> None:
    # Single-use invite; its own expiry matches the subscription where applicable.
    link = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name=f"purchase-{user_id}",
        member_limit=1,
        expire_date=int(expiration.timestamp()) if expiration else None,
    )
    lang = language(user_id)
    expiry_text = TEXT[lang]["periods"]["life"] if expiration is None else expiration.astimezone().strftime(TEXT[lang]["expires_until"])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=TEXT[lang]["access"], url=link.invite_link)]])
    text = TEXT[lang]["access_message"].format(plan=TEXT[lang]["plans"][plan.key], expiry=expiry_text)
    await message.answer(text, reply_markup=keyboard)


async def access_screen(bot: Bot, user_id: int, plan: Plan, expiration: datetime | None) -> tuple[str, InlineKeyboardMarkup]:
    """Create the single-use invite link and build the 'payment received' screen."""
    link = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name=f"purchase-{user_id}",
        member_limit=1,
        expire_date=int(expiration.timestamp()) if expiration else None,
    )
    lang = language(user_id)
    expiry = TEXT[lang]["periods"][plan.key] if expiration is not None else TEXT[lang]["periods"]["life"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=TEXT[lang]["access"], url=link.invite_link)]])
    text = f"{TEXT[lang]['paid']}\n\n{TEXT[lang]['plans'][plan.key]}\n{TEXT[lang]['expires']}: {expiry}"
    return text, keyboard


async def show_access(callback: CallbackQuery, bot: Bot, plan: Plan, expiration: datetime | None) -> None:
    """Replace a payment screen with the access link instead of sending another message."""
    text, keyboard = await access_screen(bot, callback.from_user.id, plan, expiration)
    await edit_menu_message(callback, text, keyboard)


@router.message(F.successful_payment)
async def stars_paid(message: Message, bot: Bot) -> None:
    payment = message.successful_payment
    parts = payment.invoice_payload.split(":")
    if len(parts) != 3 or parts[0] != "stars" or parts[2] != str(message.from_user.id):
        logging.error("Unexpected successful payment payload: %s", payment.invoice_payload)
        return
    plan = PLANS.get(parts[1])
    if not plan or payment.currency != "XTR" or payment.total_amount != plan.stars:
        logging.error("Stars payment validation failed for %s", message.from_user.id)
        return
    expiration = grant_purchase(message.from_user.id, plan, str(payment.total_amount), "stars", payment.telegram_payment_charge_id)
    # Turn the user's last bot screen into the access screen; only screens from the
    # same chat can be reused (start may be issued in groups).
    screen = _last_screen.pop(message.from_user.id, None)
    if screen is not None and screen.chat.id == message.chat.id:
        try:
            text, keyboard = await access_screen(bot, message.from_user.id, plan, expiration)
            await edit_screen(screen, text, keyboard)
            return
        except Exception:
            logging.exception("Cannot edit the last screen into the access message")
    await send_access(message, bot, message.from_user.id, plan, expiration)


async def crypto_api(method: str, payload: dict) -> dict:
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://pay.crypt.bot/api/{method}", json=payload, headers=headers, timeout=20) as response:
            data = await response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error", {}).get("name", "Crypto Pay error"))
    return data["result"]


async def crystalpay_api(method: str, payload: dict) -> dict:
    """Call Crystal Pay API v3 and return its response body."""
    if not CRYSTALPAY_AUTH_LOGIN or not CRYSTALPAY_AUTH_SECRET:
        raise RuntimeError("Crystal Pay credentials are not configured")
    payload = {"auth_login": CRYSTALPAY_AUTH_LOGIN, "auth_secret": CRYSTALPAY_AUTH_SECRET, **payload}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://api.crystalpay.io/v3/{method}/", json=payload, timeout=20) as response:
            data = await response.json()
    if data.get("error"):
        raise RuntimeError("; ".join(data.get("errors") or ["Crystal Pay error"]))
    return data


async def crystalpay_rub_methods_available() -> bool:
    """Check that the cashier has at least one enabled incoming RUB method."""
    result = await crystalpay_api("method/list", {})
    for method in result.get("items", {}).values():
        incoming = method.get("in") or {}
        if method.get("currency") == "RUB" and incoming.get("enabled") is True:
            return True
    return False


@router.callback_query(F.data.startswith("crypto:"))
async def crypto_invoice(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    lang = language(callback.from_user.id)
    plan = PLANS.get(key)
    if not plan:
        await callback.answer(TEXT[lang]["plan_not_found"], show_alert=True)
        return
    try:
        invoice = await crypto_api("createInvoice", {
            "asset": CRYPTO_ASSET, "amount": plan.crypto_amount,
            "description": TEXT[lang]["crypto_description"].format(plan=TEXT[lang]["plans"][key]),
            "payload": f"crypto:{key}:{callback.from_user.id}",
            "expires_in": 3600,
        })
    except Exception:
        logging.exception("Cannot create Crypto Pay invoice")
        await callback.answer(TEXT[lang]["invoice_create_failed"], show_alert=True)
        return
    invoice_id = str(invoice["invoice_id"])
    with closing(db()) as conn:
        conn.execute("INSERT OR IGNORE INTO payments(user_id,plan_key,amount,method,payment_id,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
                     (callback.from_user.id, key, plan.crypto_amount, "crypto", invoice_id, utcnow().isoformat()))
        conn.commit()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXT[lang]["pay"], url=invoice["pay_url"])],
        [InlineKeyboardButton(text=TEXT[lang]["check"], callback_data=f"check:{invoice_id}")],
        [InlineKeyboardButton(text=TEXT[lang]["back"], callback_data="menu")],
        [InlineKeyboardButton(text=TEXT[lang]["language"], callback_data=f"lang:{'en' if lang == 'ru' else 'ru'}:menu")],
    ])
    await edit_menu_message(callback, TEXT[lang]["invoice"], keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("check:"))
async def check_crypto(callback: CallbackQuery, bot: Bot) -> None:
    invoice_id = callback.data.split(":", 1)[1]
    lang = language(callback.from_user.id)
    with closing(db()) as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payment_id=? AND user_id=? AND method='crypto'", (invoice_id, callback.from_user.id)).fetchone()
    if not payment:
        await callback.answer(TEXT[lang]["invoice_not_found"], show_alert=True)
        return
    try:
        result = await crypto_api("getInvoices", {"invoice_ids": invoice_id})
        invoice = result["items"][0]
    except Exception:
        logging.exception("Cannot check Crypto Pay invoice")
        await callback.answer(TEXT[lang]["invoice_check_failed"], show_alert=True)
        return
    if invoice["status"] == "paid":
        plan = PLANS[payment["plan_key"]]
        expiration = grant_purchase(callback.from_user.id, plan, payment["amount"], "crypto", invoice_id)
        await callback.answer(TEXT[lang]["payment_confirmed"])
        await show_access(callback, bot, plan, expiration)
    elif invoice["status"] in {"expired", "cancelled"}:
        with closing(db()) as conn:
            conn.execute("UPDATE payments SET status=? WHERE payment_id=?", (invoice["status"], invoice_id))
            conn.commit()
        await callback.answer(TEXT[lang]["invoice_closed"], show_alert=True)
    else:
        await callback.answer(TEXT[lang]["crypto_pending"], show_alert=True)


@router.callback_query(F.data.startswith("crystal:"))
async def crystal_invoice(callback: CallbackQuery) -> None:
    lang = language(callback.from_user.id)
    await callback.answer(TEXT[lang]["rub_temporarily_disabled"], show_alert=True)


@router.callback_query(F.data.startswith("crystal_check:"))
async def check_crystal(callback: CallbackQuery, bot: Bot) -> None:
    invoice_id = callback.data.split(":", 1)[1]
    lang = language(callback.from_user.id)
    with closing(db()) as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payment_id=? AND user_id=? AND method='crystal'", (invoice_id, callback.from_user.id)).fetchone()
    if not payment:
        await callback.answer(TEXT[lang]["invoice_not_found"], show_alert=True)
        return
    try:
        invoice = await crystalpay_api("invoice/info", {"id": invoice_id})
    except Exception:
        logging.exception("Cannot check Crystal Pay invoice")
        await callback.answer(TEXT[lang]["invoice_check_failed"], show_alert=True)
        return
    if invoice["state"] == "payed":
        plan = PLANS[payment["plan_key"]]
        expiration = grant_purchase(callback.from_user.id, plan, payment["amount"], "crystal", invoice_id)
        await callback.answer(TEXT[lang]["payment_confirmed"])
        await show_access(callback, bot, plan, expiration)
    elif invoice["state"] in {"expired", "cancelled"}:
        with closing(db()) as conn:
            conn.execute("UPDATE payments SET status=? WHERE payment_id=?", (invoice["state"], invoice_id))
            conn.commit()
        await callback.answer(TEXT[lang]["invoice_closed"], show_alert=True)
    elif invoice["state"] in {"failed", "unavailable"}:
        with closing(db()) as conn:
            conn.execute("UPDATE payments SET status=? WHERE payment_id=?", (invoice["state"], invoice_id))
            conn.commit()
        message_key = "payment_failed" if invoice["state"] == "failed" else "payment_unavailable"
        await callback.answer(TEXT[lang][message_key], show_alert=True)
    elif invoice["state"] == "wrongamount":
        await callback.answer(TEXT[lang]["payment_wrong_amount"], show_alert=True)
    else:
        await callback.answer(TEXT[lang]["payment_pending"], show_alert=True)


async def main() -> None:
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

"""
Автоматический квалификатор лидов (B2B) — Telegram-бот на aiogram 3.x.

Сценарий: приветствие -> боль -> роль (ЛПР) -> бюджет -> сроки -> имя -> контакт -> подтверждение.
После подтверждения считается скоринг (hot/warm/cold), лид пишется в Google Sheets
и, если hot/warm, менеджеру уходит уведомление в Telegram.

Запуск: python bot.py  (переменные окружения — см. .env.example)
"""

import asyncio
import html
import logging
import os
from dataclasses import asdict, dataclass

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.methods import GetUpdates
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from claude_extractor import clarify_question, is_vague, summarize_pain
from sheets_client import append_lead

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MANAGERS_CHAT_ID = int(os.environ["MANAGERS_CHAT_ID"])  # ваш личный ID или ID группы

router = Router()


class RetryMiddleware(BaseRequestMiddleware):
    """Повторяет запрос к Telegram при сетевом сбое (нестабильное соединение).

    Сбой SSL-рукопожатия происходит до отправки запроса, поэтому повтор
    не создаёт дубликатов сообщений. getUpdates пропускаем: у поллинга
    своя логика переподключения.
    """

    def __init__(self, retries: int = 5, delay: float = 1.5) -> None:
        self.retries = retries
        self.delay = delay

    async def __call__(self, make_request, bot, method):
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)
        for attempt in range(1, self.retries + 1):
            try:
                return await make_request(bot, method)
            except TelegramNetworkError:
                if attempt == self.retries:
                    raise
                logger.warning(
                    "Сбой сети при запросе к Telegram (попытка %s/%s), повторяю...",
                    attempt, self.retries,
                )
                await asyncio.sleep(self.delay)


# ---- Состояния диалога ----
class Form(StatesGroup):
    pain = State()
    clarify = State()
    role = State()
    budget = State()
    timeline = State()
    name = State()
    contact = State()
    confirm = State()


# ---- Настройки кнопок и весов скоринга (подстройте под свой продукт) ----
ROLE_OPTIONS = [
    ("Я принимаю решение сам(а)", "authority_full", 25),
    ("Согласовываю с руководителем/командой", "authority_partial", 15),
    ("Просто изучаю рынок", "authority_none", 0),
]

BUDGET_OPTIONS = [
    ("до 500 000 ₽", "budget_low", 5),
    ("500 000 – 2 000 000 ₽", "budget_mid", 20),
    ("2 000 000 – 5 000 000 ₽", "budget_high", 25),
    ("более 5 000 000 ₽", "budget_vhigh", 25),
    ("пока не готов(а) назвать", "budget_unknown", 0),
]

TIMELINE_OPTIONS = [
    ("Срочно, в течение месяца", "time_urgent", 25),
    ("1–3 месяца", "time_soon", 20),
    ("3–6 месяцев", "time_mid", 10),
    ("Просто изучаю, сроков нет", "time_none", 0),
]


@dataclass
class LeadData:
    telegram_id: int = 0
    username: str = ""
    pain_raw: str = ""
    pain_summary: str = ""
    role_label: str = ""
    role_score: int = 0
    budget_label: str = ""
    budget_score: int = 0
    timeline_label: str = ""
    timeline_score: int = 0
    name: str = ""
    contact: str = ""

    def pain_score(self) -> int:
        # Боль считается "чёткой", если резюме получилось длиннее нескольких слов
        return 25 if len(self.pain_summary.split()) >= 3 else 10

    def total_score(self) -> int:
        return self.pain_score() + self.role_score + self.budget_score + self.timeline_score

    def status(self) -> str:
        s = self.total_score()
        if s >= 75:
            return "🔥 hot"
        if s >= 40:
            return "🌤 warm"
        return "❄️ cold"


# ---- Вспомогательные функции ----
def keyboard(options) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, key, _ in options:
        kb.button(text=label, callback_data=key)
    kb.adjust(1)
    return kb.as_markup()


def confirm_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, всё верно", callback_data="confirm_yes")
    kb.button(text="✏️ Начать заново", callback_data="confirm_no")
    kb.adjust(1)
    return kb.as_markup()


async def get_lead(state: FSMContext) -> LeadData:
    data = await state.get_data()
    return LeadData(**data["lead"])


async def save_lead(state: FSMContext, lead: LeadData) -> None:
    await state.update_data(lead=asdict(lead))


def find_option(options, key):
    return next((label, score) for label, k, score in options if k == key)


# ---- Хендлеры ----
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    lead = LeadData(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
    )
    await save_lead(state, lead)
    await message.answer(
        "Здравствуйте! 👋 Я помогу быстро разобраться в задаче и соединю вас "
        "с нужным специалистом.\n\nРасскажите в двух словах: какую задачу "
        "вы сейчас хотите решить и что подтолкнуло заняться этим именно сейчас?"
    )
    await state.set_state(Form.pain)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Диалог прерван. Напишите /start, чтобы начать заново.")


@router.message(Form.pain, F.text)
async def on_pain(message: Message, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.pain_raw = message.text

    # Вызовы Claude и Google Sheets синхронные — выносим в поток,
    # чтобы не блокировать цикл событий бота.
    if await asyncio.to_thread(is_vague, message.text):
        question = await asyncio.to_thread(clarify_question, message.text)
        await save_lead(state, lead)
        await message.answer(question)
        await state.set_state(Form.clarify)
        return

    lead.pain_summary = await asyncio.to_thread(summarize_pain, message.text)
    await save_lead(state, lead)
    await ask_role(message, state)


@router.message(Form.clarify, F.text)
async def on_clarify(message: Message, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.pain_raw += "\n" + message.text
    lead.pain_summary = await asyncio.to_thread(summarize_pain, lead.pain_raw)
    await save_lead(state, lead)
    await ask_role(message, state)


async def ask_role(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Понял(а), спасибо! А как вы участвуете в принятии решения по этому вопросу?",
        reply_markup=keyboard(ROLE_OPTIONS),
    )
    await state.set_state(Form.role)


@router.callback_query(Form.role, F.data.in_({k for _, k, _ in ROLE_OPTIONS}))
async def on_role(callback: CallbackQuery, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.role_label, lead.role_score = find_option(ROLE_OPTIONS, callback.data)
    await save_lead(state, lead)

    await callback.message.edit_text(f"Роль: {lead.role_label}")
    await callback.message.answer(
        "Какой бюджет вы закладываете на решение этой задачи?",
        reply_markup=keyboard(BUDGET_OPTIONS),
    )
    await state.set_state(Form.budget)
    await callback.answer()


@router.callback_query(Form.budget, F.data.in_({k for _, k, _ in BUDGET_OPTIONS}))
async def on_budget(callback: CallbackQuery, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.budget_label, lead.budget_score = find_option(BUDGET_OPTIONS, callback.data)
    await save_lead(state, lead)

    await callback.message.edit_text(f"Бюджет: {lead.budget_label}")
    await callback.message.answer(
        "Когда планируете внедрить решение?",
        reply_markup=keyboard(TIMELINE_OPTIONS),
    )
    await state.set_state(Form.timeline)
    await callback.answer()


@router.callback_query(Form.timeline, F.data.in_({k for _, k, _ in TIMELINE_OPTIONS}))
async def on_timeline(callback: CallbackQuery, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.timeline_label, lead.timeline_score = find_option(TIMELINE_OPTIONS, callback.data)
    await save_lead(state, lead)

    await callback.message.edit_text(f"Сроки: {lead.timeline_label}")
    await callback.message.answer("Как к вам обращаться (имя и компания)?")
    await state.set_state(Form.name)
    await callback.answer()


@router.message(Form.name, F.text)
async def on_name(message: Message, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.name = message.text
    await save_lead(state, lead)
    await message.answer(
        "И оставьте, пожалуйста, телефон или email — куда сможет написать специалист."
    )
    await state.set_state(Form.contact)


@router.message(Form.contact, F.text)
async def on_contact(message: Message, state: FSMContext) -> None:
    lead = await get_lead(state)
    lead.contact = message.text
    await save_lead(state, lead)

    await message.answer(
        "Проверьте, всё ли верно:\n\n"
        f"• Задача: {html.escape(lead.pain_summary)}\n"
        f"• Роль: {html.escape(lead.role_label)}\n"
        f"• Бюджет: {html.escape(lead.budget_label)}\n"
        f"• Сроки: {html.escape(lead.timeline_label)}\n"
        f"• Имя: {html.escape(lead.name)}\n"
        f"• Контакт: {html.escape(lead.contact)}",
        reply_markup=confirm_keyboard(),
    )
    await state.set_state(Form.confirm)


@router.callback_query(Form.confirm, F.data == "confirm_no")
async def on_confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    lead = await get_lead(state)
    # Сохраняем только идентификацию, остальное собираем заново
    fresh = LeadData(telegram_id=lead.telegram_id, username=lead.username)
    await save_lead(state, fresh)
    await callback.message.edit_text("Хорошо, давайте уточним ещё раз.")
    await callback.message.answer("Какую задачу вы хотите решить?")
    await state.set_state(Form.pain)
    await callback.answer()


@router.callback_query(Form.confirm, F.data == "confirm_yes")
async def on_confirm_yes(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    lead = await get_lead(state)
    status = lead.status()
    score = lead.total_score()

    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        await asyncio.to_thread(append_lead, lead, status, score)
    except Exception:
        logger.exception("Не удалось записать лид в Google Sheets")

    if status != "❄️ cold":
        who = f"@{lead.username}" if lead.username else f"id {lead.telegram_id}"
        await bot.send_message(
            chat_id=MANAGERS_CHAT_ID,
            text=(
                f"<b>Новый лид: {status}</b> (скоринг {score}/100)\n\n"
                f"<b>Задача:</b> {html.escape(lead.pain_summary)}\n"
                f"<b>Роль:</b> {html.escape(lead.role_label)}\n"
                f"<b>Бюджет:</b> {html.escape(lead.budget_label)}\n"
                f"<b>Сроки:</b> {html.escape(lead.timeline_label)}\n"
                f"<b>Имя:</b> {html.escape(lead.name)}\n"
                f"<b>Контакт:</b> {html.escape(lead.contact)}\n"
                f"<b>Telegram:</b> {html.escape(who)}"
            ),
        )
        await callback.message.answer(
            "Отлично, спасибо! Передал(а) вашу заявку специалисту — он свяжется "
            "с вами в ближайшее время."
        )
    else:
        await callback.message.answer(
            "Спасибо за подробности! Пока это выглядит как ранний этап — "
            "пришлю вам полезные материалы, а если появятся конкретные "
            "сроки и бюджет, всегда можно вернуться ко мне."
        )

    await state.clear()
    await callback.answer()


# ---- Защита от «неожиданного» ввода ----
@router.message(Form.pain, ~F.text)
@router.message(Form.clarify, ~F.text)
@router.message(Form.name, ~F.text)
@router.message(Form.contact, ~F.text)
async def expect_text(message: Message) -> None:
    await message.answer("Пожалуйста, ответьте текстом.")


@router.message(Form.role)
@router.message(Form.budget)
@router.message(Form.timeline)
@router.message(Form.confirm)
async def expect_button(message: Message) -> None:
    await message.answer("Пожалуйста, выберите вариант с помощью кнопок выше.")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Напишите /start, чтобы начать диалог.")


async def main() -> None:
    # Если Telegram недоступен напрямую, задайте TELEGRAM_PROXY в .env,
    # например: http://127.0.0.1:7890 (для socks5 нужен пакет aiohttp-socks)
    proxy = os.environ.get("TELEGRAM_PROXY")
    session = AiohttpSession(proxy=proxy, timeout=60) if proxy else AiohttpSession(timeout=60)

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot.session.middleware(RetryMiddleware(retries=5, delay=1.5))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

"""
Обработчики: /start, /help, текст + inline-кнопка «Уточнить».
"""
from aiogram import types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest

from llm.agent import LLMAgent
from search.engine import SearchTool
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Глобальная ссылка на бота (для пересоздания сессии)
_bot_ref = None

# История разговоров: user_id → список сообщений
_conversations: dict[int, list[dict]] = {}

# Флаг: пользователь хочет уточнить (история прикреплена только для следующего сообщения)
_wants_clarify: set[int] = set()


def set_bot_ref(bot):
    global _bot_ref
    _bot_ref = bot


def _get_history(uid: int) -> list[dict]:
    return _conversations.get(uid, [])


def _add_message(uid: int, role: str, content: str):
    if uid not in _conversations:
        _conversations[uid] = []
    _conversations[uid].append({"role": role, "content": content})
    if len(_conversations[uid]) > 20:
        _conversations[uid] = _conversations[uid][-20:]


def _clarify_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Уточнить вопрос", callback_data="clarify")]
    ])


async def _send_with_retry(message, text, reply_markup=None):
    """Отправляет сообщение с retry при обрыве соединения."""
    from main import create_session
    from aiogram import Bot
    from config import BOT_TOKEN

    for attempt in range(3):
        try:
            if reply_markup:
                await message.answer(text, reply_markup=reply_markup)
            else:
                await message.answer(text)
            return
        except (TelegramNetworkError, TelegramBadRequest, ConnectionError, OSError) as e:
            logger.warning(f"Ошибка отправки (попытка {attempt+1}): {e}")
            # Пересоздаём сессию бота
            global _bot_ref
            if _bot_ref:
                try:
                    await _bot_ref.session.close()
                except:
                    pass
                _bot_ref.session = create_session()


async def _delete_safe(status_msg):
    """Безопасно удаляет статусное сообщение."""
    try:
        await status_msg.delete()
    except (TelegramNetworkError, TelegramBadRequest, Exception):
        pass


async def register_handlers(dp, llm: LLMAgent, search_tool: SearchTool):
    logger.info("Регистрация обработчиков...")

    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        uid = message.from_user.id
        _conversations[uid] = []
        await message.answer(
            "🤖 Привет! Я ИИ-помощник с поиском в интернете.\n\n"
            "Напиши вопрос — я найду информацию и отвечу.\n\n"
            "Примеры:\n"
            "• Что такое TurboQuant?\n"
            "• Как работает квантовый компьютер?\n"
            "• Последние новости технологий"
        )

    @dp.message(Command("help"))
    async def cmd_help(message: types.Message):
        await message.answer(
            "📖 <b>Как это работает:</b>\n\n"
            "Я сам решаю когда и что искать в интернете.\n"
            "Просто напиши вопрос — найду и отвечу.\n\n"
            "После ответа появится кнопка «🔍 Уточнить вопрос» — "
            "можно задать уточняющий вопрос в контексте разговора."
        )

    @dp.callback_query(lambda c: c.data == "clarify")
    async def cb_clarify(callback: types.CallbackQuery):
        uid = callback.from_user.id

        # Ставим флаг — следующее сообщение будет с историей
        _wants_clarify.add(uid)

        history = _get_history(uid)
        last_user_msg = ""
        for msg in reversed(history):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break

        text = "Задай уточняющий вопрос:"
        if last_user_msg:
            text = f"📝 Было: {last_user_msg[:120]}\n\n{text}"

        await callback.message.answer(text)
        await callback.answer()

    @dp.message()
    async def handle_message(message: types.Message):
        text = message.text.strip()
        if not text:
            return

        uid = message.from_user.id

        # История только если пользователь нажал «Уточнить»
        use_history = uid in _wants_clarify
        history = _get_history(uid) if use_history else []

        if use_history:
            _wants_clarify.discard(uid)  # Сбрасываем флаг
            logger.info(f"Сообщение от {uid}: {text[:60]}... (с историей, {len(history)} сообщений)")
        else:
            logger.info(f"Сообщение от {uid}: {text[:60]}...")

        status = await message.answer("🤔 Думаю...")

        try:
            answer = await llm.chat(text, search_tool=search_tool, history=history)

            # Сохраняем в историю всегда
            _add_message(uid, "user", text)
            _add_message(uid, "assistant", answer)

            # Отправка чанками с retry
            if len(answer) > 4000:
                chunks = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        await _send_with_retry(message, chunk, reply_markup=_clarify_keyboard())
                    else:
                        await _send_with_retry(message, chunk)
            else:
                await _send_with_retry(message, answer, reply_markup=_clarify_keyboard())

            await _delete_safe(status)

        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            try:
                await status.edit_text(f"❌ Ошибка: {e}\n\nПопробуй ещё раз.")
            except:
                await _send_with_retry(message, f"❌ Ошибка: {e}\n\nПопробуй ещё раз.")

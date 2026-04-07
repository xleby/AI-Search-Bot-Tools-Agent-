"""
Точка входа бота №3 — LLM Tools Agent.
"""
import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest

from config import BOT_TOKEN, HTTP_PROXY
from core.handlers import register_handlers
from llm.agent import LLMAgent
from search.engine import SearchTool
from utils.logger import setup_logger

logger = setup_logger(__name__)


def create_session() -> AiohttpSession:
    """Создаёт новую сессию (нужно после обрыва связи с прокси)."""
    session = AiohttpSession()
    if HTTP_PROXY:
        session.api = TelegramAPIServer(
            base=f"{HTTP_PROXY}/bot{{token}}/{{method}}",
            file=f"{HTTP_PROXY}/file/bot{{token}}/{{path}}",
        )
    return session


async def main():
    logger.info("=" * 60)
    logger.info("🤖 Запуск AI Bot #3 — Tools Agent")
    logger.info("=" * 60)

    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        sys.exit(1)

    if HTTP_PROXY:
        logger.info(f"📡 Прокси: {HTTP_PROXY}")

    aiogram_session = create_session()
    bot = Bot(token=BOT_TOKEN, session=aiogram_session)
    dp = Dispatcher()

    llm = LLMAgent()
    search_tool = SearchTool()

    await register_handlers(dp, llm, search_tool)

    from core.handlers import set_bot_ref
    set_bot_ref(bot)

    me = await bot.get_me()
    logger.info(f"✅ Бот: @{me.username} (ID: {me.id})")
    logger.info("🎉 Готов!")

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Остановлен")
    finally:
        await llm.close()
        await search_tool.close()


async def _shutdown(bot: Bot, llm: LLMAgent, search_tool: SearchTool):
    logger.info("🛑 Остановка...")
    try:
        await bot.session.close()
    except:
        pass
    try:
        await llm.close()
    except:
        pass
    try:
        await search_tool.close()
    except:
        pass
    logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлен пользователем")

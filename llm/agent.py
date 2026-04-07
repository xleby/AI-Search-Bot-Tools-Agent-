"""
LLM-агент с поддержкой tools (function calling).
Модель сама решает когда искать, что искать, и генерирует ответ.
"""
import json
from typing import Optional
import aiohttp

from utils.logger import setup_logger
from config import MODEL_URL, MATERIALS_CHAR_LIMIT

logger = setup_logger(__name__)

# Определение tool для модели
TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Ищи информацию в интернете. "
            "Используй когда пользователь задаёт вопрос требующий актуальных данных, "
            "фактов, новостей или информации о технология/событиях. "
            "Если вопрос общий (привет, как дела) — tool вызывать не надо."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-3 поисковых запроса. Включи запросы на русском И английском."
                }
            },
            "required": ["queries"]
        }
    }
}]

SYSTEM_PROMPT = """Ты поисковый ассистент. Отвечай на вопросы пользователей с помощью поиска в интернете.
Пиши обычным текстом, без какого-либо форматирования.
НЕ используй звёздочки * для жирного текста, НЕ используй ## для заголовков, НЕ используй ``` для кода.
Просто пиши текст обычными буквами, как в обычном чате."""


class LLMAgent:
    """
    LLM-агент с tools.
    Цикл: пользователь → LLM → tool call → выполняем → LLM → ответ.
    """

    def __init__(self, model_url: str = MODEL_URL):
        self.model_url = model_url.rstrip('/')
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def chat(self, question: str, search_tool, history: list[dict] = None) -> str:
        """
        Обрабатывает вопрос через LLM с tools.

        Args:
            question: Вопрос пользователя
            search_tool: экземпляр SearchTool
            history: История разговора (список сообщений)

        Returns:
            str: Ответ
        """
        logger.info(f"LLMAgent: вопрос = {question[:60]}...")
        if history:
            logger.info(f"LLMAgent: история = {len(history)} сообщений")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Добавляем историю если есть
        if history:
            messages.extend(history[-10:])  # Последние 10 сообщений

        messages.append({"role": "user", "content": question})

        # Цикл: LLM может вызвать tool, затем ответить
        max_iterations = 5
        for i in range(max_iterations):
            session = await self._get_session()
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 4000,
            }

            async with session.post(
                f"{self.model_url}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Qwen API error: {resp.status} - {error_text[:200]}")
                    return f"❌ Ошибка Qwen API: {resp.status}"

                data = await resp.json()

            if not data.get("choices"):
                return "❌ Пустой ответ от модели"

            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "")

            # Вариант 1: модель хочет вызвать tool
            if finish_reason == "tool_calls" and message.get("tool_calls"):
                tool_calls = message["tool_calls"]

                # Добавляем ответ модели в историю
                messages.append(message)

                for tc in tool_calls:
                    func = tc["function"]
                    func_name = func["name"]
                    args = json.loads(func["arguments"])

                    if func_name == "search_web":
                        queries = args.get("queries", [])
                        if not queries:
                            queries = [question]

                        logger.info(f"  Tool call: search_web({queries})")

                        # Выполняем поиск
                        results = await search_tool.search_and_retrieve(queries)

                        # Возвращаем результат модели
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": results[:MATERIALS_CHAR_LIMIT],
                        })
                    else:
                        logger.warning(f"Неизвестный tool: {func_name}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": f"Unknown tool: {func_name}",
                        })

                # Продолжаем цикл — модель получит результаты и ответит
                continue

            # Вариант 2: модель дала финальный ответ
            if finish_reason == "stop" and message.get("content"):
                answer = message["content"].strip()
                logger.info(f"LLMAgent: ответ готов, {len(answer)} символов")
                return answer

            # Вариант 3: что-то непонятное
            logger.warning(f"Неожиданный finish_reason: {finish_reason}")
            content = message.get("content", "")
            if content:
                return content
            return "⚠️ Не удалось получить ответ от модели"

        return "⚠️ Превышено максимальное количество итераций"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

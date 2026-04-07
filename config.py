"""
Конфигурация.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из директории проекта
load_dotenv(override=True)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

# HTTP прокси (aiogram требует http://, даже если прокси HTTPS)
_http_proxy = os.getenv("HTTP_PROXY", "").strip()
HTTP_PROXY: str = _http_proxy.replace("https://", "http://") if _http_proxy else ""
SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
MODEL_URL: str = os.getenv("MODEL_URL", "http://127.0.0.1:8080").rstrip("/")
MAX_SEARCH_RESULTS: int = int(os.getenv("MAX_SEARCH_RESULTS", "5"))
MAX_ARTICLES_TO_PARSE: int = int(os.getenv("MAX_ARTICLES_TO_PARSE", "5"))
MATERIALS_CHAR_LIMIT: int = int(os.getenv("MATERIALS_CHAR_LIMIT", "90000"))

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

"""
Поиск + парсинг статей с умной фильтрацией.
Используется как tool для LLM-агента.
"""
import asyncio
import re
import html
from html.parser import HTMLParser
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import aiohttp
import trafilatura

from utils.logger import setup_logger
from config import SEARXNG_URL, MAX_SEARCH_RESULTS, MAX_ARTICLES_TO_PARSE, MATERIALS_CHAR_LIMIT

logger = setup_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=3)


def _extract_text(html_content: str, url: str) -> str:
    return trafilatura.extract(html_content, url=url, include_comments=False,
                                include_tables=True, include_links=False, no_fallback=False) or ""


# ---- ФИЛЬТРАЦИЯ ----

# Блокируем только откровенный мусор (спам, парковки, сокращатели)
BLOCKED_DOMAINS = {
    # Сокращатели URL
    'bit.ly', 'goo.gl', 't.co', 'tinyurl.com', 'ow.ly', 'is.gd', 'buff.ly',
    # Паркованные домены / спамерские
    'pinterest.com', 'pinterest.ru',
    'slideshare.net',
    # Агрегаторы-мусор
    'link.springer.com',
}

# Приоритеты доменов (больше = лучше)
DOMAIN_PRIORITY = {
    # Авторитетные источники
    'wikipedia.org': 100, 'ru.wikipedia.org': 100, 'en.wikipedia.org': 100,
    # Официальные сайты
    '.gov': 95, '.edu': 95,
    # Технические
    'habr.com': 90, 'habr.ru': 90,
    'ixbt.com': 85, 'ixbt.ru': 85, '3dnews.ru': 85,
    'arxiv.org': 85, 'github.com': 85, 'stackoverflow.com': 85,
    'docs.microsoft.com': 85, 'developer.mozilla.org': 85,
    'readthedocs.io': 80,
    # Новости / СМИ
    'reuters.com': 80, 'tass.ru': 80, 'ria.ru': 80, 'lenta.ru': 75,
    'vc.ru': 75, 'thebell.io': 75, 'kommersant.ru': 75,
    'ixbt.com': 80, '3dnews.ru': 80, 'overclockers.ru': 75,
    # Reddit/YouTube/форумы — хорошие, но чуть ниже
    'reddit.com': 60, 'youtube.com': 55, 'twitter.com': 50, 'x.com': 50,
    'habr.com': 85,  # Уже выше, но пусть будет
}


def _get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except:
        return ""


def _is_blocked(url: str) -> bool:
    """Проверяет блок-лист доменов."""
    domain = _get_domain(url)
    if domain in BLOCKED_DOMAINS:
        return True
    for blocked in BLOCKED_DOMAINS:
        if domain.endswith('.' + blocked):
            return True
    return False


def _get_priority(url: str) -> int:
    """Возвращает приоритет домена (больше = лучше)."""
    domain = _get_domain(url)
    # Точное совпадение
    if domain in DOMAIN_PRIORITY:
        return DOMAIN_PRIORITY[domain]
    # По суффиксу (.gov, .edu)
    for suffix, priority in DOMAIN_PRIORITY.items():
        if suffix.startswith('.') and domain.endswith(suffix):
            return priority
    # По умолчанию — средний
    return 40


def _score_snippet(result: dict) -> int:
    """Оценивает качество сниппета SearXNG."""
    content = result.get('content', '')
    title = result.get('title', '')
    
    score = 0
    
    # Длина контента
    if len(content) > 200:
        score += 30
    elif len(content) > 100:
        score += 20
    elif len(content) > 50:
        score += 10
    else:
        score -= 20  # Пустой сниппет — подозрительно
    
    # Заголовок
    if len(title) > 10:
        score += 10
    
    # URL выглядит как статья (не главная страница)
    url = result.get('url', '')
    path = urlparse(url).path
    if path and path != '/':
        score += 5
    if any(x in path for x in ['/article', '/post', '/news', '/blog', '/wiki', '/abs/']):
        score += 15
    
    return score


def _filter_and_rank(results: list[dict]) -> list[dict]:
    """
    Фильтрует и ранжирует результаты поиска.
    
    1. Блок-лист доменов
    2. Оценка сниппета
    3. Приоритизация доменов
    4. Сортировка
    """
    # 1. Блок-лист
    filtered = [r for r in results if not _is_blocked(r.get('url', ''))]
    logger.info(f"Блок-лит: {len(results)} → {len(filtered)}")
    
    # 2-3. Оценка + приоритет
    for r in filtered:
        r['_score'] = _score_snippet(r) + _get_priority(r.get('url', ''))
    
    # 4. Сортировка по убыванию
    ranked = sorted(filtered, key=lambda x: x['_score'], reverse=True)
    
    return ranked


class _SearXNGHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.in_article = False
        self.in_h3 = False
        self.in_content = False
        self.in_url = False
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get('class', '')
        if tag == 'article' and 'result' in cls:
            self.in_article = True
            self.current = {'title': '', 'url': '', 'content': ''}
            return
        if not self.in_article or not self.current:
            return
        if tag == 'a' and 'url_header' in cls:
            h = d.get('href', '')
            if h.startswith('http'):
                self.current['url'] = h
        if tag == 'h3':
            self.in_h3 = True; self._buf = ""
        if tag == 'p' and 'content' in cls:
            self.in_content = True; self._buf = ""
        if tag == 'div' and 'selectable_url' in cls:
            self.in_url = True; self._buf = ""

    def handle_endtag(self, tag):
        if tag == 'article':
            if self.current and self.current.get('url'):
                self.current['title'] = html.unescape(re.sub(r'\s+', ' ', self.current['title']).strip())
                self.current['content'] = html.unescape(re.sub(r'\s+', ' ', self.current['content']).strip())
                self.results.append(self.current)
            self.current = None; self.in_article = False
            return
        if not self.in_article:
            return
        if tag == 'h3':
            if self.in_h3 and self.current: self.current['title'] = self._buf.strip()
            self.in_h3 = False; self._buf = ""
        if tag == 'p':
            if self.in_content and self.current: self.current['content'] = self._buf.strip()
            self.in_content = False; self._buf = ""
        if tag == 'div':
            if self.in_url and self.current and not self.current.get('url'):
                u = self._buf.strip()
                if u.startswith('http'): self.current['url'] = u
            self.in_url = False; self._buf = ""

    def handle_data(self, data):
        if self.in_h3 or self.in_content or self.in_url:
            self._buf += data


class SearchTool:
    """
    Tool для LLM-агента: ищет в интернете и возвращает тексты статей.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        logger.info("SearchTool инициализирован")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(),
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            )
            try:
                await self._session.get(f"{SEARXNG_URL}/", allow_redirects=True)
            except Exception:
                pass
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _search(self, query: str) -> list[dict]:
        try:
            session = await self._get_session()
            async with session.get(f"{SEARXNG_URL}/search", params={
                'q': query, 'language': 'ru', 'categories': 'general', 'safesearch': '0'
            }) as resp:
                if resp.status != 200:
                    return []
                parser = _SearXNGHTMLParser()
                parser.feed(await resp.text())
                return parser.results[:MAX_SEARCH_RESULTS]
        except Exception as e:
            logger.error(f"Ошибка поиска '{query}': {e}")
            return []

    async def _parse_url(self, url: str, timeout: int = 15) -> str:
        try:
            session = await self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return ""
                html_content = await resp.text(errors='replace')
            loop = asyncio.get_event_loop()
            return (await loop.run_in_executor(_executor, _extract_text, html_content, url)).strip()
        except Exception:
            return ""

    async def search_and_retrieve(self, queries: list[str]) -> str:
        """
        Главная функция: ищет по запросам, фильтрует, парсит лучшие статьи.

        Фильтрация:
        1. Блок-лист доменов (спам, сокращатели)
        2. Оценка сниппета (длина контента)
        3. Приоритизация (wiki > habr > новости > reddit)
        4. Адаптивный парсинг: 3-5 статей

        Returns:
            str: Объединённый текст статей для LLM
        """
        logger.info(f"SearchTool: запросы = {queries}")

        # 1. Поиск (параллельно)
        tasks = [self._search(q) for q in queries]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        all_results = []
        for r in results_list:
            if isinstance(r, list):
                all_results.extend(r)

        # Дедупликация
        seen = set()
        unique = []
        for r in all_results:
            if r['url'] not in seen:
                seen.add(r['url'])
                unique.append(r)

        if not unique:
            return "Поиск не дал результатов."

        logger.info(f"Найдено {len(unique)} уникальных результатов до фильтрации")

        # 2. Фильтрация + ранжирование
        ranked = _filter_and_rank(unique)

        if not ranked:
            return "Поиск не дал результатов после фильтрации."

        # Покажем топ-5
        logger.info("Топ источников:")
        for i, r in enumerate(ranked[:5]):
            logger.info(f"  [{i+1}] score={r['_score']} | {r.get('url', '')[:60]} | {r.get('title', '')[:40]}")

        # 3. Адаптивный парсинг: сначала 3 лучших
        batch1 = ranked[:3]
        urls1 = [r['url'] for r in batch1]
        logger.info(f"Парсинг батча 1 ({len(urls1)})...")

        articles1 = await self._parse_urls_with_fallback(urls1)

        total_chars = sum(len(a['text']) for a in articles1 if a['ok'])

        # Если 3 статьи дали >10K символов — достаточно
        MIN_GOOD_CHARS = 10_000
        if total_chars >= MIN_GOOD_CHARS:
            logger.info(f"3 статьи дали {total_chars} символов — достаточно")
        elif len(ranked) > 3:
            # Парсим ещё 2
            batch2 = ranked[3:5]
            urls2 = [r['url'] for r in batch2]
            logger.info(f"Мало текста, парсим батч 2 ({len(urls2)})...")
            articles2 = await self._parse_urls_with_fallback(urls2)
            articles1.extend(articles2)
            total_chars = sum(len(a['text']) for a in articles1 if a['ok'])
            logger.info(f"Всего {len(articles1)} статей, {total_chars} символов")

        # Собираем результат
        texts = []
        for a in articles1:
            if a['ok']:
                texts.append(f"[{a['url']}]\n{a['text']}")

        if not texts:
            # Fallback: используем заголовки и описания из поиска
            texts = [f"[{r['url']}]\n{r.get('title', '')}\n{r.get('content', '')}" 
                     for r in ranked[:MAX_ARTICLES_TO_PARSE] if r.get('content')]

        combined = "\n\n---\n\n".join(texts)

        # Обрезаем до лимита
        if len(combined) > MATERIALS_CHAR_LIMIT:
            combined = combined[:MATERIALS_CHAR_LIMIT]
            logger.info(f"Обрезано до {MATERIALS_CHAR_LIMIT} символов")

        logger.info(f"SearchTool: {len(combined)} символов текста")
        return combined

    async def _parse_urls_with_fallback(self, urls: list[str]) -> list[dict]:
        """Парсит URLs, возвращает список {url, text, ok}."""
        tasks = [self._parse_url(u) for u in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out = []
        for url, result in zip(urls, results):
            if isinstance(result, str) and result:
                out.append({'url': url, 'text': result, 'ok': True})
            else:
                out.append({'url': url, 'text': '', 'ok': False})
        return out

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

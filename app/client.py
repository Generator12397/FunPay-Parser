import asyncio
import random
from typing import Optional

import aiohttp

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

# статусы, при которых имеет смысл повторить запрос
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """Не удалось получить страницу после всех попыток."""


class FunpayClient:
    """Async-клиент: ограничение параллельности, пауза между запросами, ретраи с backoff."""

    def __init__(self, concurrency: int = 5, delay: float = 0.0,
                 timeout: float = 30.0, retries: int = 3):
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        self._delay = max(0.0, float(delay))
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._retries = max(1, int(retries))
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "FunpayClient":
        self._session = aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def get(self, url: str) -> str:
        if self._session is None:
            raise FetchError("Клиент не открыт: используйте `async with FunpayClient() as client`")
        async with self._semaphore:
            last_error: Optional[Exception] = None
            for attempt in range(self._retries):
                if attempt:
                    await asyncio.sleep(min(2 ** attempt, 8) + random.uniform(0.0, 0.5))
                try:
                    async with self._session.get(url) as response:
                        if response.status == 200:
                            text = await response.text()
                            if self._delay:
                                await asyncio.sleep(self._delay)
                            return text
                        last_error = FetchError("HTTP %d для %s" % (response.status, url))
                        if response.status not in _RETRYABLE_STATUSES:
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = FetchError("%s: %s (%s)" % (type(exc).__name__, exc, url))
            raise last_error or FetchError("Не удалось получить %s" % url)

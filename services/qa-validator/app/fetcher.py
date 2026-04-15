import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    final_url: str | None
    status_code: int | None
    text: str
    error: str | None


class LiveFetcher:
    def __init__(self) -> None:
        self.settings = get_settings()
        timeout = aiohttp.ClientTimeout(
            total=self.settings.http_timeout_seconds,
            sock_connect=self.settings.http_connect_timeout_seconds,
            sock_read=self.settings.http_read_timeout_seconds,
        )
        connector = aiohttp.TCPConnector(
            force_close=True,
            enable_cleanup_closed=True,
            limit=self.settings.max_concurrency,
            ttl_dns_cache=300,
        )
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": self.settings.user_agent},
            raise_for_status=False,
        )

    async def close(self) -> None:
        await self.session.close()

    async def fetch(self, url: str) -> FetchResult:
        backoff = 1.0
        last_error: Optional[str] = None

        for attempt in range(self.settings.http_max_retries + 1):
            try:
                async with self.session.get(url, allow_redirects=True) as resp:
                    text = await resp.text(errors="ignore")
                    return FetchResult(
                        url=url,
                        final_url=str(resp.url),
                        status_code=resp.status,
                        text=text,
                        error=None,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = str(exc)
                logger.warning("fetch_error url=%s attempt=%s error=%s", url, attempt + 1, last_error)
                if attempt >= self.settings.http_max_retries:
                    break
                await asyncio.sleep(backoff)
                backoff *= 2

        return FetchResult(
            url=url,
            final_url=None,
            status_code=None,
            text="",
            error=last_error or "unknown_fetch_error",
        )

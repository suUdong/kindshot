"""Shared LLM client with exponential backoff retry and rate limit detection."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from kindshot.config import Config

logger = logging.getLogger(__name__)


class LlmTimeoutError(Exception):
    """LLM call timed out after all retries."""


class LlmCallError(Exception):
    """LLM call failed (non-timeout)."""


class LlmClient:
    """Shared async Anthropic client with retry, semaphore, and rate limit handling."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: Optional[object] = None
        self._semaphore = asyncio.Semaphore(max(1, config.llm_max_concurrency))

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=self._config.anthropic_api_key,
                timeout=self._config.llm_sdk_timeout_s,
            )
        return self._client

    async def call(
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0,
        max_retries: int = 3,
    ) -> tuple[str, int]:
        """Call LLM with retry. Returns (raw_text, latency_ms).

        Raises LlmTimeoutError or LlmCallError on final failure.
        """
        client = self._get_client()
        last_err: Exception | None = None
        resp = None
        t0 = time.monotonic()

        for attempt in range(max_retries):
            try:
                async with self._semaphore:
                    resp = await asyncio.wait_for(
                        client.messages.create(
                            model=self._config.llm_model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            messages=[{"role": "user", "content": prompt}],
                        ),
                        timeout=self._config.llm_wait_for_s,
                    )
                break
            except asyncio.TimeoutError as e:
                last_err = e
                if attempt < max_retries - 1:
                    delay = min(2 ** attempt, 8)
                    logger.info("LLM timeout (attempt %d/%d), retry in %ds", attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning("LLM timeout (final after %d attempts)", max_retries)
                raise LlmTimeoutError(str(e)) from e
            except Exception as e:
                last_err = e
                is_rate_limit = "rate" in str(e).lower() or "429" in str(e)
                if attempt < max_retries - 1:
                    delay = min(2 ** (attempt + 1), 16) if is_rate_limit else min(2 ** attempt, 8)
                    logger.info("LLM %s (attempt %d/%d), retry in %ds",
                                "rate limited" if is_rate_limit else "call error",
                                attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.warning("LLM call error (final after %d attempts): %s", max_retries, e)
                raise LlmCallError(str(e)) from e

        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp is None:
            raise LlmCallError(f"LLM failed after {max_retries} retries: {last_err}")

        try:
            raw_text = resp.content[0].text
        except (IndexError, AttributeError) as e:
            raise LlmCallError(f"unexpected response structure: {e}") from e

        return raw_text, latency_ms

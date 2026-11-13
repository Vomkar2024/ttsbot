"""Optional OpenAI-compatible LLM translator for idiomatic Hinglish support."""

import asyncio
import json
import logging
import re
from typing import Optional

import aiohttp

from .base import (
    HinglishTranslation,
    TranslationError,
    TranslationTimeoutError,
)

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = (
    "You are a bilingual translator for a Discord bot in Hinglish mode.\n"
    "Given an incoming message (which may be Romanized Hinglish, Hindi, English, or mixed), "
    "produce a JSON object with exactly two string keys:\n"
    '- "english": Clear, natural English rendering of the exact meaning of the message.\n'
    '- "hindi": Natural Hindi in Devanagari script expressing the EXACT same meaning as "english".\n'
    "Do not add commentary, extra sentences, or markdown formatting outside the JSON object."
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class OpenAITranslatorProvider:
    """Swappable LLM translation provider using any OpenAI-compatible Chat Completions API."""

    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        model: str = DEFAULT_OPENAI_MODEL,
        timeout: float = 10.0,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        self.model = (model or DEFAULT_OPENAI_MODEL).strip()
        self.timeout = float(timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_session(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        if (
            self._session is None
            or getattr(self._session, "closed", False) is True
            or self._session_loop is not loop
        ):
            self._session = aiohttp.ClientSession()
            self._session_loop = loop
        return self._session

    async def close(self) -> None:
        if self._session is not None and getattr(self._session, "closed", False) is not True:
            close_fn = getattr(self._session, "close", None)
            if callable(close_fn):
                res = close_fn()
                if asyncio.iscoroutine(res):
                    await res
        self._session = None
        self._session_loop = None

    async def is_available(self) -> bool:
        return bool(self.api_key)

    def _parse_response_json(self, raw_content: str) -> tuple[str, str]:
        cleaned = _CODE_FENCE_RE.sub("", (raw_content or "").strip()).strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise TranslationError(f"Translator returned invalid JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            raise TranslationError("Translator JSON response must be an object.")

        english_text = str(payload.get("english", "")).strip()
        hindi_text = str(payload.get("hindi", "")).strip()

        if not english_text or not hindi_text:
            raise TranslationError(
                "Translator JSON response must contain non-empty 'english' and 'hindi' fields."
            )
        return english_text, hindi_text

    async def translate_to_english_and_hindi(
        self,
        text: str,
        *,
        timeout: Optional[float] = None,
    ) -> HinglishTranslation:
        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Cannot translate empty or whitespace-only text.")

        if not self.api_key:
            raise TranslationError(
                "OPENAI_API_KEY is not configured for OpenAITranslatorProvider."
            )

        cleaned_source = text.strip()
        effective_timeout = float(timeout if timeout is not None else self.timeout)
        endpoint = f"{self.base_url}/chat/completions"

        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": cleaned_source},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        client_timeout = aiohttp.ClientTimeout(total=effective_timeout)
        session = self._get_session()

        try:
            async with session.post(
                endpoint,
                json=request_body,
                headers=headers,
                timeout=client_timeout,
            ) as resp:
                body_text = await resp.text()
                if resp.status >= 400:
                    raise TranslationError(
                        f"OpenAI translation API returned HTTP {resp.status}: {body_text[:200]}"
                    )
                data = json.loads(body_text)
        except asyncio.TimeoutError as exc:
            logger.warning(
                "[OpenAITranslatorProvider] Request timed out after %.1fs",
                effective_timeout,
            )
            raise TranslationTimeoutError(
                f"Translation request timed out after {effective_timeout:.1f}s"
            ) from exc
        except aiohttp.ClientError as exc:
            raise TranslationError(f"Translation HTTP request failed: {exc}") from exc
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError(f"Unexpected OpenAI translation error: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError("Malformed response structure from OpenAI translation API.") from exc

        english_text, hindi_text = self._parse_response_json(content)
        return HinglishTranslation(
            source_text=cleaned_source,
            english_text=english_text,
            hindi_text=hindi_text,
            provider=self.provider_name,
        )


__all__ = [
    "OpenAITranslatorProvider",
    "DEFAULT_OPENAI_BASE_URL",
    "DEFAULT_OPENAI_MODEL",
]


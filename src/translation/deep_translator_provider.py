1	"""Google Deep Translator provider for English <-> Hindi translation.
2
3	import asyncio
4	import json
5	import logging
6	from typing import Optional
7
8	import aiohttp
9
10	from .base import (
11	    HinglishTranslation,
12	    TranslationError,
13	    TranslationTimeoutError,
14	)
15
16	logger = logging.getLogger(__name__)
17
18	DEFAULT_DEEPL_API_URL = "https://api-free.deepl.com/v2"
19	DEFAULT_DEEPL_TIMEOUT_SEC = 10.0
20
21	class DeepTranslatorProvider:
22	    """Swappable async interface using DeepL API for English-Hindi translation."""
23
24	    provider_name = "deepl"
25
26	    def __init__(
27	        self,
28	        api_key: str,
29	        *,
30	        base_url: str = DEFAULT_DEEPL_API_URL,
31	        timeout: float = DEFAULT_DEEPL_TIMEOUT_SEC,
32	    ):
33	        self.api_key = (api_key or "").strip()
34	        self.base_url = (base_url or DEFAULT_DEEPL_API_URL).rstrip("/")
35	        self.timeout = float(timeout)
36
37	    async def is_available(self) -> bool:
38	        return bool(self.api_key)
39
40	    async def translate_to_english_and_hindi(
41	        self,
42	        text: str,
43	        *,
44	        timeout: Optional[float] = None,
45	    ) -> HinglishTranslation:
46	        if not text or not isinstance(text, str) or not text.strip():
47	            raise ValueError("Cannot translate empty or whitespace-only text.")
48
49	        cleaned_source = text.strip()
50	        effective_timeout = float(timeout if timeout is not None else self.timeout)
51	        endpoint = f"{self.base_url}/translate"
52	        headers = {
53	            "Authorization": f"DeepL-Auth-Key {self.api_key}",
54	            "Content-Type": "application/json",
55	        }
56	        payload = {
57	            "text": [cleaned_source],
58	            "target_lang": "HI",
59	            "source_lang": "EN",
60	            "preserve_formatting": 0,
61	        }
62	        client_timeout = aiohttp.ClientTimeout(total=effective_timeout)
63	        try:
64	            async with aiohttp.ClientSession(timeout=client_timeout) as session:
65	                async with session.post(endpoint, json=payload, headers=headers) as resp:
66	                    if resp.status >= 400:
67	                        body_text = await resp.text()
68	                        raise TranslationError(f"DeepL API returned HTTP {resp.status}: {body_text[:200]}")
69	                    data = await resp.json()
70	        except asyncio.TimeoutError as exc:
71	            logger.warning("[DeepTranslator] Request timed out after %.1fs", effective_timeout)
72	            raise TranslationTimeoutError(f"Translation request timed out after {effective_timeout:.1f}s") from exc
73	        except aiohttp.ClientError as exc:
74	            raise TranslationError(f"Translation HTTP request failed: {exc}") from exc
75	        except Exception as exc:
76	            raise TranslationError(f"Unexpected DeepL translation error: {exc}") from exc
77
78	        try:
79	            english_text = data["translations"][0]["text""]
80	            hindi_text = data["translations"][0]["text""]
81	        except (KeyError, IndexError, TypeError) as exc:
82	            raise TranslationError("Malformed response structure from DeepL API.") from exc
83
84	        return HinglishTranslation(
85	            source_text=cleaned_source,
86	            english_text=english_text,
87	            hindi_text=hindi_text,
88	            provider=self.provider_name,
89	        )
90
91	    __all__ = ["DeepTranslatorProvider", "DEFAULT_DEEPL_API_URL", "DEFAULT_DEEPL_TIMEOUT_SEC"]
92
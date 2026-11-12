---
name: translation-cache-and-retry
description: Implemented translation caching in SQLite and added exponential backoff retry to DeepTranslatorProvider to mitigate 429 errors.
metadata:
  type: project
---

[[translation-cache-and-retry]] helps resolve "429 Too Many Requests" from Google Translate API.

**Why:** Google's free translation tier rate-limits sequential requests.
**How to apply:**
- Caching: The `DeepTranslatorProvider` now uses `SettingsStore` (SQLite) to store `(text: str) -> (english: str, hindi: str)` mappings.
- Retry: `_translate_sync` implements exponential backoff (1s, 2s, 4s) when API calls fail or return empty content.
- Service Integration: `HinglishService` checks the cache before invoking the translation provider.

The cache schema was added to `SettingsStore` (SQLite `translation_cache` table). If further rate-limiting persists, consider switching to `OpenAITranslatorProvider` if an API key is available.

"""Hinglish mode orchestration: synced English text + Hindi voice/attachment audio."""

import asyncio
from dataclasses import dataclass, field
import io
import logging
from typing import Any, Awaitable, Callable, List, Optional

import discord

from ..constants import (
    DEFAULT_DISCORD_MAX_FILE_BYTES,
    DEFAULT_MAX_MESSAGE_CHARS,
    DEFAULT_TRANSLATION_TIMEOUT_SEC,
    DEFAULT_TTS_TIMEOUT_SEC,
)
from ..translation.base import (
    HinglishTranslation,
    TranslationError,
    TranslationTimeoutError,
    TranslatorProvider,
)
from ..tts.format_message import clean_discord_text
from ..tts.unified_provider import SynthesizedAudio, detect_audio_format
from ..voice.serial_queue import QueueFullError
from ..voice.session_manager import VoiceSessionError, VoiceSessionManager

logger = logging.getLogger(__name__)

DELIVERY_AUTO = "auto"
DELIVERY_VOICE = "voice"
DELIVERY_FILE = "file"


@dataclass
class HinglishResult:
    """Outcome of processing a message in Hinglish mode."""

    source_text: str
    english_text: str
    hindi_text: str
    delivered_via: str  # "voice" | "file" | "text_only" | "none"
    audio: Optional[SynthesizedAudio] = None
    voice_channel_id: Optional[str] = None
    error_code: Optional[str] = None
    voice_error_code: Optional[str] = None
    status_note: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.english_text) and self.delivered_via in ("voice", "file")


class HinglishService:
    """Coordinates translation, Hindi TTS synthesis, and Discord delivery.

    Delivery strategy choice:
    We use a **voice-first with attached-audio-file fallback** flow:
    - When a voice channel context is active (the user is in a voice channel or
      the bot already has an active guild voice session), joining/playing via
      Discord Voice keeps text channels uncluttered and supports conversational
      serial queuing via `VoiceSessionManager`.
    - When no voice channel is active, attaching the synthesized Hindi audio file
      alongside the English text guarantees both text and audio still reach the
      channel rather than silently dropping speech.
    """

    def __init__(
        self,
        translator: TranslatorProvider,
        tts_provider: Any,
        session_manager: Optional[VoiceSessionManager] = None,
        *,
        translation_timeout_sec: float = DEFAULT_TRANSLATION_TIMEOUT_SEC,
        tts_timeout_sec: float = DEFAULT_TTS_TIMEOUT_SEC,
        max_audio_file_bytes: int = DEFAULT_DISCORD_MAX_FILE_BYTES,
        max_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    ):
        self.translator = translator
        self.tts_provider = tts_provider
        self.session_manager = session_manager
        self.translation_timeout_sec = float(translation_timeout_sec)
        self.tts_timeout_sec = float(tts_timeout_sec)
        self.max_audio_file_bytes = int(max_audio_file_bytes)
        self.max_chars = int(max_chars)

    def sanitize_input(self, raw_text: str) -> str:
        """Cleans Discord mentions, URLs, custom emoji tags, and control characters."""
        return clean_discord_text(raw_text, max_chars=self.max_chars, strip_emojis=True)

    async def _synthesize_hindi(
        self,
        hindi_text: str,
        *,
        voice: str = "default",
    ) -> SynthesizedAudio:
        """Synthesizes Hindi speech using the injected TTS provider with an explicit timeout."""
        if not self.tts_provider:
            raise RuntimeError("No TTS provider is configured.")

        async def _run_synth() -> SynthesizedAudio:
            if hasattr(self.tts_provider, "synthesize_audio") and callable(
                self.tts_provider.synthesize_audio
            ):
                return await self.tts_provider.synthesize_audio(
                    hindi_text,
                    language="hi",
                    voice=voice,
                )

            raw_bytes = await self.tts_provider.synthesize(
                hindi_text,
                language="hi",
                voice=voice,
            )
            if not raw_bytes:
                raise RuntimeError("TTS provider returned empty audio bytes.")
            fmt = detect_audio_format(raw_bytes)
            mime_map = {"wav": "audio/wav", "ogg": "audio/ogg", "mp3": "audio/mpeg"}
            return SynthesizedAudio(
                data=raw_bytes,
                audio_format=fmt,
                filename_extension=fmt,
                mime_type=mime_map.get(fmt, "audio/mpeg"),
            )

        audio = await asyncio.wait_for(_run_synth(), timeout=self.tts_timeout_sec)
        if not audio.data:
            raise RuntimeError("TTS provider returned empty audio bytes.")
        return audio

    @staticmethod
    def _resolve_guild_me(guild: Any) -> Any:
        if not guild:
            return None
        me = getattr(guild, "me", None)
        if not me and hasattr(guild, "members"):
            me = getattr(guild.members, "me", None)
        return me

    def _check_voice_permissions(self, guild: Any, voice_channel: Any) -> Optional[tuple[str, str]]:
        """Validates Connect and Speak permissions on the target voice channel."""
        if not voice_channel or not hasattr(voice_channel, "permissions_for"):
            return None
        me = self._resolve_guild_me(guild)
        if not me:
            return None

        perms = voice_channel.permissions_for(me)
        has_connect = getattr(perms, "connect", True)
        has_speak = getattr(perms, "speak", True)

        if hasattr(perms, "has") and callable(perms.has):
            has_connect = perms.has("connect") or perms.has(1)
            has_speak = perms.has("speak") or perms.has(2)

        ch_id = getattr(voice_channel, "id", "voice")
        if not has_connect:
            return (
                "MISSING_CONNECT_PERM",
                f"Bot lacks the **Connect** permission in <#{ch_id}>.",
            )
        if not has_speak:
            return (
                "MISSING_SPEAK_PERM",
                f"Bot lacks the **Speak** permission in <#{ch_id}>.",
            )
        return None

    def _check_attach_files_permission(self, guild: Any, text_channel: Any) -> Optional[tuple[str, str]]:
        """Validates Attach Files permission on the target text channel."""
        if not text_channel or not hasattr(text_channel, "permissions_for"):
            return None
        target_guild = guild or getattr(text_channel, "guild", None)
        me = self._resolve_guild_me(target_guild)
        if not me:
            return None

        perms = text_channel.permissions_for(me)
        can_attach = getattr(perms, "attach_files", True)
        if hasattr(perms, "has") and callable(perms.has):
            can_attach = perms.has("attach_files")

        if not can_attach:
            return (
                "MISSING_ATTACH_FILES_PERM",
                "Bot lacks the **Attach Files** permission in this channel.",
            )
        return None

    def _effective_file_limit_bytes(self, guild: Any) -> int:
        guild_limit = getattr(guild, "filesize_limit", None) if guild else None
        if isinstance(guild_limit, int) and guild_limit > 0:
            return min(self.max_audio_file_bytes, guild_limit)
        return self.max_audio_file_bytes

    @staticmethod
    def _is_session_healthy(session: Any) -> bool:
        if not session or getattr(session, "is_destroyed", False):
            return False
        vc = getattr(session, "voice_client", None)
        if vc is None:
            return True
        if hasattr(vc, "is_connected"):
            return bool(vc.is_connected() if callable(vc.is_connected) else vc.is_connected)
        return True

    async def process_message(
        self,
        raw_text: str,
        *,
        guild: Any = None,
        member: Any = None,
        text_channel: Any = None,
        author_name: str = "User",
        author_id: str = "",
        delivery_mode: str = DELIVERY_AUTO,
        hindi_voice: str = "default",
    ) -> HinglishResult:
        """Translates input into synced English+Hindi and prepares/executes audio delivery."""
        cleaned = self.sanitize_input(raw_text)
        if not cleaned:
            return HinglishResult(
                source_text=raw_text or "",
                english_text="",
                hindi_text="",
                delivered_via="none",
                error_code="EMPTY_TEXT",
                status_note="❌ Please provide text to translate and speak in Hinglish mode.",
            )

        # 1. Translate once into paired English (display) and Hindi (spoken) text
        try:
            translation: HinglishTranslation = await self.translator.translate_to_english_and_hindi(
                cleaned,
                timeout=self.translation_timeout_sec,
            )
        except TranslationTimeoutError as exc:
            logger.warning("[HinglishService] Translation timed out: %s", exc)
            return HinglishResult(
                source_text=cleaned,
                english_text="",
                hindi_text="",
                delivered_via="none",
                error_code="TRANSLATION_TIMEOUT",
                status_note=f"❌ Translation service timed out ({exc}).",
            )
        except (TranslationError, ValueError) as exc:
            logger.warning("[HinglishService] Translation failed: %s", exc)
            return HinglishResult(
                source_text=cleaned,
                english_text="",
                hindi_text="",
                delivered_via="none",
                error_code="TRANSLATION_FAILED",
                status_note=f"❌ Translation failed: {exc}",
            )

        mode = (delivery_mode or DELIVERY_AUTO).strip().lower()
        if mode not in (DELIVERY_AUTO, DELIVERY_VOICE, DELIVERY_FILE):
            mode = DELIVERY_AUTO

        member_vc = getattr(getattr(member, "voice", None), "channel", None)
        guild_id = str(getattr(guild, "id", "")) if guild else ""
        active_session = (
            self.session_manager.get_session(guild_id)
            if (self.session_manager and guild_id)
            else None
        )
        if active_session and not self._is_session_healthy(active_session):
            active_session = None

        # Check early if explicit voice mode was requested when no voice channel is active
        if mode == DELIVERY_VOICE and member_vc is None and active_session is None:
            return HinglishResult(
                source_text=cleaned,
                english_text=translation.english_text,
                hindi_text=translation.hindi_text,
                delivered_via="text_only",
                error_code="USER_NOT_IN_VOICE",
                voice_error_code="USER_NOT_IN_VOICE",
                status_note=(
                    "⚠️ You are not in a voice channel. Join a voice channel to hear Hindi voice playback, "
                    "or use `auto` / `file` delivery."
                ),
            )

        # 2. Synthesize Hindi audio from translation.hindi_text (same meaning as english_text)
        try:
            audio = await self._synthesize_hindi(translation.hindi_text, voice=hindi_voice)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning("[HinglishService] Hindi TTS timed out: %s", exc)
            return HinglishResult(
                source_text=cleaned,
                english_text=translation.english_text,
                hindi_text=translation.hindi_text,
                delivered_via="text_only",
                error_code="TTS_TIMEOUT",
                status_note=(
                    f"⚠️ Hindi TTS timed out after {self.tts_timeout_sec:.1f}s; "
                    "showing English text only."
                ),
            )
        except Exception as exc:
            logger.warning("[HinglishService] Hindi TTS failed: %s", exc)
            return HinglishResult(
                source_text=cleaned,
                english_text=translation.english_text,
                hindi_text=translation.hindi_text,
                delivered_via="text_only",
                error_code="TTS_FAILED",
                status_note=f"⚠️ Hindi TTS synthesis failed ({exc}); showing English text only.",
            )

        # 3. Route delivery: voice channel first when active, else attached audio file
        warnings: List[str] = []
        voice_error_code: Optional[str] = None
        should_try_voice = mode == DELIVERY_VOICE or (
            mode == DELIVERY_AUTO and (member_vc is not None or active_session is not None)
        )

        if should_try_voice:
            voice_outcome = await self._deliver_via_voice(
                guild=guild,
                member_vc=member_vc,
                active_session=active_session,
                translation=translation,
                audio=audio,
                author_name=author_name,
                author_id=author_id,
                hindi_voice=hindi_voice,
            )
            if voice_outcome["success"]:
                vc_name = voice_outcome.get("channel_name")
                vc_label = f"#{vc_name}" if vc_name else f"voice channel {voice_outcome['channel_id']}"
                return HinglishResult(
                    source_text=cleaned,
                    english_text=translation.english_text,
                    hindi_text=translation.hindi_text,
                    delivered_via=DELIVERY_VOICE,
                    audio=audio,
                    voice_channel_id=voice_outcome["channel_id"],
                    status_note=f"🔊 Playing Hindi audio in {vc_label}.",
                )

            voice_error_code = voice_outcome["error_code"]
            voice_error_msg = voice_outcome["message"]

            if mode == DELIVERY_VOICE:
                return HinglishResult(
                    source_text=cleaned,
                    english_text=translation.english_text,
                    hindi_text=translation.hindi_text,
                    delivered_via="text_only",
                    error_code=voice_error_code,
                    voice_error_code=voice_error_code,
                    status_note=f"⚠️ {voice_error_msg}",
                )

            # In auto mode, fall through to file attachment while keeping the voice failure visible
            warnings.append(voice_error_msg)

        elif mode == DELIVERY_AUTO:
            warnings.append("No active voice channel (user is not in voice); sending audio file instead.")

        # 4. File attachment delivery path (no voice channel active, file mode, or auto fallback)
        attach_perm_err = self._check_attach_files_permission(guild, text_channel)
        if attach_perm_err:
            code, msg = attach_perm_err
            warnings.append(msg)
            return HinglishResult(
                source_text=cleaned,
                english_text=translation.english_text,
                hindi_text=translation.hindi_text,
                delivered_via="text_only",
                error_code=code,
                voice_error_code=voice_error_code,
                status_note="⚠️ " + " ".join(warnings),
                warnings=warnings,
            )

        max_bytes = self._effective_file_limit_bytes(guild)
        if len(audio.data) > max_bytes:
            size_mb = len(audio.data) / (1024 * 1024)
            limit_mb = max_bytes / (1024 * 1024)
            size_msg = (
                f"Hindi audio file ({size_mb:.2f} MB) exceeds Discord's upload limit "
                f"({limit_mb:.2f} MB). Shorten the message or join a voice channel."
            )
            warnings.append(size_msg)
            return HinglishResult(
                source_text=cleaned,
                english_text=translation.english_text,
                hindi_text=translation.hindi_text,
                delivered_via="text_only",
                error_code="FILE_TOO_LARGE",
                voice_error_code=voice_error_code,
                status_note="⚠️ " + " ".join(warnings),
                warnings=warnings,
            )

        note_prefix = f"⚠️ {' '.join(warnings)} " if warnings and voice_error_code else (
            f"🔇 {warnings[0]} " if warnings else ""
        )
        return HinglishResult(
            source_text=cleaned,
            english_text=translation.english_text,
            hindi_text=translation.hindi_text,
            delivered_via=DELIVERY_FILE,
            audio=audio,
            voice_error_code=voice_error_code,
            status_note=f"{note_prefix}📎 Attached Hindi audio (`hinglish_hi.{audio.filename_extension}`).".strip(),
            warnings=warnings,
        )

    async def _deliver_via_voice(
        self,
        *,
        guild: Any,
        member_vc: Any,
        active_session: Any,
        translation: HinglishTranslation,
        audio: SynthesizedAudio,
        author_name: str,
        author_id: str,
        hindi_voice: str,
    ) -> dict:
        if not self.session_manager or not guild:
            return {
                "success": False,
                "error_code": "VOICE_UNAVAILABLE",
                "message": "Voice playback is not available in this context.",
            }

        session = active_session
        target_vc = member_vc

        if target_vc is not None:
            # Verify Connect & Speak permissions before attempting join/move
            perm_err = self._check_voice_permissions(guild, target_vc)
            if perm_err:
                return {"success": False, "error_code": perm_err[0], "message": perm_err[1]}

            need_join_or_move = session is None or str(getattr(session, "channel_id", "")) != str(target_vc.id)
            if need_join_or_move:
                try:
                    join_res = await self.session_manager.join(guild, target_vc)
                    session = join_res["session"]
                except VoiceSessionError as exc:
                    return {"success": False, "error_code": exc.code, "message": exc.message}
                except discord.Forbidden:
                    return {
                        "success": False,
                        "error_code": "MISSING_VOICE_PERMISSION",
                        "message": "Bot lacks **Connect** or **Speak** permission in the voice channel.",
                    }
                except Exception as exc:
                    return {
                        "success": False,
                        "error_code": "VOICE_JOIN_FAILED",
                        "message": f"Could not join voice channel: {exc}",
                    }
        elif session is not None:
            # Reusing existing session: verify Speak permission on active channel if resolvable
            session_ch_id = getattr(session, "channel_id", None)
            if session_ch_id and hasattr(guild, "get_channel"):
                try:
                    existing_ch = guild.get_channel(int(session_ch_id))
                    perm_err = self._check_voice_permissions(guild, existing_ch)
                    if perm_err:
                        return {"success": False, "error_code": perm_err[0], "message": perm_err[1]}
                except (ValueError, TypeError):
                    pass

        sq = getattr(session, "serial_queue", getattr(session, "serialQueue", None))
        if not sq:
            return {
                "success": False,
                "error_code": "VOICE_QUEUE_UNAVAILABLE",
                "message": "Active voice session has no playback queue.",
            }

        try:
            sq.enqueue(
                {
                    "spokenText": translation.hindi_text,
                    "spoken_text": translation.hindi_text,
                    "text": translation.english_text,
                    "authorName": author_name,
                    "author_name": author_name,
                    "userId": str(author_id),
                    "user_id": str(author_id),
                    "language": "hi",
                    "voice": hindi_voice,
                    "audioBytes": audio.data,
                    "audio_bytes": audio.data,
                }
            )
        except QueueFullError:
            return {
                "success": False,
                "error_code": "QUEUE_FULL",
                "message": "Voice playback queue is currently full.",
            }
        except Exception as exc:
            return {
                "success": False,
                "error_code": "VOICE_ENQUEUE_FAILED",
                "message": f"Failed to queue Hindi voice playback: {exc}",
            }

        channel_name = None
        if target_vc is not None:
            channel_name = getattr(target_vc, "name", None)
        elif guild and hasattr(guild, "get_channel"):
            try:
                ch = guild.get_channel(int(getattr(session, "channel_id", 0)))
                channel_name = getattr(ch, "name", None) if ch else None
            except (ValueError, TypeError):
                pass

        return {
            "success": True,
            "channel_id": str(getattr(session, "channel_id", "")),
            "channel_name": channel_name,
        }

    def format_discord_embed(
        self,
        result: HinglishResult,
        *,
        author_name: Optional[str] = None,
    ) -> discord.Embed:
        """Builds a clean Discord embed showing the English text and audio delivery status."""
        if result.error_code in ("EMPTY_TEXT", "TRANSLATION_TIMEOUT", "TRANSLATION_FAILED"):
            return discord.Embed(
                title="🌐 Hinglish Mode",
                description=result.status_note,
                color=0xED4245,
            )

        color = 0x57F287 if result.ok else 0xFEE75C
        embed = discord.Embed(
            description=result.english_text,
            color=color,
        )
        if author_name:
            embed.set_author(name=f"{author_name} (English)")
        else:
            embed.title = "🌐 Hinglish (English Text)"

        if result.status_note:
            embed.set_footer(text=result.status_note[:2048])
        return embed

    async def send_result(
        self,
        sender: Callable[..., Awaitable[Any]],
        result: HinglishResult,
        *,
        author_name: Optional[str] = None,
    ) -> HinglishResult:
        """Sends the HinglishResult via `sender` (channel.send or interaction.followup.send).

        Handles runtime Discord API Forbidden (missing Attach Files) and HTTP 413
        (Payload Too Large) gracefully by retrying with English text and a warning.
        """
        embed = self.format_discord_embed(result, author_name=author_name)

        if result.delivered_via == DELIVERY_FILE and result.audio is not None:
            filename = f"hinglish_hi.{result.audio.filename_extension}"
            file_obj = discord.File(io.BytesIO(result.audio.data), filename=filename)
            try:
                await sender(embed=embed, file=file_obj)
                return result
            except discord.Forbidden:
                logger.warning("[HinglishService] Discord rejected file upload (Forbidden / Attach Files missing).")
                result.delivered_via = "text_only"
                result.error_code = "MISSING_ATTACH_FILES_PERM"
                result.status_note = "⚠️ Bot lacks the **Attach Files** permission; showing English text only."
                embed = self.format_discord_embed(result, author_name=author_name)
                try:
                    await sender(embed=embed)
                except discord.Forbidden:
                    logger.warning("[HinglishService] Also lacks Send Messages permission in target channel.")
                return result
            except discord.HTTPException as exc:
                is_too_large = getattr(exc, "status", None) == 413 or getattr(exc, "code", None) == 40005
                if is_too_large:
                    logger.warning("[HinglishService] Discord rejected audio attachment as too large: %s", exc)
                    result.delivered_via = "text_only"
                    result.error_code = "FILE_TOO_LARGE"
                    result.status_note = (
                        "⚠️ Hindi audio file exceeded Discord's upload limit; showing English text only."
                    )
                    embed = self.format_discord_embed(result, author_name=author_name)
                    await sender(embed=embed)
                    return result
                raise

        try:
            await sender(embed=embed)
        except discord.Forbidden:
            logger.warning("[HinglishService] Missing Send Messages permission when delivering Hinglish text.")
        return result


__all__ = [
    "HinglishService",
    "HinglishResult",
    "DELIVERY_AUTO",
    "DELIVERY_VOICE",
    "DELIVERY_FILE",
]

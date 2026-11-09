"""Discord slash commands for TTS."""

import logging
from typing import List, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from ..constants import (
    COMMAND_CLEAR,
    COMMAND_JOIN,
    COMMAND_LANGUAGE,
    COMMAND_LEAVE,
    COMMAND_QUEUE,
    COMMAND_SETUP,
    COMMAND_SKIP,
    COMMAND_STATUS,
    COMMAND_STOP,
    COMMAND_VOICE,
    LANGUAGE_CHOICES,
    VOICE_VARIANT_CHOICES,
)
from ..storage.settings_store import SettingsStore
from ..voice.session_manager import VoiceSessionManager

logger = logging.getLogger(__name__)

LANG_APP_CHOICES = [
    app_commands.Choice(name=c["name"], value=c["value"])
    for c in LANGUAGE_CHOICES
]

VOICE_APP_CHOICES = [
    app_commands.Choice(name=c["name"], value=c["value"])
    for c in VOICE_VARIANT_CHOICES
]


class TtsCommandGroup:
    """Deprecated: commands are now top-level slash commands without the /tts prefix."""
    pass


def setup_tts_commands(
    tree: app_commands.CommandTree,
    session_manager: VoiceSessionManager,
    settings_store: SettingsStore,
) -> List[app_commands.Command]:
    """Registers all TTS slash commands as top-level application commands."""

    @tree.command(
        name=COMMAND_SETUP,
        description="Choose the text channel that should be read aloud (Manage Server required)",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The text or voice channel to read aloud (defaults to current channel)"
    )
    async def setup(
        interaction: discord.Interaction,
        channel: Optional[Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel]] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        user_perms = getattr(interaction.user, "guild_permissions", None) or getattr(interaction, "permissions", None)
        if not (user_perms and user_perms.manage_guild):
            await interaction.response.send_message(
                "❌ You need the **Manage Server** permission to configure the TTS text channel.",
                ephemeral=True,
            )
            return

        target_channel = channel or interaction.channel
        if not target_channel:
            await interaction.response.send_message(
                "❌ Could not determine which channel to configure. Please specify a channel.",
                ephemeral=True,
            )
            return

        settings_store.set_configured_channel(str(interaction.guild.id), str(target_channel.id))

        await interaction.response.send_message(
            f"✅ Designated <#{target_channel.id}> as the text channel to be read aloud."
        )

    @tree.command(
        name=COMMAND_JOIN,
        description="Join the voice channel you are currently in",
    )
    async def join(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        member = interaction.user
        if not isinstance(member, discord.Member) and interaction.guild:
            member = interaction.guild.get_member(interaction.user.id) or interaction.user

        voice_state = getattr(member, "voice", None)
        voice_channel = getattr(voice_state, "channel", None) if voice_state else None

        if not voice_channel:
            await interaction.followup.send(
                "❌ You must be connected to a voice channel to use `/join`."
            )
            return

        try:
            result = await session_manager.join(interaction.guild, voice_channel)
            reused = result.get("reused", False)
            configured_channel_id = settings_store.get_configured_channel(str(interaction.guild.id))

            if configured_channel_id and configured_channel_id != str(voice_channel.id):
                channel_note = (
                    f"\nReading messages from <#{voice_channel.id}> (voice chat) and <#{configured_channel_id}>."
                )
            else:
                channel_note = f"\nReading messages from <#{voice_channel.id}>."

            if reused:
                await interaction.followup.send(
                    f"🔊 Already connected to <#{voice_channel.id}>.{channel_note}"
                )
            else:
                await interaction.followup.send(
                    f"✅ Joined voice channel <#{voice_channel.id}>.{channel_note}"
                )
        except Exception as err:
            await interaction.followup.send(f"❌ Could not join voice channel: {err}")

    @tree.command(
        name=COMMAND_LEAVE,
        description="Leave voice and discard current and pending speech",
    )
    async def leave(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            left = await session_manager.leave(str(interaction.guild.id))
            if left:
                await interaction.followup.send(
                    "👋 Left the voice channel and discarded current and pending speech."
                )
            else:
                await interaction.followup.send(
                    "ℹ️ The bot is not currently connected to voice in this server."
                )
        except Exception as err:
            await interaction.followup.send(f"❌ Error leaving voice: {err}")

    @tree.command(
        name=COMMAND_VOICE,
        description="Select an eSpeak voice variant for yourself, or set the server default",
    )
    @app_commands.describe(
        variant="eSpeak voice variant to use",
        server="Apply as server-wide default instead of personal override (Manage Server required)",
    )
    @app_commands.choices(variant=VOICE_APP_CHOICES)
    async def voice(
        interaction: discord.Interaction,
        variant: str,
        server: Optional[bool] = False,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        if server:
            user_perms = getattr(interaction.user, "guild_permissions", None) or getattr(interaction, "permissions", None)
            if not (user_perms and user_perms.manage_guild):
                await interaction.response.send_message(
                    "❌ You need the **Manage Server** permission to set the server-wide default voice variant.",
                    ephemeral=True,
                )
                return

            settings_store.set_guild_defaults(guild_id, voice=variant)
            await interaction.response.send_message(
                f"✅ Updated server default voice variant to `{variant}`."
            )
            return

        settings_store.set_user_preferences(guild_id, user_id, voice=variant)
        await interaction.response.send_message(
            f"✅ Updated your personal voice variant override to `{variant}`.",
            ephemeral=True,
        )

    @tree.command(
        name=COMMAND_LANGUAGE,
        description="Select the speech language for yourself, or set the server default",
    )
    @app_commands.describe(
        language="Language code for speech synthesis",
        server="Apply as server-wide default instead of personal override (Manage Server required)",
    )
    @app_commands.choices(language=LANG_APP_CHOICES)
    async def language(
        interaction: discord.Interaction,
        language: str,
        server: Optional[bool] = False,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        if server:
            user_perms = getattr(interaction.user, "guild_permissions", None) or getattr(interaction, "permissions", None)
            if not (user_perms and user_perms.manage_guild):
                await interaction.response.send_message(
                    "❌ You need the **Manage Server** permission to set the server-wide default language.",
                    ephemeral=True,
                )
                return

            settings_store.set_guild_defaults(guild_id, language=language)
            await interaction.response.send_message(
                f"✅ Updated server default speech language to `{language}`."
            )
            return

        settings_store.set_user_preferences(guild_id, user_id, language=language)
        await interaction.response.send_message(
            f"✅ Updated your personal speech language override to `{language}`.",
            ephemeral=True,
        )

    @tree.command(
        name=COMMAND_QUEUE,
        description="Inspect the current message and waiting queue",
    )
    async def queue(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = session_manager.get_session(str(interaction.guild.id))
        if not session:
            await interaction.response.send_message(
                "❌ The bot is not currently connected to voice in this server.",
                ephemeral=True,
            )
            return

        status = session.serial_queue.get_status()
        embed = discord.Embed(
            title="🔊 TTS Queue",
            color=0x5865F2,
        )

        current = status.get("currentJob") or status.get("current_job")
        if current:
            embed.add_field(
                name="Now Speaking",
                value=f"**{current['authorName']}**: \"{current['text'][:100]}\"",
                inline=False,
            )
        else:
            embed.add_field(
                name="Now Speaking",
                value="*Silence (idle)*",
                inline=False,
            )

        embed.add_field(
            name="Queue Depth",
            value=f"{status.get('pendingCount', 0)} message(s) waiting",
            inline=False,
        )

        pending = status.get("pending", [])
        if pending:
            preview_lines = [
                f"{item['position']}. **{item['authorName']}** ({item['textLength']} chars)"
                for item in pending[:5]
            ]
            embed.add_field(
                name="Upcoming Messages",
                value="\n".join(preview_lines),
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @tree.command(
        name=COMMAND_SKIP,
        description="Cancel only the current message; the next waiting item starts",
    )
    async def skip(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = session_manager.get_session(str(interaction.guild.id))
        if not session:
            await interaction.response.send_message(
                "❌ The bot is not currently connected to voice in this server.",
                ephemeral=True,
            )
            return

        result = session.serial_queue.skip()
        if result.get("skipped"):
            await interaction.response.send_message(
                f"⏭️ Skipped current message. ({result.get('pendingRemaining', 0)} remaining in queue)"
            )
        else:
            await interaction.response.send_message(
                "ℹ️ No message is currently speaking to skip."
            )

    @tree.command(
        name=COMMAND_STOP,
        description="Cancel current speech and discard all waiting items",
    )
    async def stop(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = session_manager.get_session(str(interaction.guild.id))
        if not session:
            await interaction.response.send_message(
                "❌ The bot is not currently connected to voice in this server.",
                ephemeral=True,
            )
            return

        result = session.serial_queue.stop()
        dropped = result.get("droppedCount", 0)
        await interaction.response.send_message(
            f"⏹️ Cancelled current speech and discarded {dropped} waiting item(s)."
        )

    @tree.command(
        name=COMMAND_CLEAR,
        description="Discard waiting items without interrupting current speech",
    )
    async def clear(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = session_manager.get_session(str(interaction.guild.id))
        if not session:
            await interaction.response.send_message(
                "❌ The bot is not currently connected to voice in this server.",
                ephemeral=True,
            )
            return

        result = session.serial_queue.clear()
        cleared = result.get("clearedCount", 0)
        curr = result.get("currentPlaying", False)
        suffix = " (Current speech continues)" if curr else ""
        await interaction.response.send_message(
            f"🧹 Discarded {cleared} waiting item(s).{suffix}"
        )

    @tree.command(
        name=COMMAND_STATUS,
        description="Show configured text channel, voice connection, and server default",
    )
    async def status(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        session = session_manager.get_session(guild_id)
        configured_channel_id = settings_store.get_configured_channel(guild_id)
        guild_defaults = settings_store.get_guild_settings(guild_id)
        user_prefs = settings_store.get_user_preferences(guild_id, user_id)
        effective = settings_store.resolve_preferences(guild_id, user_id)

        embed = discord.Embed(
            title="🎙️ TTS Status",
            color=0x57F287 if session else 0xED4245,
        )

        embed.add_field(
            name="Voice Connection",
            value=f"🟢 Connected in <#{session.channel_id}>" if session else "🔴 Disconnected (`/join`)",
            inline=True,
        )

        provider = getattr(session_manager, "tts_provider", None)
        default_eng = getattr(provider, "default_engine", "gtts") if provider else "gtts"
        engine_label = "Google Translate TTS (Natural)" if default_eng == "gtts" else "eSpeak NG (Offline)"
        embed.add_field(
            name="Speech Engine",
            value=f"⚡ `{engine_label}`",
            inline=True,
        )

        session_channel_id = getattr(session, "channel_id", None) if session else None
        if configured_channel_id and session_channel_id:
            if configured_channel_id == session_channel_id:
                listening_channels = f"<#{configured_channel_id}> (Voice chat)"
            else:
                listening_channels = f"<#{session_channel_id}> (Voice chat) + <#{configured_channel_id}>"
        elif session_channel_id:
            listening_channels = f"<#{session_channel_id}> (Voice chat)"
        elif configured_channel_id:
            listening_channels = f"<#{configured_channel_id}>"
        else:
            listening_channels = "⚠️ *None (Join a voice channel or run `/setup`)*"

        embed.add_field(
            name="Listening Channels",
            value=listening_channels,
            inline=True,
        )

        g_lang = guild_defaults.get("defaultLanguage") if guild_defaults else None
        g_voice = guild_defaults.get("defaultVoice") if guild_defaults else None
        embed.add_field(
            name="Server Defaults",
            value=f"Language: `{g_lang or 'Default (en)'}`\nVoice Variant: `{g_voice or 'Default'}`",
            inline=False,
        )

        u_lang = user_prefs.get("language") if user_prefs else None
        u_voice = user_prefs.get("voice") if user_prefs else None
        embed.add_field(
            name="Your Personal Overrides",
            value=f"Language: `{u_lang or 'None (using server default)'}`\nVoice Variant: `{u_voice or 'None (using server default)'}`",
            inline=False,
        )

        embed.add_field(
            name="Your Effective Voice Profile",
            value=f"Language: `{effective['language']}` | Voice Variant: `{effective['voice']}`",
            inline=False,
        )

        await interaction.response.send_message(embed=embed)

    if not getattr(tree, "_tts_error_handler_registered", False):
        @tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            cmd_name = getattr(interaction.command, "name", "command")
            logger.error("Command error during /%s: %s", cmd_name, error, exc_info=True)
            msg = f"❌ An error occurred while executing `/{cmd_name}`: {error}"
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass

        setattr(tree, "_tts_error_handler_registered", True)

    return [
        setup,
        join,
        leave,
        voice,
        language,
        queue,
        skip,
        stop,
        clear,
        status,
    ]


class TTSCommandsCog(commands.Cog, name="TTS"):
    """Cog wrapper for Discord TTS slash commands."""

    def __init__(
        self,
        bot: commands.Bot,
        session_manager: VoiceSessionManager,
        settings_store: SettingsStore,
    ):
        self.bot = bot
        self.session_manager = session_manager
        self.settings_store = settings_store
        self.commands = setup_tts_commands(self.bot.tree, session_manager, settings_store)


__all__ = ["setup_tts_commands", "TtsCommandGroup", "TTSCommandsCog"]

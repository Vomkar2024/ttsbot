"""Discord slash command group for VoiceBridge TTS."""

from typing import Optional

import discord
from discord import app_commands

from ..constants import (
    COMMAND_TTS,
    LANGUAGE_CHOICES,
    SUBCOMMAND_CLEAR,
    SUBCOMMAND_JOIN,
    SUBCOMMAND_LANGUAGE,
    SUBCOMMAND_LEAVE,
    SUBCOMMAND_QUEUE,
    SUBCOMMAND_SETUP,
    SUBCOMMAND_SKIP,
    SUBCOMMAND_STATUS,
    SUBCOMMAND_STOP,
    SUBCOMMAND_VOICE,
    VOICE_VARIANT_CHOICES,
)
from ..storage.settings_store import SettingsStore
from ..voice.session_manager import VoiceSessionManager

LANG_APP_CHOICES = [
    app_commands.Choice(name=c["name"], value=c["value"])
    for c in LANGUAGE_CHOICES
]

VOICE_APP_CHOICES = [
    app_commands.Choice(name=c["name"], value=c["value"])
    for c in VOICE_VARIANT_CHOICES
]


class TtsCommandGroup(app_commands.Group):
    def __init__(
        self,
        session_manager: VoiceSessionManager,
        settings_store: SettingsStore,
    ):
        super().__init__(
            name=COMMAND_TTS,
            description="VoiceBridge local text-to-speech bot controls and preferences",
        )
        self.session_manager = session_manager
        self.settings_store = settings_store

    @app_commands.command(
        name=SUBCOMMAND_SETUP,
        description="Choose the text channel that should be read aloud (Manage Server required)",
    )
    @app_commands.describe(
        channel="The text channel to read aloud (defaults to current channel)"
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ You need the **Manage Server** permission to configure the TTS text channel.",
                ephemeral=True,
            )
            return

        target_channel = channel or interaction.channel
        self.settings_store.set_configured_channel(str(interaction.guild.id), str(target_channel.id))

        await interaction.response.send_message(
            f"✅ Designated <#{target_channel.id}> as the text channel to be read aloud."
        )

    @app_commands.command(
        name=SUBCOMMAND_JOIN,
        description="Join the voice channel you are currently in",
    )
    async def join(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        member = interaction.user
        voice_state = getattr(member, "voice", None)
        voice_channel = getattr(voice_state, "channel", None) if voice_state else None

        if not voice_channel:
            await interaction.followup.send(
                "❌ You must be connected to a voice channel to use `/tts join`."
            )
            return

        try:
            result = await self.session_manager.join(interaction.guild, voice_channel)
            reused = result.get("reused", False)
            configured_channel_id = self.settings_store.get_configured_channel(str(interaction.guild.id))

            channel_note = (
                f"\nReading messages from <#{configured_channel_id}>."
                if configured_channel_id
                else "\n⚠️ No text channel configured yet! Run `/tts setup` to select one."
            )

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

    @app_commands.command(
        name=SUBCOMMAND_LEAVE,
        description="Leave voice and discard current and pending speech",
    )
    async def leave(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        left = await self.session_manager.leave(str(interaction.guild.id))
        if left:
            await interaction.followup.send(
                "👋 Left the voice channel and discarded current and pending speech."
            )
        else:
            await interaction.followup.send(
                "ℹ️ The bot is not currently connected to voice in this server."
            )

    @app_commands.command(
        name=SUBCOMMAND_VOICE,
        description="Select an eSpeak voice variant for yourself, or set the server default",
    )
    @app_commands.describe(
        variant="eSpeak voice variant to use",
        server="Apply as server-wide default instead of personal override (Manage Server required)",
    )
    @app_commands.choices(variant=VOICE_APP_CHOICES)
    async def voice(
        self,
        interaction: discord.Interaction,
        variant: str,
        server: Optional[bool] = False,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        if server:
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message(
                    "❌ You need the **Manage Server** permission to set the server-wide default voice variant.",
                    ephemeral=True,
                )
                return

            self.settings_store.set_guild_defaults(guild_id, voice=variant)
            await interaction.response.send_message(
                f"✅ Updated server default voice variant to `{variant}`."
            )
            return

        self.settings_store.set_user_preferences(guild_id, user_id, voice=variant)
        await interaction.response.send_message(
            f"✅ Updated your personal voice variant override to `{variant}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name=SUBCOMMAND_LANGUAGE,
        description="Select the speech language for yourself, or set the server default",
    )
    @app_commands.describe(
        language="Language code for speech synthesis",
        server="Apply as server-wide default instead of personal override (Manage Server required)",
    )
    @app_commands.choices(language=LANG_APP_CHOICES)
    async def language(
        self,
        interaction: discord.Interaction,
        language: str,
        server: Optional[bool] = False,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        if server:
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message(
                    "❌ You need the **Manage Server** permission to set the server-wide default language.",
                    ephemeral=True,
                )
                return

            self.settings_store.set_guild_defaults(guild_id, language=language)
            await interaction.response.send_message(
                f"✅ Updated server default speech language to `{language}`."
            )
            return

        self.settings_store.set_user_preferences(guild_id, user_id, language=language)
        await interaction.response.send_message(
            f"✅ Updated your personal speech language override to `{language}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name=SUBCOMMAND_QUEUE,
        description="Inspect the current message and waiting queue",
    )
    async def queue(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = self.session_manager.get_session(str(interaction.guild.id))
        if not session:
            await interaction.response.send_message(
                "❌ The bot is not currently connected to voice in this server.",
                ephemeral=True,
            )
            return

        status = session.serial_queue.get_status()
        embed = discord.Embed(
            title="🔊 VoiceBridge Queue",
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

    @app_commands.command(
        name=SUBCOMMAND_SKIP,
        description="Cancel only the current message; the next waiting item starts",
    )
    async def skip(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = self.session_manager.get_session(str(interaction.guild.id))
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

    @app_commands.command(
        name=SUBCOMMAND_STOP,
        description="Cancel current speech and discard all waiting items",
    )
    async def stop(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = self.session_manager.get_session(str(interaction.guild.id))
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

    @app_commands.command(
        name=SUBCOMMAND_CLEAR,
        description="Discard waiting items without interrupting current speech",
    )
    async def clear(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        session = self.session_manager.get_session(str(interaction.guild.id))
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

    @app_commands.command(
        name=SUBCOMMAND_STATUS,
        description="Show configured text channel, voice connection, and server default",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "The `/tts` command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)

        session = self.session_manager.get_session(guild_id)
        configured_channel_id = self.settings_store.get_configured_channel(guild_id)
        guild_defaults = self.settings_store.get_guild_settings(guild_id)
        user_prefs = self.settings_store.get_user_preferences(guild_id, user_id)
        effective = self.settings_store.resolve_preferences(guild_id, user_id)

        embed = discord.Embed(
            title="🎙️ VoiceBridge TTS Status",
            color=0x57F287 if session else 0xED4245,
        )

        embed.add_field(
            name="Voice Connection",
            value=f"🟢 Connected in <#{session.channel_id}>" if session else "🔴 Disconnected (`/tts join`)",
            inline=True,
        )

        embed.add_field(
            name="Configured Text Channel",
            value=f"<#{configured_channel_id}>" if configured_channel_id else "⚠️ *None configured (`/tts setup`)*",
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


def setup_tts_commands(
    tree: app_commands.CommandTree,
    session_manager: VoiceSessionManager,
    settings_store: SettingsStore,
) -> TtsCommandGroup:
    group = TtsCommandGroup(session_manager=session_manager, settings_store=settings_store)
    tree.add_command(group)
    return group


__all__ = ["setup_tts_commands", "TtsCommandGroup"]


"""Discord slash commands for Hinglish mode (English text + synced Hindi audio)."""

import logging
from typing import List, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from ..constants import COMMAND_HINGLISH, HINGLISH_DELIVERY_CHOICES
from ..hinglish.service import DELIVERY_AUTO, HinglishService
from ..storage.settings_store import SettingsStore
from ..voice.session_manager import VoiceSessionManager

logger = logging.getLogger(__name__)

DELIVERY_APP_CHOICES = [
    app_commands.Choice(name=c["name"], value=c["value"])
    for c in HINGLISH_DELIVERY_CHOICES
]


def _resolve_author_name(interaction: discord.Interaction) -> str:
    member = interaction.user
    if not isinstance(member, discord.Member) and interaction.guild:
        resolved = interaction.guild.get_member(interaction.user.id)
        if resolved:
            member = resolved
    return (
        getattr(member, "display_name", None)
        or getattr(member, "name", None)
        or "User"
    )


def _resolve_member(interaction: discord.Interaction):
    user = interaction.user
    if isinstance(user, discord.Member):
        return user
    if interaction.guild and hasattr(interaction.guild, "get_member"):
        resolved = interaction.guild.get_member(getattr(user, "id", 0))
        if resolved is not None:
            return resolved
    return user


def setup_hinglish_commands(
    tree: app_commands.CommandTree,
    hinglish_service: HinglishService,
    settings_store: SettingsStore,
    session_manager: Optional[VoiceSessionManager] = None,
) -> List[app_commands.Group]:
    """Registers the `/hinglish` command group on the application command tree."""

    # Remove existing group if re-registering (useful in tests)
    existing = tree.get_command(COMMAND_HINGLISH)
    if existing is not None:
        tree.remove_command(COMMAND_HINGLISH)

    hinglish_group = app_commands.Group(
        name=COMMAND_HINGLISH,
        description="Hinglish mode: show English text in chat and deliver Hindi audio",
    )

    @hinglish_group.command(
        name="say",
        description="Show English text in this channel and speak/attach corresponding Hindi audio",
    )
    @app_commands.describe(
        text="Message content (English, Hindi, or Hinglish) to output as English text + Hindi audio",
        delivery="Where to deliver Hindi audio (auto joins voice if active, else attaches file)",
    )
    @app_commands.choices(delivery=DELIVERY_APP_CHOICES)
    async def say(
        interaction: discord.Interaction,
        text: str,
        delivery: str = DELIVERY_AUTO,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        member = _resolve_member(interaction)
        author_name = _resolve_author_name(interaction)
        author_id = str(getattr(interaction.user, "id", ""))

        result = await hinglish_service.process_message(
            text,
            guild=interaction.guild,
            member=member,
            text_channel=interaction.channel,
            author_name=author_name,
            author_id=author_id,
            delivery_mode=delivery,
        )

        await hinglish_service.send_result(
            interaction.followup.send,
            result,
            author_name=author_name,
        )

    @hinglish_group.command(
        name="mode",
        description="Enable or disable automatic Hinglish mode for channel messages (Manage Server required)",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        enabled="Turn automatic Hinglish mode ON or OFF for channel messages",
        channel="Target channel for Hinglish mode (defaults to current channel)",
        delivery="Audio delivery strategy for automatic Hinglish messages",
    )
    @app_commands.choices(delivery=DELIVERY_APP_CHOICES)
    async def mode(
        interaction: discord.Interaction,
        enabled: bool,
        channel: Optional[Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel]] = None,
        delivery: str = DELIVERY_AUTO,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        user_perms = getattr(interaction.user, "guild_permissions", None) or getattr(
            interaction, "permissions", None
        )
        if not (user_perms and user_perms.manage_guild):
            await interaction.response.send_message(
                "❌ You need the **Manage Server** permission to configure Hinglish mode.",
                ephemeral=True,
            )
            return

        target_channel = channel or interaction.channel
        channel_id = str(target_channel.id) if target_channel else None

        settings_store.set_hinglish_mode(
            str(interaction.guild.id),
            enabled,
            channel_id=channel_id,
            delivery_mode=delivery,
        )

        if enabled:
            ch_text = f" in <#{channel_id}>" if channel_id else ""
            await interaction.response.send_message(
                f"✅ **Hinglish mode enabled**{ch_text} (delivery: `{delivery}`).\n"
                "Incoming messages will be posted in **English** and spoken/attached in **Hindi**."
            )
        else:
            await interaction.response.send_message(
                "⏹️ **Hinglish mode disabled** for automatic channel messages. "
                "You can still use `/hinglish say` on demand."
            )

    @hinglish_group.command(
        name="status",
        description="Show Hinglish mode configuration, translator backend, and voice state",
    )
    async def status(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild.id)
        cfg = settings_store.get_hinglish_mode(guild_id)
        session = session_manager.get_session(guild_id) if session_manager else None

        embed = discord.Embed(
            title="🌐 Hinglish Mode Status",
            color=0x57F287 if cfg["enabled"] else 0x5865F2,
        )
        embed.add_field(
            name="Auto Mode",
            value="🟢 Enabled" if cfg["enabled"] else "🔴 Disabled (`/hinglish mode`)",
            inline=True,
        )
        embed.add_field(
            name="Channel",
            value=f"<#{cfg['channel_id']}>" if cfg["channel_id"] else "Not set",
            inline=True,
        )
        embed.add_field(
            name="Delivery Mode",
            value=f"`{cfg['delivery_mode']}`",
            inline=True,
        )
        translator_name = getattr(getattr(hinglish_service, "translator", None), "provider_name", "unknown")
        embed.add_field(
            name="Translator Layer",
            value=f"`{translator_name}`",
            inline=True,
        )
        embed.add_field(
            name="Voice Connection",
            value=f"🟢 Active in <#{session.channel_id}>" if session else "🔇 Inactive (file fallback)",
            inline=True,
        )

        await interaction.response.send_message(embed=embed)

    tree.add_command(hinglish_group)
    return [hinglish_group]


class HinglishCommandsCog(commands.Cog, name="Hinglish"):
    """Cog wrapper for Discord Hinglish mode slash commands."""

    def __init__(
        self,
        bot: commands.Bot,
        hinglish_service: HinglishService,
        settings_store: SettingsStore,
        session_manager: Optional[VoiceSessionManager] = None,
    ):
        self.bot = bot
        self.hinglish_service = hinglish_service
        self.settings_store = settings_store
        self.session_manager = session_manager
        self.commands = setup_hinglish_commands(
            self.bot.tree,
            hinglish_service=hinglish_service,
            settings_store=settings_store,
            session_manager=session_manager,
        )


__all__ = ["setup_hinglish_commands", "HinglishCommandsCog"]


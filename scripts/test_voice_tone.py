"""Discord Voice Playback & UDP Transmission Diagnostic Tool.

Tests Discord voice playback independently of TTS generation using a pure 440 Hz sine tone.
Diagnoses Voice Gateway handshake, UDP endpoint discovery, PyNaCl encryption, and audio transmission.
"""

import argparse
import asyncio
import os
import shutil
import sys
import time
from typing import Optional

from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

# Add project root to sys.path so src modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.voice.dave_compat import (
    check_dependency_warnings,
    ensure_dave_compat,
    get_voice_dependency_versions,
    inspect_voice_connection_state,
)
from src.voice.tone_generator import ToneAudioSource, generate_sine_wav
from src.voice.session_manager import resolve_ffmpeg_path


def check_prerequisites():
    """Validates voice dependencies and audio decoders."""
    print("=" * 65)
    print(" 🛠️  DISCORD VOICE & AUDIO PRE-FLIGHT CHECK")
    print("=" * 65)

    import discord
    import discord.voice_client

    compat = ensure_dave_compat()
    versions = get_voice_dependency_versions()

    print(f"• Python version       : {sys.version.split()[0]}")
    print(f"• discord.py version   : {discord.__version__}")

    # Check PyNaCl
    has_nacl = getattr(discord.voice_client, "has_nacl", False)
    if has_nacl:
        print(f"• PyNaCl (libsodium)   : ✅ Installed v{versions.get('pynacl') or 'unknown'} (UDP voice encryption ready)")
    else:
        print("• PyNaCl (libsodium)   : ❌ MISSING! (Voice requires PyNaCl: run 'pip install \"PyNaCl>=1.5.0,<1.6\"')")

    # Check davey (DAVE protocol)
    has_dave = getattr(discord.voice_client, "has_dave", False)
    if has_dave:
        compat_note = " (compat bridge active)" if compat.get("applied") or compat.get("already_patched") else ""
        print(f"• davey (DAVE protocol): ✅ Installed v{versions.get('davey') or 'unknown'}{compat_note}")
    else:
        print("• davey (DAVE protocol): ⚠️ Not installed (discord.py requires davey<0.2.0)")

    for warn_msg in check_dependency_warnings(versions):
        print(f"• Dependency warning   : ⚠️ {warn_msg}")

    # Check FFmpeg
    sys_ffmpeg = shutil.which("ffmpeg")
    bundled_ffmpeg = None
    try:
        import imageio_ffmpeg
        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    resolved = resolve_ffmpeg_path("ffmpeg")

    print(f"• System FFmpeg        : {'✅ Found at ' + sys_ffmpeg if sys_ffmpeg else '❌ Not found on PATH'}")
    print(f"• Bundled FFmpeg       : {'✅ Found at ' + bundled_ffmpeg if bundled_ffmpeg else '❌ Not found (imageio-ffmpeg)'}")
    print(f"• Active Resolved Path : ➡️  {resolved}")
    print("=" * 65)

    if not has_nacl:
        print("\n❌ CRITICAL: Voice cannot operate without PyNaCl. Please run:")
        print("   pip install PyNaCl")
        sys.exit(1)


async def run_diagnostic(
    token: str,
    target_guild_id: Optional[str] = None,
    target_channel_id: Optional[str] = None,
    duration: float = 3.0,
    frequency: float = 440.0,
    use_ffmpeg: bool = False,
):
    import discord

    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True

    client = discord.Client(intents=intents)

    print(f"\n[1/4] Connecting to Discord Gateway...")

    try:
        loop = asyncio.get_running_loop()
        ready_event = asyncio.Event()

        @client.event
        async def on_ready():
            print(f"✅ Gateway Connected! Logged in as: {client.user} (ID: {client.user.id})")
            ready_event.set()

        # Start bot in background task
        bot_task = loop.create_task(client.start(token))

        try:
            await asyncio.wait_for(ready_event.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            print("❌ Gateway connection timed out after 15 seconds.")
            await client.close()
            return

        # [2/4] Locate target Guild and Voice Channel
        print(f"\n[2/4] Locating target Voice Channel...")
        target_channel = None

        if target_channel_id:
            target_channel = client.get_channel(int(target_channel_id))
            if not target_channel or not isinstance(target_channel, (discord.VoiceChannel, discord.StageChannel)):
                print(f"❌ Could not find voice channel with ID: {target_channel_id}")
                await client.close()
                return

        if not target_channel:
            # Search available guilds
            guilds = client.guilds
            if not guilds:
                print("❌ The bot is not currently in any Discord servers (guilds).")
                print("   Invite the bot to a server first using an OAuth2 invite link.")
                await client.close()
                return

            selected_guild = None
            if target_guild_id:
                selected_guild = client.get_guild(int(target_guild_id))
                if not selected_guild:
                    print(f"❌ Bot is not in guild ID: {target_guild_id}")
                    await client.close()
                    return
            else:
                selected_guild = guilds[0]

            print(f"• Active Server: {selected_guild.name} (ID: {selected_guild.id})")

            # Look for a voice channel where a human user is currently active
            for ch in selected_guild.voice_channels:
                human_members = [m for m in ch.members if not m.bot]
                if human_members:
                    target_channel = ch
                    print(f"• Auto-detected voice channel with active members: '{ch.name}' ({len(human_members)} user(s))")
                    break

            # Fallback to the first voice channel in the guild
            if not target_channel and selected_guild.voice_channels:
                target_channel = selected_guild.voice_channels[0]
                print(f"• No active users found; using first channel: '{target_channel.name}' (ID: {target_channel.id})")

        if not target_channel:
            print("❌ No voice channel found in the server.")
            await client.close()
            return

        # Check bot permissions
        me = target_channel.guild.me
        perms = target_channel.permissions_for(me)
        if not perms.connect:
            print(f"❌ Bot lacks 'Connect' permission in voice channel #{target_channel.name}")
            await client.close()
            return
        if not perms.speak:
            print(f"❌ Bot lacks 'Speak' permission in voice channel #{target_channel.name}")
            await client.close()
            return

        # [3/4] Connect to Voice Channel and Establish UDP Transmission
        print(f"\n[3/4] Connecting to voice channel '#{target_channel.name}'...")
        print("   Establishing Voice WebSocket and negotiating UDP voice server endpoint...")

        start_time = time.time()
        try:
            voice_client: discord.VoiceClient = await target_channel.connect(timeout=15.0, reconnect=False)
        except Exception as conn_err:
            print(f"❌ Voice connection failed: {conn_err}")
            await client.close()
            return

        conn_latency = (time.time() - start_time) * 1000
        print(f"✅ Voice Connected in {conn_latency:.0f}ms!")

        # Inspect VoiceClient internal UDP and encryption parameters
        endpoint = getattr(voice_client, "endpoint", "Unknown")
        selected_mode = getattr(voice_client, "mode", "Unknown")
        diag = inspect_voice_connection_state(voice_client)
        print(f"• Voice Server Endpoint : {endpoint}")
        print(f"• Voice Encryption Mode : {selected_mode}")
        if diag:
            print(
                f"• DAVE E2EE State       : v{diag.get('dave_protocol_version')} "
                f"(can_encrypt={diag.get('can_encrypt')}, ready={diag.get('dave_ready')}, "
                f"status={diag.get('dave_status')})"
            )

        # [4/4] Transmit 440 Hz Test Tone
        mode_label = "FFmpeg PCMAudio" if use_ffmpeg else "Direct PCM ToneAudioSource (No FFmpeg/No TTS)"
        print(f"\n[4/4] Transmitting {duration}s @ {frequency}Hz test tone via {mode_label}...")

        play_finished = asyncio.Event()
        play_error: Optional[Exception] = None

        def after_play(err):
            nonlocal play_error
            if err:
                play_error = err
            loop.call_soon_threadsafe(play_finished.set)

        if use_ffmpeg:
            import io
            wav_data = generate_sine_wav(frequency=frequency, duration=duration)
            resolved_ffmpeg = resolve_ffmpeg_path("ffmpeg")
            raw_source = discord.FFmpegPCMAudio(
                io.BytesIO(wav_data),
                pipe=True,
                executable=resolved_ffmpeg,
            )
            audio_source = discord.PCMVolumeTransformer(raw_source, volume=1.0)
        else:
            audio_source = ToneAudioSource(frequency=frequency, duration=duration, volume=0.5)

        voice_client.play(audio_source, after=after_play)

        print("🔊 Playing tone now! Listen in your Discord voice channel...")

        try:
            # Wait for tone playback to finish or timeout
            await asyncio.wait_for(play_finished.wait(), timeout=duration + 10.0)
            if play_error:
                print(f"❌ Playback encountered error: {play_error}")
            else:
                print("✅ Playback finished successfully!")
        except asyncio.TimeoutError:
            print("⚠️ Playback timed out waiting for audio finish event.")

        # Disconnect cleanly
        print("\nDisconnecting voice client...")
        try:
            if voice_client.is_connected():
                await voice_client.disconnect(force=True)
        except Exception:
            pass

        await client.close()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass

        # Diagnostic Conclusion
        print("\n" + "=" * 65)
        print(" 🎯 DIAGNOSTIC RESULT & INTERPRETATION")
        print("=" * 65)
        print("Did you hear the 440 Hz test tone in Discord?")
        print("")
        print("🟢 YES, I HEARD IT:")
        print("   • Discord UDP Voice Transmission is 100% HEALTHY.")
        print("   • Your UDP ports, router, firewall, PyNaCl, and Voice Gateway are working.")
        print("   • The issue is strictly in AUDIO SYNTHESIS:")
        print("     - If using Google TTS (gtts): Google API rate-limit, network block, or empty text.")
        print("     - If using eSpeak: missing eSpeak NG binary on host system.")
        print("     - If using FFmpeg: check FFmpeg binary path resolution.")
        print("")
        print("🔴 NO, I DID NOT HEAR IT (or connection failed/dropped):")
        print("   • The issue is in DISCORD VOICE UDP TRANSMISSION.")
        print("   • Discord uses UDP ports (usually 50000-65535 or dynamic UDP) for audio.")
        print("   • Causes:")
        print("     - UDP traffic blocked by host firewall / cloud security group / ISP / VPN.")
        print("     - PyNaCl / libsodium encryption mismatch or DAVE protocol issue.")
        print("=" * 65)

    except Exception as exc:
        print(f"\n❌ Unexpected error during diagnostic: {exc}")
        import traceback
        traceback.print_exc()
        try:
            if not client.is_closed():
                await client.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Test Discord voice playback independently of TTS using a 440 Hz test tone."
    )
    parser.add_argument(
        "--token",
        type=str,
        default=os.getenv("DISCORD_TOKEN"),
        help="Discord Bot Token (defaults to DISCORD_TOKEN env var)",
    )
    parser.add_argument(
        "--guild",
        type=str,
        default=os.getenv("DISCORD_GUILD_ID"),
        help="Discord Guild/Server ID (defaults to DISCORD_GUILD_ID env var or first guild)",
    )
    parser.add_argument(
        "--channel",
        type=str,
        default=None,
        help="Voice Channel ID (defaults to active user voice channel)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3.0,
        help="Tone duration in seconds (default: 3.0)",
    )
    parser.add_argument(
        "--frequency",
        type=float,
        default=440.0,
        help="Tone pitch in Hertz (default: 440.0 Hz)",
    )
    parser.add_argument(
        "--use-ffmpeg",
        action="store_true",
        default=False,
        help="Route tone through FFmpeg instead of direct PCM",
    )

    args = parser.parse_args()

    check_prerequisites()

    if not args.token:
        print("\n❌ Error: No Discord bot token provided.")
        print("   Set DISCORD_TOKEN in .env or pass --token <your_token>.")
        sys.exit(1)

    try:
        asyncio.run(
            run_diagnostic(
                token=args.token,
                target_guild_id=args.guild,
                target_channel_id=args.channel,
                duration=args.duration,
                frequency=args.frequency,
                use_ffmpeg=args.use_ffmpeg,
            )
        )
    except KeyboardInterrupt:
        print("\n[Aborted] Diagnostic interrupted by user.")


if __name__ == "__main__":
    main()


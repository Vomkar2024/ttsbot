"""Diagnostic tool to verify Discord Bot Token and Gateway permissions."""

import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()


def decode_token_client_id(token: str) -> str:
    """Extract client/user ID from the base64-encoded first part of a Discord token."""
    parts = token.split(".")
    if not parts or not parts[0]:
        return ""
    encoded_id = parts[0]
    rem = len(encoded_id) % 4
    if rem:
        encoded_id += "=" * (4 - rem)
    try:
        decoded = base64.b64decode(encoded_id).decode("utf-8")
        return decoded
    except Exception:
        return ""


async def test_gateway_intents(raw_token: str, app_id: str) -> bool:
    """Test connecting to Discord Gateway with the required Message Content intent."""
    import discord

    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True
    intents.guild_messages = True
    intents.message_content = True

    client = discord.Client(intents=intents)
    print("\nTesting Gateway connection with Privileged Intents (Message Content)...")

    try:
        ready_future = asyncio.get_event_loop().create_future()

        @client.event
        async def on_ready():
            if not ready_future.done():
                ready_future.set_result(True)
            await client.close()

        # Run client.start in a task and wait for either ready or failure
        start_task = asyncio.create_task(client.start(raw_token))

        try:
            await asyncio.wait_for(ready_future, timeout=12.0)
            print("🎉 [SUCCESS] Discord Gateway accepted the connection and Message Content Intent!")
            return True
        except asyncio.TimeoutError:
            print("⚠️ Gateway connection timed out (server took too long to answer).")
            return False
        finally:
            if not client.is_closed():
                await client.close()
            try:
                await start_task
            except (asyncio.CancelledError, discord.errors.LoginFailure, discord.errors.PrivilegedIntentsRequired):
                pass

    except discord.errors.PrivilegedIntentsRequired:
        print("\n❌ [GATEWAY REFUSED] PrivilegedIntentsRequired:")
        print("   Discord refused connection because 'MESSAGE CONTENT INTENT' is not enabled.")
        print("\nHOW TO FIX:")
        print(f"1. Open your browser: https://discord.com/developers/applications/{app_id}/bot")
        print("2. Scroll down to 'Privileged Gateway Intents'.")
        print("3. Toggle 'MESSAGE CONTENT INTENT' to ON (green check).")
        print("4. Click the 'Save Changes' bar at the bottom.")
        print("5. Re-run this check: python scripts/verify_token.py")
        return False
    except discord.errors.LoginFailure:
        print("\n❌ [GATEWAY REFUSED] Discord refused the token during login.")
        return False
    except Exception as exc:
        print(f"⚠️ Gateway check encountered: {exc}")
        return True


def check_token() -> int:
    raw_token = os.getenv("DISCORD_TOKEN", "").strip()
    if (raw_token.startswith('"') and raw_token.endswith('"')) or (raw_token.startswith("'") and raw_token.endswith("'")):
        raw_token = raw_token[1:-1].strip()

    client_id = (os.getenv("CLIENT_ID", "") or os.getenv("DISCORD_CLIENT_ID", "")).strip()
    if (client_id.startswith('"') and client_id.endswith('"')) or (client_id.startswith("'") and client_id.endswith("'")):
        client_id = client_id[1:-1].strip()

    print("=" * 60)
    print(" VoiceBridge Discord Bot - Token Diagnostics")
    print("=" * 60)

    if not raw_token:
        print("❌ [Error] DISCORD_TOKEN is missing or empty in .env!")
        print("   Please open .env and set DISCORD_TOKEN=<your_token>.")
        return 1

    if raw_token == "your_discord_bot_token_here":
        print("❌ [Error] DISCORD_TOKEN is still set to placeholder 'your_discord_bot_token_here'.")
        print("   Please replace it with your actual Discord Bot Token in .env.")
        return 1

    parts = raw_token.split(".")
    if len(parts) != 3:
        print(f"⚠️ [Warning] Token does not match expected Discord 3-part format (found {len(parts)} parts).")
    else:
        print("✓ Token structure check passed (3 dot-separated parts).")

    token_client_id = decode_token_client_id(raw_token)
    if token_client_id:
        print(f"✓ Embedded Bot User ID in token: {token_client_id}")
        if client_id and client_id != token_client_id:
            print(f"⚠️ [Mismatch] CLIENT_ID in .env is '{client_id}', but token belongs to ID '{token_client_id}'.")
            print("   Consider updating CLIENT_ID in .env to match the token ID.")
        elif client_id:
            print(f"✓ Token matches CLIENT_ID ({client_id}).")
    else:
        print("⚠️ Could not decode Bot User ID from token prefix.")

    app_id = token_client_id or client_id or "YOUR_APP_ID"
    bot_portal_url = f"https://discord.com/developers/applications/{app_id}/bot"

    print("\n1. Testing REST API authentication (https://discord.com/api/v10/users/@me)...")
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={
            "Authorization": f"Bot {raw_token}",
            "User-Agent": "VoiceBridgeTTSBot (https://github.com/Vomkar2024/ttsbot, 1.0)",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            bot_username = data.get("username", "Unknown")
            discriminator = data.get("discriminator", "0")
            full_tag = f"{bot_username}#{discriminator}" if discriminator != "0" else f"@{bot_username}"
            user_id = data.get("id", "Unknown")
            is_bot = data.get("bot", False)

            print("✓ REST Authentication PASSED!")
            print(f"  Bot User: {full_tag} (ID: {user_id})")
            print(f"  Account Type: {'Bot' if is_bot else 'User'}")

    except urllib.error.HTTPError as err:
        print(f"\n❌ [Authentication Failed] HTTP {err.code}: {err.reason}")
        if err.code == 401:
            print("\n➡️ Discord refused the token: The token is invalid, expired, or was reset.")
            print("\nHOW TO FIX:")
            print(f"1. Open your browser: {bot_portal_url}")
            print("2. Click 'Reset Token' and confirm.")
            print("3. Copy the newly generated token.")
            print("4. Update DISCORD_TOKEN in .env with the new token.")
            print("5. Re-run: python scripts/verify_token.py")
        elif err.code == 429:
            print("\n➡️ You are being rate-limited by Discord. Please wait a few minutes before trying again.")
        return 1

    except urllib.error.URLError as err:
        print(f"\n⚠️ [Network Error] Could not reach Discord API: {err.reason}")
        print("   Please check your internet connection or firewall/proxy settings.")
        return 1

    # 2. Test Gateway Intents
    gateway_ok = asyncio.run(test_gateway_intents(raw_token, app_id))
    if not gateway_ok:
        return 1

    print("\n" + "=" * 60)
    print(" ✅ All Checks Passed! Your bot token & intents are 100% ready.")
    print(" You can now run the bot with: python main.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(check_token())

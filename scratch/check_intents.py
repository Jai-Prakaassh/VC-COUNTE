import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discord
from services.config import get_settings

async def test_intents():
    settings = get_settings()
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    try:
        await client.login(settings.discord_token)
        print("LOGIN_OK: Message content & members intent allowed on Discord Portal!")
        await client.close()
    except discord.errors.PrivilegedIntentsRequired as e:
        print(f"PRIVILEGED_ERROR: {e}")
    except Exception as e:
        print(f"OTHER_ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_intents())

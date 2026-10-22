"""
Main Entry Point Wrapper for Discord VC Counter Bot.
Compatible with Pterodactyl panel / bot-hosting.net and direct python execution.
Delegates execution to bot.py.
"""

import sys
import asyncio
from bot import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

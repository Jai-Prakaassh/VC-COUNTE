"""
Discord VC Counter Bot + Real-Time Web Server Main Entrypoint.
Combines discord.py 2.x hybrid commands, real-time voice-state tracking,
automated counter channels, and FastAPI / Uvicorn in a unified asyncio event loop.
"""

from __future__ import annotations
import os
import sys
import time
import signal
import asyncio
import logging
from typing import Optional
import discord
from discord.ext import commands
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

# Force UTF-8 encoding on Windows to prevent UnicodeEncodeError with special Discord nicknames/emojis
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

from services.config import get_settings
from services.database import db
from services.security import (
    SecurityHeadersMiddleware,
    setup_secure_logging,
)
from services.statistics import (
    build_stats_embed,
    build_user_stats_embed,
    compute_guild_statistics,
    determine_voice_state,
    format_dashboard_url,
)
from services.counters import CounterChannelManager
from api.routes import router as api_router, set_bot_instance
from api.websocket import ws_manager

# Set up credentials-redacted logging
logger = setup_secure_logging()
settings = get_settings()

# ------------------------------------------------------------------------------
# Discord Bot Setup & Intents
# ------------------------------------------------------------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = settings.enable_privileged_intents
intents.message_content = settings.enable_privileged_intents
intents.presences = settings.enable_privileged_intents


def get_configured_activity(
    status_text: Optional[str] = None,
    activity_type: Optional[str] = None,
) -> Optional[discord.Activity | discord.CustomActivity | discord.Game | discord.Streaming]:
    """Builds configured Discord activity/presence object."""
    if status_text:
        text = status_text.strip()
        act_type = (activity_type or "watching").lower().strip()
    else:
        # Check custom status first, then banner
        if settings.bot_custom_status and settings.bot_custom_status.strip().lower() not in ("none", "clear", "off"):
            return discord.CustomActivity(name=settings.bot_custom_status.strip())
        elif settings.bot_banner_status and settings.bot_banner_status.strip().lower() not in ("none", "clear", "off"):
            text = settings.bot_banner_status.strip()
            act_type = (settings.bot_banner_type or "watching").lower().strip()
        else:
            return None

    if not text or text.lower() in ("none", "clear", "off"):
        return None

    if act_type in ("custom", "bubble", "speechbubble"):
        return discord.CustomActivity(name=text)
    elif act_type == "watching":
        return discord.Activity(type=discord.ActivityType.watching, name=text)
    elif act_type == "listening":
        return discord.Activity(type=discord.ActivityType.listening, name=text)
    elif act_type == "competing":
        return discord.Activity(type=discord.ActivityType.competing, name=text)
    elif act_type == "streaming":
        return discord.Streaming(name=text, url="https://twitch.tv/discord")
    else:
        return discord.Game(name=text)


async def apply_bot_presence(
    custom_status: Optional[str] = None,
    banner_status: Optional[str] = None,
    banner_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Applies Discord presence supporting:
    1. Speech Bubble Custom Status ("THIS ONE" in user popout / Zynrax style)
    2. Profile Banner Activity ("THE BANNER ONE")
    Supports both simultaneously via Gateway presence payload or individually.
    """
    if custom_status is not None:
        if custom_status.strip().lower() in ("none", "clear", "off", "remove", "reset", ""):
            settings.bot_custom_status = ""
        else:
            settings.bot_custom_status = custom_status.strip()

    if banner_status is not None:
        if banner_status.strip().lower() in ("none", "clear", "off", "remove", "reset", ""):
            settings.bot_banner_status = ""
        else:
            settings.bot_banner_status = banner_status.strip()

    if banner_type is not None:
        settings.bot_banner_type = banner_type.lower().strip()

    c_text = settings.bot_custom_status.strip()
    b_text = settings.bot_banner_status.strip()
    b_type = (settings.bot_banner_type or "watching").lower().strip()

    activities = []

    # 1. Speech Bubble Custom Status (Type 4)
    if c_text and c_text.lower() not in ("none", "clear", "off"):
        activities.append({
            "name": "Custom Status",
            "type": 4,
            "state": c_text,
            "id": "custom",
        })

    # 2. Profile Banner Activity (Type 3 Watching, Type 0 Playing, Type 1 Streaming, etc.)
    if b_text and b_text.lower() not in ("none", "clear", "off"):
        if b_type == "watching":
            activities.append({"name": b_text, "type": 3})
        elif b_type == "listening":
            activities.append({"name": b_text, "type": 2})
        elif b_type == "competing":
            activities.append({"name": b_text, "type": 5})
        elif b_type == "streaming":
            activities.append({"name": b_text, "type": 1, "url": "https://twitch.tv/discord"})
        else:
            activities.append({"name": b_text, "type": 0})

    # Dispatch to Discord Gateway
    try:
        if len(activities) == 1 and activities[0]["type"] == 4:
            act = discord.CustomActivity(name=c_text)
            await bot.change_presence(status=discord.Status.online, activity=act)
        elif len(activities) == 1:
            act = get_configured_activity(status_text=b_text, activity_type=b_type)
            await bot.change_presence(status=discord.Status.online, activity=act)
        elif not activities:
            await bot.change_presence(status=discord.Status.online, activity=None)
        else:
            # Multiple activities (both Bubble and Banner active simultaneously)
            if bot.ws and hasattr(bot.ws, "send"):
                payload = {
                    "op": 3,
                    "d": {
                        "activities": activities,
                        "afk": False,
                        "since": 0,
                        "status": "online",
                    },
                }
                await bot.ws.send(discord.utils._to_json(payload))
    except Exception as e:
        logger.warning("Error applying presence payload: %s", e)

    return {
        "custom_status": c_text if c_text and c_text.lower() not in ("none", "clear", "off") else None,
        "banner_status": b_text if b_text and b_text.lower() not in ("none", "clear", "off") else None,
        "banner_type": b_type,
    }


# ------------------------------------------------------------------------------
# Mobile Gateway Identification Patch (Enables mobile presence & speech bubble)
# ------------------------------------------------------------------------------
orig_identify = discord.gateway.DiscordWebSocket.identify

async def mobile_identify(self) -> None:
    orig_send = self.send_as_json
    async def wrapped_send(payload):
        if payload.get("op") == self.IDENTIFY:
            payload["d"]["properties"]["os"] = "Android"
            payload["d"]["properties"]["browser"] = "Discord Android"
            payload["d"]["properties"]["device"] = "Discord Android"
        await orig_send(payload)
    self.send_as_json = wrapped_send
    await orig_identify(self)

discord.gateway.DiscordWebSocket.identify = mobile_identify


def get_bot_prefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    """
    Computes command prefixes for a message.
    Supports standard prefix (','), bot mentions, and no-prefix ('')
    for authorized users, administrators, or when server-wide no-prefix is enabled.
    """
    p = settings.bot_prefix
    prefixes = [p, f"{p} "]

    if not message.guild or not message.author or getattr(message.author, "bot", False):
        return commands.when_mentioned_or(*prefixes)(bot, message)

    author = message.author
    guild_id = str(message.guild.id)
    user_id = str(author.id)

    # 1. Guild owner or Administrator permission
    is_admin = False
    if hasattr(author, "guild_permissions") and author.guild_permissions.administrator:
        is_admin = True
    elif getattr(message.guild, "owner_id", None) == author.id:
        is_admin = True

    # 2. Server-wide no-prefix enabled
    is_server_noprefix = guild_id in getattr(bot, "noprefix_guilds", set())

    # 3. User specifically whitelisted
    is_user_noprefix = (guild_id, user_id) in getattr(bot, "noprefix_users", set()) or user_id in getattr(bot, "noprefix_global_users", set())

    if is_admin or is_server_noprefix or is_user_noprefix:
        return commands.when_mentioned_or(*prefixes, "")(bot, message)

    return commands.when_mentioned_or(*prefixes)(bot, message)


class VCCounterBot(commands.Bot):
    """Custom Discord Bot class managing commands, counters, and web server integration."""

    def __init__(self):
        super().__init__(
            command_prefix=get_bot_prefix,
            intents=intents,
            activity=get_configured_activity(),
            status=discord.Status.online,
            help_command=None,  # We implement a custom /help command below
            case_insensitive=True,
        )
        self.noprefix_users: set[tuple[str, str]] = set()
        self.noprefix_guilds: set[str] = set()
        self.noprefix_global_users: set[str] = set()
        self.counter_manager = CounterChannelManager(self)
        self.start_timestamp = time.time()
        self._lb_worker_task: Optional[asyncio.Task] = None

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        """Prevent unhandled event errors from crashing the bot."""
        logger.error("Unhandled exception in Discord event '%s'", event_method, exc_info=True)

    def start_leaderboard_worker(self) -> None:
        """Starts real-time leaderboard broadcast worker for subscribed WebSockets."""
        if self._lb_worker_task is None or self._lb_worker_task.done():
            self._lb_worker_task = asyncio.create_task(
                self._leaderboard_sync_loop(), name="leaderboard_sync_worker"
            )
            logger.info("Real-time leaderboard synchronization worker started.")

    async def _leaderboard_sync_loop(self) -> None:
        """Periodically broadcasts updated leaderboard to dashboard clients with live session data."""
        while not self.is_closed():
            try:
                await asyncio.sleep(3)
                for guild in self.guilds:
                    gid_str = str(guild.id)
                    # Broadcast if clients are subscribed to this guild
                    has_subscribers = bool(ws_manager.guild_subscriptions.get(gid_str))
                    if has_subscribers:
                        lb = await db.get_leaderboard(gid_str, limit=None, guild=guild)
                        await ws_manager.broadcast_to_guild(
                            gid_str,
                            {
                                "type": "leaderboard_update",
                                "guild_id": gid_str,
                                "leaderboard": lb,
                                "timestamp": time.time(),
                            },
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Leaderboard background broadcast notice: %s", e)

    async def setup_hook(self) -> None:
        """Called automatically before the bot connects to Discord."""
        # Initialize persistent SQLite tables
        await db.init_db()

        # Load persistent no-prefix whitelists and server-wide flags
        try:
            self.noprefix_users, self.noprefix_guilds = await db.get_all_noprefix_data()
            logger.info(
                "Loaded %d no-prefix users and %d no-prefix servers.",
                len(self.noprefix_users),
                len(self.noprefix_guilds),
            )
        except Exception as e:
            logger.warning("Could not load no-prefix settings: %s", e)

        # Inject bot client into API router
        set_bot_instance(self)

        # Start counter channel background worker
        self.counter_manager.start_worker()

        # Start live real-time leaderboard sync worker
        self.start_leaderboard_worker()

        # Synchronize application slash commands globally
        logger.info("Synchronizing application slash commands globally...")
        try:
            synced = await self.tree.sync()
            logger.info("Successfully synced %d application slash commands.", len(synced))
        except Exception as e:
            logger.error("Failed to sync application slash commands: %s", e)

    async def on_ready(self) -> None:
        """Fired when the bot successfully authenticates and loads guild cache."""
        logger.info(
            "Bot connected as: %s (ID: %s)", self.user.name, self.user.id
        )
        logger.info(
            "Serving %d guilds across Discord.", len(self.guilds)
        )

        # Apply presence: Speech Bubble status and/or Banner Activity
        try:
            res = await apply_bot_presence()
            logger.info("Bot presence applied: Bubble='%s', Banner='%s'", res.get("custom_status"), res.get("banner_status"))
        except Exception as e:
            logger.warning("Could not set bot presence in on_ready: %s", e)
        for guild in self.guilds:
            logger.info(" - Connected Guild: '%s' (ID: %s)", guild.name, guild.id)
            # Instantly sync slash commands to this specific guild for immediate availability
            try:
                self.tree.copy_global_to(guild=guild)
                synced_guild = await self.tree.sync(guild=guild)
                logger.info("Instantly synced %d slash commands directly to guild '%s'", len(synced_guild), guild.name)
            except Exception as e:
                logger.warning("Could not sync commands directly to guild %s: %s", guild.id, e)

            # Sync initial counters if configured
            self.counter_manager.mark_guild_dirty(guild.id)
            # Reconcile active sessions against Discord ground truth to purge ghost sessions
            await db.reconcile_active_sessions(guild)
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Fired when the bot is added to a new Discord server."""
        logger.info("Joined new guild: %s (ID: %s)", guild.name, guild.id)
        await db.get_or_create_guild_settings(str(guild.id))
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except Exception:
            pass

    async def close(self) -> None:
        """Clean shutdown handler."""
        logger.info("Shutting down Discord bot and stopping counter workers...")
        self.counter_manager.stop_worker()
        if self._lb_worker_task and not self._lb_worker_task.done():
            self._lb_worker_task.cancel()
        await super().close()


bot = VCCounterBot()


@bot.event
async def on_message(message: discord.Message) -> None:
    """Processes message prefix commands and logs command invocation."""
    if message.author.bot:
        return

    # Process prefix commands (,vc, ,stats, etc. or @VC TIME vc)
    await bot.process_commands(message)


# ------------------------------------------------------------------------------
# Real-Time Discord Voice State Events
# ------------------------------------------------------------------------------
@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    """
    Triggered when any member joins, leaves, moves between voice channels,
    or toggles mute, deafen, camera, or screen share status.
    """
    try:
        guild = member.guild
        guild_id = str(guild.id)
        now = time.time()

        event_action = None
        event_channel = None

        # 1. Member Joined a Voice Channel
        if before.channel is None and after.channel is not None:
            event_action = "join"
            event_channel = after.channel.name
            logger.info(
                "[%s] %s joined voice channel '%s'",
                guild.name,
                member.display_name,
                after.channel.name,
            )

        # 2. Member Left a Voice Channel
        elif before.channel is not None and after.channel is None:
            event_action = "leave"
            event_channel = before.channel.name
            logger.info(
                "[%s] %s left voice channel '%s'",
                guild.name,
                member.display_name,
                before.channel.name,
            )

        # 3. Member Moved between Voice Channels
        elif (
            before.channel is not None
            and after.channel is not None
            and before.channel.id != after.channel.id
        ):
            event_action = "move"
            event_channel = after.channel.name
            logger.info(
                "[%s] %s switched voice channel from '%s' to '%s'",
                guild.name,
                member.display_name,
                before.channel.name,
                after.channel.name,
            )

        # 4. Member Voice Flags Changed (Mute, Deafen, Stream, Video)
        else:
            event_action = "status_change"
            event_channel = after.channel.name if after.channel else None

        # Track granular state transitions (unmuted, muted, deafened, afk, disconnected)
        new_voice_state = determine_voice_state(member, after.channel)
        avatar_url = str(member.display_avatar.url) if member.display_avatar else None

        if not member.bot:
            await db.update_member_voice_state(
                guild_id=guild_id,
                user_id=str(member.id),
                user_name=member.name,
                display_name=member.display_name,
                avatar_url=avatar_url,
                new_state=new_voice_state,
                channel_id=str(after.channel.id) if after.channel else None,
            )

        # Compute updated statistics snapshot
        stats = compute_guild_statistics(guild)

        # Broadcast event notification to subscribed WebSocket clients
        if event_action and event_action != "status_change":
            await ws_manager.broadcast_to_guild(
                guild_id,
                {
                    "type": "voice_event",
                    "guild_id": guild_id,
                    "action": event_action,
                    "user": member.name,
                    "display_name": member.display_name,
                    "avatar_url": str(member.display_avatar.url) if member.display_avatar else None,
                    "is_bot": bool(member.bot),
                    "channel": event_channel,
                    "timestamp": now,
                },
            )

        # Broadcast instant stats update to WebSockets
        await ws_manager.broadcast_to_guild(
            guild_id,
            {
                "type": "stats_update",
                "guild_id": guild_id,
                "data": stats,
                "timestamp": now,
            },
        )

        # Broadcast user breakdown update if not a bot
        if not member.bot:
            user_breakdown = await db.get_user_voice_breakdown(guild_id, str(member.id), member)
            await ws_manager.broadcast_to_guild(
                guild_id,
                {
                    "type": "user_stats_update",
                    "guild_id": guild_id,
                    "user_id": str(member.id),
                    "data": user_breakdown,
                    "timestamp": now,
                },
            )

        # Broadcast live leaderboard update
        lb = await db.get_leaderboard(guild_id, limit=20, guild=guild)
        await ws_manager.broadcast_to_guild(
            guild_id,
            {
                "type": "leaderboard_update",
                "guild_id": guild_id,
                "leaderboard": lb,
                "timestamp": now,
            },
        )

        # Flag guild for counter channels update check (rate-limit safe)
        bot.counter_manager.mark_guild_dirty(guild.id)
    except Exception as e:
        logger.error("Error in on_voice_state_update: %s", e, exc_info=True)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Fired when a user joins the server."""
    try:
        stats = compute_guild_statistics(member.guild)
        await ws_manager.broadcast_to_guild(
            str(member.guild.id),
            {"type": "stats_update", "guild_id": str(member.guild.id), "data": stats, "timestamp": time.time()},
        )
        bot.counter_manager.mark_guild_dirty(member.guild.id)
    except Exception as e:
        logger.error("Error in on_member_join: %s", e, exc_info=True)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    """Fired when a user leaves the server."""
    try:
        stats = compute_guild_statistics(member.guild)
        await ws_manager.broadcast_to_guild(
            str(member.guild.id),
            {"type": "stats_update", "guild_id": str(member.guild.id), "data": stats, "timestamp": time.time()},
        )
        bot.counter_manager.mark_guild_dirty(member.guild.id)
    except Exception as e:
        logger.error("Error in on_member_remove: %s", e, exc_info=True)


# ------------------------------------------------------------------------------
# Hybrid Discord Commands (Both Slash Commands & Prefix Commands)
# ------------------------------------------------------------------------------
@bot.hybrid_command(
    name="stats",
    aliases=["serverstats", "guildstats"],
    description="Display comprehensive real-time voice and server statistics (or member stats if tagged).",
)
async def cmd_stats(
    ctx: commands.Context,
    member: Optional[discord.Member] = None,
) -> None:
    """Displays real-time server statistics, or member stats if a member is specified."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used within a server.", ephemeral=True)
        return

    await ctx.defer()
    if member:
        stats = await db.get_user_voice_breakdown(str(ctx.guild.id), str(member.id), member)
        embed = build_user_stats_embed(ctx.guild, member, stats, settings.dashboard_url)
        await ctx.send(embed=embed)
        return

    stats = compute_guild_statistics(ctx.guild)
    embed = build_stats_embed(ctx.guild, stats, settings.dashboard_url)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="vc",
    aliases=["voicestats", "userstats", "vcstats", "vstats", "myvc", "user", "u"],
    description="Display voice stats breakdown for yourself or a tagged member (e.g. ,vc or ,vc @user).",
)
async def cmd_vc(
    ctx: commands.Context,
    member: Optional[discord.Member] = None,
) -> None:
    """Displays detailed voice state breakdown for a member or the command caller."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used within a server.", ephemeral=True)
        return

    target = member or ctx.author
    await ctx.defer()

    stats = await db.get_user_voice_breakdown(str(ctx.guild.id), str(target.id), target)
    embed = build_user_stats_embed(ctx.guild, target, stats, settings.dashboard_url)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="resetdaily",
    aliases=["dailyreset", "resetday"],
    description="Reset Daily voice stats for a member or all members (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_resetdaily(
    ctx: commands.Context,
    target: Optional[str] = None,
) -> None:
    """
    Resets Daily voice breakdown. All Time and Weekly breakdown remain intact.
    Usage:
      ,resetdaily @user   - Resets daily breakdown for tagged member
      ,resetdaily all     - Resets daily breakdown for ALL members in the server
    """
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)
    guild_id = str(ctx.guild.id)
    p = settings.bot_prefix

    # Check if target is "all"
    if target and target.strip().lower() == "all":
        count = await db.reset_guild_daily(guild_id, guild=ctx.guild)

        # Broadcast live reset to web dashboard clients
        stats = compute_guild_statistics(ctx.guild)
        await ws_manager.broadcast_to_guild(
            guild_id,
            {
                "type": "stats_update",
                "guild_id": guild_id,
                "data": stats,
                "timestamp": time.time(),
            },
        )

        embed = discord.Embed(
            title="🌅 Daily Voice Statistics Reset",
            description=(
                f"✅ Successfully reset **Daily Breakdown** to `0h 0m 0s` for **all members** in **{ctx.guild.name}**.\n\n"
                "🛡️ **Note:** All Time Breakdown and Weekly Breakdown remain 100% intact!"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Live web dashboard and breakdown cards have been updated.")
        await ctx.send(embed=embed)
        return

    # Check if a member was tagged/mentioned or provided by ID/name
    target_member = None
    if ctx.message and ctx.message.mentions:
        target_member = ctx.message.mentions[0]
    elif target:
        clean_target = target.strip("<@!> ")
        if clean_target.isdigit():
            target_member = ctx.guild.get_member(int(clean_target))
        if not target_member:
            target_member = discord.utils.find(
                lambda m: m.name.lower() == target.lower() or m.display_name.lower() == target.lower(),
                ctx.guild.members,
            )

    if not target_member:
        embed = discord.Embed(
            title="❓ How to Use Daily Reset Command",
            description=(
                "Reset only the **Daily Breakdown** for a member or everyone:\n\n"
                f"• `{p}resetdaily @user` — Reset daily stats for a single member.\n"
                f"• `{p}resetdaily all` — Reset daily stats for **all** members.\n"
                f"• Or use `/resetdaily target:@User`\n\n"
                "✨ *Daily reset resets the daily counter to 0h 0m 0s, but does NOT remove or reduce All Time or Weekly breakdown.*"
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed, ephemeral=True)
        return

    # Reset single member daily
    await db.reset_user_daily(guild_id, str(target_member.id), guild=ctx.guild)

    # Broadcast updated user stats to WebSocket so open modal updates immediately
    new_breakdown = await db.get_user_voice_breakdown(guild_id, str(target_member.id), target_member)
    await ws_manager.broadcast_to_guild(
        guild_id,
        {
            "type": "user_stats_update",
            "guild_id": guild_id,
            "user_id": str(target_member.id),
            "data": new_breakdown,
            "timestamp": time.time(),
        },
    )

    embed = discord.Embed(
        title="🌅 Member Daily Voice Stats Reset",
        description=(
            f"✅ Successfully reset **Daily Breakdown** to `0h 0m 0s` for **{target_member.display_name}** (`{target_member.name}`).\n\n"
            f"🛡️ **All Time Breakdown** and **Weekly Breakdown** are fully preserved!"
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    if target_member.display_avatar:
        embed.set_thumbnail(url=target_member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="resetweekly",
    aliases=["weeklyreset", "resetweek"],
    description="Reset Weekly voice stats for a member or all members (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_resetweekly(
    ctx: commands.Context,
    target: Optional[str] = None,
) -> None:
    """
    Resets Weekly voice breakdown. All Time breakdown remains intact.
    Usage:
      ,resetweekly @user   - Resets weekly breakdown for tagged member
      ,resetweekly all     - Resets weekly breakdown for ALL members in the server
    """
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)
    guild_id = str(ctx.guild.id)
    p = settings.bot_prefix

    # Check if target is "all"
    if target and target.strip().lower() == "all":
        count = await db.reset_guild_weekly(guild_id, guild=ctx.guild)

        # Broadcast live reset to web dashboard clients
        stats = compute_guild_statistics(ctx.guild)
        await ws_manager.broadcast_to_guild(
            guild_id,
            {
                "type": "stats_update",
                "guild_id": guild_id,
                "data": stats,
                "timestamp": time.time(),
            },
        )

        embed = discord.Embed(
            title="📅 Weekly Voice Statistics Reset",
            description=(
                f"✅ Successfully reset **Weekly Breakdown** to `0h 0m 0s` for **all members** in **{ctx.guild.name}**.\n\n"
                "🛡️ **Note:** All Time Breakdown remains 100% intact!"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Live web dashboard and breakdown cards have been updated.")
        await ctx.send(embed=embed)
        return

    # Check if a member was tagged/mentioned or provided by ID/name
    target_member = None
    if ctx.message and ctx.message.mentions:
        target_member = ctx.message.mentions[0]
    elif target:
        clean_target = target.strip("<@!> ")
        if clean_target.isdigit():
            target_member = ctx.guild.get_member(int(clean_target))
        if not target_member:
            target_member = discord.utils.find(
                lambda m: m.name.lower() == target.lower() or m.display_name.lower() == target.lower(),
                ctx.guild.members,
            )

    if not target_member:
        embed = discord.Embed(
            title="❓ How to Use Weekly Reset Command",
            description=(
                "Reset only the **Weekly Breakdown** for a member or everyone:\n\n"
                f"• `{p}resetweekly @user` — Reset weekly stats for a single member.\n"
                f"• `{p}resetweekly all` — Reset weekly stats for **all** members.\n"
                f"• Or use `/resetweekly target:@User`\n\n"
                "✨ *Weekly reset resets the weekly counter to 0h 0m 0s, but does NOT remove or reduce All Time breakdown.*"
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed, ephemeral=True)
        return

    # Reset single member weekly
    await db.reset_user_weekly(guild_id, str(target_member.id), guild=ctx.guild)

    # Broadcast updated user stats to WebSocket so open modal updates immediately
    new_breakdown = await db.get_user_voice_breakdown(guild_id, str(target_member.id), target_member)
    await ws_manager.broadcast_to_guild(
        guild_id,
        {
            "type": "user_stats_update",
            "guild_id": guild_id,
            "user_id": str(target_member.id),
            "data": new_breakdown,
            "timestamp": time.time(),
        },
    )

    embed = discord.Embed(
        title="📅 Member Weekly Voice Stats Reset",
        description=(
            f"✅ Successfully reset **Weekly Breakdown** to `0h 0m 0s` for **{target_member.display_name}** (`{target_member.name}`).\n\n"
            f"🛡️ **All Time Breakdown** is fully preserved!"
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    if target_member.display_avatar:
        embed.set_thumbnail(url=target_member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="resetvc",
    aliases=["vcreset", "resettime"],
    description="Reset VC statistics for a tagged member or all members (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_resetvc(
    ctx: commands.Context,
    target: Optional[str] = None,
    scope: Optional[str] = None,
) -> None:
    """
    Resets voice statistics.
    Usage:
      ,resetvc @user          - Resets ALL-TIME VC stats and logs for tagged member
      ,resetvc all            - Resets ALL-TIME VC stats for ALL members in the server
      ,resetvc @user daily    - Resets Daily stats only for tagged member
      ,resetvc all daily      - Resets Daily stats only for ALL members
      ,resetvc @user weekly   - Resets Weekly stats only for tagged member
      ,resetvc all weekly     - Resets Weekly stats only for ALL members
    """
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    # Check if target or scope specifies daily or weekly
    target_str = (target or "").strip().lower()
    scope_str = (scope or "").strip().lower()

    if "daily" in target_str or scope_str == "daily":
        clean = target_str.replace("daily", "").strip() or None
        await cmd_resetdaily(ctx, target=clean)
        return

    if "weekly" in target_str or scope_str == "weekly":
        clean = target_str.replace("weekly", "").strip() or None
        await cmd_resetweekly(ctx, target=clean)
        return

    await ctx.defer(ephemeral=True)
    guild_id = str(ctx.guild.id)

    # Check if target is "all"
    if target and target.strip().lower() == "all":
        count = await db.reset_all_voice_time(guild_id, guild=ctx.guild)

        # Broadcast live reset to web dashboard clients
        stats = compute_guild_statistics(ctx.guild)
        await ws_manager.broadcast_to_guild(
            guild_id,
            {
                "type": "stats_update",
                "guild_id": guild_id,
                "data": stats,
                "timestamp": time.time(),
            },
        )

        embed = discord.Embed(
            title="🔄 Voice Statistics Reset",
            description=f"✅ Successfully reset voice time and state records for **all members** ({count} records cleared) in **{ctx.guild.name}**.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Live web dashboard leaderboard has been updated.")
        await ctx.send(embed=embed)
        return

    # Check if a member was tagged/mentioned or provided by ID/name
    target_member = None
    if ctx.message and ctx.message.mentions:
        target_member = ctx.message.mentions[0]
    elif target:
        clean_target = target.strip("<@!> ")
        if clean_target.isdigit():
            target_member = ctx.guild.get_member(int(clean_target))
        if not target_member:
            target_member = discord.utils.find(
                lambda m: m.name.lower() == target.lower() or m.display_name.lower() == target.lower(),
                ctx.guild.members,
            )

    if not target_member:
        embed = discord.Embed(
            title="❓ How to Use Reset Commands",
            description=(
                "Choose what to reset:\n\n"
                f"• `{settings.bot_prefix}resetdaily @user` — Reset **Daily** stats only (All-Time & Weekly intact)\n"
                f"• `{settings.bot_prefix}resetdaily all` — Reset **Daily** stats for all members\n"
                f"• `{settings.bot_prefix}resetweekly @user` — Reset **Weekly** stats only (All-Time intact)\n"
                f"• `{settings.bot_prefix}resetweekly all` — Reset **Weekly** stats for all members\n"
                f"• `{settings.bot_prefix}resetvc @user` — Reset **All-Time** stats and history for a member\n"
                f"• `{settings.bot_prefix}resetvc all` — Reset **All-Time** stats for all members\n"
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed, ephemeral=True)
        return

    # Reset single member
    await db.reset_user_voice_time(guild_id, str(target_member.id), guild=ctx.guild)

    # Broadcast updated user stats to WebSocket so open modals update immediately
    new_breakdown = await db.get_user_voice_breakdown(guild_id, str(target_member.id), target_member)
    await ws_manager.broadcast_to_guild(
        guild_id,
        {
            "type": "user_stats_update",
            "guild_id": guild_id,
            "user_id": str(target_member.id),
            "data": new_breakdown,
            "timestamp": time.time(),
        },
    )

    # Also broadcast stats_update so leaderboard refreshes
    stats = compute_guild_statistics(ctx.guild)
    await ws_manager.broadcast_to_guild(
        guild_id,
        {
            "type": "stats_update",
            "guild_id": guild_id,
            "data": stats,
            "timestamp": time.time(),
        },
    )

    embed = discord.Embed(
        title="🔄 Member Voice Stats Reset",
        description=f"✅ Successfully reset all voice time and state logs for **{target_member.display_name}** (`{target_member.name}`).",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    if target_member.display_avatar:
        embed.set_thumbnail(url=target_member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="setup",
    description="Create or configure automated voice counter channels (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_setup(ctx: commands.Context) -> None:
    """Sets up Discord voice counter channels."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used within a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    success, message, channels = await bot.counter_manager.setup_counter_channels(
        ctx.guild
    )

    embed = discord.Embed(
        title="📊 Counter Channels Setup",
        description=message,
        color=discord.Color.green() if success else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    if success:
        embed.add_field(
            name="Created Channels",
            value=(
                f"• Category ID: `{channels.get('category_id')}`\n"
                f"• Members: <#{channels.get('member_counter_id')}>\n"
                f"• In Voice: <#{channels.get('voice_counter_id')}>\n"
                f"• Active VCs: <#{channels.get('active_vc_counter_id')}>"
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Discord Rate Limits",
            value=(
                "Discord strictly limits channel name edits to **2 renames per 10 minutes**.\n"
                "The bot automatically batches and throttles updates to avoid API suspensions."
            ),
            inline=False,
        )

    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="removesetup",
    aliases=["delsetup", "setupremove", "teardown"],
    description="Remove the automated voice counter channels and category (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_removesetup(ctx: commands.Context) -> None:
    """Removes Discord voice counter channels and category."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used within a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    success, message = await bot.counter_manager.remove_counter_channels(ctx.guild)

    embed = discord.Embed(
        title="🗑️ Counter Channels Teardown",
        description=message,
        color=discord.Color.green() if success else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    # Broadcast updated stats to dashboard so "Counter Status: Not Setup" updates
    stats = compute_guild_statistics(ctx.guild)
    await ws_manager.broadcast_to_guild(
        str(ctx.guild.id),
        {"type": "stats_update", "guild_id": str(ctx.guild.id), "data": stats, "timestamp": time.time()},
    )

    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="dashboard",
    description="Get the live URL to the real-time web statistics dashboard.",
)
async def cmd_dashboard(ctx: commands.Context) -> None:
    """Displays the dashboard link and privacy status."""
    guild_id = str(ctx.guild.id) if ctx.guild else None
    url = format_dashboard_url(settings.dashboard_url, guild_id=guild_id)

    is_public = False
    if guild_id:
        cfg = await db.get_guild_settings(guild_id)
        is_public = bool(cfg.get("is_public_dashboard", 0)) if cfg else False

    mode_text = (
        "🟢 **Public Mode** (Anyone with the link can view)"
        if is_public
        else "🔒 **Private Mode** (Requires Administrator Secret Key)"
    )

    embed = discord.Embed(
        title="🌐 Real-Time Statistics Dashboard",
        description=(
            f"Access live voice channel graphs, occupant lists, and statistics:\n\n"
            f"🔗 **[Open Web Dashboard]({url})**\n\n"
            f"**Access Mode:** {mode_text}\n"
            f"*(Admins can change privacy mode with `/config dashboard public:True`)*"
        ),
        color=discord.Color.from_rgb(88, 101, 242),
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="refresh",
    description="Manually refresh statistics and sync counter channels.",
)
async def cmd_refresh(ctx: commands.Context) -> None:
    """Force refreshes statistics and counter channels."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    # Compute new stats and broadcast
    stats = compute_guild_statistics(ctx.guild)
    await ws_manager.broadcast_to_guild(
        str(ctx.guild.id),
        {
            "type": "stats_update",
            "guild_id": str(ctx.guild.id),
            "data": stats,
            "timestamp": time.time(),
        },
    )

    # Sync counter channels if configured
    await bot.counter_manager.sync_guild_counters(ctx.guild, force=False)

    await ctx.send(
        "✅ Server statistics refreshed and dispatched to all connected dashboard clients!",
        ephemeral=True,
    )


class LeaderboardView(discord.ui.View):
    """
    Interactive paginated view for the guild voice leaderboard.
    Allows browsing through all members across multiple pages, toggling between
    All-Time, Daily, and Weekly timeframes, and jumping directly to the user's rank.
    """

    def __init__(
        self,
        guild: discord.Guild,
        requester: discord.Member | discord.User,
        all_members: List[Dict[str, Any]],
        timeframe: str = "all",
        page: int = 1,
        per_page: int = 10,
        highlight_user_id: Optional[str] = None,
    ):
        super().__init__(timeout=180.0)
        self.guild = guild
        self.requester = requester
        self.all_members = all_members
        self.timeframe = (timeframe or "all").lower().strip()
        if self.timeframe not in ("all", "daily", "weekly"):
            self.timeframe = "all"
        self.per_page = per_page
        self.total_pages = max(1, (len(all_members) + per_page - 1) // per_page)
        self.page = max(1, min(page, self.total_pages))
        self.highlight_user_id = highlight_user_id
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _format_time(self, total_sec: int) -> str:
        hours, remainder = divmod(total_sec, 3600)
        mins, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {mins}m {secs}s"
        elif mins > 0:
            return f"{mins}m {secs}s"
        return f"{secs}s"

    def build_embed(self) -> discord.Embed:
        tf_info = {
            "daily": ("Daily Voice Leaderboard", discord.Color.orange()),
            "weekly": ("Weekly Voice Leaderboard", discord.Color.blue()),
            "all": ("All-Time Voice Leaderboard", discord.Color.gold()),
        }
        label, color = tf_info.get(self.timeframe, ("All-Time Voice Leaderboard", discord.Color.gold()))

        guild_name = self.guild.name
        if "•" in guild_name:
            guild_title = guild_name.split("•")[0].strip() or guild_name
        elif " - " in guild_name:
            guild_title = guild_name.split(" - ")[0].strip() or guild_name
        elif " | " in guild_name:
            guild_title = guild_name.split(" | ")[0].strip() or guild_name
        else:
            guild_title = guild_name

        embed = discord.Embed(
            title=f"<:Dragon:1561066327471558796>  {guild_title} • {label}",
            color=color,
            timestamp=discord.utils.utcnow(),
        )

        if not self.all_members:
            embed.description = f"No recorded voice activity for **{label}** yet. Jump into a voice channel to get on the board!"
            embed.set_footer(text=f"Timeframe: {label} • Discord VC Counter")
            return embed

        start_idx = (self.page - 1) * self.per_page
        end_idx = start_idx + self.per_page
        current_page_items = self.all_members[start_idx:end_idx]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for entry in current_page_items:
            rank = entry["rank"]
            medal = medals[rank - 1] if rank <= 3 else f"#{rank}"
            time_str = self._format_time(entry["total_seconds"])
            
            # Only show 👉 if a member was explicitly mentioned / targeted
            is_highlighted = bool(self.highlight_user_id and str(entry["user_id"]) == str(self.highlight_user_id))
            indicator = "👉 " if is_highlighted else ""
            user_mention = f"<@{entry['user_id']}>" if entry.get("user_id") else entry.get('display_name', 'Unknown')
            lines.append(f"{indicator}{medal} **{user_mention} — {time_str}**")

        # Pinned user position section so everyone can see themselves even outside Top 10
        user_id_str = str(self.requester.id)
        user_entry = next((u for u in self.all_members if str(u["user_id"]) == user_id_str), None)
        pos_header = "📌 Position:" if (self.highlight_user_id and str(self.highlight_user_id) == user_id_str) else "📌 Your Position:"
        if user_entry:
            u_rank = user_entry["rank"]
            u_time_str = self._format_time(user_entry["total_seconds"])
            user_section = (
                f"\n─────────────────────────────\n"
                f"{pos_header} ** #{u_rank}  <@{self.requester.id}> — {u_time_str} **"
            )
        else:
            user_section = (
                f"\n─────────────────────────────\n"
                f"{pos_header} ** Unranked ** (Join a voice channel to get on this leaderboard!)"
            )

        embed.description = "\n".join(lines) + user_section
        embed.set_footer(
            text=f"Page {self.page} of {self.total_pages} • {len(self.all_members)} Total Ranked Members • {label}"
        )
        return embed

    def _update_buttons(self) -> None:
        self.clear_items()

        # Row 0: Timeframe Switch Buttons (All-Time, Daily, Weekly)
        all_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary if self.timeframe == "all" else discord.ButtonStyle.secondary,
            emoji="🏆",
            label="All-Time",
            row=0,
            custom_id="lb_tf_all",
        )
        all_btn.callback = self.on_all_time_click
        self.add_item(all_btn)

        daily_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary if self.timeframe == "daily" else discord.ButtonStyle.secondary,
            emoji="🌅",
            label="Daily",
            row=0,
            custom_id="lb_tf_daily",
        )
        daily_btn.callback = self.on_daily_click
        self.add_item(daily_btn)

        weekly_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary if self.timeframe == "weekly" else discord.ButtonStyle.secondary,
            emoji="📅",
            label="Weekly",
            row=0,
            custom_id="lb_tf_weekly",
        )
        weekly_btn.callback = self.on_weekly_click
        self.add_item(weekly_btn)

        # Row 1: Pagination Controls & My Rank
        prev_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            emoji="◀️",
            label="Prev",
            disabled=(self.page <= 1),
            row=1,
            custom_id="lb_prev",
        )
        prev_btn.callback = self.on_prev_click
        self.add_item(prev_btn)

        indicator_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=f"{self.page} / {self.total_pages}",
            disabled=True,
            row=1,
            custom_id="lb_page_ind",
        )
        self.add_item(indicator_btn)

        next_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            emoji="▶️",
            label="Next",
            disabled=(self.page >= self.total_pages),
            row=1,
            custom_id="lb_next",
        )
        next_btn.callback = self.on_next_click
        self.add_item(next_btn)

        my_rank_btn = discord.ui.Button(
            style=discord.ButtonStyle.success,
            emoji="🎯",
            label="My Rank",
            row=1,
            custom_id="lb_my_rank",
        )
        my_rank_btn.callback = self.on_my_rank_click
        self.add_item(my_rank_btn)

    async def switch_timeframe(self, interaction: discord.Interaction, new_tf: str) -> None:
        if self.timeframe == new_tf:
            await interaction.response.defer()
            return

        self.timeframe = new_tf
        self.all_members = await db.get_leaderboard(
            str(self.guild.id),
            limit=None,
            guild=self.guild,
            timeframe=new_tf,
        )
        self.total_pages = max(1, (len(self.all_members) + self.per_page - 1) // self.per_page)
        self.page = 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_all_time_click(self, interaction: discord.Interaction) -> None:
        await self.switch_timeframe(interaction, "all")

    async def on_daily_click(self, interaction: discord.Interaction) -> None:
        await self.switch_timeframe(interaction, "daily")

    async def on_weekly_click(self, interaction: discord.Interaction) -> None:
        await self.switch_timeframe(interaction, "weekly")

    async def on_prev_click(self, interaction: discord.Interaction) -> None:
        if self.page > 1:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    async def on_next_click(self, interaction: discord.Interaction) -> None:
        if self.page < self.total_pages:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    async def on_my_rank_click(self, interaction: discord.Interaction) -> None:
        clicker_id = str(interaction.user.id)
        user_entry = next((u for u in self.all_members if str(u["user_id"]) == clicker_id), None)
        if not user_entry:
            tf_label = "Daily" if self.timeframe == "daily" else ("Weekly" if self.timeframe == "weekly" else "All-Time")
            await interaction.response.send_message(
                f"❌ You do not have any recorded voice activity in this server for **{tf_label}** yet! Join a voice channel to get on the leaderboard.",
                ephemeral=True,
            )
            return

        target_page = (user_entry["rank"] - 1) // self.per_page + 1
        self.requester = interaction.user
        self.page = target_page
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


def resolve_member_and_timeframe(
    ctx: commands.Context,
    arg1: Optional[str] = None,
    arg2: Optional[str] = None,
) -> tuple[discord.Member | discord.User, str, Optional[int], bool]:
    """
    Intelligently resolves caller/target member, timeframe, page, and whether an explicit
    target was specified from up to 2 arguments in any order.
    """
    target_member: Optional[discord.Member | discord.User] = None
    timeframe: Optional[str] = None
    page: Optional[int] = None
    has_explicit_target = False

    # Check direct Discord message mentions first
    if ctx.message and ctx.message.mentions:
        target_member = ctx.message.mentions[0]
        has_explicit_target = True

    args = [a for a in (arg1, arg2) if a is not None and str(a).strip() != ""]

    for raw_arg in args:
        val = str(raw_arg).strip()
        val_lower = val.lower()

        # Check for timeframe keywords
        if val_lower in ("daily", "day", "today", "d"):
            if timeframe is None:
                timeframe = "daily"
                continue
        elif val_lower in ("weekly", "week", "w"):
            if timeframe is None:
                timeframe = "weekly"
                continue
        elif val_lower in ("all", "alltime", "all-time", "total", "lifetime"):
            if timeframe is None:
                timeframe = "all"
                continue

        # Check for user mention or user ID
        clean_id = val.strip("<@!> ")
        if clean_id.isdigit():
            # If 15+ digits, it's a Discord snowflake ID
            if len(clean_id) >= 15:
                if ctx.guild:
                    m = ctx.guild.get_member(int(clean_id))
                    if m:
                        target_member = m
                        has_explicit_target = True
                        continue
            else:
                # Small digit count -> Page number
                if page is None:
                    page = int(clean_id)
                    continue

        # Check if argument matches a member's name or display_name
        if target_member is None and ctx.guild:
            m = discord.utils.find(
                lambda member: member.name.lower() == val_lower or member.display_name.lower() == val_lower,
                ctx.guild.members,
            )
            if m:
                target_member = m
                has_explicit_target = True
                continue

    if target_member is None:
        target_member = ctx.author

    if timeframe is None:
        timeframe = "all"

    return target_member, timeframe, page, has_explicit_target


@bot.hybrid_command(
    name="leaderboard",
    aliases=["lb", "top", "vctop"],
    description="Display voice activity statistics for the server in real-time.",
)
@discord.app_commands.describe(
    arg1="Timeframe ('daily', 'weekly', 'all'), page number, or member",
    arg2="Timeframe, page number, or member",
)
async def cmd_leaderboard(
    ctx: commands.Context,
    arg1: Optional[str] = None,
    arg2: Optional[str] = None,
) -> None:
    """Displays real-time voice activity leaderboard with timeframe toggles, pagination, and member focusing."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer()

    target_member, tf, parsed_page, has_explicit = resolve_member_and_timeframe(ctx, arg1, arg2)
    all_members = await db.get_leaderboard(str(ctx.guild.id), limit=None, guild=ctx.guild, timeframe=tf)

    # Determine page to display:
    pg = parsed_page or 1
    if parsed_page is None:
        # If focusing on a member, jump directly to that member's page in the leaderboard!
        user_entry = next((u for u in all_members if str(u["user_id"]) == str(target_member.id)), None)
        if user_entry:
            pg = (user_entry["rank"] - 1) // 10 + 1
        else:
            pg = 1

    highlight_uid = str(target_member.id) if has_explicit else None

    view = LeaderboardView(
        guild=ctx.guild,
        requester=target_member,
        all_members=all_members,
        timeframe=tf,
        page=pg,
        highlight_user_id=highlight_uid,
    )
    embed = view.build_embed()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


@bot.hybrid_command(
    name="dailylb",
    aliases=["daily", "dlb", "daily-lb"],
    description="Display Daily voice activity leaderboard.",
)
@discord.app_commands.describe(
    arg1="Page number, 'lb', or member",
    arg2="Page number or member",
)
async def cmd_dailylb(
    ctx: commands.Context,
    arg1: Optional[str] = None,
    arg2: Optional[str] = None,
) -> None:
    """Displays Daily voice leaderboard directly (supports ,dailylb, ,daily lb, ,daily lb @user, ,dlb)."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer()
    target_member, _, parsed_page, has_explicit = resolve_member_and_timeframe(ctx, arg1, arg2)
    all_members = await db.get_leaderboard(str(ctx.guild.id), limit=None, guild=ctx.guild, timeframe="daily")

    pg = parsed_page or 1
    if parsed_page is None:
        user_entry = next((u for u in all_members if str(u["user_id"]) == str(target_member.id)), None)
        if user_entry:
            pg = (user_entry["rank"] - 1) // 10 + 1
        else:
            pg = 1

    highlight_uid = str(target_member.id) if has_explicit else None

    view = LeaderboardView(
        guild=ctx.guild,
        requester=target_member,
        all_members=all_members,
        timeframe="daily",
        page=pg,
        highlight_user_id=highlight_uid,
    )
    embed = view.build_embed()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


@bot.hybrid_command(
    name="weeklylb",
    aliases=["weekly", "wlb", "weekly-lb"],
    description="Display Weekly voice activity leaderboard.",
)
@discord.app_commands.describe(
    arg1="Page number, 'lb', or member",
    arg2="Page number or member",
)
async def cmd_weeklylb(
    ctx: commands.Context,
    arg1: Optional[str] = None,
    arg2: Optional[str] = None,
) -> None:
    """Displays Weekly voice leaderboard directly (supports ,weeklylb, ,weekly lb, ,weekly lb @user, ,wlb)."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer()
    target_member, _, parsed_page, has_explicit = resolve_member_and_timeframe(ctx, arg1, arg2)
    all_members = await db.get_leaderboard(str(ctx.guild.id), limit=None, guild=ctx.guild, timeframe="weekly")

    pg = parsed_page or 1
    if parsed_page is None:
        user_entry = next((u for u in all_members if str(u["user_id"]) == str(target_member.id)), None)
        if user_entry:
            pg = (user_entry["rank"] - 1) // 10 + 1
        else:
            pg = 1

    highlight_uid = str(target_member.id) if has_explicit else None

    view = LeaderboardView(
        guild=ctx.guild,
        requester=target_member,
        all_members=all_members,
        timeframe="weekly",
        page=pg,
        highlight_user_id=highlight_uid,
    )
    embed = view.build_embed()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


@bot.hybrid_command(
    name="alltimelb",
    aliases=["alltime", "all-time", "atlb", "all"],
    description="Display All-Time voice activity leaderboard.",
)
@discord.app_commands.describe(
    arg1="Page number, 'lb', or member",
    arg2="Page number or member",
)
async def cmd_alltimelb(
    ctx: commands.Context,
    arg1: Optional[str] = None,
    arg2: Optional[str] = None,
) -> None:
    """Displays All-Time voice leaderboard directly (supports ,alltimelb, ,all time lb, ,all time lb @user, ,alltime)."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer()
    target_member, _, parsed_page, has_explicit = resolve_member_and_timeframe(ctx, arg1, arg2)
    all_members = await db.get_leaderboard(str(ctx.guild.id), limit=None, guild=ctx.guild, timeframe="all")

    pg = parsed_page or 1
    if parsed_page is None:
        user_entry = next((u for u in all_members if str(u["user_id"]) == str(target_member.id)), None)
        if user_entry:
            pg = (user_entry["rank"] - 1) // 10 + 1
        else:
            pg = 1

    highlight_uid = str(target_member.id) if has_explicit else None

    view = LeaderboardView(
        guild=ctx.guild,
        requester=target_member,
        all_members=all_members,
        timeframe="all",
        page=pg,
        highlight_user_id=highlight_uid,
    )
    embed = view.build_embed()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


@bot.hybrid_command(
    name="rank",
    aliases=["myrank", "position", "myposition"],
    description="Check your or another member's exact position on the server leaderboard.",
)
@discord.app_commands.describe(
    target="Member or timeframe ('daily', 'weekly', 'all')",
    timeframe="Leaderboard timeframe ('all', 'daily', 'weekly') or member",
)
async def cmd_rank(
    ctx: commands.Context,
    target: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> None:
    """Displays exact server ranking and position for yourself or a specified member."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer()

    target_member, tf, _, _ = resolve_member_and_timeframe(ctx, target, timeframe)

    pos = await db.get_user_leaderboard_position(str(ctx.guild.id), str(target_member.id), guild=ctx.guild, timeframe=tf)
    total_sec = pos["total_seconds"]
    hours, remainder = divmod(total_sec, 3600)
    mins, secs = divmod(remainder, 60)
    if hours > 0:
        time_str = f"{hours}h {mins}m {secs}s"
    elif mins > 0:
        time_str = f"{mins}m {secs}s"
    else:
        time_str = f"{secs}s"

    tf_labels = {
        "daily": "Daily",
        "weekly": "Weekly",
        "all": "All-Time",
    }
    label = tf_labels.get(tf, "All-Time")

    embed = discord.Embed(
        title=f"<:Dragon:1561066327471558796>  Server Rank ({label}) — {pos['display_name']}",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    if target_member.display_avatar:
        embed.set_thumbnail(url=target_member.display_avatar.url)

    if pos["is_ranked"]:
        medals = {1: "🥇 1st Place", 2: "🥈 2nd Place", 3: "🥉 3rd Place"}
        rank_str = medals.get(pos["rank"], f"#{pos['rank']}")

        embed.description = (
            f"<@{target_member.id}> is currently ranked **{rank_str}** out of **{pos['total_ranked']}** active voice members ({label})!\n\n"
            f"⏱️ **{label} Voice Time:** **{time_str}**\n\n"
            f"*Use `{settings.bot_prefix}leaderboard {tf}` or `/leaderboard` to view the full leaderboard.*"
        )
    else:
        embed.description = (
            f"<@{target_member.id}> is currently **Unranked** for **{label}**.\n\n"
            f"No voice activity recorded yet. Join any voice channel to claim a spot on the leaderboard!"
        )

    embed.set_footer(text=f"Discord VC Counter • {label} Voice Analytics")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="config",
    description="Configure dashboard and counter settings (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_config(
    ctx: commands.Context,
    public_dashboard: Optional[bool] = None,
) -> None:
    """Configures server dashboard and counter settings."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)
    guild_id = str(ctx.guild.id)

    if public_dashboard is not None:
        await db.set_public_mode(guild_id, public_dashboard)

    cfg = await db.get_or_create_guild_settings(guild_id)
    is_public = bool(cfg.get("is_public_dashboard", 0))

    embed = discord.Embed(
        title=f"⚙️ Configuration for {ctx.guild.name}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Dashboard Public View",
        value="🔓 **Enabled (Public)**" if is_public else "🔒 **Disabled (Private - Admin Token Required)**",
        inline=False,
    )
    embed.add_field(
        name="Counter Channels Setup",
        value="✅ Active" if cfg.get("category_id") else "❌ Not configured (run `/setup`)",
        inline=False,
    )
    embed.add_field(
        name="To Change Settings",
        value="Run `/config public_dashboard:True` or `/config public_dashboard:False`",
        inline=False,
    )
    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="noprefix",
    aliases=["np", "setnoprefix"],
    description="Configure and manage No-Prefix access for members or the server (Admin only).",
)
@discord.app_commands.describe(
    action="Action: 'add', 'remove', 'list', 'server' (enable/disable), or 'toggle'",
    target="Member to add/remove, or 'enable'/'disable' for server-wide setting",
)
async def cmd_noprefix(
    ctx: commands.Context,
    action: Optional[str] = None,
    target: Optional[str] = None,
) -> None:
    """
    Manages No-Prefix mode. Whitelisted members or the entire server can run commands
    directly (e.g. 'rank', 'lb', 'vc', 'stats') without typing any prefix.
    """
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    guild_id = str(ctx.guild.id)
    is_admin = bool(
        ctx.author.guild_permissions.administrator or ctx.guild.owner_id == ctx.author.id
    )

    action_clean = (action or "").lower().strip()

    # 1. Action: LIST (or no action provided)
    if not action_clean or action_clean in ("list", "show", "view", "status"):
        whitelisted_uids = await db.list_noprefix_users(guild_id)
        is_server_all = guild_id in bot.noprefix_guilds

        embed = discord.Embed(
            title="⚡ No-Prefix Configuration — HeartBeatZ",
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=discord.utils.utcnow(),
        )

        status_text = "🟢 **Enabled for EVERYONE** (All members can run commands without prefix)" if is_server_all else "🟡 **Whitelist Only** (Only administrators & whitelisted members have No-Prefix)"
        embed.description = (
            f"**Server Status:** {status_text}\n\n"
            f"**Default Access:** Server Owner & Administrators always have No-Prefix access."
        )

        if whitelisted_uids:
            member_tags = [f"• <@{uid}> (`{uid}`)" for uid in whitelisted_uids]
            embed.add_field(
                name=f"👥 Whitelisted Members ({len(whitelisted_uids)})",
                value="\n".join(member_tags),
                inline=False,
            )
        else:
            embed.add_field(
                name="👥 Whitelisted Members",
                value="No individual members added yet. Use `,noprefix add @user` to add someone!",
                inline=False,
            )

        p = settings.bot_prefix
        embed.add_field(
            name="📖 How to Setup & Use No-Prefix",
            value=(
                f"• **Add Member:** `{p}noprefix add @user` (gives `@user` no-prefix)\n"
                f"• **Remove Member:** `{p}noprefix remove @user` (removes `@user`)\n"
                f"• **Toggle Server-Wide:** `{p}noprefix server [enable/disable]`\n"
                f"• **View List:** `{p}noprefix list`\n"
                f"*(Aliases: `{p}np`, `{p}setnoprefix`)*"
            ),
            inline=False,
        )
        embed.set_footer(text="Discord VC Counter • No-Prefix System")
        await ctx.send(embed=embed)
        return

    # For mutating actions (add, remove, server/toggle), require Administrator
    if not is_admin:
        await ctx.send(
            "⛔ **Access Denied:** Only Server Administrators or the Server Owner can configure No-Prefix.",
            ephemeral=True,
        )
        return

    # 2. Action: SERVER / TOGGLE
    if action_clean in ("server", "toggle", "all", "serverwide", "server-wide"):
        target_clean = (target or "").lower().strip()
        current_state = guild_id in bot.noprefix_guilds

        if target_clean in ("enable", "on", "true", "yes", "1"):
            new_state = True
        elif target_clean in ("disable", "off", "false", "no", "0"):
            new_state = False
        else:
            new_state = not current_state  # Toggle

        await db.set_guild_noprefix_all(guild_id, new_state)
        if new_state:
            bot.noprefix_guilds.add(guild_id)
            state_msg = "🟢 **ENABLED for ALL members** in this server! Everyone can now use commands (like `rank`, `lb`, `vc`, `stats`, `help`) without typing any prefix."
        else:
            bot.noprefix_guilds.discard(guild_id)
            state_msg = "🔴 **DISABLED for all members**. Commands now require the `,` prefix, except for administrators and whitelisted members."

        embed = discord.Embed(
            title="⚡ Server-Wide No-Prefix Updated",
            description=state_msg,
            color=discord.Color.green() if new_state else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Discord VC Counter • No-Prefix System")
        await ctx.send(embed=embed)
        return

    # 3. Action: ADD
    if action_clean in ("add", "give", "+"):
        target_member = None
        if ctx.message and ctx.message.mentions:
            target_member = ctx.message.mentions[0]
        elif target:
            clean_id = target.strip("<@!> ")
            if clean_id.isdigit():
                target_member = ctx.guild.get_member(int(clean_id))

        if not target_member:
            await ctx.send("❌ Please mention or provide the ID of the member to add. *Example:* `,noprefix add @user`", ephemeral=True)
            return

        target_uid = str(target_member.id)
        await db.add_noprefix_user(guild_id, target_uid, added_by=str(ctx.author.id))
        bot.noprefix_users.add((guild_id, target_uid))

        embed = discord.Embed(
            title="✅ No-Prefix Granted",
            description=(
                f"Successfully added <@{target_uid}> to the No-Prefix list!\n\n"
                f"They can now run any command (e.g. `rank`, `lb`, `vc`, `stats`, `help`) without typing the prefix."
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Discord VC Counter • No-Prefix System")
        await ctx.send(embed=embed)
        return

    # 4. Action: REMOVE
    if action_clean in ("remove", "delete", "del", "-"):
        target_uid = None
        if ctx.message and ctx.message.mentions:
            target_uid = str(ctx.message.mentions[0].id)
        elif target:
            clean_id = target.strip("<@!> ")
            if clean_id.isdigit():
                target_uid = clean_id

        if not target_uid:
            await ctx.send("❌ Please mention or provide the ID of the member to remove. *Example:* `,noprefix remove @user`", ephemeral=True)
            return

        await db.remove_noprefix_user(guild_id, target_uid)
        bot.noprefix_users.discard((guild_id, target_uid))

        embed = discord.Embed(
            title="🗑️ No-Prefix Removed",
            description=f"Successfully removed <@{target_uid}> from the No-Prefix list.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Discord VC Counter • No-Prefix System")
        await ctx.send(embed=embed)
        return

    # Fallback / Invalid Action
    p = settings.bot_prefix
    await ctx.send(
        f"❓ Unknown action `{action}`. Valid actions: `add`, `remove`, `list`, `server`.\n"
        f"*Example:* `{p}noprefix add @user` or `{p}noprefix server enable`",
        ephemeral=True,
    )


@bot.hybrid_command(
    name="customstatus",
    aliases=["speechbubble", "bubble"],
    description="Set the speech bubble custom status next to the bot's profile pic (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_customstatus(
    ctx: commands.Context,
    *,
    status_text: str,
) -> None:
    """Sets the Speech Bubble Custom Status (appears right next to profile picture)."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    if status_text.strip().lower() in ("none", "clear", "remove", "off", "reset"):
        await apply_bot_presence(custom_status="clear")
        embed = discord.Embed(
            title="💬 Speech Bubble Custom Status Cleared",
            description="✅ Successfully removed the speech bubble status next to the profile picture.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed, ephemeral=True)
        return

    await apply_bot_presence(custom_status=status_text)

    embed = discord.Embed(
        title="💬 Speech Bubble Custom Status Set",
        description=(
            f"✅ Bot custom status has been updated!\n\n"
            f"**Speech Bubble Text:**\n"
            f"> **`{status_text}`**\n\n"
            f"*(This appears directly inside the speech bubble cloud next to the bot's profile pic)*"
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="bannerstatus",
    description="Set the activity banner status on the bot's profile card (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_bannerstatus(
    ctx: commands.Context,
    status_text: str,
    activity_type: Optional[str] = "watching",
) -> None:
    """Sets the Activity Banner status on the bot profile (watching, playing, streaming, listening)."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    if status_text.strip().lower() in ("none", "clear", "remove", "off", "reset"):
        await apply_bot_presence(banner_status="clear")
        embed = discord.Embed(
            title="🏷️ Profile Banner Activity Cleared",
            description="✅ Successfully removed the activity banner from the bot profile.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed, ephemeral=True)
        return

    act_type = (activity_type or "watching").lower().strip()
    await apply_bot_presence(banner_status=status_text, banner_type=act_type)

    type_label = act_type.capitalize()
    embed = discord.Embed(
        title="🏷️ Profile Banner Activity Set",
        description=(
            f"✅ Bot banner activity has been updated!\n\n"
            f"• **Activity Type:** `{type_label}`\n"
            f"• **Banner Display:** `{type_label} {status_text}`\n\n"
            f"*(This appears as the floating activity box on the bot's profile banner)*"
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="bothstatus",
    description="Set BOTH the speech bubble status AND the profile banner activity (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_bothstatus(
    ctx: commands.Context,
    bubble_text: str,
    banner_text: str,
    banner_type: Optional[str] = "watching",
) -> None:
    """Sets BOTH the Speech Bubble Custom Status AND the Profile Banner Activity."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)
    b_type = (banner_type or "watching").lower().strip()
    await apply_bot_presence(custom_status=bubble_text, banner_status=banner_text, banner_type=b_type)

    embed = discord.Embed(
        title="✨ Both Statuses Updated",
        description=(
            f"✅ Successfully set **both** types of status on the bot!\n\n"
            f"1. 💬 **Speech Bubble Status (Near Avatar):**\n"
            f"> **`{bubble_text}`**\n\n"
            f"2. 🏷️ **Profile Banner Activity:**\n"
            f"> **`{b_type.capitalize()} {banner_text}`**"
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Use ,clearstatus anytime to remove both.")
    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="setstatus",
    description="Update or configure bot status (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_setstatus(
    ctx: commands.Context,
    status_text: str,
    status_type: Optional[str] = "custom",
) -> None:
    """Smart status dispatcher: sets speech bubble (custom) or banner activity (watching, playing)."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    if status_text.strip().lower() in ("none", "clear", "remove", "off", "reset"):
        await apply_bot_presence(custom_status="clear", banner_status="clear")
        embed = discord.Embed(
            title="✨ Both Statuses Cleared",
            description="✅ Successfully removed both the speech bubble status and the banner activity.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed, ephemeral=True)
        return

    st_type = (status_type or "custom").lower().strip()
    if st_type in ("custom", "bubble", "speechbubble"):
        await apply_bot_presence(custom_status=status_text)
        embed = discord.Embed(
            title="💬 Speech Bubble Custom Status Set",
            description=f"✅ Updated the speech bubble next to the profile pic:\n\n> **`{status_text}`**",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
    else:
        await apply_bot_presence(banner_status=status_text, banner_type=st_type)
        embed = discord.Embed(
            title="🏷️ Profile Banner Activity Set",
            description=f"✅ Updated the banner activity:\n\n> **`{st_type.capitalize()} {status_text}`**",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )

    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="clearstatus",
    aliases=["resetstatus", "removestatus", "delstatus"],
    description="Clear and remove bot statuses: speech bubble, banner, or both (Admin only).",
)
@commands.has_permissions(administrator=True)
async def cmd_clearstatus(
    ctx: commands.Context,
    target: Optional[str] = "all",
) -> None:
    """Completely removes bot status: speech bubble, profile banner, or both."""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)
    t = (target or "all").lower().strip()

    if t in ("bubble", "custom", "speechbubble", "this"):
        await apply_bot_presence(custom_status="clear")
        embed = discord.Embed(
            title="💬 Speech Bubble Status Cleared",
            description="✅ Successfully removed the **Speech Bubble Custom Status** (near avatar)!\n\nThe profile banner activity remains unchanged.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
    elif t in ("banner", "activity", "bannerstatus"):
        await apply_bot_presence(banner_status="clear")
        embed = discord.Embed(
            title="🏷️ Profile Banner Activity Cleared",
            description="✅ Successfully removed the **Profile Banner Activity**!\n\nThe speech bubble status remains unchanged.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
    else:
        await apply_bot_presence(custom_status="clear", banner_status="clear")
        embed = discord.Embed(
            title="✨ Both Statuses Cleared",
            description=(
                "✅ Successfully cleared **both** statuses from the bot!\n\n"
                "• 💬 The **Speech Bubble Status** next to the avatar has been removed.\n"
                "• 🏷️ The **Profile Banner Activity** has been removed.\n\n"
                "The bot now appears as a clean online member with no active status bubble or banner."
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )

    embed.set_footer(text="Use ,customstatus or ,bannerstatus anytime to set a new status.")
    await ctx.send(embed=embed, ephemeral=True)


@bot.hybrid_command(
    name="clear",
    aliases=["purge", "clean", "prune"],
    description="Bulk delete recent messages in the current text channel (Manage Messages only).",
)
@commands.has_permissions(manage_messages=True)
async def cmd_clear(
    ctx: commands.Context,
    amount: int = commands.parameter(
        default=5,
        description="Number of messages to delete (1-100, default: 5)",
    ),
) -> None:
    """Bulk deletes messages from the channel."""
    if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
        await ctx.send("❌ This command can only be used in a text channel within a server.", ephemeral=True)
        return

    if amount < 1:
        await ctx.send("❌ Amount must be at least 1 message.", ephemeral=True)
        return
    if amount > 100:
        await ctx.send("❌ Amount cannot exceed 100 messages at once (Discord limit).", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    try:
        # If run via prefix command, delete the invocation message itself as well
        purge_limit = amount
        if ctx.interaction is None and ctx.message:
            try:
                await ctx.message.delete()
            except Exception:
                pass

        deleted = await ctx.channel.purge(limit=purge_limit)
        msg_count = len(deleted)

        embed = discord.Embed(
            title="🧹 Messages Cleared",
            description=f"✅ Successfully cleared **{msg_count}** message{'s' if msg_count != 1 else ''} from {ctx.channel.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")

        if ctx.interaction:
            await ctx.send(embed=embed, ephemeral=True)
        else:
            conf = await ctx.channel.send(embed=embed)
            await asyncio.sleep(5)
            try:
                await conf.delete()
            except Exception:
                pass
    except discord.Forbidden:
        await ctx.send("⛔ Bot is missing the **Manage Messages** permission in this channel.", ephemeral=True)
    except Exception as e:
        logger.error("Error during message purge: %s", e, exc_info=True)
        await ctx.send(f"❌ Failed to clear messages: {str(e)}", ephemeral=True)


@bot.hybrid_command(
    name="help",
    description="Display comprehensive bot command help and dashboard instructions.",
)
async def cmd_help(ctx: commands.Context) -> None:
    """Displays command manual and instructions."""
    p = settings.bot_prefix

    embed = discord.Embed(
        title="📖 Discord VC Counter Bot — Complete Command Guide",
        description=(
            "Welcome to **Discord VC Counter Bot**! This bot tracks real-time voice channel activity, "
            "maintains automatic counter channels, serves a live web dashboard, and supports custom bot presences.\n\n"
            f"**Default Prefix:** `{p}` (also supports **⚡ No-Prefix Mode** & Slash Commands `/`)"
        ),
        color=discord.Color.from_rgb(88, 101, 242),
    )

    embed.add_field(
        name="⚡ No-Prefix Mode (Run Commands Without Any Prefix!)",
        value=(
            f"• 💡 **What is No-Prefix?**\n"
            f"  Allows you to run commands directly without typing `{p}` (e.g. `rank`, `lb`, `vc`, `stats`, `help`)!\n\n"
            f"• 👑 **Default Access:**\n"
            f"  Server Owners and Administrators always have No-Prefix access by default.\n\n"
            f"• ⚙️ **How to Setup No-Prefix (`{p}noprefix` or `/noprefix`):**\n"
            f"  `{p}noprefix add @user` — Give No-Prefix access to a member\n"
            f"  `{p}noprefix remove @user` — Revoke No-Prefix access from a member\n"
            f"  `{p}noprefix list` — View all whitelisted members in this server\n"
            f"  `{p}noprefix server [enable/disable]` — Enable or disable No-Prefix for **everyone** in the server!\n"
            f"  *(Aliases: `{p}np`, `{p}setnoprefix`)*"
        ),
        inline=False,
    )

    embed.add_field(
        name="📊 General Commands (Everyone)",
        value=(
            f"`{p}vc` or `/vc` — View your own voice statistics breakdown (All Time, Daily, Weekly).\n"
            f"`{p}vc @user` or `/vc @user` — View voice breakdown for a tagged member.\n"
            f"`{p}stats` or `/stats` — View live voice and server member statistics.\n"
            f"`{p}dashboard` or `/dashboard` — Get the real-time web dashboard link.\n"
            f"`{p}refresh` or `/refresh` — Refresh statistics and push to dashboard.\n"
            f"`{p}help` or `/help` — Show this command manual."
        ),
        inline=False,
    )

    embed.add_field(
        name="🏆 Leaderboard & Rank System (,lb & ,rank)",
        value=(
            f"• 📈 **Leaderboard Commands (`{p}lb` or `/leaderboard`):**\n"
            f"  `{p}lb` — All-Time server leaderboard\n"
            f"  `{p}lb daily` — Daily server leaderboard\n"
            f"  `{p}lb weekly` — Weekly server leaderboard\n"
            f"  `{p}lb @user` — Open leaderboard focused on `@user`'s page\n"
            f"  `{p}lb @user daily` or `{p}lb daily @user` — Daily leaderboard for `@user`\n"
            f"  `{p}lb @user weekly` or `{p}lb weekly @user` — Weekly leaderboard for `@user`\n"
            f"  `{p}dailylb` (or `{p}daily lb`) / `{p}weeklylb` / `{p}alltimelb` — Dedicated shortcuts\n\n"
            f"• 🎯 **Rank Commands (`{p}rank` or `/rank`):**\n"
            f"  `{p}rank` — Your All-Time rank card\n"
            f"  `{p}rank daily` — Your Daily rank card\n"
            f"  `{p}rank weekly` — Your Weekly rank card\n"
            f"  `{p}rank @user` — `@user`'s All-Time rank card\n"
            f"  `{p}rank @user daily` or `{p}rank daily @user` — `@user`'s Daily rank card\n"
            f"  `{p}rank @user weekly` or `{p}rank weekly @user` — `@user`'s Weekly rank card"
        ),
        inline=False,
    )

    embed.add_field(
        name="💬 Bot Status Commands (Both Types Supported!)",
        value=(
            f"• 💬 **Speech Bubble Status (\"THIS ONE\" next to avatar):**\n"
            f"  `{p}customstatus <text>` or `/customstatus` (aliases: `{p}speechbubble`, `{p}bubble`)\n"
            f"  *Example:* `{p}customstatus Hiring Weekly Paid staffs For .gg/heartbeatz`\n\n"
            f"• 🏷️ **Profile Banner Activity (\"THE BANNER ONE\" on profile card):**\n"
            f"  `{p}bannerstatus <text> [type]` or `/bannerstatus`\n"
            f"  *Types:* `watching`, `playing`, `streaming`, `listening`\n"
            f"  *Example:* `{p}bannerstatus Voice Channels watching`\n\n"
            f"• ✨ **Set Both Statuses At Once:**\n"
            f"  `{p}bothstatus <bubble_text> <banner_text> [type]` or `/bothstatus`\n\n"
            f"• 🧹 **Clear Status Commands:**\n"
            f"  `{p}clearstatus` or `/clearstatus` — Clear **both** speech bubble and banner!\n"
            f"  `{p}clearstatus bubble` — Clear **only** speech bubble.\n"
            f"  `{p}clearstatus banner` — Clear **only** banner activity."
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ Administrative & Moderation Commands",
        value=(
            f"`{p}clear [amount]` or `/clear` — Bulk-delete messages in current channel (Manage Messages).\n"
            f"`{p}setup` or `/setup` — Automatically create category and voice counter channels.\n"
            f"`{p}removesetup` or `/removesetup` — Remove and delete counter channels & category.\n"
            f"`{p}resetdaily [@user/all]` or `/resetdaily` — Reset Daily stats only (All-Time & Weekly intact).\n"
            f"`{p}resetweekly [@user/all]` or `/resetweekly` — Reset Weekly stats only (All-Time intact).\n"
            f"`{p}resetvc @user` — Reset All-Time VC stats and history for a member.\n"
            f"`{p}resetvc all` — Reset All-Time VC stats and history for **all members**.\n"
            f"`{p}config [public_dashboard:bool]` — Toggle public dashboard mode or view configuration."
        ),
        inline=False,
    )

    embed.add_field(
        name="🧹 How to Clear & Reset Commands (Complete Manual)",
        value=(
            f"• **Clear Both Bot Statuses:** `{p}clearstatus` (clears speech bubble AND banner)\n"
            f"• **Clear Speech Bubble Only:** `{p}clearstatus bubble` or `{p}customstatus clear`\n"
            f"• **Clear Banner Activity Only:** `{p}clearstatus banner` or `{p}bannerstatus clear`\n"
            f"• **Clear Chat Messages:** `{p}clear 10` or `/clear amount:10` (cleans text messages)\n"
            f"• **Clear Daily Voice:** `{p}resetdaily @user` (single) or `{p}resetdaily all` (everyone)\n"
            f"• **Clear Weekly Voice:** `{p}resetweekly @user` (single) or `{p}resetweekly all` (everyone)\n"
            f"• **Clear All-Time History:** `{p}resetvc @user` (single) or `{p}resetvc all` (everyone)\n"
            f"• **Clear Counter Setup:** `{p}removesetup` (removes counter channels & category)"
        ),
        inline=False,
    )

    embed.add_field(
        name="🌐 Web Dashboard Features",
        value=(
            "• Live WebSockets: updates instantly on voice joins/leaves without page reload.\n"
            "• Granular Breakdowns: displays All-Time, Daily, and Weekly stats when clicking members.\n"
            "• Security: constant-time secret authentication and rate limiting."
        ),
        inline=False,
    )

    embed.set_footer(
        text=f"Prefix: {p} • Supports No-Prefix Mode, Slash Commands & Prefix Commands"
    )
    await ctx.send(embed=embed)


# ------------------------------------------------------------------------------
# Error Handling
# ------------------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: discord.app_commands.AppCommandError
) -> None:
    """Handles slash command errors."""
    if isinstance(error, discord.app_commands.MissingPermissions):
        msg = "⛔ You do not have the required Discord permissions to run this command."
    else:
        msg = f"❌ An unexpected error occurred: {str(error)}"
        logger.error("Slash command error: %s", error, exc_info=True)

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    """Handles prefix command errors."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You do not have the required Discord permissions to run this command.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore invalid prefix commands
    else:
        logger.error("Prefix command error: %s", error, exc_info=True)
        await ctx.send(f"❌ Error: {str(error)}")


# ------------------------------------------------------------------------------
# FastAPI Application Factory & Lifespan
# ------------------------------------------------------------------------------
def create_app() -> FastAPI:
    """Builds and configures the FastAPI web application."""
    app = FastAPI(
        title="Discord VC Counter API & Real-Time Dashboard",
        description="High-performance backend delivering real-time Discord statistics over WebSockets.",
        version="1.0.0",
    )

    # Security Headers Middleware
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # API and WebSocket routes
    app.include_router(api_router)

    # Mount static dashboard frontend
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard")
    if os.path.isdir(dashboard_path):
        app.mount(
            "/dashboard",
            StaticFiles(directory=dashboard_path, html=True),
            name="dashboard",
        )

    return app


# ------------------------------------------------------------------------------
# Combined Asyncio Runner (Discord Bot + FastAPI / Uvicorn)
# ------------------------------------------------------------------------------
async def main() -> None:
    """Main async launcher running Discord bot and Uvicorn web server concurrently."""
    token = settings.discord_token
    if not token or token == "your_discord_bot_token_here":
        logger.critical(
            "DISCORD_TOKEN is not configured! Please provide a valid bot token in .env or environment."
        )
        sys.exit(1)

    app = create_app()

    # Uvicorn Server Configuration
    uvicorn_config = uvicorn.Config(
        app=app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,  # Use custom secure logging
    )
    server = uvicorn.Server(uvicorn_config)

    logger.info("Starting Web Dashboard & API on http://%s:%s", settings.host, settings.port)
    logger.info("Connecting to Discord Gateway...")

    async def run_bot_resilient(bot_token: str) -> None:
        """Runs the Discord bot with automatic reconnect on network drops."""
        backoff = 3
        while True:
            try:
                await bot.start(bot_token)
            except (discord.errors.LoginFailure, discord.errors.PrivilegedIntentsRequired):
                raise
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
            except Exception as e:
                logger.error(
                    "Discord connection interrupted: %s. Reconnecting in %ss...",
                    e,
                    backoff,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # Run bot and web server in parallel
    try:
        await asyncio.gather(
            run_bot_resilient(token),
            server.serve(),
        )
    except discord.errors.PrivilegedIntentsRequired:
        client_id = settings.discord_client_id or "YOUR_APPLICATION_ID"
        logger.critical(
            "\n"
            "=================================================================================\n"
            "⛔ DISCORD PRIVILEGED INTENTS REQUIRED:\n"
            "Your bot needs 'Server Members Intent' and 'Message Content Intent' enabled!\n"
            "1. Open your browser to: https://discord.com/developers/applications/%s/bot\n"
            "2. Scroll down to 'Privileged Gateway Intents'\n"
            "3. Turn ON: [x] SERVER MEMBERS INTENT and [x] MESSAGE CONTENT INTENT\n"
            "4. Click 'Save Changes' at the bottom and restart this bot.\n"
            "\n"
            "TIP: If you want to run the bot right now without privileged intents,\n"
            "add 'ENABLE_PRIVILEGED_INTENTS=false' to your .env file.\n"
            "=================================================================================\n",
            client_id,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Termination signal received.")
    except Exception as e:
        logger.error("Fatal exception in main runner: %s", e, exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.close()
        logger.info("Service shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

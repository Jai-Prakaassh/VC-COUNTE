"""
Discord Counter Channels Service.
Manages automatic channel creation, persistent settings, and rate-limit-safe
channel name synchronization (respecting Discord's 2-renames-per-10-minutes rule).
"""

from __future__ import annotations
import time
import asyncio
import logging
from typing import Dict, Optional, Tuple
import discord
from discord.errors import Forbidden, HTTPException, NotFound
from services.config import get_settings
from services.database import db
from services.statistics import compute_guild_statistics

logger = logging.getLogger("counters")


class CounterChannelManager:
    """
    Manages Discord voice counter channels with robust rate-limiting defense.
    Discord strictly limits channel name edits to 2 requests per 10 minutes per channel.
    This manager throttles updates and only makes API calls when the counter value actually changes.
    """

    def __init__(self, bot: discord.Client):
        self.bot = bot
        # channel_id -> last_updated_timestamp
        self._last_updated_time: Dict[int, float] = {}
        # channel_id -> last_known_name
        self._last_channel_name: Dict[int, str] = {}
        # guild_id -> bool indicating update is pending
        self._pending_guild_updates: Dict[int, bool] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    def start_worker(self) -> None:
        """Starts the background worker task to process pending counter updates."""
        if self._worker_task is None or self._worker_task.done():
            self._running = True
            self._worker_task = asyncio.create_task(
                self._background_update_loop(), name="counter_channel_worker"
            )
            logger.info("Counter channel background synchronization worker started.")

    def stop_worker(self) -> None:
        """Stops the background worker."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    def mark_guild_dirty(self, guild_id: int) -> None:
        """Flags that a guild's voice or member statistics have changed."""
        self._pending_guild_updates[guild_id] = True

    async def _background_update_loop(self) -> None:
        """Background loop that periodically checks and syncs dirty guilds."""
        while self._running:
            try:
                # Check pending updates every 30 seconds
                await asyncio.sleep(30)
                dirty_guild_ids = [
                    gid for gid, pending in self._pending_guild_updates.items() if pending
                ]
                for guild_id in dirty_guild_ids:
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        await self.sync_guild_counters(guild)
                        self._pending_guild_updates[guild_id] = False
                    else:
                        self._pending_guild_updates.pop(guild_id, None)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in counter channel background loop: %s", e, exc_info=True)

    async def setup_counter_channels(
        self, guild: discord.Guild
    ) -> Tuple[bool, str, Dict[str, Optional[int]]]:
        """
        Creates or resets the statistics category and counter channels for a guild.
        Returns (success: bool, message: str, channel_ids: dict).
        """
        settings = get_settings()

        # Check bot permissions
        bot_member = guild.me
        if not bot_member.guild_permissions.manage_channels:
            return (
                False,
                "Bot lacks the **Manage Channels** permission required to create counter channels.",
                {},
            )

        try:
            # Overwrites: everyone can view, but cannot connect or speak in counter channels
            everyone_overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    connect=False,
                    speak=False,
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    manage_channels=True,
                    connect=True,
                ),
            }

            stats = compute_guild_statistics(guild)
            counts = stats["counts"]

            category_name = "📊 SERVER STATS 📊"

            # Check if previous category exists in DB
            guild_cfg = await db.get_guild_settings(str(guild.id))
            existing_category = None
            if guild_cfg and guild_cfg.get("category_id"):
                existing_category = guild.get_channel(int(guild_cfg["category_id"]))

            category = existing_category
            if category is None or not isinstance(category, discord.CategoryChannel):
                category = await guild.create_category(
                    name=category_name,
                    overwrites=everyone_overwrites,
                    position=0,
                    reason="VC Counter Bot Setup",
                )

            # Templates
            mem_tpl = (
                guild_cfg.get("member_counter_template")
                if guild_cfg
                else "👥 Members: {count}"
            ) or "👥 Members: {count}"
            vc_tpl = (
                guild_cfg.get("voice_counter_template")
                if guild_cfg
                else "🔊 In Voice: {count}"
            ) or "🔊 In Voice: {count}"
            act_tpl = (
                guild_cfg.get("active_vc_counter_template")
                if guild_cfg
                else "🎙️ Active VCs: {count}"
            ) or "🎙️ Active VCs: {count}"

            mem_name = mem_tpl.format(count=counts["total_members"])
            vc_name = vc_tpl.format(count=counts["in_voice_total"])
            act_name = act_tpl.format(count=counts["active_voice_channels"])

            # Create or update 3 voice counter channels under this category
            # 1. Total Members
            mem_ch = await self._find_or_create_counter_channel(
                guild, category, guild_cfg, "member_counter_id", mem_name
            )

            # 2. In Voice
            vc_ch = await self._find_or_create_counter_channel(
                guild, category, guild_cfg, "voice_counter_id", vc_name
            )

            # 3. Active VCs
            act_ch = await self._find_or_create_counter_channel(
                guild, category, guild_cfg, "active_vc_counter_id", act_name
            )

            # Persist channel IDs in database
            await db.update_counter_channels(
                guild_id=str(guild.id),
                category_id=str(category.id),
                member_counter_id=str(mem_ch.id) if mem_ch else None,
                voice_counter_id=str(vc_ch.id) if vc_ch else None,
                active_vc_counter_id=str(act_ch.id) if act_ch else None,
            )

            now = time.time()
            if mem_ch:
                self._last_updated_time[mem_ch.id] = now
                self._last_channel_name[mem_ch.id] = mem_name
            if vc_ch:
                self._last_updated_time[vc_ch.id] = now
                self._last_channel_name[vc_ch.id] = vc_name
            if act_ch:
                self._last_updated_time[act_ch.id] = now
                self._last_channel_name[act_ch.id] = act_name

            return (
                True,
                "Automatic statistics counter channels have been successfully created and configured!",
                {
                    "category_id": category.id,
                    "member_counter_id": mem_ch.id if mem_ch else None,
                    "voice_counter_id": vc_ch.id if vc_ch else None,
                    "active_vc_counter_id": act_ch.id if act_ch else None,
                },
            )

        except Forbidden:
            logger.error("Missing permissions to set up counter channels in guild %s", guild.id)
            return (
                False,
                "Permission Denied: Bot requires 'Manage Channels' permission.",
                {},
            )
        except Exception as e:
            logger.error("Failed to setup counter channels: %s", e, exc_info=True)
            return False, f"An error occurred during counter setup: {str(e)}", {}

    async def remove_counter_channels(
        self, guild: discord.Guild
    ) -> Tuple[bool, str]:
        """
        Deletes the counter channels and category from Discord and clears their IDs in the database.
        Returns (success: bool, message: str).
        """
        bot_member = guild.me
        if not bot_member.guild_permissions.manage_channels:
            return (
                False,
                "Bot lacks the **Manage Channels** permission required to delete counter channels.",
            )

        guild_cfg = await db.get_guild_settings(str(guild.id))
        if not guild_cfg or not any(
            guild_cfg.get(k)
            for k in [
                "category_id",
                "member_counter_id",
                "voice_counter_id",
                "active_vc_counter_id",
            ]
        ):
            return False, "No active counter channels are configured for this server."

        deleted_count = 0
        # Delete counter channels first
        for key in ["member_counter_id", "voice_counter_id", "active_vc_counter_id"]:
            cid = guild_cfg.get(key)
            if cid:
                ch = guild.get_channel(int(cid))
                if ch:
                    try:
                        await ch.delete(reason="VC Counter Setup Removed")
                        deleted_count += 1
                    except Exception as e:
                        logger.warning("Could not delete channel %s: %s", cid, e)

        # Delete category
        cat_id = guild_cfg.get("category_id")
        if cat_id:
            cat = guild.get_channel(int(cat_id))
            if cat:
                try:
                    await cat.delete(reason="VC Counter Setup Removed")
                    deleted_count += 1
                except Exception as e:
                    logger.warning("Could not delete category %s: %s", cat_id, e)

        # Clear in database
        await db.update_counter_channels(
            guild_id=str(guild.id),
            category_id=None,
            member_counter_id=None,
            voice_counter_id=None,
            active_vc_counter_id=None,
        )

        return True, f"Successfully removed {deleted_count} counter channels and category from the server."

    async def _find_or_create_counter_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        guild_cfg: Optional[Dict],
        cfg_key: str,
        initial_name: str,
    ) -> discord.VoiceChannel:
        """Finds existing configured channel or creates a new voice channel."""
        ch_id = int(guild_cfg[cfg_key]) if guild_cfg and guild_cfg.get(cfg_key) else None
        existing = guild.get_channel(ch_id) if ch_id else None

        if existing and isinstance(existing, discord.VoiceChannel):
            if existing.name != initial_name:
                try:
                    await existing.edit(name=initial_name, reason="VC Counter Initial Sync")
                except Exception:
                    pass
            return existing

        # Create new voice channel
        return await category.create_voice_channel(
            name=initial_name,
            reason="VC Counter Creation",
        )

    async def sync_guild_counters(
        self, guild: discord.Guild, force: bool = False
    ) -> None:
        """
        Synchronizes counter channels for a guild, honoring the cooldown interval
        to prevent hitting Discord's 2-renames-per-10-minutes rate limit.
        """
        settings = get_settings()
        cooldown = max(300, settings.counter_update_interval_seconds)

        guild_cfg = await db.get_guild_settings(str(guild.id))
        if not guild_cfg or not guild_cfg.get("category_id"):
            return

        bot_member = guild.me
        if not bot_member.guild_permissions.manage_channels:
            logger.warning(
                "Skipping counter sync for guild %s: missing Manage Channels permission.",
                guild.id,
            )
            return

        stats = compute_guild_statistics(guild)
        counts = stats["counts"]
        now = time.time()

        channel_configs = [
            (
                guild_cfg.get("member_counter_id"),
                guild_cfg.get("member_counter_template") or "👥 Members: {count}",
                counts["total_members"],
            ),
            (
                guild_cfg.get("voice_counter_id"),
                guild_cfg.get("voice_counter_template") or "🔊 In Voice: {count}",
                counts["in_voice_total"],
            ),
            (
                guild_cfg.get("active_vc_counter_id"),
                guild_cfg.get("active_vc_counter_template") or "🎙️ Active VCs: {count}",
                counts["active_voice_channels"],
            ),
        ]

        for ch_id_str, template, count_value in channel_configs:
            if not ch_id_str:
                continue

            try:
                ch_id = int(ch_id_str)
                channel = guild.get_channel(ch_id)
                if not channel or not isinstance(channel, discord.VoiceChannel):
                    continue

                new_name = template.format(count=count_value)
                last_time = self._last_updated_time.get(ch_id, 0)
                last_name = self._last_channel_name.get(ch_id, channel.name)

                # Skip if name hasn't changed
                if new_name == last_name and channel.name == new_name:
                    continue

                # Check cooldown unless forced
                time_since_last = now - last_time
                if not force and time_since_last < cooldown:
                    # Still in cooldown window, keep guild marked for next run
                    self._pending_guild_updates[guild.id] = True
                    continue

                # Perform the rename
                await channel.edit(name=new_name, reason="VC Counter Dynamic Update")
                self._last_updated_time[ch_id] = now
                self._last_channel_name[ch_id] = new_name
                logger.info(
                    "Updated counter channel %s in %s -> %s",
                    ch_id,
                    guild.name,
                    new_name,
                )

            except Forbidden:
                logger.warning("Forbidden to edit counter channel %s in %s", ch_id_str, guild.name)
            except NotFound:
                logger.warning("Counter channel %s no longer exists in %s", ch_id_str, guild.name)
            except HTTPException as e:
                if e.status == 429:
                    logger.warning("Discord 429 Rate Limit encountered updating channel %s: %s", ch_id_str, e)
                    self._pending_guild_updates[guild.id] = True
                else:
                    logger.error("HTTP error updating channel %s: %s", ch_id_str, e)
            except Exception as e:
                logger.error("Unexpected error updating counter channel %s: %s", ch_id_str, e)

"""
Asynchronous Database Operations for Discord VC Counter.
Manages persistent guild settings, counter channel IDs, and voice activity tracking.
"""

from __future__ import annotations
import os
import time
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, List, Optional
import aiosqlite
from services.config import get_settings

logger = logging.getLogger("database")


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_settings().database_path
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        directory = os.path.dirname(self.db_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            yield db

    async def init_db(self) -> None:
        """Initializes tables and indices."""
        async with self.get_connection() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    is_public_dashboard INTEGER NOT NULL DEFAULT 0,
                    category_id TEXT,
                    member_counter_id TEXT,
                    voice_counter_id TEXT,
                    active_vc_counter_id TEXT,
                    member_counter_template TEXT DEFAULT '👥 Members: {count}',
                    voice_counter_template TEXT DEFAULT '🔊 In Voice: {count}',
                    active_vc_counter_template TEXT DEFAULT '🎙️ Active VCs: {count}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_activity (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    avatar_url TEXT,
                    total_seconds INTEGER NOT NULL DEFAULT 0,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS active_sessions (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    joined_at REAL NOT NULL,
                    current_state TEXT NOT NULL DEFAULT 'unmuted',
                    state_started_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_state_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    state_type TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    duration INTEGER NOT NULL
                )
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activity_guild
                ON voice_activity (guild_id, total_seconds DESC)
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_state_logs_user_time
                ON voice_state_logs (guild_id, user_id, started_at)
                """
            )

            # Safe migrations for existing active_sessions table
            try:
                await db.execute("ALTER TABLE active_sessions ADD COLUMN current_state TEXT NOT NULL DEFAULT 'unmuted'")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE active_sessions ADD COLUMN state_started_at REAL NOT NULL DEFAULT 0")
            except Exception:
                pass

            # Table for independent user daily & weekly reset markers
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_reset_markers (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    daily_reset_at REAL NOT NULL DEFAULT 0,
                    weekly_reset_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            # Table for persistent user-level no-prefix whitelist
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS noprefix_users (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    added_by TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )

            # Safe migrations for guild_settings daily and weekly reset timestamps
            try:
                await db.execute("ALTER TABLE guild_settings ADD COLUMN daily_reset_at REAL NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE guild_settings ADD COLUMN weekly_reset_at REAL NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE guild_settings ADD COLUMN noprefix_all INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

            await db.commit()
            logger.info("Database initialized successfully at %s", self.db_path)

    async def get_guild_settings(self, guild_id: str) -> Optional[Dict]:
        """Fetch settings for a guild. Returns dict or None."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?",
                (str(guild_id),),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        return None

    async def get_or_create_guild_settings(
        self, guild_id: str, default_public: Optional[bool] = None
    ) -> Dict:
        """Retrieves settings, or creates default row if not exists."""
        settings = get_settings()
        is_public = (
            default_public
            if default_public is not None
            else settings.default_public_dashboard
        )

        existing = await self.get_guild_settings(guild_id)
        if existing:
            return existing

        now = time.time()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, is_public_dashboard, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO NOTHING
                """,
                (str(guild_id), 1 if is_public else 0, now, now),
            )
            await db.commit()

        created = await self.get_guild_settings(guild_id)
        return created or {}

    async def update_counter_channels(
        self,
        guild_id: str,
        category_id: Optional[str],
        member_counter_id: Optional[str],
        voice_counter_id: Optional[str],
        active_vc_counter_id: Optional[str],
    ) -> None:
        """Saves counter channel IDs for a guild."""
        now = time.time()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, is_public_dashboard, category_id,
                    member_counter_id, voice_counter_id, active_vc_counter_id,
                    created_at, updated_at
                ) VALUES (?, 0, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    category_id = excluded.category_id,
                    member_counter_id = excluded.member_counter_id,
                    voice_counter_id = excluded.voice_counter_id,
                    active_vc_counter_id = excluded.active_vc_counter_id,
                    updated_at = excluded.updated_at
                """,
                (
                    str(guild_id),
                    str(category_id) if category_id else None,
                    str(member_counter_id) if member_counter_id else None,
                    str(voice_counter_id) if voice_counter_id else None,
                    str(active_vc_counter_id) if active_vc_counter_id else None,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def set_public_mode(self, guild_id: str, is_public: bool) -> None:
        """Toggles public dashboard mode for a guild."""
        now = time.time()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO guild_settings (guild_id, is_public_dashboard, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    is_public_dashboard = excluded.is_public_dashboard,
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), 1 if is_public else 0, now, now),
            )
            await db.commit()

    async def get_all_noprefix_data(self) -> tuple[set[tuple[str, str]], set[str]]:
        """Returns (set of (guild_id, user_id) pairs, set of guild_ids where noprefix_all is True)."""
        async with self.get_connection() as db:
            async with db.execute("SELECT guild_id, user_id FROM noprefix_users") as cursor:
                user_pairs = {(str(r[0]), str(r[1])) for r in await cursor.fetchall()}
            async with db.execute("SELECT guild_id FROM guild_settings WHERE noprefix_all = 1") as cursor:
                all_guilds = {str(r[0]) for r in await cursor.fetchall()}
            return user_pairs, all_guilds

    async def add_noprefix_user(self, guild_id: str, user_id: str, added_by: str) -> bool:
        """Adds a member to the server's no-prefix whitelist."""
        now = time.time()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO noprefix_users (guild_id, user_id, added_by, added_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(guild_id), str(user_id), str(added_by), now),
            )
            await db.commit()
            return True

    async def remove_noprefix_user(self, guild_id: str, user_id: str) -> bool:
        """Removes a member from the server's no-prefix whitelist."""
        async with self.get_connection() as db:
            async with db.execute(
                "DELETE FROM noprefix_users WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)),
            ) as cursor:
                await db.commit()
                return cursor.rowcount > 0

    async def list_noprefix_users(self, guild_id: str) -> list[str]:
        """Lists user IDs of members with no-prefix in this guild."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT user_id FROM noprefix_users WHERE guild_id = ? ORDER BY added_at ASC",
                (str(guild_id),),
            ) as cursor:
                return [str(r[0]) for r in await cursor.fetchall()]

    async def set_guild_noprefix_all(self, guild_id: str, enabled: bool) -> None:
        """Enables or disables server-wide no-prefix for all members in a guild."""
        now = time.time()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO guild_settings (guild_id, is_public_dashboard, noprefix_all, created_at, updated_at)
                VALUES (?, 0, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    noprefix_all = excluded.noprefix_all,
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), 1 if enabled else 0, now, now),
            )
            await db.commit()

    async def is_guild_noprefix_all(self, guild_id: str) -> bool:
        """Checks if server-wide no-prefix is enabled for a guild."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT noprefix_all FROM guild_settings WHERE guild_id = ?",
                (str(guild_id),),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row["noprefix_all"]:
                    return bool(row["noprefix_all"])
        return False

    async def record_voice_join(
        self, guild_id: str, user_id: str, channel_id: str
    ) -> None:
        """Records when a user joins a voice channel."""
        now = time.time()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO active_sessions (guild_id, user_id, channel_id, joined_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    joined_at = excluded.joined_at
                """,
                (str(guild_id), str(user_id), str(channel_id), now),
            )
            await db.commit()

    async def record_voice_leave(
        self,
        guild_id: str,
        user_id: str,
        user_name: str,
        display_name: str,
        avatar_url: Optional[str] = None,
    ) -> int:
        """
        Records when a user leaves voice channels.
        Calculates session duration and updates total accumulated voice seconds.
        Returns the duration in seconds.
        """
        now = time.time()
        session_duration = 0
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT joined_at FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    joined_at = row["joined_at"]
                    session_duration = max(0, int(now - joined_at))

            await db.execute(
                "DELETE FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)),
            )

            if session_duration > 0:
                await db.execute(
                    """
                    INSERT INTO voice_activity (
                        guild_id, user_id, user_name, display_name,
                        avatar_url, total_seconds, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        user_name = excluded.user_name,
                        display_name = excluded.display_name,
                        avatar_url = excluded.avatar_url,
                        total_seconds = voice_activity.total_seconds + excluded.total_seconds,
                        last_seen = excluded.last_seen
                    """,
                    (
                        str(guild_id),
                        str(user_id),
                        user_name,
                        display_name,
                        avatar_url or "",
                        session_duration,
                        now,
                    ),
                )

            await db.commit()
        return session_duration

    async def update_member_voice_state(
        self,
        guild_id: str,
        user_id: str,
        user_name: str,
        display_name: str,
        avatar_url: Optional[str],
        new_state: str,
        channel_id: Optional[str] = None,
    ) -> None:
        """
        Records a voice state transition (unmuted, muted, deafened, afk, disconnected).
        Accurately credits the elapsed duration of the previous state to voice_state_logs.
        """
        now = time.time()
        guild_id = str(guild_id)
        user_id = str(user_id)

        async with self.get_connection() as db:
            prev_state = None
            state_started_at = None
            joined_at = now

            async with db.execute(
                "SELECT channel_id, current_state, state_started_at, joined_at FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    prev_state = row["current_state"] or "unmuted"
                    state_started_at = row["state_started_at"] or row["joined_at"]
                    joined_at = row["joined_at"]

            # If previous state was active, record completed duration in that state
            if prev_state and state_started_at:
                elapsed = max(0, int(now - state_started_at))
                if elapsed > 0:
                    await db.execute(
                        """
                        INSERT INTO voice_state_logs (
                            guild_id, user_id, state_type, started_at, ended_at, duration
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, user_id, prev_state, state_started_at, now, elapsed),
                    )
                    # Accumulate to voice_activity total_seconds
                    await db.execute(
                        """
                        INSERT INTO voice_activity (
                            guild_id, user_id, user_name, display_name,
                            avatar_url, total_seconds, last_seen
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id) DO UPDATE SET
                            user_name = excluded.user_name,
                            display_name = excluded.display_name,
                            avatar_url = excluded.avatar_url,
                            total_seconds = voice_activity.total_seconds + excluded.total_seconds,
                            last_seen = excluded.last_seen
                        """,
                        (
                            guild_id,
                            user_id,
                            user_name,
                            display_name,
                            avatar_url or "",
                            elapsed,
                            now,
                        ),
                    )

            # If member left voice completely
            if new_state == "disconnected" or not channel_id:
                await db.execute(
                    "DELETE FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            else:
                # Member is in voice in new_state
                await db.execute(
                    """
                    INSERT INTO active_sessions (
                        guild_id, user_id, channel_id, joined_at, current_state, state_started_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        current_state = excluded.current_state,
                        state_started_at = excluded.state_started_at
                    """,
                    (guild_id, user_id, str(channel_id), joined_at, new_state, now),
                )

            await db.commit()

    async def get_user_voice_breakdown(
        self,
        guild_id: str,
        user_id: str,
        member: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Retrieves real-time breakdown of user's voice activity across All Time, Daily, and Weekly.
        Includes real-time ongoing session time if member is currently connected.
        """
        now = time.time()
        guild_id = str(guild_id)
        user_id = str(user_id)

        # Check guild-wide and user-specific reset markers
        guild_daily_reset = 0.0
        guild_weekly_reset = 0.0
        user_daily_reset = 0.0
        user_weekly_reset = 0.0

        user_name = "Unknown User"
        display_name = "Unknown"
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        legacy_total = 0

        async with self.get_connection() as db:
            async with db.execute(
                "SELECT daily_reset_at, weekly_reset_at FROM guild_settings WHERE guild_id = ?",
                (guild_id,),
            ) as cursor:
                g_row = await cursor.fetchone()
                if g_row:
                    guild_daily_reset = float(g_row["daily_reset_at"] or 0)
                    guild_weekly_reset = float(g_row["weekly_reset_at"] or 0)

            async with db.execute(
                "SELECT daily_reset_at, weekly_reset_at FROM voice_reset_markers WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cursor:
                u_row = await cursor.fetchone()
                if u_row:
                    user_daily_reset = float(u_row["daily_reset_at"] or 0)
                    user_weekly_reset = float(u_row["weekly_reset_at"] or 0)

            effective_daily_reset = max(guild_daily_reset, user_daily_reset)
            effective_weekly_reset = max(guild_weekly_reset, user_weekly_reset)

            daily_cutoff = max(now - 86400, effective_daily_reset)
            weekly_cutoff = max(now - (7 * 86400), effective_weekly_reset)

            async with db.execute(
                "SELECT user_name, display_name, avatar_url, total_seconds FROM voice_activity WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    user_name = row["user_name"]
                    display_name = row["display_name"]
                    avatar_url = row["avatar_url"] or avatar_url
                    legacy_total = row["total_seconds"] or 0

            if member:
                user_name = getattr(member, "name", user_name)
                display_name = getattr(member, "display_name", display_name)
                if hasattr(member, "display_avatar") and member.display_avatar:
                    avatar_url = str(member.display_avatar.url)

            # All-time breakdown
            all_time = {"unmuted": 0, "muted": 0, "deafened": 0, "afk": 0}
            async with db.execute(
                """
                SELECT state_type, SUM(duration) as total_dur
                FROM voice_state_logs
                WHERE guild_id = ? AND user_id = ?
                GROUP BY state_type
                """,
                (guild_id, user_id),
            ) as cursor:
                for r in await cursor.fetchall():
                    st = r["state_type"]
                    if st in all_time:
                        all_time[st] = int(r["total_dur"] or 0)

            # Reconcile any prior total_seconds from voice_activity
            logged_sum = sum(all_time.values())
            if legacy_total > logged_sum:
                all_time["unmuted"] += (legacy_total - logged_sum)

            # Daily breakdown (last 24 hours, restricted by daily reset)
            daily = {"unmuted": 0, "muted": 0, "deafened": 0, "afk": 0}
            async with db.execute(
                """
                SELECT state_type, SUM(duration) as total_dur
                FROM voice_state_logs
                WHERE guild_id = ? AND user_id = ? AND started_at >= ?
                GROUP BY state_type
                """,
                (guild_id, user_id, daily_cutoff),
            ) as cursor:
                for r in await cursor.fetchall():
                    st = r["state_type"]
                    if st in daily:
                        daily[st] = int(r["total_dur"] or 0)

            # Weekly breakdown (last 7 days, restricted by weekly reset)
            weekly = {"unmuted": 0, "muted": 0, "deafened": 0, "afk": 0}
            async with db.execute(
                """
                SELECT state_type, SUM(duration) as total_dur
                FROM voice_state_logs
                WHERE guild_id = ? AND user_id = ? AND started_at >= ?
                GROUP BY state_type
                """,
                (guild_id, user_id, weekly_cutoff),
            ) as cursor:
                for r in await cursor.fetchall():
                    st = r["state_type"]
                    if st in weekly:
                        weekly[st] = int(r["total_dur"] or 0)

            # Check if currently active in voice and add ongoing segment
            is_connected = False
            current_channel_id = None
            current_state = None
            current_channel_name = None

            # First check if Discord member object confirms they are connected
            member_connected = bool(
                member and hasattr(member, "voice") and member.voice and member.voice.channel
            )
            if member_connected:
                is_connected = True
                current_channel_id = str(member.voice.channel.id)
                current_channel_name = member.voice.channel.name

            async with db.execute(
                "SELECT channel_id, current_state, state_started_at FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    # If member object was provided and member is NOT in voice, this active_session row is stale!
                    if member is not None and not member_connected:
                        await db.execute(
                            "DELETE FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                            (guild_id, user_id),
                        )
                    else:
                        is_connected = True
                        current_channel_id = current_channel_id or row["channel_id"]
                        current_state = row["current_state"] or "unmuted"
                        st_time = row["state_started_at"] or now
                        ongoing_all = max(0, int(now - st_time))
                        ongoing_daily = max(0, int(now - max(st_time, daily_cutoff)))
                        ongoing_weekly = max(0, int(now - max(st_time, weekly_cutoff)))
                        if current_state in all_time:
                            all_time[current_state] += ongoing_all
                        if current_state in daily:
                            daily[current_state] += ongoing_daily
                        if current_state in weekly:
                            weekly[current_state] += ongoing_weekly

            # Calculate totals
            all_time["total"] = sum(all_time[s] for s in ["unmuted", "muted", "deafened", "afk"])
            daily["total"] = sum(daily[s] for s in ["unmuted", "muted", "deafened", "afk"])
            weekly["total"] = sum(weekly[s] for s in ["unmuted", "muted", "deafened", "afk"])

            def with_formatted(d: Dict[str, int]) -> Dict[str, Any]:
                res = dict(d)
                for k, v in d.items():
                    res[f"{k}_formatted"] = format_duration(v)
                return res

            return {
                "guild_id": guild_id,
                "user_id": user_id,
                "user_name": user_name,
                "display_name": display_name,
                "avatar_url": avatar_url,
                "is_connected": is_connected,
                "current_channel_id": current_channel_id,
                "current_channel_name": current_channel_name,
                "current_state": current_state,
                "all_time": with_formatted(all_time),
                "daily": with_formatted(daily),
                "weekly": with_formatted(weekly),
                "timestamp": now,
            }

    async def reconcile_active_sessions(self, guild: Any) -> None:
        """
        Reconciles the database active_sessions table against actual Discord voice channel occupants.
        Removes orphan sessions for users who are no longer in voice,
        and registers any active voice occupants who might be missing from active_sessions.
        """
        if not guild or not hasattr(guild, "voice_channels"):
            return

        now = time.time()
        guild_id = str(guild.id)

        # Collect all non-bot members actually in voice channels in Discord
        actual_voice_members: Dict[str, Any] = {}
        for ch in (guild.voice_channels + getattr(guild, "stage_channels", [])):
            for m in ch.members:
                if not getattr(m, "bot", False):
                    actual_voice_members[str(m.id)] = (m, ch)

        actual_uids = set(actual_voice_members.keys())

        async with self.get_connection() as db:
            # Delete any active_sessions for users who are NOT in voice
            if actual_uids:
                placeholders = ",".join("?" for _ in actual_uids)
                await db.execute(
                    f"DELETE FROM active_sessions WHERE guild_id = ? AND user_id NOT IN ({placeholders})",
                    (guild_id, *actual_uids),
                )
            else:
                await db.execute(
                    "DELETE FROM active_sessions WHERE guild_id = ?",
                    (guild_id,),
                )

            # Ensure all actual voice members exist in active_sessions
            for uid, (m, ch) in actual_voice_members.items():
                vs = getattr(m, "voice", None)
                st = "unmuted"
                if vs:
                    if (guild.afk_channel and ch.id == guild.afk_channel.id) or "afk" in ch.name.lower():
                        st = "afk"
                    elif vs.deaf or vs.self_deaf:
                        st = "deafened"
                    elif vs.mute or vs.self_mute:
                        st = "muted"

                await db.execute(
                    """
                    INSERT INTO active_sessions (guild_id, user_id, channel_id, joined_at, current_state, state_started_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        channel_id = excluded.channel_id
                    """,
                    (guild_id, uid, str(ch.id), now, st, now),
                )

            await db.commit()

    async def get_leaderboard(
        self,
        guild_id: str,
        limit: Optional[int] = None,
        guild: Optional[Any] = None,
        timeframe: str = "all",
    ) -> List[Dict]:
        """
        Returns voice chatters for a guild in real-time, sorted by total_seconds descending.
        Supports timeframes: 'all' (all-time), 'daily' (today/last 24h), and 'weekly' (last 7 days).
        Seamlessly integrates completed sessions with ongoing active sessions.
        If limit is None or <= 0, returns all ranked members.
        """
        now = time.time()
        guild_id = str(guild_id)
        tf = (timeframe or "all").lower().strip()
        if tf in ("daily", "day", "today", "d"):
            tf = "daily"
        elif tf in ("weekly", "week", "w"):
            tf = "weekly"
        else:
            tf = "all"

        # Collect actual active voice member IDs if guild is provided
        actual_voice_uids = None
        if guild and hasattr(guild, "voice_channels"):
            actual_voice_uids = {
                str(m.id)
                for ch in (guild.voice_channels + getattr(guild, "stage_channels", []))
                for m in ch.members
                if not getattr(m, "bot", False)
            }

        async with self.get_connection() as db:
            # 1. Fetch cutoffs if daily or weekly
            guild_daily_reset = 0.0
            guild_weekly_reset = 0.0
            user_daily_resets: Dict[str, float] = {}
            user_weekly_resets: Dict[str, float] = {}

            if tf in ("daily", "weekly"):
                async with db.execute(
                    "SELECT daily_reset_at, weekly_reset_at FROM guild_settings WHERE guild_id = ?",
                    (guild_id,),
                ) as cursor:
                    g_row = await cursor.fetchone()
                    if g_row:
                        guild_daily_reset = float(g_row["daily_reset_at"] or 0)
                        guild_weekly_reset = float(g_row["weekly_reset_at"] or 0)

                async with db.execute(
                    "SELECT user_id, daily_reset_at, weekly_reset_at FROM voice_reset_markers WHERE guild_id = ?",
                    (guild_id,),
                ) as cursor:
                    for r in await cursor.fetchall():
                        uid_str = str(r["user_id"])
                        user_daily_resets[uid_str] = float(r["daily_reset_at"] or 0)
                        user_weekly_resets[uid_str] = float(r["weekly_reset_at"] or 0)

            # 2. Preload member profile information from voice_activity
            profiles_map: Dict[str, Dict[str, Any]] = {}
            async with db.execute(
                """
                SELECT user_id, user_name, display_name, avatar_url, total_seconds, last_seen
                FROM voice_activity
                WHERE guild_id = ?
                """,
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    profiles_map[str(r["user_id"])] = {
                        "user_id": str(r["user_id"]),
                        "user_name": r["user_name"],
                        "display_name": r["display_name"],
                        "avatar_url": r["avatar_url"],
                        "total_seconds": int(r["total_seconds"] or 0),
                        "last_seen": float(r["last_seen"] or now),
                    }

            # 3. Fetch active sessions and purge stale records
            async with db.execute(
                """
                SELECT user_id, channel_id, joined_at, current_state, state_started_at
                FROM active_sessions
                WHERE guild_id = ?
                """,
                (guild_id,),
            ) as cursor:
                active_rows = await cursor.fetchall()

            stale_uids = []
            if actual_voice_uids is not None:
                for a in active_rows:
                    uid = str(a["user_id"])
                    if uid not in actual_voice_uids:
                        stale_uids.append(uid)
                if stale_uids:
                    placeholders = ",".join("?" for _ in stale_uids)
                    await db.execute(
                        f"DELETE FROM active_sessions WHERE guild_id = ? AND user_id IN ({placeholders})",
                        (guild_id, *stale_uids),
                    )
                    await db.commit()

            users_map: Dict[str, Dict[str, Any]] = {}

            if tf == "all":
                # ALL-TIME: Historical totals from voice_activity
                for uid, p in profiles_map.items():
                    users_map[uid] = {
                        "user_id": uid,
                        "user_name": p["user_name"],
                        "display_name": p["display_name"],
                        "avatar_url": p["avatar_url"],
                        "total_seconds": p["total_seconds"],
                        "last_seen": p["last_seen"],
                        "is_active": False,
                    }

                # Add ongoing elapsed time for active members
                for a in active_rows:
                    uid = str(a["user_id"])
                    if actual_voice_uids is not None and uid not in actual_voice_uids:
                        continue
                    st_time = a["state_started_at"] or a["joined_at"] or now
                    ongoing_sec = max(0, int(now - st_time))
                    if uid in users_map:
                        users_map[uid]["total_seconds"] += ongoing_sec
                        users_map[uid]["is_active"] = True
                        users_map[uid]["last_seen"] = now
                    else:
                        users_map[uid] = {
                            "user_id": uid,
                            "user_name": f"User_{uid[-4:]}",
                            "display_name": f"Member {uid[-4:]}",
                            "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
                            "total_seconds": ongoing_sec,
                            "last_seen": now,
                            "is_active": True,
                        }

            elif tf == "daily":
                # DAILY: Filter voice_state_logs within daily cutoff (last 24 hours)
                default_cutoff = max(now - 86400, guild_daily_reset)
                async with db.execute(
                    """
                    SELECT user_id, started_at, duration
                    FROM voice_state_logs
                    WHERE guild_id = ? AND started_at >= ?
                    """,
                    (guild_id, now - 86400),
                ) as cursor:
                    for r in await cursor.fetchall():
                        uid = str(r["user_id"])
                        u_cutoff = max(default_cutoff, user_daily_resets.get(uid, 0.0))
                        st_log = float(r["started_at"] or 0)
                        if st_log >= u_cutoff:
                            dur = int(r["duration"] or 0)
                            if uid not in users_map:
                                p = profiles_map.get(uid, {})
                                users_map[uid] = {
                                    "user_id": uid,
                                    "user_name": p.get("user_name", f"User_{uid[-4:]}"),
                                    "display_name": p.get("display_name", f"Member {uid[-4:]}"),
                                    "avatar_url": p.get("avatar_url", "https://cdn.discordapp.com/embed/avatars/0.png"),
                                    "total_seconds": 0,
                                    "last_seen": now,
                                    "is_active": False,
                                }
                            users_map[uid]["total_seconds"] += dur

                # Add ongoing daily segment for connected members
                for a in active_rows:
                    uid = str(a["user_id"])
                    if actual_voice_uids is not None and uid not in actual_voice_uids:
                        continue
                    u_cutoff = max(default_cutoff, user_daily_resets.get(uid, 0.0))
                    st_time = a["state_started_at"] or a["joined_at"] or now
                    ongoing_daily = max(0, int(now - max(st_time, u_cutoff)))
                    if uid not in users_map:
                        p = profiles_map.get(uid, {})
                        users_map[uid] = {
                            "user_id": uid,
                            "user_name": p.get("user_name", f"User_{uid[-4:]}"),
                            "display_name": p.get("display_name", f"Member {uid[-4:]}"),
                            "avatar_url": p.get("avatar_url", "https://cdn.discordapp.com/embed/avatars/0.png"),
                            "total_seconds": 0,
                            "last_seen": now,
                            "is_active": True,
                        }
                    users_map[uid]["total_seconds"] += ongoing_daily
                    users_map[uid]["is_active"] = True

            elif tf == "weekly":
                # WEEKLY: Filter voice_state_logs within weekly cutoff (last 7 days)
                default_cutoff = max(now - (7 * 86400), guild_weekly_reset)
                async with db.execute(
                    """
                    SELECT user_id, started_at, duration
                    FROM voice_state_logs
                    WHERE guild_id = ? AND started_at >= ?
                    """,
                    (guild_id, now - (7 * 86400)),
                ) as cursor:
                    for r in await cursor.fetchall():
                        uid = str(r["user_id"])
                        u_cutoff = max(default_cutoff, user_weekly_resets.get(uid, 0.0))
                        st_log = float(r["started_at"] or 0)
                        if st_log >= u_cutoff:
                            dur = int(r["duration"] or 0)
                            if uid not in users_map:
                                p = profiles_map.get(uid, {})
                                users_map[uid] = {
                                    "user_id": uid,
                                    "user_name": p.get("user_name", f"User_{uid[-4:]}"),
                                    "display_name": p.get("display_name", f"Member {uid[-4:]}"),
                                    "avatar_url": p.get("avatar_url", "https://cdn.discordapp.com/embed/avatars/0.png"),
                                    "total_seconds": 0,
                                    "last_seen": now,
                                    "is_active": False,
                                }
                            users_map[uid]["total_seconds"] += dur

                # Add ongoing weekly segment for connected members
                for a in active_rows:
                    uid = str(a["user_id"])
                    if actual_voice_uids is not None and uid not in actual_voice_uids:
                        continue
                    u_cutoff = max(default_cutoff, user_weekly_resets.get(uid, 0.0))
                    st_time = a["state_started_at"] or a["joined_at"] or now
                    ongoing_weekly = max(0, int(now - max(st_time, u_cutoff)))
                    if uid not in users_map:
                        p = profiles_map.get(uid, {})
                        users_map[uid] = {
                            "user_id": uid,
                            "user_name": p.get("user_name", f"User_{uid[-4:]}"),
                            "display_name": p.get("display_name", f"Member {uid[-4:]}"),
                            "avatar_url": p.get("avatar_url", "https://cdn.discordapp.com/embed/avatars/0.png"),
                            "total_seconds": 0,
                            "last_seen": now,
                            "is_active": True,
                        }
                    users_map[uid]["total_seconds"] += ongoing_weekly
                    users_map[uid]["is_active"] = True

        # 4. Enrich usernames and avatars from Discord guild member cache if provided
        if guild and hasattr(guild, "get_member"):
            for uid, user_data in users_map.items():
                try:
                    m = guild.get_member(int(uid))
                    if m:
                        user_data["user_name"] = m.name
                        user_data["display_name"] = m.display_name
                        if m.display_avatar:
                            user_data["avatar_url"] = str(m.display_avatar.url)
                except Exception:
                    pass

        # 5. Filter only members with total_seconds > 0 and sort descending
        leaderboard = [u for u in users_map.values() if u["total_seconds"] > 0]
        leaderboard.sort(key=lambda x: x["total_seconds"], reverse=True)

        for idx, entry in enumerate(leaderboard, start=1):
            entry["rank"] = idx
            entry["timeframe"] = tf

        if limit is not None and limit > 0:
            return leaderboard[:limit]
        return leaderboard

    async def get_user_leaderboard_position(
        self,
        guild_id: str,
        user_id: str,
        guild: Optional[Any] = None,
        timeframe: str = "all",
    ) -> Dict[str, Any]:
        """
        Returns a specific user's rank and stats within the server leaderboard for a given timeframe.
        If the user is not in the top 10 (or even unranked), this accurately returns their position.
        """
        user_id = str(user_id)
        guild_id = str(guild_id)
        tf = (timeframe or "all").lower().strip()
        if tf in ("daily", "day", "today", "d"):
            tf = "daily"
        elif tf in ("weekly", "week", "w"):
            tf = "weekly"
        else:
            tf = "all"

        all_ranked = await self.get_leaderboard(guild_id, limit=None, guild=guild, timeframe=tf)
        total_ranked = len(all_ranked)

        for entry in all_ranked:
            if str(entry["user_id"]) == user_id:
                return {
                    "user_id": user_id,
                    "rank": entry["rank"],
                    "total_ranked": total_ranked,
                    "display_name": entry["display_name"],
                    "user_name": entry["user_name"],
                    "avatar_url": entry["avatar_url"],
                    "total_seconds": entry["total_seconds"],
                    "is_active": entry.get("is_active", False),
                    "is_ranked": True,
                    "timeframe": tf,
                }

        # If not on the leaderboard yet (0 seconds)
        display_name = f"Member {user_id[-4:]}"
        user_name = f"User_{user_id[-4:]}"
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        if guild and hasattr(guild, "get_member"):
            try:
                m = guild.get_member(int(user_id))
                if m:
                    display_name = m.display_name
                    user_name = m.name
                    if m.display_avatar:
                        avatar_url = str(m.display_avatar.url)
            except Exception:
                pass

        return {
            "user_id": user_id,
            "rank": None,
            "total_ranked": total_ranked,
            "display_name": display_name,
            "user_name": user_name,
            "avatar_url": avatar_url,
            "total_seconds": 0,
            "is_active": False,
            "is_ranked": False,
            "timeframe": tf,
        }

    async def reset_user_daily(
        self, guild_id: str, user_id: str, guild: Optional[Any] = None
    ) -> bool:
        """
        Resets only the Daily Breakdown for a specific member without touching All Time or Weekly.
        """
        now = time.time()
        guild_id = str(guild_id)
        user_id = str(user_id)

        in_voice = False
        if guild and hasattr(guild, "get_member"):
            m = guild.get_member(int(user_id)) if user_id.isdigit() else None
            if m and getattr(m, "voice", None) and m.voice.channel:
                in_voice = True

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO voice_reset_markers (guild_id, user_id, daily_reset_at, weekly_reset_at)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    daily_reset_at = excluded.daily_reset_at
                """,
                (guild_id, user_id, now),
            )
            if in_voice:
                await db.execute(
                    "UPDATE active_sessions SET state_started_at = ? WHERE guild_id = ? AND user_id = ?",
                    (now, guild_id, user_id),
                )
            else:
                await db.execute(
                    "DELETE FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            await db.commit()
        return True

    async def reset_guild_daily(
        self, guild_id: str, guild: Optional[Any] = None
    ) -> int:
        """
        Resets the Daily Breakdown for ALL members in a guild without touching All Time or Weekly.
        Returns the number of active voice records in the server.
        """
        now = time.time()
        guild_id = str(guild_id)
        await self.get_or_create_guild_settings(guild_id)

        connected_uids = set()
        if guild and hasattr(guild, "voice_channels"):
            for vc in (guild.voice_channels + getattr(guild, "stage_channels", [])):
                for m in vc.members:
                    if not getattr(m, "bot", False):
                        connected_uids.add(str(m.id))

        async with self.get_connection() as db:
            await db.execute(
                "UPDATE guild_settings SET daily_reset_at = ?, updated_at = ? WHERE guild_id = ?",
                (now, now, guild_id),
            )
            await db.execute(
                "UPDATE voice_reset_markers SET daily_reset_at = ? WHERE guild_id = ?",
                (now, guild_id),
            )

            if connected_uids:
                placeholders = ",".join("?" for _ in connected_uids)
                await db.execute(
                    f"UPDATE active_sessions SET state_started_at = ? WHERE guild_id = ? AND user_id IN ({placeholders})",
                    (now, guild_id, *connected_uids),
                )
                await db.execute(
                    f"DELETE FROM active_sessions WHERE guild_id = ? AND user_id NOT IN ({placeholders})",
                    (guild_id, *connected_uids),
                )
            else:
                await db.execute("DELETE FROM active_sessions WHERE guild_id = ?", (guild_id,))

            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM voice_activity WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            count = row["cnt"] if row else 0
            await db.commit()
        return count

    async def reset_user_weekly(
        self, guild_id: str, user_id: str, guild: Optional[Any] = None
    ) -> bool:
        """
        Resets only the Weekly Breakdown for a specific member without touching All Time or Daily.
        """
        now = time.time()
        guild_id = str(guild_id)
        user_id = str(user_id)

        in_voice = False
        if guild and hasattr(guild, "get_member"):
            m = guild.get_member(int(user_id)) if user_id.isdigit() else None
            if m and getattr(m, "voice", None) and m.voice.channel:
                in_voice = True

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO voice_reset_markers (guild_id, user_id, daily_reset_at, weekly_reset_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    weekly_reset_at = excluded.weekly_reset_at
                """,
                (guild_id, user_id, now),
            )
            if in_voice:
                await db.execute(
                    "UPDATE active_sessions SET state_started_at = ? WHERE guild_id = ? AND user_id = ?",
                    (now, guild_id, user_id),
                )
            else:
                await db.execute(
                    "DELETE FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            await db.commit()
        return True

    async def reset_guild_weekly(
        self, guild_id: str, guild: Optional[Any] = None
    ) -> int:
        """
        Resets the Weekly Breakdown for ALL members in a guild without touching All Time or Daily.
        Returns the number of active voice records in the server.
        """
        now = time.time()
        guild_id = str(guild_id)
        await self.get_or_create_guild_settings(guild_id)

        connected_uids = set()
        if guild and hasattr(guild, "voice_channels"):
            for vc in (guild.voice_channels + getattr(guild, "stage_channels", [])):
                for m in vc.members:
                    if not getattr(m, "bot", False):
                        connected_uids.add(str(m.id))

        async with self.get_connection() as db:
            await db.execute(
                "UPDATE guild_settings SET weekly_reset_at = ?, updated_at = ? WHERE guild_id = ?",
                (now, now, guild_id),
            )
            await db.execute(
                "UPDATE voice_reset_markers SET weekly_reset_at = ? WHERE guild_id = ?",
                (now, guild_id),
            )

            if connected_uids:
                placeholders = ",".join("?" for _ in connected_uids)
                await db.execute(
                    f"UPDATE active_sessions SET state_started_at = ? WHERE guild_id = ? AND user_id IN ({placeholders})",
                    (now, guild_id, *connected_uids),
                )
                await db.execute(
                    f"DELETE FROM active_sessions WHERE guild_id = ? AND user_id NOT IN ({placeholders})",
                    (guild_id, *connected_uids),
                )
            else:
                await db.execute("DELETE FROM active_sessions WHERE guild_id = ?", (guild_id,))

            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM voice_activity WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            count = row["cnt"] if row else 0
            await db.commit()
        return count

    async def reset_user_voice_time(
        self, guild_id: str, user_id: str, guild: Optional[Any] = None
    ) -> bool:
        """
        Resets voice time and state logs for a specific member in a guild.
        If the member is currently in voice, resets their ongoing session timestamps to now.
        Otherwise, purges their active_sessions entry.
        """
        now = time.time()
        guild_id = str(guild_id)
        user_id = str(user_id)

        in_voice = False
        if guild and hasattr(guild, "get_member"):
            m = guild.get_member(int(user_id)) if user_id.isdigit() else None
            if m and getattr(m, "voice", None) and m.voice.channel:
                in_voice = True

        async with self.get_connection() as db:
            await db.execute(
                "DELETE FROM voice_activity WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await db.execute(
                "DELETE FROM voice_state_logs WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await db.execute(
                "DELETE FROM voice_reset_markers WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            if in_voice:
                await db.execute(
                    "UPDATE active_sessions SET joined_at = ?, state_started_at = ? WHERE guild_id = ? AND user_id = ?",
                    (now, now, guild_id, user_id),
                )
            else:
                await db.execute(
                    "DELETE FROM active_sessions WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            await db.commit()
        return True

    async def reset_all_voice_time(
        self, guild_id: str, guild: Optional[Any] = None
    ) -> int:
        """
        Resets voice time and state logs for ALL members in a guild.
        Only keeps active_sessions for members who are physically in Discord voice right now,
        resetting their start timestamps to now.
        Returns the number of members affected.
        """
        now = time.time()
        guild_id = str(guild_id)

        connected_uids = set()
        if guild and hasattr(guild, "voice_channels"):
            for vc in (guild.voice_channels + getattr(guild, "stage_channels", [])):
                for m in vc.members:
                    if not getattr(m, "bot", False):
                        connected_uids.add(str(m.id))

        async with self.get_connection() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM voice_activity WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            count = row["cnt"] if row else 0

            await db.execute("DELETE FROM voice_activity WHERE guild_id = ?", (guild_id,))
            await db.execute("DELETE FROM voice_state_logs WHERE guild_id = ?", (guild_id,))
            await db.execute("DELETE FROM voice_reset_markers WHERE guild_id = ?", (guild_id,))
            await db.execute(
                "UPDATE guild_settings SET daily_reset_at = 0, weekly_reset_at = 0 WHERE guild_id = ?",
                (guild_id,),
            )

            if connected_uids:
                placeholders = ",".join("?" for _ in connected_uids)
                await db.execute(
                    f"UPDATE active_sessions SET joined_at = ?, state_started_at = ? WHERE guild_id = ? AND user_id IN ({placeholders})",
                    (now, now, guild_id, *connected_uids),
                )
                await db.execute(
                    f"DELETE FROM active_sessions WHERE guild_id = ? AND user_id NOT IN ({placeholders})",
                    (guild_id, *connected_uids),
                )
            else:
                await db.execute("DELETE FROM active_sessions WHERE guild_id = ?", (guild_id,))

            await db.commit()
        return count


def format_duration(seconds: int) -> str:
    """Formats seconds into '{h}h {m}m {s}s' format (e.g. '0h 0m 3s')."""
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours}h {minutes}m {secs}s"


# Global database instance
db = Database()

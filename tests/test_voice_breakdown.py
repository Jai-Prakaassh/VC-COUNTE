"""
Unit Tests for Voice State Categorization, Duration Formatting, and Embed Builder.
"""

from unittest.mock import MagicMock
import pytest
import discord
from services.database import format_duration
from services.statistics import determine_voice_state, build_user_stats_embed


def test_format_duration():
    """Verify correct string formatting of seconds into 'Xh Ym Zs'."""
    assert format_duration(0) == "0h 0m 0s"
    assert format_duration(3) == "0h 0m 3s"
    assert format_duration(65) == "0h 1m 5s"
    assert format_duration(3600) == "1h 0m 0s"
    assert format_duration(3665) == "1h 1m 5s"
    assert format_duration(7322) == "2h 2m 2s"


def test_determine_voice_state_priority():
    """Verify AFK, Deafen, Mute, and Unmuted state detection hierarchy."""
    guild = MagicMock(spec=discord.Guild)
    afk_channel = MagicMock(spec=discord.VoiceChannel)
    afk_channel.id = 999
    afk_channel.name = "AFK Lounge"
    guild.afk_channel = afk_channel

    normal_channel = MagicMock(spec=discord.VoiceChannel)
    normal_channel.id = 111
    normal_channel.name = "General Voice"

    member = MagicMock(spec=discord.Member)
    member.guild = guild

    # Case 1: Disconnected
    member.voice = None
    assert determine_voice_state(member, None) == "disconnected"

    # Case 2: In AFK channel (even if unmuted)
    vs = MagicMock()
    vs.self_mute = False
    vs.mute = False
    vs.self_deaf = False
    vs.deaf = False
    vs.channel = afk_channel
    member.voice = vs
    assert determine_voice_state(member, afk_channel) == "afk"

    # Case 3: In regular channel, but deafened (takes priority over mute)
    vs.channel = normal_channel
    vs.self_deaf = True
    vs.self_mute = True
    assert determine_voice_state(member, normal_channel) == "deafened"

    # Case 4: In regular channel, muted
    vs.self_deaf = False
    vs.self_mute = True
    assert determine_voice_state(member, normal_channel) == "muted"

    # Case 5: In regular channel, unmuted
    vs.self_mute = False
    assert determine_voice_state(member, normal_channel) == "unmuted"


def test_build_user_stats_embed():
    """Verify embed title, descriptions, breakdowns, and live status."""
    guild = MagicMock(spec=discord.Guild)
    guild.name = "Test Guild"
    guild.id = 12345
    guild.emojis = []

    member = MagicMock(spec=discord.Member)
    member.id = 67890
    member.name = "testuser"
    member.display_name = "TestUser"
    member.display_avatar = MagicMock()
    member.display_avatar.url = "https://cdn.discordapp.com/avatars/67890/avatar.png"

    stats = {
        "guild_id": "12345",
        "user_id": "67890",
        "user_name": "testuser",
        "display_name": "TestUser",
        "avatar_url": "https://cdn.discordapp.com/avatars/67890/avatar.png",
        "is_connected": True,
        "current_channel_id": "111",
        "current_channel_name": "General Voice",
        "current_state": "unmuted",
        "all_time": {
            "unmuted": 3,
            "muted": 0,
            "deafened": 0,
            "afk": 0,
            "total": 3,
            "unmuted_formatted": "0h 0m 3s",
            "muted_formatted": "0h 0m 0s",
            "deafened_formatted": "0h 0m 0s",
            "afk_formatted": "0h 0m 0s",
            "total_formatted": "0h 0m 3s",
        },
        "daily": {
            "unmuted": 3,
            "muted": 0,
            "deafened": 0,
            "afk": 0,
            "total": 3,
            "unmuted_formatted": "0h 0m 3s",
            "muted_formatted": "0h 0m 0s",
            "deafened_formatted": "0h 0m 0s",
            "afk_formatted": "0h 0m 0s",
            "total_formatted": "0h 0m 3s",
        },
        "weekly": {
            "unmuted": 3,
            "muted": 0,
            "deafened": 0,
            "afk": 0,
            "total": 3,
            "unmuted_formatted": "0h 0m 3s",
            "muted_formatted": "0h 0m 0s",
            "deafened_formatted": "0h 0m 0s",
            "afk_formatted": "0h 0m 0s",
            "total_formatted": "0h 0m 3s",
        },
    }

    embed = build_user_stats_embed(guild, member, stats, "http://localhost:8000")

    assert "<:mic:1562509315192131644>" in embed.title
    assert "TestUser Voice Stats" in embed.title
    assert "**  <@67890> ** spent a total of 0h 0m 3s in voice channels!" in embed.description
    assert "<:red_arrow:1562507531853762631> ・Unmuted: 0h 0m 3s" in embed.description
    assert "・AFK:" not in embed.description
    assert "All Time Breakdown:" in embed.description
    assert "Daily Breakdown:" in embed.description
    assert "Weekly Breakdown:" in embed.description
    assert "Voice states are being updated at real time" in embed.description
    assert any("<:xieron_stolen_emoji_1792601457:1562508505238470856> Live Voice Status" in f.name for f in embed.fields)
    assert any("View on Web Dashboard" in f.name for f in embed.fields)


@pytest.mark.asyncio
async def test_daily_and_weekly_reset_decoupling(tmp_path):
    """
    Verify that resetting Daily sets daily breakdown to 0h 0m 0s without reducing
    All Time or Weekly breakdown, and resetting Weekly preserves All Time.
    """
    import time
    from services.database import Database

    test_db_path = str(tmp_path / "test_reset.db")
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()

    guild_id = "111"
    user_id = "222"
    now = time.time()

    async with test_db.get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, avatar_url, total_seconds, last_seen)
            VALUES (?, ?, 'alice', 'Alice', '', 3600, ?)
            """,
            (guild_id, user_id, now),
        )
        await conn.execute(
            """
            INSERT INTO voice_state_logs (guild_id, user_id, state_type, started_at, ended_at, duration)
            VALUES (?, ?, 'unmuted', ?, ?, 3600)
            """,
            (guild_id, user_id, now - 7200, now - 3600),
        )
        await conn.commit()

    # Initial check: all time, daily, weekly are 3600
    b1 = await test_db.get_user_voice_breakdown(guild_id, user_id)
    assert b1["all_time"]["unmuted"] == 3600
    assert b1["daily"]["unmuted"] == 3600
    assert b1["weekly"]["unmuted"] == 3600

    # Reset Daily for user
    await test_db.reset_user_daily(guild_id, user_id)
    b2 = await test_db.get_user_voice_breakdown(guild_id, user_id)

    # Daily must be reset to 0
    assert b2["daily"]["unmuted"] == 0
    assert b2["daily"]["total"] == 0
    assert b2["daily"]["unmuted_formatted"] == "0h 0m 0s"

    # All Time and Weekly must NOT be reduced or removed!
    assert b2["all_time"]["unmuted"] == 3600
    assert b2["all_time"]["total"] == 3600
    assert b2["weekly"]["unmuted"] == 3600
    assert b2["weekly"]["total"] == 3600

    # Reset Weekly for user
    await test_db.reset_user_weekly(guild_id, user_id)
    b3 = await test_db.get_user_voice_breakdown(guild_id, user_id)

    # Weekly must be reset to 0
    assert b3["weekly"]["unmuted"] == 0
    assert b3["weekly"]["total"] == 0
    assert b3["weekly"]["unmuted_formatted"] == "0h 0m 0s"

    # All Time must STILL NOT be removed or reduced!
    assert b3["all_time"]["unmuted"] == 3600
    assert b3["all_time"]["total"] == 3600


@pytest.mark.asyncio
async def test_guild_wide_daily_and_weekly_reset_decoupling(tmp_path):
    """
    Verify that resetting Guild Daily resets daily breakdown for all members
    without touching All Time or Weekly breakdown.
    """
    import time
    from services.database import Database

    test_db_path = str(tmp_path / "test_guild_reset.db")
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()

    guild_id = "555"
    user_a = "user_a"
    user_b = "user_b"
    now = time.time()

    async with test_db.get_connection() as conn:
        for uid in [user_a, user_b]:
            await conn.execute(
                """
                INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, avatar_url, total_seconds, last_seen)
                VALUES (?, ?, ?, ?, '', 1800, ?)
                """,
                (guild_id, uid, uid, uid.title(), now),
            )
            await conn.execute(
                """
                INSERT INTO voice_state_logs (guild_id, user_id, state_type, started_at, ended_at, duration)
                VALUES (?, ?, 'unmuted', ?, ?, 1800)
                """,
                (guild_id, uid, now - 3600, now - 1800),
            )
        await conn.commit()

    # Reset Guild Daily
    count = await test_db.reset_guild_daily(guild_id)
    assert count == 2

    # Check both users
    for uid in [user_a, user_b]:
        b = await test_db.get_user_voice_breakdown(guild_id, uid)
        assert b["daily"]["total"] == 0
        assert b["daily"]["total_formatted"] == "0h 0m 0s"
        assert b["all_time"]["total"] == 1800
        assert b["weekly"]["total"] == 1800

    # Reset Guild Weekly
    count_w = await test_db.reset_guild_weekly(guild_id)
    assert count_w == 2

    # Check both users
    for uid in [user_a, user_b]:
        bw = await test_db.get_user_voice_breakdown(guild_id, uid)
        assert bw["weekly"]["total"] == 0
        assert bw["weekly"]["total_formatted"] == "0h 0m 0s"
        assert bw["all_time"]["total"] == 1800


@pytest.mark.asyncio
async def test_realtime_leaderboard_with_active_sessions(tmp_path):
    """
    Verify that get_leaderboard dynamically includes ongoing active sessions,
    allowing currently connected users to climb the ranking in real-time.
    """
    import time
    from services.database import Database

    test_db_path = str(tmp_path / "test_lb.db")
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()

    guild_id = "999"
    now = time.time()

    async with test_db.get_connection() as conn:
        # User 1: 1000 historical seconds, currently offline
        await conn.execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen)
            VALUES (?, 'u1', 'alice', 'Alice', 1000, ?)
            """,
            (guild_id, now - 5000),
        )
        # User 2: 500 historical seconds, joined 600 seconds ago and active
        await conn.execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen)
            VALUES (?, 'u2', 'bob', 'Bob', 500, ?)
            """,
            (guild_id, now - 5000),
        )
        await conn.execute(
            """
            INSERT INTO active_sessions (guild_id, user_id, channel_id, joined_at, current_state, state_started_at)
            VALUES (?, 'u2', 'c1', ?, 'unmuted', ?)
            """,
            (guild_id, now - 600, now - 600),
        )
        # User 3: 0 historical seconds (first time in voice), active for 300 seconds
        await conn.execute(
            """
            INSERT INTO active_sessions (guild_id, user_id, channel_id, joined_at, current_state, state_started_at)
            VALUES (?, 'u3', 'c1', ?, 'unmuted', ?)
            """,
            (guild_id, now - 300, now - 300),
        )
        await conn.commit()

    lb = await test_db.get_leaderboard(guild_id, limit=10)

    assert len(lb) == 3
    # Bob should be #1 with 500 + 600 = 1100s
    assert lb[0]["user_id"] == "u2"
    assert lb[0]["total_seconds"] >= 1100
    assert lb[0]["is_active"] is True

    # Alice should be #2 with 1000s
    assert lb[1]["user_id"] == "u1"
    assert lb[1]["total_seconds"] == 1000
    assert lb[1]["is_active"] is False

    # User 3 should be #3 with 300s
    assert lb[2]["user_id"] == "u3"
    assert lb[2]["total_seconds"] >= 300
    assert lb[2]["is_active"] is True
    # Verify 1-indexed ranks
    assert lb[0]["rank"] == 1
    assert lb[1]["rank"] == 2
    assert lb[2]["rank"] == 3


@pytest.mark.asyncio
async def test_leaderboard_all_members_and_user_position(tmp_path):
    """
    Verify that get_leaderboard returns all members when limit=None,
    and get_user_leaderboard_position accurately finds members ranked outside top 10.
    """
    import time
    from services.database import Database

    test_db_path = str(tmp_path / "test_all_lb.db")
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()

    guild_id = "888"
    now = time.time()

    # Insert 25 members into voice_activity with decreasing time
    async with test_db.get_connection() as conn:
        for i in range(1, 26):
            sec = (26 - i) * 100
            await conn.execute(
                """
                INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, f"user_{i}", f"name_{i}", f"Member {i}", sec, now),
            )
        await conn.commit()

    # 1. Test get_leaderboard with limit=10 (top 10 only)
    top_10 = await test_db.get_leaderboard(guild_id, limit=10)
    assert len(top_10) == 10
    assert top_10[0]["user_id"] == "user_1"
    assert top_10[0]["rank"] == 1
    assert top_10[9]["user_id"] == "user_10"
    assert top_10[9]["rank"] == 10

    # 2. Test get_leaderboard with limit=None (all 25 members)
    all_members = await test_db.get_leaderboard(guild_id, limit=None)
    assert len(all_members) == 25
    assert all_members[24]["user_id"] == "user_25"
    assert all_members[24]["rank"] == 25

    # 3. Test get_user_leaderboard_position for member in top 10 (user_3)
    pos_3 = await test_db.get_user_leaderboard_position(guild_id, "user_3")
    assert pos_3["is_ranked"] is True
    assert pos_3["rank"] == 3
    assert pos_3["total_ranked"] == 25
    assert pos_3["total_seconds"] == 2300

    # 4. Test get_user_leaderboard_position for member OUTSIDE top 10 (user_19 -> Rank 19)
    pos_19 = await test_db.get_user_leaderboard_position(guild_id, "user_19")
    assert pos_19["is_ranked"] is True
    assert pos_19["rank"] == 19
    assert pos_19["total_ranked"] == 25
    assert pos_19["total_seconds"] == 700

    # 5. Test get_user_leaderboard_position for unranked user
    pos_unranked = await test_db.get_user_leaderboard_position(guild_id, "unknown_user_999")
    assert pos_unranked["is_ranked"] is False
    assert pos_unranked["rank"] is None
    assert pos_unranked["total_seconds"] == 0
    assert pos_unranked["total_ranked"] == 25


@pytest.mark.asyncio
async def test_reconcile_active_sessions_and_reset(tmp_path):
    """
    Verify that reconcile_active_sessions purges ghost sessions,
    and reset_all_voice_time only resets active sessions for members actually in Discord voice.
    """
    import time
    from unittest.mock import MagicMock
    from services.database import Database

    test_db_path = str(tmp_path / "test_reconcile.db")
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()

    guild_id = "777"
    now = time.time()

    # User 1 is really in voice in Discord
    # User 2 is a ghost (was in active_sessions from earlier, but not in Discord voice)
    user_1_mock = MagicMock(spec=discord.Member)
    user_1_mock.id = 1001
    user_1_mock.name = "real_voice_user"
    user_1_mock.display_name = "RealUser"
    user_1_mock.bot = False
    vs1 = MagicMock()
    vs1.mute = False
    vs1.self_mute = False
    vs1.deaf = False
    vs1.self_deaf = False
    user_1_mock.voice = vs1

    ch_mock = MagicMock(spec=discord.VoiceChannel)
    ch_mock.id = 555
    ch_mock.name = "General VC"
    ch_mock.members = [user_1_mock]

    guild_mock = MagicMock(spec=discord.Guild)
    guild_mock.id = 777
    guild_mock.voice_channels = [ch_mock]
    guild_mock.stage_channels = []
    guild_mock.afk_channel = None

    # Insert both user 1 and user 2 into active_sessions
    async with test_db.get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO active_sessions (guild_id, user_id, channel_id, joined_at, current_state, state_started_at)
            VALUES (?, '1001', '555', ?, 'unmuted', ?)
            """,
            (guild_id, now - 500, now - 500),
        )
        await conn.execute(
            """
            INSERT INTO active_sessions (guild_id, user_id, channel_id, joined_at, current_state, state_started_at)
            VALUES (?, '2002', '555', ?, 'unmuted', ?)
            """,
            (guild_id, now - 1000, now - 1000),
        )
        await conn.commit()

    # Test reconcile_active_sessions
    await test_db.reconcile_active_sessions(guild_mock)

    # User 2002 must be purged, User 1001 must be preserved
    async with test_db.get_connection() as conn:
        async with conn.execute("SELECT user_id FROM active_sessions WHERE guild_id = ?", (guild_id,)) as cur:
            active_uids = [r["user_id"] for r in await cur.fetchall()]

    assert active_uids == ["1001"]

    # Now test reset_all_voice_time with guild_mock passed
    # Put some historical time in voice_activity
    async with test_db.get_connection() as conn:
        await conn.execute(
            "INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen) VALUES (?, '1001', 'u1', 'U1', 5000, ?)",
            (guild_id, now),
        )
        await conn.commit()

    await test_db.reset_all_voice_time(guild_id, guild=guild_mock)

    # Historical time must be wiped
    async with test_db.get_connection() as conn:
        async with conn.execute("SELECT total_seconds FROM voice_activity WHERE guild_id = ?", (guild_id,)) as cur:
            rows = await cur.fetchall()
            assert len(rows) == 0

        # User 1001 was in voice during reset, so their active_session was reset to now (~0 elapsed)
        async with conn.execute("SELECT joined_at, state_started_at FROM active_sessions WHERE guild_id = ? AND user_id = '1001'", (guild_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert abs(row["joined_at"] - time.time()) < 5
            assert abs(row["state_started_at"] - time.time()) < 5

    # Check leaderboard: User 1001 has < 5s (not 5000s!), User 2002 has 0s and is NOT active
    lb = await test_db.get_leaderboard(guild_id, guild=guild_mock)
    if lb:
        assert lb[0]["user_id"] == "1001"
        assert lb[0]["total_seconds"] < 10
    user_2002_pos = await test_db.get_user_leaderboard_position(guild_id, "2002", guild=guild_mock)
    assert user_2002_pos["is_ranked"] is False
    assert user_2002_pos["is_active"] is False
    assert user_2002_pos["total_seconds"] == 0


@pytest.mark.asyncio
async def test_leaderboard_timeframes_daily_weekly_all(tmp_path):
    """
    Verify get_leaderboard and get_user_leaderboard_position accurately filter
    voice logs and return distinct rankings for 'daily', 'weekly', and 'all'.
    """
    import time
    from services.database import Database

    test_db_path = str(tmp_path / "test_timeframes.db")
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()

    guild_id = "9999"
    now = time.time()

    # User A: Activity 1 hour ago (3600s) -> In Daily, Weekly, and All-Time
    # User B: Activity 3 days ago (7200s) -> In Weekly and All-Time, but NOT Daily
    # User C: Activity 10 days ago (14400s) -> In All-Time, but NOT Weekly or Daily
    async with test_db.get_connection() as conn:
        # User A voice_activity total: 3600s
        await conn.execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, "user_a", "alice", "Alice", 3600, now - 3600),
        )
        # User A voice_state_log: 1 hour ago
        await conn.execute(
            """
            INSERT INTO voice_state_logs (guild_id, user_id, state_type, started_at, ended_at, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, "user_a", "unmuted", now - 3600, now, 3600),
        )

        # User B voice_activity total: 7200s
        await conn.execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, "user_b", "bob", "Bob", 7200, now - 3 * 86400),
        )
        # User B voice_state_log: 3 days ago
        await conn.execute(
            """
            INSERT INTO voice_state_logs (guild_id, user_id, state_type, started_at, ended_at, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, "user_b", "unmuted", now - 3 * 86400, now - 3 * 86400 + 7200, 7200),
        )

        # User C voice_activity total: 14400s
        await conn.execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, user_name, display_name, total_seconds, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, "user_c", "charlie", "Charlie", 14400, now - 10 * 86400),
        )
        # User C voice_state_log: 10 days ago
        await conn.execute(
            """
            INSERT INTO voice_state_logs (guild_id, user_id, state_type, started_at, ended_at, duration)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, "user_c", "unmuted", now - 10 * 86400, now - 10 * 86400 + 14400, 14400),
        )
        await conn.commit()

    # 1. Test Daily Leaderboard
    daily_lb = await test_db.get_leaderboard(guild_id, timeframe="daily")
    assert len(daily_lb) == 1
    assert daily_lb[0]["user_id"] == "user_a"
    assert daily_lb[0]["rank"] == 1
    assert daily_lb[0]["total_seconds"] == 3600

    # User A daily position
    pos_a_daily = await test_db.get_user_leaderboard_position(guild_id, "user_a", timeframe="daily")
    assert pos_a_daily["is_ranked"] is True
    assert pos_a_daily["rank"] == 1

    # User B daily position (not on daily leaderboard)
    pos_b_daily = await test_db.get_user_leaderboard_position(guild_id, "user_b", timeframe="daily")
    assert pos_b_daily["is_ranked"] is False
    assert pos_b_daily["total_seconds"] == 0

    # 2. Test Weekly Leaderboard
    weekly_lb = await test_db.get_leaderboard(guild_id, timeframe="weekly")
    assert len(weekly_lb) == 2
    assert weekly_lb[0]["user_id"] == "user_b"
    assert weekly_lb[0]["rank"] == 1
    assert weekly_lb[0]["total_seconds"] == 7200
    assert weekly_lb[1]["user_id"] == "user_a"
    assert weekly_lb[1]["rank"] == 2
    assert weekly_lb[1]["total_seconds"] == 3600

    pos_b_weekly = await test_db.get_user_leaderboard_position(guild_id, "user_b", timeframe="weekly")
    assert pos_b_weekly["is_ranked"] is True
    assert pos_b_weekly["rank"] == 1

    pos_c_weekly = await test_db.get_user_leaderboard_position(guild_id, "user_c", timeframe="weekly")
    assert pos_c_weekly["is_ranked"] is False

    # 3. Test All-Time Leaderboard
    all_lb = await test_db.get_leaderboard(guild_id, timeframe="all")
    assert len(all_lb) == 3
    assert all_lb[0]["user_id"] == "user_c"
    assert all_lb[0]["rank"] == 1
    assert all_lb[0]["total_seconds"] == 14400
    assert all_lb[1]["user_id"] == "user_b"
    assert all_lb[1]["rank"] == 2
    assert all_lb[1]["total_seconds"] == 7200
    assert all_lb[2]["user_id"] == "user_a"
    assert all_lb[2]["rank"] == 3
    assert all_lb[2]["total_seconds"] == 3600

    pos_c_all = await test_db.get_user_leaderboard_position(guild_id, "user_c", timeframe="all")
    assert pos_c_all["is_ranked"] is True
    assert pos_c_all["rank"] == 1







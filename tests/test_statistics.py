"""
Unit Tests for Discord Voice Channel Statistics and Filtering.
Verifies accurate counting of humans vs bots, channel occupancy, and search/sort operations.
"""

from unittest.mock import MagicMock
import pytest
import discord
from services.statistics import (
    compute_guild_statistics,
    filter_and_sort_channels,
    serialize_voice_channel,
    serialize_voice_member,
)


def create_mock_member(
    user_id: int,
    name: str,
    display_name: str,
    is_bot: bool = False,
    is_muted: bool = False,
    is_deafened: bool = False,
    is_streaming: bool = False,
    is_video: bool = False,
):
    """Helper to create a mock discord.Member with voice state."""
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.name = name
    member.display_name = display_name
    member.bot = is_bot

    avatar_mock = MagicMock()
    avatar_mock.url = f"https://cdn.discordapp.com/avatars/{user_id}/avatar.png"
    member.display_avatar = avatar_mock

    vs_mock = MagicMock()
    vs_mock.self_mute = is_muted
    vs_mock.mute = False
    vs_mock.self_deaf = is_deafened
    vs_mock.deaf = False
    vs_mock.self_stream = is_streaming
    vs_mock.self_video = is_video
    member.voice = vs_mock

    return member


def create_mock_channel(
    channel_id: int,
    name: str,
    members: list,
    user_limit: int = 0,
    category_name: str = "Voice Channels",
    is_stage: bool = False,
):
    """Helper to create a mock VoiceChannel or StageChannel."""
    spec_cls = discord.StageChannel if is_stage else discord.VoiceChannel
    channel = MagicMock(spec=spec_cls)
    channel.id = channel_id
    channel.name = name
    channel.members = members
    channel.user_limit = user_limit
    channel.position = 0

    category_mock = MagicMock()
    category_mock.name = category_name
    category_mock.position = 0
    channel.category = category_mock

    return channel


def test_serialize_voice_member():
    """Test serialization of a single voice participant."""
    m = create_mock_member(
        101, "alex", "Alex The Great", is_bot=False, is_muted=True, is_streaming=True
    )
    data = serialize_voice_member(m)

    assert data["id"] == "101"
    assert data["name"] == "alex"
    assert data["display_name"] == "Alex The Great"
    assert data["is_bot"] is False
    assert data["is_muted"] is True
    assert data["is_streaming"] is True
    assert data["is_deafened"] is False


def test_compute_guild_statistics_breakdown():
    """Test total members, human/bot counts, and active channels in a guild."""
    # 3 human members, 1 bot member
    m1 = create_mock_member(1, "alice", "Alice", is_bot=False)
    m2 = create_mock_member(2, "bob", "Bob", is_bot=False)
    m3 = create_mock_member(3, "charlie", "Charlie", is_bot=False)
    m_bot = create_mock_member(99, "musicbot", "Music Bot", is_bot=True)

    # VC 1 has Alice and Music Bot (active)
    vc1 = create_mock_channel(1001, "General Voice", [m1, m_bot], user_limit=10)
    # VC 2 has Bob (active)
    vc2 = create_mock_channel(1002, "Gaming Room", [m2], user_limit=5)
    # VC 3 is empty
    vc3 = create_mock_channel(1003, "AFK Lounge", [], user_limit=0)

    guild = MagicMock(spec=discord.Guild)
    guild.id = 5555
    guild.name = "Test Community"
    guild.icon = None
    guild.member_count = 4
    guild.members = [m1, m2, m3, m_bot]
    guild.voice_channels = [vc1, vc2, vc3]
    guild.stage_channels = []

    stats = compute_guild_statistics(guild)
    counts = stats["counts"]

    # Member verification
    assert counts["total_members"] == 4
    assert counts["human_members"] == 3
    assert counts["bot_members"] == 1

    # Voice counts verification (Alice, Bob, Music Bot in voice)
    assert counts["in_voice_total"] == 3
    assert counts["in_voice_humans"] == 2
    assert counts["in_voice_bots"] == 1

    # Active channels verification
    assert counts["active_voice_channels"] == 2
    assert counts["total_voice_channels"] == 3
    assert counts["total_stage_channels"] == 0
    assert counts["total_audio_channels"] == 3


def test_filter_and_sort_channels():
    """Test searching, filtering, and sorting channel collections."""
    channels = [
        {
            "id": "1",
            "name": "General VC",
            "category": "Community",
            "member_count": 3,
            "occupants": [{"name": "alice", "display_name": "Alice"}],
        },
        {
            "id": "2",
            "name": "Music VC",
            "category": "Media",
            "member_count": 5,
            "occupants": [{"name": "bob", "display_name": "Bob"}],
        },
        {
            "id": "3",
            "name": "Quiet Room",
            "category": "Community",
            "member_count": 0,
            "occupants": [],
        },
    ]

    # Test Active Only
    active = filter_and_sort_channels(channels, filter_mode="active_only")
    assert len(active) == 2
    assert all(c["member_count"] > 0 for c in active)

    # Test Empty Only
    empty = filter_and_sort_channels(channels, filter_mode="empty_only")
    assert len(empty) == 1
    assert empty[0]["name"] == "Quiet Room"

    # Test Search by channel name
    music_search = filter_and_sort_channels(channels, query="music")
    assert len(music_search) == 1
    assert music_search[0]["id"] == "2"

    # Test Search by occupant name
    alice_search = filter_and_sort_channels(channels, query="alice")
    assert len(alice_search) == 1
    assert alice_search[0]["id"] == "1"

    # Test Sort by occupants desc
    sorted_desc = filter_and_sort_channels(channels, sort_by="occupants_desc")
    assert sorted_desc[0]["id"] == "2"  # 5 occupants
    assert sorted_desc[1]["id"] == "1"  # 3 occupants
    assert sorted_desc[2]["id"] == "3"  # 0 occupants

    # Test Sort by name asc
    sorted_name = filter_and_sort_channels(channels, sort_by="name_asc")
    assert sorted_name[0]["name"] == "General VC"
    assert sorted_name[1]["name"] == "Music VC"
    assert sorted_name[2]["name"] == "Quiet Room"


def test_resolve_member_and_timeframe():
    """Verify intelligent argument resolution for all rank and lb command variations."""
    from bot import resolve_member_and_timeframe

    # Mock ctx, author, guild, and target member
    caller = MagicMock()
    caller.id = 111111111111111111
    caller.name = "caller"

    target_user = MagicMock()
    target_user.id = 222222222222222222
    target_user.name = "target"
    target_user.display_name = "TargetUser"

    guild = MagicMock()
    guild.get_member.side_effect = lambda uid: target_user if uid == target_user.id else None
    guild.members = [caller, target_user]

    ctx = MagicMock()
    ctx.author = caller
    ctx.guild = guild
    ctx.message = MagicMock()
    ctx.message.mentions = []

    # 1. No args -> caller, "all", None, False
    m, tf, p, exp = resolve_member_and_timeframe(ctx)
    assert m == caller
    assert tf == "all"
    assert p is None
    assert exp is False

    # 2. Timeframe only
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "daily")
    assert m == caller
    assert tf == "daily"
    assert exp is False

    m, tf, p, exp = resolve_member_and_timeframe(ctx, "weekly")
    assert m == caller
    assert tf == "weekly"
    assert exp is False

    # 3. Page number only (e.g. ,lb 2)
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "2")
    assert m == caller
    assert tf == "all"
    assert p == 2
    assert exp is False

    # 4. Timeframe and page (e.g. ,lb daily 3)
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "daily", "3")
    assert m == caller
    assert tf == "daily"
    assert p == 3
    assert exp is False

    # 5. Target member only via snowflake / mention string (e.g. ,rank @user or ,lb @user)
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "<@222222222222222222>")
    assert m == target_user
    assert tf == "all"
    assert exp is True

    # 6. Target member + timeframe (e.g. ,rank @user daily or ,lb @user daily)
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "<@222222222222222222>", "daily")
    assert m == target_user
    assert tf == "daily"
    assert exp is True

    # 7. Timeframe + target member in reverse order (e.g. ,rank weekly @user or ,lb @user weekly)
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "weekly", "<@222222222222222222>")
    assert m == target_user
    assert tf == "weekly"
    assert exp is True

    # 8. Direct message mention present
    ctx.message.mentions = [target_user]
    m, tf, p, exp = resolve_member_and_timeframe(ctx, "daily")
    assert m == target_user
    assert tf == "daily"
    assert exp is True

    m, tf, p, exp = resolve_member_and_timeframe(ctx, "weekly")
    assert m == target_user
    assert tf == "weekly"
    assert exp is True


def test_get_bot_prefix_and_noprefix():
    """Verify prefix resolution logic for normal users, administrators, whitelisted members, and server-wide no-prefix."""
    from bot import get_bot_prefix

    bot = MagicMock()
    bot.user = MagicMock(id=999999999999999999)
    bot.noprefix_users = set()
    bot.noprefix_guilds = set()
    bot.noprefix_global_users = set()

    guild = MagicMock()
    guild.id = 111111
    guild.owner_id = 999111

    # Case 1: Regular user without no-prefix
    regular_user = MagicMock()
    regular_user.id = 222222
    regular_user.bot = False
    regular_user.guild_permissions = MagicMock(administrator=False)

    msg_regular = MagicMock()
    msg_regular.guild = guild
    msg_regular.author = regular_user
    msg_regular.content = "rank"

    prefixes = get_bot_prefix(bot, msg_regular)
    assert "" not in prefixes
    assert "," in prefixes

    # Case 2: Server administrator -> automatically gets No-Prefix
    admin_user = MagicMock()
    admin_user.id = 333333
    admin_user.bot = False
    admin_user.guild_permissions = MagicMock(administrator=True)

    msg_admin = MagicMock()
    msg_admin.guild = guild
    msg_admin.author = admin_user
    msg_admin.content = "rank"

    admin_prefixes = get_bot_prefix(bot, msg_admin)
    assert "" in admin_prefixes
    assert "," in admin_prefixes

    # Case 3: Guild owner -> automatically gets No-Prefix
    owner_user = MagicMock()
    owner_user.id = 999111
    owner_user.bot = False
    owner_user.guild_permissions = MagicMock(administrator=False)

    msg_owner = MagicMock()
    msg_owner.guild = guild
    msg_owner.author = owner_user

    owner_prefixes = get_bot_prefix(bot, msg_owner)
    assert "" in owner_prefixes

    # Case 4: Regular user added to bot.noprefix_users whitelist
    bot.noprefix_users.add(("111111", "222222"))
    whitelisted_prefixes = get_bot_prefix(bot, msg_regular)
    assert "" in whitelisted_prefixes

    # Case 5: Server-wide no-prefix enabled for everyone
    bot.noprefix_users.clear()
    other_user = MagicMock()
    other_user.id = 444444
    other_user.bot = False
    other_user.guild_permissions = MagicMock(administrator=False)

    msg_other = MagicMock()
    msg_other.guild = guild
    msg_other.author = other_user

    assert "" not in get_bot_prefix(bot, msg_other)

    # Enable server-wide
    bot.noprefix_guilds.add("111111")
    assert "" in get_bot_prefix(bot, msg_other)



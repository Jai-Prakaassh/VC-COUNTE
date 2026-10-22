"""
Statistics Calculation and Serialization Service.
Computes comprehensive real-time statistics from Discord Guild models,
including human vs bot breakdowns, voice channel occupancy, and channel metadata.
"""

from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
import discord


def get_member_avatar_url(member: discord.Member) -> str:
    """Safely extracts a CDN avatar URL or default Discord avatar."""
    if member.display_avatar:
        return str(member.display_avatar.url)
    return "https://cdn.discordapp.com/embed/avatars/0.png"


def serialize_voice_member(member: discord.Member) -> Dict[str, Any]:
    """Serializes a single Discord voice participant into a JSON-friendly dict."""
    vs = member.voice
    return {
        "id": str(member.id),
        "name": member.name,
        "display_name": member.display_name,
        "avatar_url": get_member_avatar_url(member),
        "is_bot": bool(member.bot),
        "is_muted": bool(vs.self_mute or vs.mute) if vs else False,
        "is_deafened": bool(vs.self_deaf or vs.deaf) if vs else False,
        "is_streaming": bool(vs.self_stream) if vs else False,
        "is_video": bool(vs.self_video) if vs else False,
    }


def serialize_voice_channel(
    channel: discord.VoiceChannel | discord.StageChannel,
) -> Dict[str, Any]:
    """Serializes a voice or stage channel and its current occupants."""
    is_stage = isinstance(channel, discord.StageChannel)
    category_name = channel.category.name if channel.category else "Uncategorized"
    user_limit = getattr(channel, "user_limit", 0)

    # Sort occupants by name for stable UI rendering
    sorted_members = sorted(channel.members, key=lambda m: m.display_name.lower())
    serialized_members = [serialize_voice_member(m) for m in sorted_members]

    return {
        "id": str(channel.id),
        "name": channel.name,
        "type": "stage" if is_stage else "voice",
        "category": category_name,
        "position": channel.position,
        "user_limit": user_limit,
        "member_count": len(channel.members),
        "occupants": serialized_members,
    }


def compute_guild_statistics(guild: discord.Guild) -> Dict[str, Any]:
    """
    Computes real-time statistics for a Discord guild.

    Bot vs Human Accounting:
    - total_members: Total cached guild members (humans + bots).
    - human_members: Number of non-bot members.
    - bot_members: Number of bot accounts.
    - in_voice_total: Unique members currently in voice/stage channels.
    - in_voice_humans: Non-bot members currently in voice.
    - in_voice_bots: Bot accounts currently in voice (e.g. music bots).
    - active_voice_channels: Channels with at least 1 occupant.
    - total_voice_channels: Standard voice channels.
    - total_stage_channels: Stage voice channels.
    """
    members = list(guild.members)
    total_members = guild.member_count or len(members)

    # Human vs Bot calculation
    if members:
        human_members = sum(1 for m in members if not m.bot)
        bot_members = sum(1 for m in members if m.bot)
    else:
        human_members = total_members
        bot_members = 0

    voice_channels = list(guild.voice_channels)
    stage_channels = list(guild.stage_channels)
    all_audio_channels = voice_channels + stage_channels

    # Deduplicate unique members in voice
    unique_voice_members: Dict[int, discord.Member] = {}
    for ch in all_audio_channels:
        for m in ch.members:
            unique_voice_members[m.id] = m

    in_voice_total = len(unique_voice_members)
    in_voice_humans = sum(1 for m in unique_voice_members.values() if not m.bot)
    in_voice_bots = sum(1 for m in unique_voice_members.values() if m.bot)

    active_channels_count = sum(
        1 for ch in all_audio_channels if len(ch.members) > 0
    )

    # Sort channels by category position then channel position
    all_audio_channels.sort(
        key=lambda c: (
            c.category.position if c.category else -1,
            c.position,
        )
    )

    serialized_channels = [serialize_voice_channel(ch) for ch in all_audio_channels]

    guild_icon_url = str(guild.icon.url) if guild.icon else None

    return {
        "guild_id": str(guild.id),
        "guild_name": guild.name,
        "guild_icon_url": guild_icon_url,
        "timestamp": time.time(),
        "counts": {
            "total_members": total_members,
            "human_members": human_members,
            "bot_members": bot_members,
            "in_voice_total": in_voice_total,
            "in_voice_humans": in_voice_humans,
            "in_voice_bots": in_voice_bots,
            "active_voice_channels": active_channels_count,
            "total_voice_channels": len(voice_channels),
            "total_stage_channels": len(stage_channels),
            "total_audio_channels": len(all_audio_channels),
        },
        "channels": serialized_channels,
    }


def filter_and_sort_channels(
    channels: List[Dict[str, Any]],
    query: Optional[str] = None,
    sort_by: str = "occupants_desc",
    filter_mode: str = "all",
) -> List[Dict[str, Any]]:
    """
    Filters and sorts a list of serialized channels.
    sort_by options: 'occupants_desc', 'occupants_asc', 'name_asc', 'name_desc'
    filter_mode options: 'all', 'active_only', 'empty_only'
    """
    filtered = list(channels)

    # Apply active / empty filter
    if filter_mode == "active_only":
        filtered = [c for c in filtered if c["member_count"] > 0]
    elif filter_mode == "empty_only":
        filtered = [c for c in filtered if c["member_count"] == 0]

    # Apply search query
    if query:
        q = query.strip().lower()
        filtered = [
            c
            for c in filtered
            if q in c["name"].lower()
            or q in c["category"].lower()
            or any(
                q in occ["name"].lower() or q in occ["display_name"].lower()
                for occ in c.get("occupants", [])
            )
        ]

    # Sort
    if sort_by == "occupants_desc":
        filtered.sort(key=lambda c: (-c["member_count"], c["name"].lower()))
    elif sort_by == "occupants_asc":
        filtered.sort(key=lambda c: (c["member_count"], c["name"].lower()))
    elif sort_by == "name_asc":
        filtered.sort(key=lambda c: c["name"].lower())
    elif sort_by == "name_desc":
        filtered.sort(key=lambda c: c["name"].lower(), reverse=True)

    return filtered


def format_dashboard_url(
    base_url: str,
    guild_id: Optional[str | int] = None,
    user_id: Optional[str | int] = None,
) -> str:
    """Formats a clean dashboard link directing directly to /dashboard/ with query parameters."""
    clean = (base_url or "http://localhost:8000").rstrip("/")
    if not clean.endswith("/dashboard"):
        clean = f"{clean}/dashboard"
    params = []
    if guild_id:
        params.append(f"guild={guild_id}")
    if user_id:
        params.append(f"user={user_id}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"{clean}/{query}"


def build_stats_embed(
    guild: discord.Guild, stats: Dict[str, Any], dashboard_url: str
) -> discord.Embed:
    """Builds a Discord embed for /stats and !stats."""
    counts = stats["counts"]

    embed = discord.Embed(
        title=f"📊 {guild.name} — Real-Time Voice Statistics",
        color=discord.Color.from_rgb(88, 101, 242),  # Discord Blurple
        timestamp=discord.utils.utcnow(),
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(
        name="👥 Server Members",
        value=f"**{counts['total_members']:,}** Total\n└ {counts['human_members']:,} Humans • {counts['bot_members']:,} Bots",
        inline=True,
    )

    embed.add_field(
        name="🔊 In Voice Channels",
        value=f"**{counts['in_voice_total']:,}** Connected\n└ {counts['in_voice_humans']:,} Humans • {counts['in_voice_bots']:,} Bots",
        inline=True,
    )

    embed.add_field(
        name="🎙️ Active Voice Channels",
        value=f"**{counts['active_voice_channels']:,}** Active\n└ {counts['total_voice_channels']:,} VC • {counts['total_stage_channels']:,} Stage",
        inline=True,
    )

    # Active channels summary preview
    active_channels = [c for c in stats["channels"] if c["member_count"] > 0]
    if active_channels:
        preview_lines = []
        for ch in active_channels[:5]:
            limit_str = f"/{ch['user_limit']}" if ch["user_limit"] > 0 else ""
            preview_lines.append(
                f"• **{ch['name']}**: `{ch['member_count']}{limit_str}` members"
            )
        if len(active_channels) > 5:
            preview_lines.append(f"*...and {len(active_channels) - 5} more on the dashboard*")
        embed.add_field(
            name="🔥 Top Active Channels",
            value="\n".join(preview_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="💤 Voice Channels",
            value="No members are currently in voice channels.",
            inline=False,
        )

    guild_dash_url = format_dashboard_url(dashboard_url, guild_id=guild.id)
    embed.add_field(
        name="🌐 Live Web Dashboard",
        value=f"[Open Real-Time Dashboard]({guild_dash_url})",
        inline=False,
    )

    embed.set_footer(
        text="Discord VC Counter • Real-Time WebSockets • Updates automatically"
    )
    return embed


def determine_voice_state(
    member: discord.Member,
    channel: Optional[discord.VoiceChannel | discord.StageChannel] = None,
) -> str:
    """
    Categorizes the member's voice presence into: 'afk', 'deafened', 'muted', 'unmuted', or 'disconnected'.
    """
    vs = getattr(member, "voice", None)
    ch = channel or (vs.channel if vs else None)
    if not ch or not vs:
        return "disconnected"
    guild = member.guild
    # AFK channel check
    if (guild.afk_channel and ch.id == guild.afk_channel.id) or "afk" in ch.name.lower():
        return "afk"
    # Deafened (Server or Self)
    if vs.deaf or vs.self_deaf:
        return "deafened"
    # Muted (Server or Self)
    if vs.mute or vs.self_mute:
        return "muted"
    # Unmuted (active speaking capability)
    return "unmuted"


def build_user_stats_embed(
    guild: discord.Guild,
    member: discord.Member,
    stats: Dict[str, Any],
    dashboard_url: str,
) -> discord.Embed:
    """Builds a Discord embed for user voice stats with granular state breakdowns."""
    mic_emoji = "<:mic:1562509315192131644>"
    red_arrow = "<:red_arrow:1562507531853762631>"
    live_emoji = "<:xieron_stolen_emoji_1792601457:1562508505238470856>"

    all_time = stats["all_time"]
    daily = stats["daily"]
    weekly = stats["weekly"]
    name = member.display_name

    embed = discord.Embed(
        title=f"{mic_emoji}  ** @{name} Voice Stats**",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=discord.utils.utcnow(),
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    desc = (
        f"**  <@{member.id}> ** spent a total of {all_time['total_formatted']} in voice channels!\n\n"
        f"**All Time Breakdown:**\n"
        f"{red_arrow} ・Unmuted: {all_time['unmuted_formatted']}\n"
        f"{red_arrow} ・Muted: {all_time['muted_formatted']}\n"
        f"{red_arrow} ・Deafened: {all_time['deafened_formatted']}\n\n"
        f"**Daily Breakdown:**\n"
        f"{red_arrow} ・Total: {daily['total_formatted']}\n"
        f"{red_arrow} ・Unmuted: {daily['unmuted_formatted']}\n"
        f"{red_arrow} ・Muted: {daily['muted_formatted']}\n"
        f"{red_arrow} ・Deafened: {daily['deafened_formatted']}\n\n"
        f"**Weekly Breakdown:**\n"
        f"{red_arrow} ・Total: {weekly['total_formatted']}\n"
        f"{red_arrow} ・Unmuted: {weekly['unmuted_formatted']}\n"
        f"{red_arrow} ・Muted: {weekly['muted_formatted']}\n"
        f"{red_arrow} ・Deafened: {weekly['deafened_formatted']}\n\n"
        f"Voice states are being updated at real time"
    )
    embed.description = desc

    if stats.get("is_connected"):
        ch_name = stats.get("current_channel_name") or "Voice Channel"
        cur_st = (stats.get("current_state") or "unmuted").capitalize()
        embed.add_field(
            name=f"{live_emoji} Live Voice Status",
            value=f"Currently in {ch_name} ({cur_st})",
            inline=False,
        )

    guild_dash_url = format_dashboard_url(dashboard_url, guild_id=guild.id, user_id=member.id)
    embed.add_field(
        name="🌐 View on Web Dashboard",
        value=f"[Open Real-Time Stats]({guild_dash_url})",
        inline=False,
    )

    embed.set_footer(
        text="Discord VC Counter • Real-Time Voice Analytics"
    )
    return embed


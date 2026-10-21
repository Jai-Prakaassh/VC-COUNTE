"""
FastAPI HTTP Routes and WebSocket Endpoints.
Provides RESTful APIs for guild data, health checks, authentication,
and real-time bidirectional WebSocket synchronization.
"""

from __future__ import annotations
import time
import logging
from typing import Any, Dict, List, Optional
import discord
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from services.config import get_settings
from services.database import db
from services.security import (
    check_auth_optional,
    get_client_ip,
    rate_limiter,
    verify_admin_token,
    verify_secret,
)
from services.statistics import (
    compute_guild_statistics,
    filter_and_sort_channels,
)
from api.websocket import ws_manager

logger = logging.getLogger("routes")
router = APIRouter()

# Global reference to the bot instance, set by bot.py on startup
discord_bot: Optional[discord.Client] = None
start_time: float = time.time()


def set_bot_instance(bot: discord.Client) -> None:
    """Injects running Discord bot client into the API layer."""
    global discord_bot
    discord_bot = bot


def get_bot() -> discord.Client:
    """Returns the running Discord bot or raises 503 if not ready."""
    if discord_bot is None or not discord_bot.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Discord bot service is starting or not yet ready.",
        )
    return discord_bot


# ------------------------------------------------------------------------------
# Request / Response Models
# ------------------------------------------------------------------------------
class AuthVerifyRequest(BaseModel):
    token: str = Field(..., description="Administrator dashboard secret token")


class GuildConfigUpdate(BaseModel):
    is_public: Optional[bool] = Field(
        None, description="Whether the dashboard is publicly viewable without auth"
    )
    member_counter_template: Optional[str] = Field(
        None, description="Template for member count channel"
    )
    voice_counter_template: Optional[str] = Field(
        None, description="Template for in-voice channel"
    )
    active_vc_counter_template: Optional[str] = Field(
        None, description="Template for active VC count channel"
    )


# ------------------------------------------------------------------------------
# Health & Root Routes
# ------------------------------------------------------------------------------
@router.get("/")
async def root_redirect():
    """Redirects base path to the web dashboard UI."""
    return RedirectResponse(url="/dashboard/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/health")
async def health_check():
    """
    Health-check endpoint for Docker, VPS monitors, and container orchestrators.
    Returns bot connectivity status, WebSocket latency, and uptime.
    """
    bot = discord_bot
    bot_ready = bot is not None and bot.is_ready()
    latency_ms = round(bot.latency * 1000, 2) if (bot_ready and bot.latency) else 0.0
    uptime_seconds = round(time.time() - start_time, 1)

    return {
        "status": "healthy" if bot_ready else "degraded",
        "bot_connected": bot_ready,
        "bot_user": str(bot.user) if bot_ready and bot.user else None,
        "latency_ms": latency_ms,
        "uptime_seconds": uptime_seconds,
        "guilds_count": len(bot.guilds) if bot_ready else 0,
        "timestamp": time.time(),
    }


@router.get("/api/presence")
async def get_presence_status():
    """Returns the live presence, status, and activities of the bot."""
    bot = discord_bot
    if bot is None or not bot.is_ready():
        return {"status": "bot_not_ready"}

    settings = get_settings()
    guild_data = []
    for g in bot.guilds:
        me = g.me
        acts = []
        for a in me.activities:
            acts.append({
                "type": getattr(a, "type", None).value if hasattr(getattr(a, "type", None), "value") else str(getattr(a, "type", None)),
                "name": getattr(a, "name", None),
                "state": getattr(a, "state", None),
                "details": getattr(a, "details", None),
            })
        guild_data.append({
            "guild_id": str(g.id),
            "guild_name": g.name,
            "status": str(me.status),
            "raw_status": str(me.raw_status),
            "mobile_status": str(me.mobile_status),
            "desktop_status": str(me.desktop_status),
            "web_status": str(me.web_status),
            "activities": acts,
        })

    return {
        "bot_user": str(bot.user),
        "configured_custom_status": settings.bot_custom_status,
        "configured_banner_status": settings.bot_banner_status,
        "guilds": guild_data,
    }



# ------------------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------------------
@router.post("/api/auth/verify")
async def verify_auth(payload: AuthVerifyRequest, request: Request):
    """
    Validates dashboard administrator secret token.
    Protected by IP rate limiting to prevent brute force attacks.
    """
    client_ip = get_client_ip(request)
    if rate_limiter.is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait a moment before trying again.",
        )

    settings = get_settings()
    is_valid = verify_secret(payload.token, settings.dashboard_secret)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrator secret token.",
        )

    return {
        "valid": True,
        "role": "admin",
        "message": "Authentication successful.",
    }


# ------------------------------------------------------------------------------
# Guilds API
# ------------------------------------------------------------------------------
@router.get("/api/guilds")
async def list_guilds(is_admin: bool = Depends(check_auth_optional)):
    """
    Lists all Discord guilds the bot belongs to.
    Returns public guilds to unauthenticated users, and all guilds to admins.
    """
    bot = get_bot()
    guilds_list = []

    for guild in bot.guilds:
        guild_cfg = await db.get_or_create_guild_settings(str(guild.id))
        is_public = bool(guild_cfg.get("is_public_dashboard", 0))

        # Show if public or if user authenticated as admin
        if is_public or is_admin:
            guilds_list.append(
                {
                    "id": str(guild.id),
                    "name": guild.name,
                    "icon_url": str(guild.icon.url) if guild.icon else None,
                    "member_count": guild.member_count or len(guild.members),
                    "is_public": is_public,
                }
            )

    return {"guilds": guilds_list}


async def _authorize_guild_access(guild_id: str, is_admin: bool) -> discord.Guild:
    """Helper to check permissions and retrieve guild object."""
    bot = get_bot()
    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discord server not found or bot is not a member.",
        )

    guild_cfg = await db.get_or_create_guild_settings(guild_id)
    is_public = bool(guild_cfg.get("is_public_dashboard", 0))

    if not is_public and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This server dashboard is private. Administrator authentication required.",
        )

    return guild


@router.get("/api/guilds/{guild_id}/stats")
async def get_guild_stats(
    guild_id: str, is_admin: bool = Depends(check_auth_optional)
):
    """Returns complete real-time statistics snapshot for a guild."""
    guild = await _authorize_guild_access(guild_id, is_admin)
    stats = compute_guild_statistics(guild)
    guild_cfg = await db.get_guild_settings(guild_id)

    stats["is_public"] = bool(guild_cfg.get("is_public_dashboard", 0)) if guild_cfg else False
    stats["has_counters"] = bool(guild_cfg and guild_cfg.get("category_id"))
    return stats


@router.get("/api/guilds/{guild_id}/channels")
async def get_guild_channels(
    guild_id: str,
    query: Optional[str] = Query(None, description="Search term for channel or occupant"),
    sort_by: str = Query("occupants_desc", description="Sorting criteria"),
    filter_mode: str = Query("all", description="'all', 'active_only', or 'empty_only'"),
    is_admin: bool = Depends(check_auth_optional),
):
    """Returns filtered and sorted voice channel list."""
    guild = await _authorize_guild_access(guild_id, is_admin)
    stats = compute_guild_statistics(guild)
    channels = filter_and_sort_channels(
        stats["channels"], query=query, sort_by=sort_by, filter_mode=filter_mode
    )
    return {"channels": channels, "total_count": len(channels)}


@router.get("/api/guilds/{guild_id}/leaderboard")
async def get_guild_leaderboard(
    guild_id: str,
    limit: Optional[int] = Query(None, ge=1, le=1000),
    timeframe: str = Query("all", pattern="^(all|daily|weekly)$"),
    is_admin: bool = Depends(check_auth_optional),
):
    """Returns voice members for a guild with live ongoing active sessions for the specified timeframe."""
    await _authorize_guild_access(guild_id, is_admin)
    bot = discord_bot
    guild = None
    if bot and bot.is_ready():
        try:
            guild = bot.get_guild(int(guild_id))
        except (ValueError, TypeError):
            pass
    leaderboard = await db.get_leaderboard(guild_id, limit=limit, guild=guild, timeframe=timeframe)
    return {
        "guild_id": guild_id,
        "timeframe": timeframe,
        "leaderboard": leaderboard,
        "total_ranked": len(leaderboard),
    }


@router.get("/api/guilds/{guild_id}/users/{user_id}/rank")
async def get_user_guild_rank(
    guild_id: str,
    user_id: str,
    timeframe: str = Query("all", pattern="^(all|daily|weekly)$"),
    is_admin: bool = Depends(check_auth_optional),
):
    """Returns a specific user's rank and position in the guild leaderboard for the specified timeframe."""
    guild = await _authorize_guild_access(guild_id, is_admin)
    pos = await db.get_user_leaderboard_position(guild_id, user_id, guild=guild, timeframe=timeframe)
    return pos


@router.get("/api/guilds/{guild_id}/users/{user_id}/stats")
async def get_user_voice_stats(
    guild_id: str,
    user_id: str,
    is_admin: bool = Depends(check_auth_optional),
):
    """Returns detailed real-time voice state breakdown for a specific user."""
    guild = await _authorize_guild_access(guild_id, is_admin)
    member = guild.get_member(int(user_id)) if user_id.isdigit() else None
    breakdown = await db.get_user_voice_breakdown(guild_id, user_id, member)
    return breakdown


@router.post("/api/guilds/{guild_id}/config")
async def update_guild_config(
    guild_id: str,
    config: GuildConfigUpdate,
    _auth: bool = Depends(verify_admin_token),
):
    """Updates guild settings (e.g. toggle public mode). Admin only."""
    bot = get_bot()
    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discord server not found.",
        )

    if config.is_public is not None:
        await db.set_public_mode(guild_id, config.is_public)

    updated = await db.get_guild_settings(guild_id)
    return {"status": "success", "settings": updated}


@router.post("/api/guilds/{guild_id}/refresh")
async def trigger_refresh(
    guild_id: str,
    request: Request,
    is_admin: bool = Depends(check_auth_optional),
):
    """Manually triggers statistics refresh and broadcasts to WebSockets."""
    client_ip = get_client_ip(request)
    if rate_limiter.is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait a moment.",
        )

    guild = await _authorize_guild_access(guild_id, is_admin)
    stats = compute_guild_statistics(guild)

    # Broadcast updated stats to connected WebSockets
    await ws_manager.broadcast_to_guild(
        guild_id,
        {
            "type": "stats_update",
            "guild_id": guild_id,
            "data": stats,
            "timestamp": time.time(),
        },
    )

    return {"status": "success", "stats": stats}


# ------------------------------------------------------------------------------
# WebSocket Real-Time Stream
# ------------------------------------------------------------------------------
@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    guild_id: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
):
    """
    WebSocket endpoint providing real-time voice-state updates,
    heartbeats, and instant synchronizations.
    """
    await ws_manager.connect(websocket, token=token, guild_id=guild_id)

    # If guild_id provided on connect, send immediate initial state if bot ready
    if guild_id and discord_bot and discord_bot.is_ready():
        try:
            guild = discord_bot.get_guild(int(guild_id))
            if guild:
                stats = compute_guild_statistics(guild)
                await ws_manager.send_personal_message(
                    {
                        "type": "initial_state",
                        "guild_id": guild_id,
                        "data": stats,
                        "timestamp": time.time(),
                    },
                    websocket,
                )
        except Exception as e:
            logger.debug("Failed sending initial state via WS: %s", e)

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = raw_text
                import json
                payload = json.loads(msg)
                msg_type = payload.get("type")

                # Handle ping/pong heartbeat
                if msg_type == "ping":
                    await ws_manager.send_personal_message(
                        {"type": "pong", "timestamp": time.time()}, websocket
                    )

                # Handle dynamic guild subscription
                elif msg_type == "subscribe":
                    sub_guild_id = str(payload.get("guild_id", ""))
                    if sub_guild_id:
                        ws_manager.subscribe(websocket, sub_guild_id)
                        # Send refreshed state for new subscription
                        if discord_bot and discord_bot.is_ready():
                            g = discord_bot.get_guild(int(sub_guild_id))
                            if g:
                                stats = compute_guild_statistics(g)
                                await ws_manager.send_personal_message(
                                    {
                                        "type": "initial_state",
                                        "guild_id": sub_guild_id,
                                        "data": stats,
                                        "timestamp": time.time(),
                                    },
                                    websocket,
                                )

                # Handle client manual refresh request
                elif msg_type == "request_sync":
                    req_guild_id = str(payload.get("guild_id", ""))
                    if req_guild_id and discord_bot and discord_bot.is_ready():
                        g = discord_bot.get_guild(int(req_guild_id))
                        if g:
                            stats = compute_guild_statistics(g)
                            await ws_manager.send_personal_message(
                                {
                                    "type": "stats_update",
                                    "guild_id": req_guild_id,
                                    "data": stats,
                                    "timestamp": time.time(),
                                },
                                websocket,
                            )

            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error("Error processing WebSocket message: %s", e)

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.debug("WebSocket connection terminated: %s", e)
        ws_manager.disconnect(websocket)

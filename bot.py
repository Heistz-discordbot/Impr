"""
Cruxer Discord Bot (formerly Imperium) - discord.py 2.x

Install (Components V2 layouts need discord.py 2.6 or newer):
  pip install -U "discord.py>=2.6" aiohttp python-dotenv

Requires the "Message Content Intent" and "Server Members Intent" turned ON in
the Discord Developer Portal (Bot tab).

The bot also needs these server permissions to use every feature below:
Manage Channels, Manage Roles, Manage Webhooks, Manage Guild (for invites),
Kick Members, View Audit Log, Embed Links, Attach Files, Read Message History,
Create Instant Invite (for /nuke-restore to recreate invites).

Data (raid numbers, ticket info, stats, panel/leaderboard message ids,
rate limits, win streaks, saved configuration, the permanent ticket archive)
is stored in cruxer_data.json next to this file, so it survives restarts.
If you already have an imperium_data.json, the bot keeps using it (nothing is
lost by the rename). Set CRUXER_DATA_FILE (or the old IMPERIUM_DATA_FILE) in
your .env to store it somewhere else, for example a persistent volume on your
host - if your host wipes files on every redeploy, you MUST do this or the
global leaderboard will reset.

GLOBAL LEADERBOARD HISTORY is now protected three ways:
  1) a second file (cruxer_global.json, or CRUXER_GLOBAL_FILE) that holds only the global
     totals + the full raid history and is merged back in on every start
  2) it is rebuilt from the permanent ticket archive if the totals ever fall behind
  3) the bot DMs the developer a backup file every 6 hours (and on start-up). If your host
     wipes the disk, upload the latest one with /data-restore and nothing is lost.

Roblox features (/hitlist, /see, /whois, /frnd, join checks, helper warnings,
request verification, automatic server link + region) need ROBLOX_COOKIE in
your .env - the .ROBLOSECURITY cookie of a Roblox account (use a spare/alt
account, never your main). Roblox's presence API refuses anonymous requests.

Optional .env values:
  RALVORA_API_URL / RALVORA_API_KEY  - a region API to try first (see detect_server_region)
  CRUXER_GLOBAL_FILE                 - where the extra global-leaderboard file lives

Changes in this version (latest first):
  - .av [user]  (prefix command, also .avatar / .pfp): avatar + profile of a user
  - /anti-nuke (was /antinuke) now records everything it locks / deletes / revokes, and
    /nuke-restore puts the channels, locks and invites back
  - /hitlist add is instant: runs every lookup in parallel, posts the alert straight away if
    the target is already in a server, fills in the region afterwards, polls every 10s
  - /bot stats can now show the stats of ANY server the bot is in (dropdown, paged) and still
    remove the bot from any of them; shows missing permissions per server
  - Old/legacy (embed) panels are removed automatically on every start and refresh
  - /purge keeps only the newest working panel (or leaderboard) and deletes the older ones
  - Global leaderboard history is permanent (see above); /data-backup and /data-restore
  - /nuke -> "This guy thought he's him lol."
  - /raid panel and /backup panel open the Raid / Backup ticket-making form directly
  - Tickets have an Edit button (requester only) to change region, players, clan and server link
  - After 50 messages in a ticket the bot posts "Ah, this is a real mess." with a
    "Jump to Information" button (repeats every 50 further messages)
  - Ticket info message is now tracked by id (the live duration timer no longer breaks in
    busy tickets)
  Earlier changes (all still included):
  - Renamed Imperium -> Cruxer everywhere (old category/data file still recognised)
  - Request limit is a 24 hour window that starts at the member's first request
  - "Dispatched Successfully" info card after a request is created
  - /frnd request, /see, /hitlist add profile, region auto-detect, farewell channel
  - Atomic data file with .bak backup, corrupt file moved aside
  - Requests need an existing Roblox profile AND a joinable game; verified join link stored
  - One panel/leaderboard copy per channel, on_ready setup runs once
  - #snipe channel for all hitlist/see alerts
  - /whois, /sync, /add, /audit, /view, /link, /unlink, /blacklist, /role,
    /tickets-export, /bot stats, /overview, /member-stats
  - Helper offline warnings (in the ticket after 5 minutes, in #logs at the end)
  - A member's SERVER stats reset when they leave that server (global never resets)
"""

import asyncio
import copy
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import time
import uuid
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ======================= CONFIG =======================

BOT_NAME = "Cruxer"
LEGACY_BOT_NAME = "Imperium"

TOKEN = os.getenv("DISCORD_TOKEN")

DEVELOPER_ID = 1455426573741330495

GUILD_ID = 1554169015662936114
TICKET_CATEGORY_ID = 1554174156671549553
BATTLE_PANEL_CHANNEL_ID = 1554169017592451175
RAID_RESULTS_CHANNEL_ID = 1554468736440729650
LEADERBOARD_CHANNEL_ID = 1554469313782743050
LOGS_CHANNEL_ID = 1554469729769365555
MVPS_CHANNEL_ID = 1554551411214131351
SNIPE_CHANNEL_ID = 1555445538566967296

VIEWER_ROLE_ID = 0
PING_ROLE_ID = 0

EMBED_COLOR = 0x2B2D31

AUTHORIZE_CATEGORY_NAME = BOT_NAME
CATEGORY_NAMES = {BOT_NAME.lower(), LEGACY_BOT_NAME.lower()}  # old servers still have "Imperium"
BATTLE_PANEL_CHANNEL_NAME = "battle-panel"
RAID_RESULTS_CHANNEL_NAME = "raid-results"
LEADERBOARD_CHANNEL_NAME = "leaderboard"
LOGS_CHANNEL_NAME = "logs"
MVPS_CHANNEL_NAME = "mvps"
SNIPE_CHANNEL_NAME = "snipe"
README_CHANNEL_NAME = "read-me"
FAREWELL_CHANNEL_NAME = "cruxer-farewell"

SUPPORT_SERVER_INVITE = None

PREFIXES = (".", "!")  # ".av" works; "!sync" keeps working

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_NEW_DATA = os.path.join(_BASE_DIR, "cruxer_data.json")
_OLD_DATA = os.path.join(_BASE_DIR, "imperium_data.json")
DATA_FILE = (
    os.getenv("CRUXER_DATA_FILE")
    or os.getenv("IMPERIUM_DATA_FILE")
    or (_OLD_DATA if os.path.exists(_OLD_DATA) and not os.path.exists(_NEW_DATA) else _NEW_DATA)
)
BACKUP_FILE = DATA_FILE + ".bak"
GLOBAL_FILE = os.getenv("CRUXER_GLOBAL_FILE") or os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)), "cruxer_global.json")

LEADERBOARD_PAGE_SIZE = 10
DURATION_UPDATE_SECONDS = 30
DAILY_REQUEST_LIMIT = 2
RATE_WINDOW_SECONDS = 24 * 60 * 60  # window starts at the member's first request
ANTINUKE_WINDOW_SECONDS = 600  # look back 10 minutes for recent activity

ROBLOX_COOKIE = os.getenv("ROBLOX_COOKIE")
RALVORA_API_URL = os.getenv("RALVORA_API_URL")
RALVORA_API_KEY = os.getenv("RALVORA_API_KEY")
TRACKER_POLL_SECONDS = 10
MAX_TRACKED_PER_GUILD = 25
OWN = "[OWN] "
JOIN_WARNING_SECONDS = 300      # warn if a helper is still offline this long after joining
JOIN_CHECK_POLL_SECONDS = 30
REQUIRE_LINK = False            # True = members must /link before they can request

TICKET_MESS_THRESHOLD = 50      # "Ah, this is a real mess." after this many messages (then every +50)
MESS_PREFIX = "**Ah, this is a real mess.**"
BACKUP_INTERVAL_HOURS = 6       # DM the developer a data backup this often

# ======================================================


# ======================================================
# DATA STORE
# ======================================================

def load_data():
    data = None
    for path in (DATA_FILE, BACKUP_FILE):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if path == BACKUP_FILE:
                print(f"[{BOT_NAME}] Main data file was unreadable - restored from the backup.")
            break
        except json.JSONDecodeError as exc:
            print(f"[{BOT_NAME}] {path} is corrupt ({exc!r}). Moving it aside, NOT overwriting it.")
            try:
                os.replace(path, f"{path}.corrupt-{int(time.time())}")
            except OSError:
                pass
        except OSError as exc:
            raise SystemExit(f"[{BOT_NAME}] Cannot read {path}: {exc!r}. Refusing to start so no data is lost.")
    if data is None:
        data = {}

    data.setdefault("raid_counters", {})
    data.setdefault("tickets", {})
    data.setdefault("stats", {"guilds": {}, "global": {}})
    data["stats"].setdefault("guilds", {})
    data["stats"].setdefault("global", {})
    data["stats"].setdefault("daily", {})
    data.setdefault("panel_messages", {})
    data.setdefault("leaderboard_messages", {})
    data.setdefault("authorized_guilds", [])
    data.setdefault("request_limits", {})
    data.setdefault("win_streaks", {})
    data.setdefault("configs", {})
    data.setdefault("trackers", {})
    data.setdefault("blacklist", {})
    data.setdefault("audit", [])
    data.setdefault("links", {})
    data.setdefault("pending_links", {})
    data.setdefault("panel_channels", {})
    data.setdefault("leaderboard_channels", {})
    data.setdefault("ticket_archive", {})  # permanent copy of every ticket, never cleared
    data.setdefault("global_history", [])  # permanent, append-only list of every finished raid
    data.setdefault("nuke_snapshots", {})  # what /anti-nuke changed, so /nuke-restore can undo it
    return data


def _atomic_write_json(path: str, payload) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic: the file is never half-written


def save_data():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(DATA_FILE):
        try:
            shutil.copyfile(DATA_FILE, BACKUP_FILE)
        except OSError:
            pass
    os.replace(tmp, DATA_FILE)  # atomic: the file is never half-written


def save_global_file():
    """Second copy of ONLY the global leaderboard totals + full raid history (never reset)."""
    try:
        _atomic_write_json(GLOBAL_FILE, {
            "saved_at": int(time.time()),
            "global": DATA["stats"]["global"],
            "history": DATA["global_history"],
        })
    except OSError as exc:
        print(f"[{BOT_NAME}] Could not write the global history file: {exc!r}")


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


DATA_WAS_FRESH = not (os.path.exists(DATA_FILE) or os.path.exists(BACKUP_FILE))
DATA = load_data()
BOT_START_TIME = int(time.time())


def merge_global_sources():
    """Make sure the global leaderboard can only ever go UP: merge the extra global file and
    rebuild from the permanent ticket archive. Safe to run as often as you like."""
    changed = False
    g = DATA["stats"]["global"]

    gf = _read_json(GLOBAL_FILE) or {}
    for uid, count in (gf.get("global") or {}).items():
        if isinstance(count, int) and count > g.get(uid, 0):
            g[uid] = count
            changed = True

    known = {(h.get("ts"), h.get("guild_id"), h.get("raid_number")) for h in DATA["global_history"]}
    for h in gf.get("history") or []:
        key = (h.get("ts"), h.get("guild_id"), h.get("raid_number"))
        if key not in known:
            DATA["global_history"].append(h)
            known.add(key)
            changed = True

    tally: dict[str, int] = {}
    for t in DATA["ticket_archive"].values():
        if t.get("ended_at") and t.get("result"):
            for uid in t.get("helper_order", []):
                tally[uid] = tally.get(uid, 0) + 1
    for uid, count in tally.items():
        if count > g.get(uid, 0):
            g[uid] = count
            changed = True

    DATA["global_history"].sort(key=lambda h: h.get("ts", 0))
    if changed:
        save_data()
    save_global_file()


merge_global_sources()


def next_raid_number(guild_id: int) -> int:
    gid = str(guild_id)
    DATA["raid_counters"][gid] = DATA["raid_counters"].get(gid, 0) + 1
    save_data()
    return DATA["raid_counters"][gid]


def get_ticket(channel_id: int):
    return DATA["tickets"].get(str(channel_id))


def save_ticket(channel_id: int, ticket: dict):
    DATA["tickets"][str(channel_id)] = ticket
    # Permanent archive: survives /deauthorize and ticket deletion.
    snapshot = copy.deepcopy(ticket)
    snapshot["channel_id"] = channel_id
    snapshot["archived_at"] = int(time.time())
    DATA["ticket_archive"][f"{ticket['guild_id']}:{ticket['raid_number']}"] = snapshot
    save_data()


def today_str() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def add_raid_credit(guild_id: int, user_ids, raid_number=None, result=None, guild_name=None):
    gid = str(guild_id)
    guild_stats = DATA["stats"]["guilds"].setdefault(gid, {})
    global_stats = DATA["stats"]["global"]

    # Daily stats: keep only today's bucket so the file does not grow forever.
    today = today_str()
    daily_for_guild = DATA["stats"]["daily"].setdefault(gid, {})
    for old_day in [d for d in daily_for_guild if d != today]:
        daily_for_guild.pop(old_day, None)
    daily_stats = daily_for_guild.setdefault(today, {})

    for uid in user_ids:
        uid = str(uid)
        guild_stats[uid] = guild_stats.get(uid, 0) + 1
        global_stats[uid] = global_stats.get(uid, 0) + 1
        daily_stats[uid] = daily_stats.get(uid, 0) + 1

    # Permanent history entry (never trimmed, never touched by /deauthorize).
    DATA["global_history"].append({
        "ts": int(time.time()),
        "guild_id": guild_id,
        "guild_name": guild_name,
        "raid_number": raid_number,
        "result": result,
        "helpers": [str(u) for u in user_ids],
    })

    save_data()
    save_global_file()


def get_raid_count(guild_id: int, user_id: int, scope: str) -> int:
    if scope == "global":
        return DATA["stats"]["global"].get(str(user_id), 0)
    return DATA["stats"]["guilds"].get(str(guild_id), {}).get(str(user_id), 0)


def leaderboard_entries(guild_id: int, scope: str):
    if scope == "global":
        source = DATA["stats"]["global"]
    elif scope == "daily":
        source = DATA["stats"]["daily"].get(str(guild_id), {}).get(today_str(), {})
    else:
        source = DATA["stats"]["guilds"].get(str(guild_id), {})

    entries = [(int(uid), count) for uid, count in source.items() if count > 0]
    entries.sort(key=lambda pair: pair[1], reverse=True)
    return entries


def get_rank(guild_id: int, user_id: int, scope: str):
    entries = leaderboard_entries(guild_id, scope)
    for i, (uid, count) in enumerate(entries):
        if uid == user_id:
            return i + 1, count, len(entries)
    return None, 0, len(entries)


# ---- request limit: a 24h window that starts at the member's FIRST request ----

def _active_window(guild_id: int, user_id: int):
    """Returns the member's live limit entry, or None when there is no active 24h window."""
    entry = DATA["request_limits"].get(str(guild_id), {}).get(str(user_id))
    if not entry or "window_start" not in entry:  # old format (per-day counter) is simply ignored
        return None
    if time.time() >= entry["window_start"] + RATE_WINDOW_SECONDS:
        return None
    return entry


def rate_limit_status(guild_id: int, user_id: int, limit: int = DAILY_REQUEST_LIMIT):
    """Check the limit WITHOUT using up a request. Returns (allowed, used, resets_at_timestamp_or_None)."""
    entry = _active_window(guild_id, user_id)
    if entry is None:
        return True, 0, None
    return entry["count"] < limit, entry["count"], entry["window_start"] + RATE_WINDOW_SECONDS


def check_and_increment_rate_limit(guild_id: int, user_id: int, limit: int = DAILY_REQUEST_LIMIT):
    """Uses one request. Returns (allowed, used, resets_at_timestamp)."""
    entry = _active_window(guild_id, user_id)
    if entry is None:
        entry = {"window_start": int(time.time()), "count": 0}
    resets = entry["window_start"] + RATE_WINDOW_SECONDS

    guild_limits = DATA["request_limits"].setdefault(str(guild_id), {})
    if entry["count"] >= limit:
        guild_limits[str(user_id)] = entry
        save_data()
        return False, entry["count"], resets

    entry["count"] += 1
    guild_limits[str(user_id)] = entry
    save_data()
    return True, entry["count"], resets


def rate_limit_text(count: int, resets) -> str:
    when = f" Your requests reset <t:{resets}:R>." if resets else ""
    return f"Rate limit reached ({count}/{DAILY_REQUEST_LIMIT}).{when}"


def update_win_streak(guild_id: int, result: str) -> int:
    gid = str(guild_id)
    if result == "Won":
        DATA["win_streaks"][gid] = DATA["win_streaks"].get(gid, 0) + 1
    else:
        DATA["win_streaks"][gid] = 0
    save_data()
    return DATA["win_streaks"][gid]


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")

    return " ".join(parts)


def ticket_channel_name(raid_number: int) -> str:
    return f"ticket-no-{raid_number}"


def box(text: str) -> str:
    text = text if text else "None"
    return f"```{text}```"


async def get_roblox_profile(session: aiohttp.ClientSession, username: str):
    try:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if data.get("data"):
                user = data["data"][0]
                return user["id"], user["name"]
    except Exception:
        pass
    return None


def roblox_profile_url(roblox_id):
    if roblox_id:
        return f"https://www.roblox.com/users/{roblox_id}/profile"
    return None


def roblox_display(username: str, roblox_id) -> str:
    url = roblox_profile_url(roblox_id)
    if url:
        return f"[{username}]({url})"
    return f"{username} (not found on Roblox)"


def helper_line(ticket: dict, uid: str) -> str:
    """Joined members only show a redirect to their Roblox profile."""
    info = ticket["helpers"].get(uid, {})
    url = roblox_profile_url(info.get("roblox_id"))
    if url:
        return f"<@{uid}> - [Roblox Profile]({url})"
    return f"<@{uid}> - Profile not found"


def saved_roblox_name(user_id: int):
    link = DATA["links"].get(str(user_id))
    if link:
        return link["roblox_username"]
    return DATA["configs"].get(str(user_id), {}).get("roblox_username")


def find_category(guild: discord.Guild):
    """The ticket category: by id first, then by name (Cruxer, or the old Imperium name)."""
    category = guild.get_channel(TICKET_CATEGORY_ID)
    if isinstance(category, discord.CategoryChannel):
        return category
    return discord.utils.find(
        lambda c: isinstance(c, discord.CategoryChannel) and c.name.lower() in CATEGORY_NAMES,
        guild.categories,
    )


async def delete_previous(channel: discord.TextChannel, msg_key: str, chan_key: str):
    """Delete the stored panel/leaderboard message even if it lives in another channel."""
    gid = str(channel.guild.id)
    old_id = DATA[msg_key].get(gid)
    if not old_id:
        return
    old_channel_id = DATA[chan_key].get(gid)
    old_channel = channel.guild.get_channel(old_channel_id) if old_channel_id else channel
    if old_channel is None:
        old_channel = channel
    try:
        old_msg = await old_channel.fetch_message(old_id)
        await old_msg.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
        pass


# ---- finding / removing old panels and leaderboards (current AND legacy embed ones) ----

_refresh_locks: dict[str, asyncio.Lock] = {}
_readme_locks: dict[int, asyncio.Lock] = {}

PURGE_KEYS = {
    "panel": ("panel_messages", "panel_channels"),
    "leaderboard": ("leaderboard_messages", "leaderboard_channels"),
}
PURGE_MARKERS = {"panel": "tsb:panel:request", "leaderboard": "lb:scope"}
PANEL_HINTS = (
    "choose raid or backup", "backup panel", "rescue ticket", "request for raids",
    "what is the benefit of using this", "create rescue ticket",
)


def _walk_components(components):
    """Yield every component of a message, including ones nested inside Components V2 layouts."""
    stack = list(components or [])
    while stack:
        c = stack.pop()
        yield c
        for attr in ("children", "components"):
            kids = getattr(c, attr, None)
            if kids:
                try:
                    stack.extend(kids)
                except TypeError:
                    pass
        accessory = getattr(c, "accessory", None)
        if accessory:
            stack.append(accessory)


def _component_ids(components) -> set:
    found = set()
    for c in _walk_components(components):
        cid = getattr(c, "custom_id", None)
        if cid:
            found.add(cid)
    return found


def message_blob(msg: discord.Message) -> str:
    """All readable text of a message (content, embeds, V2 text displays, select labels), lowercased."""
    parts = [msg.content or ""]
    for e in msg.embeds:
        parts += [e.title or "", e.description or ""]
        for f in e.fields:
            parts += [f.name or "", f.value or ""]
        if e.footer and e.footer.text:
            parts.append(e.footer.text)
    for c in _walk_components(msg.components):
        content = getattr(c, "content", None)
        if isinstance(content, str):
            parts.append(content)
        placeholder = getattr(c, "placeholder", None)
        if isinstance(placeholder, str):
            parts.append(placeholder)
        for opt in getattr(c, "options", None) or []:
            parts += [str(getattr(opt, "label", "")), str(getattr(opt, "value", ""))]
    return "\n".join(p for p in parts if p).lower()


def msg_is_functional(msg: discord.Message, kind: str) -> bool:
    """A working, current-version panel/leaderboard (Components V2 layout carrying our marker id)."""
    if PURGE_MARKERS[kind] not in _component_ids(msg.components):
        return False
    return bool(getattr(msg.flags, "components_v2", False))


def msg_looks_like(msg: discord.Message, kind: str) -> bool:
    """Current OR legacy (old embed style) panel / leaderboard."""
    ids = _component_ids(msg.components)
    blob = message_blob(msg)
    if kind == "panel":
        if any(i.startswith("tsb:panel") for i in ids):
            return True
        return any(h in blob for h in PANEL_HINTS)
    if any(i.startswith("lb:") for i in ids):
        return True
    return "leaderboard" in blob


def _is_dedicated(channel: discord.TextChannel, kind: str) -> bool:
    if kind == "panel":
        return channel.id == BATTLE_PANEL_CHANNEL_ID or channel.name.lower() == BATTLE_PANEL_CHANNEL_NAME
    return channel.id == LEADERBOARD_CHANNEL_ID or channel.name.lower() == LEADERBOARD_CHANNEL_NAME


async def purge_old_messages(channel: discord.TextChannel, kind: str):
    """Delete the stored message AND every old/legacy panel (or leaderboard) the bot left in this channel.
    In the bot's own dedicated channel every old bot message is removed."""
    msg_key, chan_key = PURGE_KEYS[kind]
    await delete_previous(channel, msg_key, chan_key)
    wipe_all = _is_dedicated(channel, kind)
    try:
        async for msg in channel.history(limit=200):
            if msg.author.id != bot.user.id:
                continue
            if wipe_all or msg_looks_like(msg, kind):
                try:
                    await msg.delete()
                    leaderboard_state.pop(msg.id, None)
                except discord.HTTPException:
                    pass
    except (discord.Forbidden, discord.HTTPException):
        pass


async def blacklist_gate(interaction: discord.Interaction) -> bool:
    """Returns False (and tells the user) when the member is blacklisted."""
    if interaction.user.id == DEVELOPER_ID:
        return True
    entry = DATA["blacklist"].get(str(interaction.user.id))
    if not entry:
        return True

    text = f"You are blacklisted from using {BOT_NAME}.\n**Reason:** {entry.get('reason', 'No reason given')}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass
    return False


class BlacklistGate:
    """Mixin for views: blocks blacklisted members from pressing anything."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await blacklist_gate(interaction)


# background tasks must be referenced or Python may garbage-collect them mid-run
_bg_tasks: set = set()
_processed_prefix_messages: dict[int, float] = {}
_PREFIX_DEDUPE_SECONDS = 10


def mark_prefix_message_seen(message_id: int) -> bool:
    now = time.time()
    stale = [
        mid for mid, ts in _processed_prefix_messages.items()
        if now - ts > _PREFIX_DEDUPE_SECONDS
    ]
    for mid in stale:
        _processed_prefix_messages.pop(mid, None)
    if message_id in _processed_prefix_messages:
        return False
    _processed_prefix_messages[message_id] = now
    return True


def spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


# ======================================================
# READ ME
# ======================================================

async def create_or_update_readme(guild: discord.Guild, authorized: bool):
    """Create/update exactly one Cruxer read-me channel per guild."""
    lock = _readme_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        try:
            channels = [
                c for c in guild.text_channels
                if c.name.lower() == README_CHANNEL_NAME
            ]

            existing = next(
                (
                    c for c in channels
                    if (c.topic or "").lower() == f"information about {BOT_NAME}".lower()
                ),
                channels[0] if channels else None,
            )

            # Remove duplicate Cruxer-owned read-me channels.
            if existing:
                for duplicate in channels:
                    if duplicate.id == existing.id:
                        continue
                    if (duplicate.topic or "").lower() == f"information about {BOT_NAME}".lower():
                        try:
                            await duplicate.delete(
                                reason=f"{BOT_NAME}: remove duplicate read-me channel"
                            )
                        except (discord.Forbidden, discord.HTTPException):
                            pass

            embed = build_readme_embed(guild, authorized)
            view = discord.ui.View(timeout=None)
            if SUPPORT_SERVER_INVITE:
                view.add_item(
                    discord.ui.Button(
                        label="Support Server",
                        style=discord.ButtonStyle.link,
                        url=SUPPORT_SERVER_INVITE,
                    )
                )
            view.add_item(
                discord.ui.Button(
                    label="Open Developer Profile",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/users/{DEVELOPER_ID}",
                )
            )

            if existing:
                async for msg in existing.history(limit=20):
                    if msg.author == guild.me and msg.embeds:
                        await msg.edit(embed=embed, view=view)
                        return existing
                await existing.send(embed=embed, view=view)
                return existing

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    embed_links=True,
                    manage_channels=True,
                ),
            }
            channel = await guild.create_text_channel(
                name=README_CHANNEL_NAME,
                overwrites=overwrites,
                topic=f"Information about {BOT_NAME}",
                reason=f"{BOT_NAME} Read-me channel",
            )
            await channel.send(embed=embed, view=view)
            return channel

        except discord.Forbidden:
            return None
        except discord.HTTPException as exc:
            print(f"[{BOT_NAME}] Could not create/update Read-me in {guild.name}: {exc}")
            return None


def build_readme_embed(guild: discord.Guild, authorized: bool) -> discord.Embed:
    if authorized:
        description = (
            f"Thank you for authorizing {BOT_NAME} in {guild.name}.\n\n"
            f"{BOT_NAME} is a raid coordination bot. It lets members request a raid "
            "or backup, opens a private ticket for the request, tracks who joins, "
            "and records the result once the raid is over.\n\n"
            f"For questions or support, contact the developer: <@{DEVELOPER_ID}>."
        )
    else:
        description = (
            f"Hello. Thank you for inviting me to {guild.name}.\n\n"
            "This is a premium bot and this server is currently unauthorized.\n\n"
            "To authorize your server, please join the support server and "
            "contact the developer to get access.\n\n"
            f"{BOT_NAME} is a raid coordination bot. Once authorized, it lets "
            "members request a raid or backup, opens a ticket for the request, "
            "tracks who joins, and records the result once the raid is over."
        )

    embed = discord.Embed(title=BOT_NAME, description=description, color=EMBED_COLOR)
    embed.add_field(name="Developer", value=f"<@{DEVELOPER_ID}>", inline=True)
    embed.add_field(name="Status", value="Authorized" if authorized else "Unauthorized", inline=True)
    embed.set_footer(text=BOT_NAME)
    return embed


# ======================================================
# FAREWELL (when the bot is removed)
# ======================================================

def build_farewell_embed(guild_name: str) -> discord.Embed:
    return discord.Embed(
        title=f"Goodbye from {BOT_NAME}",
        description=(
            f"{BOT_NAME} has left **{guild_name}**.\n\n"
            "Thank you for using the bot and for every raid you coordinated with it. "
            f"If you want {BOT_NAME} back, or have any questions, contact the developer: <@{DEVELOPER_ID}>."
        ),
        color=EMBED_COLOR,
    )


async def send_farewell(guild: discord.Guild):
    """Create a read-only farewell channel and post the goodbye message (used right before the bot leaves)."""
    try:
        channel = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == FAREWELL_CHANNEL_NAME,
            guild.text_channels,
        )
        if channel is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
            }
            channel = await guild.create_text_channel(
                name=FAREWELL_CHANNEL_NAME,
                overwrites=overwrites,
                topic=f"A goodbye from {BOT_NAME}",
                reason=f"{BOT_NAME} farewell",
            )
        await channel.send(embed=build_farewell_embed(guild.name))
        return channel
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[{BOT_NAME}] Could not create the farewell channel in {guild.name}: {exc}")
        return None


_self_leaving: set[int] = set()  # servers the bot is leaving on purpose (farewell already sent)


async def leave_with_farewell(guild: discord.Guild):
    await send_farewell(guild)
    _self_leaving.add(guild.id)
    try:
        await guild.leave()
    except discord.HTTPException:
        _self_leaving.discard(guild.id)


# ======================================================
# ROBLOX LOW-LEVEL HELPERS (cookie requests, region detection)
# ======================================================

_roblox_csrf: str | None = None
_presence_warned = False
GAME_NAME_CACHE: dict[int, str] = {}
REGION_CACHE: dict[str, str] = {}


async def roblox_authed_post(session: aiohttp.ClientSession, url: str, payload: dict, extra_headers: dict | None = None):
    """POST with the bot's Roblox cookie + CSRF handling. Returns (status, json_or_None, headers)."""
    global _roblox_csrf
    if not ROBLOX_COOKIE:
        return None, None, {}

    headers = {"Content-Type": "application/json", "Cookie": f".ROBLOSECURITY={ROBLOX_COOKIE}"}
    if extra_headers:
        headers.update(extra_headers)

    for attempt in range(2):
        if _roblox_csrf:
            headers["X-CSRF-TOKEN"] = _roblox_csrf
        try:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 403 and resp.headers.get("x-csrf-token") and attempt == 0:
                    _roblox_csrf = resp.headers["x-csrf-token"]
                    continue
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                return resp.status, data, dict(resp.headers)
        except Exception as exc:
            print(f"[{BOT_NAME}] Roblox POST {url} failed: {exc}")
            return None, None, {}
    return None, None, {}


async def geolocate_ip(session: aiohttp.ClientSession, ip: str):
    data = await roblox_json(session, f"https://get.geojs.io/v1/ip/geo/{ip}.json")
    if not data:
        return None
    parts = []
    for key in ("city", "region", "country_code"):
        value = (data.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or None


async def detect_server_region(session: aiohttp.ClientSession, place_id, job_id):
    """Best-effort region for a live Roblox server. Returns text like "Ashburn, Virginia, US" or None.

    1) RALVORA_API_URL (if you set one in .env) is tried first. I could not find public docs for
       a "Ralvora" API, so the request/response shape below is a guess: GET ?placeId=..&jobId=..
       and the first of region / server_region / location / country in the JSON is used.
       Edit _try_ralvora() once you have the real docs.
    2) Fallback that needs nothing extra: ask Roblox's gamejoin endpoint (with the bot's cookie)
       for the server's datacenter IP and geolocate that IP. This is the same trick Roblox server
       region browser extensions use. It is approximate - Roblox datacenter IPs do not always
       map to the exact city.
    """
    if not place_id or not job_id:
        return None
    job_id = str(job_id).lower()
    if job_id in REGION_CACHE:
        return REGION_CACHE[job_id]

    region = await _try_ralvora(session, place_id, job_id)
    if not region:
        region = await _try_gamejoin_region(session, place_id, job_id)
    if region:
        REGION_CACHE[job_id] = region
        if len(REGION_CACHE) > 500:
            REGION_CACHE.pop(next(iter(REGION_CACHE)))
    return region


async def _try_ralvora(session: aiohttp.ClientSession, place_id, job_id):
    if not RALVORA_API_URL:
        return None
    headers = {"Authorization": f"Bearer {RALVORA_API_KEY}"} if RALVORA_API_KEY else {}
    data = await roblox_json(session, RALVORA_API_URL, params={"placeId": place_id, "jobId": job_id}, headers=headers)
    if isinstance(data, dict):
        for key in ("region", "server_region", "location", "country"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


async def _try_gamejoin_region(session: aiohttp.ClientSession, place_id, job_id):
    if not ROBLOX_COOKIE:
        return None
    status, data, _ = await roblox_authed_post(
        session,
        "https://gamejoin.roblox.com/v1/join-game-instance",
        {
            "placeId": int(place_id),
            "isTeleport": False,
            "gameId": str(job_id),
            "gameJoinAttemptId": str(uuid.uuid4()),
            "joinOrigin": "PlayButton",
        },
        extra_headers={"User-Agent": "Roblox/WinInet", "Referer": f"https://www.roblox.com/games/{place_id}"},
    )
    if status != 200 or not isinstance(data, dict):
        return None
    script = data.get("joinScript") or {}
    ip = None
    endpoints = script.get("UdmuxEndpoints") or []
    if endpoints and isinstance(endpoints[0], dict):
        ip = endpoints[0].get("Address")
    ip = ip or script.get("MachineAddress")
    if not ip:
        return None
    return await geolocate_ip(session, ip)


# ======================================================
# BATTLE PANEL (Components V2 layout + Raid/Backup select)
# ======================================================

class ConfigModal(discord.ui.Modal, title="Configuration"):
    def __init__(self, user_id: int):
        super().__init__()
        saved = saved_roblox_name(user_id)
        self.username = discord.ui.TextInput(
            label="Your Roblox username",
            default=saved,
            placeholder="Saved and autofilled on future requests",
            max_length=40,
        )
        self.add_item(self.username)
        self.region = discord.ui.TextInput(
            label="Default server region (optional)",
            default=DATA["configs"].get(str(user_id), {}).get("region"),
            placeholder="e.g. NA, EU, AS, OCE",
            required=False,
            max_length=30,
        )
        self.add_item(self.region)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        typed = self.username.value.strip()
        roblox = await get_roblox_profile(interaction.client.session, typed)
        name = roblox[1] if roblox else typed

        DATA["configs"][str(interaction.user.id)] = {"roblox_username": name, "region": self.region.value.strip()}
        save_data()

        if roblox:
            await interaction.followup.send(f"Configuration saved. Roblox username: **{name}**.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"Saved **{name}**, but I could not find that account on Roblox. "
                "Press Configuration again to edit it.",
                ephemeral=True,
            )


async def start_request_flow(interaction: discord.Interaction, request_type: str):
    """The ticket-making process: rate limit -> joins/in-game check -> request form.
    Used by the panel dropdown, /raid panel and /backup panel. Must answer the interaction itself."""
    if interaction.guild is None:
        return await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
    if find_category(interaction.guild) is None:
        return await interaction.response.send_message(
            f"{BOT_NAME} is not authorized in this server yet. Ask the developer to run /authorize.", ephemeral=True
        )

    allowed, count, resets = rate_limit_status(interaction.guild.id, interaction.user.id)
    if not allowed:
        return await interaction.response.send_message(rate_limit_text(count, resets), ephemeral=True)

    state, prefilled_link = "skip", None
    try:
        state, prefilled_link = await asyncio.wait_for(join_check(interaction.user.id), timeout=2.2)
    except Exception:
        state, prefilled_link = "skip", None

    if state == "blocked":
        await interaction.response.send_message(view=JoinCheckView(request_type, interaction.user.id), ephemeral=True)
    else:
        await interaction.response.send_modal(RequestModal(request_type, interaction.user.id, prefilled_link))


class PanelView(BlacklistGate, discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        config_btn = discord.ui.Button(
            label="Configuration", style=discord.ButtonStyle.secondary, custom_id="tsb:panel:config"
        )
        config_btn.callback = self.config_callback

        request_select = discord.ui.Select(
            placeholder="Choose Raid or Backup",
            custom_id="tsb:panel:request",
            options=[
                discord.SelectOption(label="Raid", value="Raid", description="Need people to raid with you"),
                discord.SelectOption(label="Backup", value="Backup", description="Got teamed on, need support"),
            ],
        )

        async def select_callback(interaction: discord.Interaction):
            request_type = request_select.values[0]
            await start_request_flow(interaction, request_type)

            # Put the select back to "Choose Raid or Backup" so the same option can be picked again.
            try:
                await interaction.message.edit(view=PanelView())
            except discord.HTTPException:
                pass

        request_select.callback = select_callback

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "## What is this ❓\n"
                "- This is a backup panel. This is where you can **request** for **raids**, simply asking "
                "for help when getting teamed on especially in The Strongest Battlegrounds for now. "
                "We will expand to different games soon.\n"
                "- Simply choose **Raid** or **Backup** from the menu below and move further."
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "## What is the benefit of using this?\n"
                "- Well, talking about **benefits**. There is a lot of benefits of using this bot.\n"
                "- Every request gets its own **private ticket**, helpers join with **one click**, and "
                "every result is **recorded** for the leaderboard. This bot is made to make things "
                "more organised and easy.\n"
                "- **Try it yourself**. You will know it by your own."
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "## Rules:\n"
                "- **No Fake Alerts**: Fake alerts will result in a blacklist.\n"
                "- **Active Profile**: Request from the Roblox account you are currently using.\n"
                "- **Assist Others**: Earn rescue ranks by helping other players.\n"
                f"- **Limit**: {DAILY_REQUEST_LIMIT} requests per member every 24 hours."
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "## Configuration:\n"
                "- Here you can save some details already before making ticket so it will be autofilled "
                "like your roblox username for further raid requests. Once saved you can further Edit "
                "Configuration by tapping the Configuration button again."
            ),
            discord.ui.ActionRow(config_btn),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "## Create rescue ticket now:\n"
                "- Choose **Raid** or **Backup** from the menu below"
            ),
            discord.ui.ActionRow(request_select),
        )
        self.add_item(container)

    async def config_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ConfigModal(interaction.user.id))


async def refresh_panel(channel: discord.TextChannel):
    gid = str(channel.guild.id)
    lock = _refresh_locks.setdefault(f"panel:{gid}", asyncio.Lock())
    async with lock:
        # Removes the stored panel AND every old / legacy (embed) panel left in this channel.
        await purge_old_messages(channel, "panel")

        new_msg = await channel.send(view=PanelView())
        DATA["panel_messages"][gid] = new_msg.id
        DATA["panel_channels"][gid] = channel.id
        save_data()
        return new_msg


# ======================================================
# REQUEST FORM
# ======================================================

def is_roblox_url(link: str) -> bool:
    return bool(re.match(r"^https?://([a-z0-9-]+\.)?roblox\.com/", link, re.I))


def parse_roblox_link(link: str):
    """Returns (place_id, job_id) found inside a Roblox link; either may be None."""
    place = re.search(r"placeId=(\d+)", link) or re.search(r"roblox\.com/(?:[a-z\-]+/)?games/(\d+)", link, re.I)
    job = re.search(r"gameInstanceId=([0-9a-fA-F\-]{8,})", link)
    return (place.group(1) if place else None, job.group(1) if job else None)


async def verify_requester(session: aiohttp.ClientSession, roblox_id: int, typed_link: str):
    """Returns (ok, error_message, verified_join_link). typed_link may be empty (linked members)."""
    if not ROBLOX_COOKIE:
        return False, "Server verification is offline (the bot has no ROBLOX_COOKIE). Tell the developer.", None

    presences = await roblox_presence(session, [roblox_id])
    if presences is None:
        return False, "I could not reach Roblox to check that you are in a game. Try again in a moment.", None

    p = presences.get(roblox_id)
    if not presence_in_game(p):
        return False, "You are not in a Roblox game right now. Join your game first, then make the request.", None

    place_id = p.get("placeId") or p.get("rootPlaceId")
    job_id = p.get("gameId")
    if not place_id or not job_id:
        return False, (
            "You are in a game, but your joins are off (or you are in a private server), so helpers cannot join you. "
            "Set your experience joins to **Everyone** in Roblox privacy settings, then try again."
        ), None

    link_place, link_job = parse_roblox_link(typed_link or "")
    if link_place and str(link_place) != str(place_id):
        return False, "That server link is for a different game than the one you are in.", None
    if link_job and link_job.lower() != str(job_id).lower():
        return False, "That server link is not the server you are currently in. Copy the link from your current server.", None

    return True, None, f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={job_id}"


class DispatchView(discord.ui.LayoutView):
    """The 'Request Dispatched Successfully' card the member sees after making a ticket."""

    def __init__(self, request_type: str, raid_number: int, channel: discord.TextChannel, used: int, resets: int):
        super().__init__(timeout=None)
        remaining = max(0, DAILY_REQUEST_LIMIT - used)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"**{request_type} Request [#{raid_number}] Dispatched Successfully!**\n\n"
                f"- **Ticket Channel:** {channel.mention}\n"
                f"- **Usage Status:** `{used}/{DAILY_REQUEST_LIMIT} used ({remaining} remaining)`\n"
                f"- **Usage Resets:** <t:{resets}:R>\n\n"
                "-# All helpers have been notified. Tap the channel link above to view your live ticket."
            ),
        ))


class RequestModal(discord.ui.Modal):
    def __init__(self, request_type: str, user_id: int = 0, server_link_default: str | None = None):
        super().__init__(title=f"{request_type} Request")
        self.request_type = request_type

        # Linked members are never asked for a server link: it is read from the server they are in.
        self.auto_link = bool(DATA["links"].get(str(user_id))) and bool(ROBLOX_COOKIE)

        self.region = discord.ui.TextInput(
            label="Server region (empty = auto-detect)",
            default=DATA["configs"].get(str(user_id), {}).get("region") or None,
            placeholder="e.g. NA, EU, AS, OCE - or leave empty",
            required=False,
            max_length=30,
        )
        self.reported_players = discord.ui.TextInput(
            label="Reported players / details",
            style=discord.TextStyle.paragraph,
            placeholder="Names of teamers, what happened, etc.",
            max_length=500,
        )
        saved_username = saved_roblox_name(user_id)
        self.username = discord.ui.TextInput(label="Your Roblox username", default=saved_username, max_length=40)

        self.add_item(self.region)
        self.add_item(self.reported_players)

        self.clan = None
        if request_type == "Raid":
            self.clan = discord.ui.TextInput(label="Enemy guild / clan", required=False, max_length=60)
            self.add_item(self.clan)

        self.server_link = None
        if not self.auto_link:
            self.server_link = discord.ui.TextInput(
                label="Server link",
                default=server_link_default,
                placeholder="https://www.roblox.com/games/...",
                max_length=300,
            )
            self.add_item(self.server_link)

        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction):
        link = self.server_link.value.strip() if self.server_link else ""
        if self.server_link and not is_roblox_url(link):
            return await interaction.response.send_message(
                "Server link must be a valid roblox.com link. Try again.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("This can only be used in a server.", ephemeral=True)

        category = find_category(guild)
        if category is None:
            return await interaction.followup.send(
                f"{BOT_NAME} is not authorized in this server yet. Run /authorize first.", ephemeral=True
            )

        for existing in category.text_channels:
            ticket = get_ticket(existing.id)
            if ticket and ticket["status"] == "open" and ticket["requester_id"] == interaction.user.id:
                return await interaction.followup.send(
                    f"You already have an open request: {existing.mention}", ephemeral=True
                )

        allowed, count, resets = rate_limit_status(guild.id, interaction.user.id)
        if not allowed:
            return await interaction.followup.send(rate_limit_text(count, resets), ephemeral=True)

        # 1) The Roblox profile must exist
        typed_name = self.username.value.strip().lstrip("@")
        roblox = await get_roblox_profile(interaction.client.session, typed_name)
        if not roblox:
            return await interaction.followup.send(
                f"I could not find a Roblox account named **{typed_name}**. Check the spelling and try again.",
                ephemeral=True,
            )
        roblox_id, roblox_name = roblox

        # 2) They must be in a joinable game right now (and the typed link, if any, must match it)
        ok, error, verified_link = await verify_requester(interaction.client.session, roblox_id, link)
        if not ok:
            return await interaction.followup.send(error, ephemeral=True)
        link = verified_link  # store the verified live join link

        # 3) Region: use what they typed, otherwise detect it from the live server
        region = self.region.value.strip()
        if not region:
            place_id, job_id = parse_roblox_link(link)
            region = await detect_server_region(interaction.client.session, place_id, job_id) or "Unknown"

        # 4) Only now use up one of their requests
        allowed, count, resets = check_and_increment_rate_limit(guild.id, interaction.user.id)
        if not allowed:
            return await interaction.followup.send(rate_limit_text(count, resets), ephemeral=True)

        raid_number = next_raid_number(guild.id)

        viewer = guild.get_role(VIEWER_ROLE_ID) or guild.default_role
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            viewer: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, manage_channels=True, attach_files=True),
        }

        try:
            channel = await guild.create_text_channel(
                name=ticket_channel_name(raid_number),
                category=category,
                overwrites=overwrites,
                topic=f"requester:{interaction.user.id}|raid:{raid_number}",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                f"I'm missing permissions. Give {BOT_NAME} Manage Channels and try again.", ephemeral=True
            )

        ticket = {
            "guild_id": guild.id,
            "raid_number": raid_number,
            "type": self.request_type,
            "requester_id": interaction.user.id,
            "roblox_username": roblox_name,
            "roblox_id": roblox_id,
            "region": region,
            "clan": self.clan.value if self.clan else None,
            "reported_players": self.reported_players.value,
            "server_link": link,
            "started_at": int(time.time()),
            "helpers": {},
            "helper_order": [],
            "status": "open",
            "result": None,
            "experience": None,
            "gank_url": None,
            "ended_at": None,
            "ended_by": None,
            "deleted_by": None,
            "results_url": None,
            "info_message_id": None,
            "message_count": 0,
            "edit_btn": True,
        }
        save_ticket(channel.id, ticket)

        embed = build_ticket_embed(ticket)
        ping = f"<@&{PING_ROLE_ID}>" if PING_ROLE_ID else None
        profile_url = roblox_profile_url(roblox_id)

        ticket_msg = await channel.send(
            content=ping,
            embed=embed,
            view=TicketView(channel.id, profile_url),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        ticket["info_message_id"] = ticket_msg.id
        save_ticket(channel.id, ticket)

        await log_ticket_created(guild, ticket, ticket_msg.jump_url, profile_url)

        await interaction.followup.send(
            view=DispatchView(self.request_type, raid_number, channel, count, resets), ephemeral=True
        )


def build_ticket_embed(ticket: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"{ticket['type']} Ticket  #{ticket['raid_number']}",
        description=(
            f"<@{ticket['requester_id']}> ({roblox_display(ticket['roblox_username'], ticket['roblox_id'])}) "
            "will assist. Double-check all details before joining."
        ),
        color=EMBED_COLOR,
    )

    embed.add_field(name="SERVER REGION", value=box(ticket["region"]), inline=True)
    if ticket["type"] == "Raid":
        embed.add_field(name="ENEMY GUILD", value=box(ticket["clan"]), inline=True)

    embed.add_field(name="REPORTED PLAYERS", value=box(ticket["reported_players"]), inline=False)
    embed.add_field(name="SERVER LINK", value=ticket["server_link"], inline=False)

    elapsed = format_duration(int(time.time()) - ticket["started_at"])
    embed.add_field(name="DURATION", value=box(elapsed), inline=False)

    if ticket["helper_order"]:
        helpers_text = "\n".join(helper_line(ticket, uid) for uid in ticket["helper_order"])
    else:
        helpers_text = "No helpers yet."
    embed.add_field(name="HELPERS", value=helpers_text, inline=False)

    footer = BOT_NAME + (" - edited by the requester" if ticket.get("edited_at") else "")
    embed.set_footer(text=footer)
    return embed


# ---- the ticket's information message (tracked by id so it can always be found) ----

async def locate_info_message_id(channel: discord.TextChannel, ticket: dict):
    mid = ticket.get("info_message_id")
    if mid:
        return mid
    try:
        async for msg in channel.history(limit=15, oldest_first=True):
            if msg.author.id == bot.user.id and msg.embeds:
                ticket["info_message_id"] = msg.id
                save_ticket(channel.id, ticket)
                return msg.id
    except discord.HTTPException:
        pass
    return None


async def refresh_ticket_message(channel: discord.TextChannel, ticket: dict, view=None) -> bool:
    """Re-render the ticket information embed (duration, helpers, edits) on its own message."""
    mid = await locate_info_message_id(channel, ticket)
    if not mid:
        return False
    kwargs = {"embed": build_ticket_embed(ticket)}
    if view is not None:
        kwargs["view"] = view
    try:
        await channel.get_partial_message(mid).edit(**kwargs)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


# ======================================================
# EDIT REQUEST MODAL (requester changes the ticket information)
# ======================================================

class EditRequestModal(discord.ui.Modal, title="Edit Request"):
    def __init__(self, channel_id: int, ticket: dict):
        super().__init__()
        self.channel_id = channel_id

        region_default = ticket["region"] if ticket.get("region") not in (None, "Unknown") else None
        self.region = discord.ui.TextInput(
            label="Server region (empty = auto-detect)",
            default=region_default[:30] if region_default else None,
            required=False,
            max_length=30,
        )
        self.reported = discord.ui.TextInput(
            label="Reported players / details",
            style=discord.TextStyle.paragraph,
            default=(ticket.get("reported_players") or "")[:500] or None,
            max_length=500,
        )
        self.add_item(self.region)
        self.add_item(self.reported)

        self.clan = None
        if ticket["type"] == "Raid":
            self.clan = discord.ui.TextInput(
                label="Enemy guild / clan",
                default=(ticket.get("clan") or "")[:60] or None,
                required=False,
                max_length=60,
            )
            self.add_item(self.clan)

        self.server_link = discord.ui.TextInput(
            label="Server link (change it only if you moved)",
            default=(ticket.get("server_link") or "")[:300] or None,
            max_length=300,
        )
        self.add_item(self.server_link)

    async def on_submit(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket or ticket["status"] != "open":
            return await interaction.response.send_message("This ticket is not open anymore.", ephemeral=True)
        if interaction.user.id not in (ticket["requester_id"], DEVELOPER_ID):
            return await interaction.response.send_message("Only the requester can edit this.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        session = interaction.client.session
        changes = []

        # server link: if it changed it must be a real roblox.com link; with a cookie it is re-verified live
        link = self.server_link.value.strip()
        if link != ticket["server_link"]:
            if not is_roblox_url(link):
                return await interaction.followup.send("Server link must be a valid roblox.com link.", ephemeral=True)
            if ROBLOX_COOKIE:
                ok, error, verified = await verify_requester(session, ticket["roblox_id"], link)
                if not ok:
                    return await interaction.followup.send(error, ephemeral=True)
                link = verified
            if link != ticket["server_link"]:
                ticket["server_link"] = link
                changes.append("Server link")

        # region: empty = detect from the (possibly new) server
        region = self.region.value.strip()
        if not region:
            place_id, job_id = parse_roblox_link(ticket["server_link"])
            region = await detect_server_region(session, place_id, job_id) or ticket.get("region") or "Unknown"
        if region != ticket.get("region"):
            ticket["region"] = region
            changes.append("Region")

        reported = self.reported.value.strip()
        if reported != ticket.get("reported_players"):
            ticket["reported_players"] = reported
            changes.append("Reported players")

        if self.clan is not None:
            clan = self.clan.value.strip() or None
            if clan != ticket.get("clan"):
                ticket["clan"] = clan
                changes.append("Enemy guild")

        if not changes:
            return await interaction.followup.send("Nothing was changed.", ephemeral=True)

        ticket["edited_at"] = int(time.time())
        ticket.setdefault("edit_history", []).append({"ts": ticket["edited_at"], "by": interaction.user.id, "fields": changes})
        save_ticket(self.channel_id, ticket)

        await refresh_ticket_message(interaction.channel, ticket)
        try:
            await interaction.channel.send(
                f"<@{interaction.user.id}> updated the request: **{', '.join(changes)}**.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass
        await interaction.followup.send("Request updated.", ephemeral=True)


# ======================================================
# JOIN RAID MODAL
# ======================================================

class JoinRobloxModal(discord.ui.Modal, title="Join Raid"):
    def __init__(self, channel_id: int, user_id: int = 0):
        super().__init__()
        self.channel_id = channel_id
        saved_username = saved_roblox_name(user_id)
        self.username = discord.ui.TextInput(label="Your Roblox username", default=saved_username, max_length=40)
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket or ticket["status"] != "open":
            return await interaction.response.send_message("This ticket is not open anymore.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        typed_name = self.username.value.strip()
        roblox = await get_roblox_profile(interaction.client.session, typed_name)
        roblox_id = roblox[0] if roblox else None
        roblox_name = roblox[1] if roblox else typed_name

        uid = str(interaction.user.id)
        ticket["helpers"][uid] = {
            "username": roblox_name,
            "roblox_id": roblox_id,
            "joined_at": int(time.time()),
            "seen_online": False,
            "warned": False,
        }
        if uid not in ticket["helper_order"]:
            ticket["helper_order"].append(uid)
        save_ticket(self.channel_id, ticket)

        channel = interaction.channel
        try:
            await channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        except discord.Forbidden:
            pass

        await refresh_ticket_message(channel, ticket)

        # Only a redirect to the member's Roblox profile - no username/bio text.
        profile_url = roblox_profile_url(roblox_id)
        join_view = None
        if profile_url:
            join_view = discord.ui.View(timeout=None)
            join_view.add_item(discord.ui.Button(label="Roblox Profile", style=discord.ButtonStyle.link, url=profile_url))
        await channel.send(f"<@{interaction.user.id}> joined the raid.", view=join_view)

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Open Server", style=discord.ButtonStyle.link, url=ticket["server_link"]))
        await interaction.followup.send("You're in. Press the button below to open the server.", view=view, ephemeral=True)


# ======================================================
# TICKET VIEW (Join Raid / End / Edit)
# ======================================================

class TicketView(BlacklistGate, discord.ui.View):
    def __init__(self, channel_id: int, profile_url: str | None = None):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        join_btn = discord.ui.Button(label="Join Raid", style=discord.ButtonStyle.success, custom_id=f"tsb:join:{channel_id}")
        join_btn.callback = self.join_callback
        self.add_item(join_btn)

        end_btn = discord.ui.Button(label="End", style=discord.ButtonStyle.danger, custom_id=f"tsb:end:{channel_id}")
        end_btn.callback = self.end_callback
        self.add_item(end_btn)

        edit_btn = discord.ui.Button(label="Edit", style=discord.ButtonStyle.secondary, custom_id=f"tsb:edit:{channel_id}")
        edit_btn.callback = self.edit_callback
        self.add_item(edit_btn)

        if profile_url:
            self.add_item(discord.ui.Button(label="Open Profile", style=discord.ButtonStyle.link, url=profile_url))

    async def join_callback(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket or ticket["status"] != "open":
            return await interaction.response.send_message("This ticket is not open anymore.", ephemeral=True)
        if interaction.user.id == ticket["requester_id"]:
            return await interaction.response.send_message("This is your own request.", ephemeral=True)
        if str(interaction.user.id) in ticket["helpers"]:
            return await interaction.response.send_message("You already joined this raid.", ephemeral=True)
        await interaction.response.send_modal(JoinRobloxModal(self.channel_id, interaction.user.id))

    async def end_callback(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket or ticket["status"] != "open":
            return await interaction.response.send_message("This ticket is not open anymore.", ephemeral=True)

        is_owner = interaction.user.id == ticket["requester_id"]
        if not is_owner and not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("Only the requester or staff can end this.", ephemeral=True)

        await interaction.response.send_message("Choose the raid result.", view=RaidResultView(self.channel_id))

    async def edit_callback(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket or ticket["status"] != "open":
            return await interaction.response.send_message("This ticket is not open anymore.", ephemeral=True)
        if interaction.user.id not in (ticket["requester_id"], DEVELOPER_ID):
            return await interaction.response.send_message("Only the person who made this request can edit it.", ephemeral=True)
        await interaction.response.send_modal(EditRequestModal(self.channel_id, ticket))


# ======================================================
# END FLOW: RESULT -> EXPERIENCE -> GANK -> FINISH -> DELETE
# ======================================================

class RaidResultView(BlacklistGate, discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        for label, style in (("Won", discord.ButtonStyle.success), ("Lost", discord.ButtonStyle.danger), ("Voided", discord.ButtonStyle.secondary)):
            btn = discord.ui.Button(label=label, style=style)
            btn.callback = self._make_callback(label)
            self.add_item(btn)

    def _make_callback(self, result: str):
        async def callback(interaction: discord.Interaction):
            ticket = get_ticket(self.channel_id)
            if not ticket:
                return await interaction.response.send_message("Ticket data not found.", ephemeral=True)
            ticket["result"] = result
            ticket["ended_by"] = interaction.user.id
            save_ticket(self.channel_id, ticket)
            await interaction.response.send_modal(RaidExperienceModal(self.channel_id))
        return callback


class RaidExperienceModal(discord.ui.Modal, title="Raid Experience"):
    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id
        self.experience = discord.ui.TextInput(
            label="How was the raid experience?", style=discord.TextStyle.paragraph, max_length=500
        )
        self.add_item(self.experience)

    async def on_submit(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("Ticket data not found.", ephemeral=True)

        ticket["experience"] = self.experience.value
        save_ticket(self.channel_id, ticket)

        await interaction.response.send_message(
            f"Result recorded: {ticket['result']}. Upload the gank image, then press Finish.",
            view=PostEndView(self.channel_id),
        )


class PostEndView(BlacklistGate, discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        upload_btn = discord.ui.Button(label="Upload Gank", style=discord.ButtonStyle.primary)
        upload_btn.callback = self.upload_callback
        self.add_item(upload_btn)

        finish_btn = discord.ui.Button(label="Finish", style=discord.ButtonStyle.success)
        finish_btn.callback = self.finish_callback
        self.add_item(finish_btn)

    async def upload_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Send the raid image as an attachment in this channel within 2 minutes.", ephemeral=True
        )

        def check(m: discord.Message):
            return m.channel.id == interaction.channel.id and m.author.id == interaction.user.id and m.attachments

        try:
            msg = await interaction.client.wait_for("message", check=check, timeout=120)
        except asyncio.TimeoutError:
            return await interaction.followup.send("No image received. Press Upload Gank to try again.", ephemeral=True)

        ticket = get_ticket(self.channel_id)
        if ticket:
            ticket["gank_url"] = msg.attachments[0].url
            save_ticket(self.channel_id, ticket)

        await interaction.followup.send("Gank image saved.", ephemeral=True)

    async def finish_callback(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("Ticket data not found.", ephemeral=True)

        is_owner = interaction.user.id == ticket["requester_id"]
        if not is_owner and not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("Only the requester or staff can finish this.", ephemeral=True)

        if not ticket["result"]:
            return await interaction.response.send_message("Choose a raid result first.", ephemeral=True)

        await interaction.response.defer()
        await finalize_raid(interaction.client, interaction.guild, interaction.channel, ticket)


class RaidersView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        btn = discord.ui.Button(label="Raiders", style=discord.ButtonStyle.secondary, custom_id=f"tsb:raiders:{channel_id}")
        btn.callback = self.raiders_callback
        self.add_item(btn)

    async def raiders_callback(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("Ticket data not found.", ephemeral=True)

        if not ticket["helper_order"]:
            return await interaction.response.send_message("No helpers confirmed for this raid.", ephemeral=True)

        lines = [helper_line(ticket, uid) for uid in ticket["helper_order"]]

        embed = discord.Embed(
            title=f"{ticket['type']} #{ticket['raid_number']} Raiders - {len(lines)}",
            description="\n".join(lines),
            color=EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def finalize_raid(bot_client: commands.Bot, guild: discord.Guild, channel: discord.TextChannel, ticket: dict):
    ticket["status"] = "ended"
    ticket["ended_at"] = int(time.time())
    save_ticket(channel.id, ticket)

    helper_ids = [int(uid) for uid in ticket["helper_order"]]
    if helper_ids:
        add_raid_credit(
            guild.id, helper_ids,
            raid_number=ticket["raid_number"], result=ticket["result"], guild_name=guild.name,
        )

    streak = update_win_streak(guild.id, ticket["result"])
    duration = format_duration(ticket["ended_at"] - ticket["started_at"])
    ended_at_str = time.strftime("%A, %d %B %Y %H:%M", time.gmtime(ticket["ended_at"]))

    results_channel = guild.get_channel(RAID_RESULTS_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=RAID_RESULTS_CHANNEL_NAME)
    if results_channel:
        assisted = ", ".join(f"<@{uid}>" for uid in ticket["helper_order"]) or "None"
        description_lines = [
            f"**Assisted** - {assisted}",
            f"**Duration** - {duration}",
            f"**Ended At** - {ended_at_str}",
            f"**Ended By** - <@{ticket['ended_by']}>" if ticket["ended_by"] else "**Ended By** - Unknown",
        ]
        if ticket["type"] == "Raid":
            description_lines.append(f"**Enemy Clan** - {ticket['clan'] or 'None'}")
        description_lines.append("")
        description_lines.append(f"**Win Streak** - #{streak}")
        if ticket["experience"]:
            description_lines.append("")
            description_lines.append(f"**Experience** - {ticket['experience']}")

        embed = discord.Embed(
            title=f"{ticket['type']} - #{ticket['raid_number']} Result",
            description="\n".join(description_lines),
            color=EMBED_COLOR,
        )
        if ticket["gank_url"]:
            embed.set_image(url=ticket["gank_url"])
        embed.set_footer(text=f"Result: {ticket['result']}")

        message_text = (
            f"{' '.join(f'<@{uid}>' for uid in ticket['helper_order'])}\n"
            f"The raid is ended. Those who are still in the server, you can leave. Result: {ticket['result']}."
        ).strip()

        results_msg = await results_channel.send(
            content=message_text or None,
            embed=embed,
            view=RaidersView(channel.id),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        ticket["results_url"] = results_msg.jump_url
        save_ticket(channel.id, ticket)

    await log_ticket_ended(guild, ticket)
    await warn_offline_helpers(guild, ticket)
    save_ticket(channel.id, ticket)
    await post_mvps(guild, ticket)

    await channel.send("This raid has been recorded.", view=DeleteTicketView(channel.id))


async def post_mvps(guild: discord.Guild, ticket: dict):
    mvps_channel = guild.get_channel(MVPS_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=MVPS_CHANNEL_NAME)
    if not mvps_channel or not ticket["helper_order"]:
        return

    mvp_ids = ticket["helper_order"][:3]
    mvp_lines = [helper_line(ticket, uid) for uid in mvp_ids]

    embed = discord.Embed(title=f"MVPs  #{ticket['raid_number']}", description="\n".join(mvp_lines), color=EMBED_COLOR)
    embed.set_footer(text="First to join this raid")

    await mvps_channel.send(
        content=" ".join(f"<@{uid}>" for uid in mvp_ids),
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=True),
    )


# ======================================================
# LOGS
# ======================================================

async def get_logs_channel(guild: discord.Guild):
    return guild.get_channel(LOGS_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=LOGS_CHANNEL_NAME)


async def log_ticket_created(guild: discord.Guild, ticket: dict, ticket_jump_url: str, profile_url):
    logs_channel = await get_logs_channel(guild)
    if not logs_channel:
        return

    created_str = time.strftime("%A, %d %B %Y %H:%M", time.gmtime(ticket["started_at"]))
    embed = discord.Embed(title=f"{ticket['type']} #{ticket['raid_number']} Ticket Created", color=EMBED_COLOR)
    embed.add_field(name="Opened By", value=f"<@{ticket['requester_id']}>", inline=True)
    embed.add_field(name="Created", value=created_str, inline=True)
    embed.add_field(name="Requester Roblox", value=ticket["roblox_username"], inline=False)
    embed.add_field(name="Region", value=ticket["region"], inline=True)
    if ticket["type"] == "Raid":
        embed.add_field(name="Enemy Guild", value=ticket["clan"] or "None", inline=True)
    embed.add_field(name="Reported Players", value=box(ticket["reported_players"]), inline=False)
    embed.set_footer(text=BOT_NAME)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="More Info", style=discord.ButtonStyle.link, url=ticket_jump_url))
    if profile_url:
        view.add_item(discord.ui.Button(label="Roblox Profile", style=discord.ButtonStyle.link, url=profile_url))

    await logs_channel.send(embed=embed, view=view)


async def log_ticket_ended(guild: discord.Guild, ticket: dict):
    logs_channel = await get_logs_channel(guild)
    if not logs_channel:
        return

    # Plain message only - no buttons/options.
    embed = discord.Embed(
        description=f"Raid #{ticket['raid_number']} is completed. Check the results in the raid results channel.",
        color=EMBED_COLOR,
    )
    await logs_channel.send(embed=embed)


async def log_ticket_deleted(guild: discord.Guild, ticket: dict, deleted_by_id: int, channel_id: int):
    logs_channel = await get_logs_channel(guild)
    if not logs_channel:
        return

    embed = discord.Embed(
        description=f"Ticket #{ticket['raid_number']} has been deleted by <@{deleted_by_id}>.",
        color=EMBED_COLOR,
    )
    view = ReopenTicketView(channel_id)
    if ticket.get("results_url"):
        view.add_item(discord.ui.Button(label="More Info", style=discord.ButtonStyle.link, url=ticket["results_url"]))

    await logs_channel.send(embed=embed, view=view)


# ======================================================
# DELETE / REOPEN
# ======================================================

class DeleteTicketView(BlacklistGate, discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        btn = discord.ui.Button(label="Delete Ticket", style=discord.ButtonStyle.danger, custom_id=f"tsb:delete:{channel_id}")
        btn.callback = self.delete_callback
        self.add_item(btn)

    async def delete_callback(self, interaction: discord.Interaction):
        ticket = get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("Ticket data not found.", ephemeral=True)

        is_owner = interaction.user.id == ticket["requester_id"]
        if not is_owner and not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("Only the requester or staff can delete this.", ephemeral=True)

        ticket["status"] = "deleted"
        ticket["deleted_by"] = interaction.user.id
        save_ticket(self.channel_id, ticket)

        await log_ticket_deleted(interaction.guild, ticket, interaction.user.id, self.channel_id)

        await interaction.response.send_message("Deleting this ticket in 5 seconds.")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except discord.NotFound:
            pass


class ReopenTicketView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        btn = discord.ui.Button(label="Re-Open Ticket", style=discord.ButtonStyle.primary, custom_id=f"tsb:reopen:{channel_id}")
        btn.callback = self.reopen_callback
        self.add_item(btn)

    async def reopen_callback(self, interaction: discord.Interaction):
        if interaction.user.id != DEVELOPER_ID:
            return await interaction.response.send_message("Only the developer can re-open a ticket.", ephemeral=True)

        ticket = get_ticket(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("Ticket data not found.", ephemeral=True)
        if ticket["status"] != "deleted":
            return await interaction.response.send_message("This ticket is not deleted.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        category = find_category(guild)

        viewer = guild.get_role(VIEWER_ROLE_ID) or guild.default_role
        requester = guild.get_member(ticket["requester_id"])
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            viewer: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, manage_channels=True, attach_files=True),
        }
        if requester:
            overwrites[requester] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        new_channel = await guild.create_text_channel(
            name=ticket_channel_name(ticket["raid_number"]),
            category=category,
            overwrites=overwrites,
            topic=f"requester:{ticket['requester_id']}|raid:{ticket['raid_number']}",
        )

        ticket["status"] = "open"
        ticket["deleted_by"] = None
        ticket["message_count"] = 0
        ticket["edit_btn"] = True
        DATA["tickets"].pop(str(self.channel_id), None)
        save_ticket(new_channel.id, ticket)

        profile_url = roblox_profile_url(ticket["roblox_id"])
        info_msg = await new_channel.send(embed=build_ticket_embed(ticket), view=TicketView(new_channel.id, profile_url))
        ticket["info_message_id"] = info_msg.id
        save_ticket(new_channel.id, ticket)

        await new_channel.send(
            "This ticket was re-opened by the developer. You can delete it below if needed.",
            view=DeleteTicketView(new_channel.id),
        )

        await interaction.followup.send(f"Ticket re-opened: {new_channel.mention}", ephemeral=True)


# ======================================================
# AUTHORIZE / DEAUTHORIZE SETUP
# ======================================================

CHANNEL_TYPES = [
    ("battle_panel", "Battle Panel", BATTLE_PANEL_CHANNEL_ID, BATTLE_PANEL_CHANNEL_NAME),
    ("raid_results", "Raid Results", RAID_RESULTS_CHANNEL_ID, RAID_RESULTS_CHANNEL_NAME),
    ("leaderboard", "Leaderboard", LEADERBOARD_CHANNEL_ID, LEADERBOARD_CHANNEL_NAME),
    ("logs", "Logs", LOGS_CHANNEL_ID, LOGS_CHANNEL_NAME),
    ("mvps", "MVPs", MVPS_CHANNEL_ID, MVPS_CHANNEL_NAME),
    ("snipe", "Snipe", SNIPE_CHANNEL_ID, SNIPE_CHANNEL_NAME),
    ("readme", "Read-me", 0, README_CHANNEL_NAME),
]


async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    category = find_category(guild)
    if category is None:
        category = await guild.create_category(AUTHORIZE_CATEGORY_NAME, reason=f"{BOT_NAME} /authorize setup")
    return category


async def run_setup(guild: discord.Guild, selected_keys: set):
    # #snipe is always created when a server is authorized (hitlist / see alerts only go there).
    selected_keys = set(selected_keys) | {"snipe"}

    category = await get_or_create_category(guild)
    created = {}

    for key, label, configured_id, name in CHANNEL_TYPES:
        if key not in selected_keys:
            continue

        if key == "readme":
            channel = await create_or_update_readme(guild, authorized=True)
            created[key] = channel
            continue

        channel = guild.get_channel(configured_id) if configured_id else None
        if not isinstance(channel, discord.TextChannel):
            channel = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == name,
                guild.text_channels,
            )
        if channel is None:
            channel = await guild.create_text_channel(name, category=category, reason=f"{BOT_NAME} /authorize setup")
        created[key] = channel

    if guild.id not in DATA["authorized_guilds"]:
        DATA["authorized_guilds"].append(guild.id)
        save_data()

    if created.get("battle_panel"):
        await refresh_panel(created["battle_panel"])
    if created.get("leaderboard"):
        await refresh_leaderboard(created["leaderboard"])

    return category, created


async def find_bot_channels(guild: discord.Guild):
    found = []
    category = find_category(guild)
    if category:
        found.extend(category.channels)

    for key, label, configured_id, name in CHANNEL_TYPES:
        channel = guild.get_channel(configured_id) if configured_id else None
        if not isinstance(channel, discord.TextChannel):
            channel = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == name,
                guild.text_channels,
            )
        if channel and channel not in found:
            found.append(channel)

    if category:
        found.append(category)

    return found


class ManualSetupSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=key) for key, label, _, _ in CHANNEL_TYPES]
        super().__init__(placeholder="Choose which channels to create", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        category, created = await run_setup(interaction.guild, set(self.values))
        lines = [f"Category: {category.mention}"]
        for key, label, _, _ in CHANNEL_TYPES:
            if key in created and created[key]:
                lines.append(f"{label}: {created[key].mention}")
        await interaction.followup.send(f"{BOT_NAME} authorized (manual setup).\n" + "\n".join(lines), ephemeral=True)


class ManualSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ManualSetupSelect())


class AuthorizeConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Auto Setup", style=discord.ButtonStyle.success)
    async def auto_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        all_keys = {key for key, _, _, _ in CHANNEL_TYPES}
        category, created = await run_setup(interaction.guild, all_keys)
        lines = [f"Category: {category.mention}"]
        for key, label, _, _ in CHANNEL_TYPES:
            if created.get(key):
                lines.append(f"{label}: {created[key].mention}")
        await interaction.followup.send(f"{BOT_NAME} authorized (auto setup).\n" + "\n".join(lines), ephemeral=True)

    @discord.ui.button(label="Manual Setup", style=discord.ButtonStyle.secondary)
    async def manual_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose which channels to create.", view=ManualSetupView(), ephemeral=True)


# ======================================================
# LEADERBOARD (Components V2 layout, matches the video)
# ======================================================

# message_id -> {"scope": "global" | "server" | "daily", "page": int}
leaderboard_state: dict[int, dict] = {}

SCOPE_LABELS = {
    "global": "Global Leaderboard",
    "server": "Server Total Leaderboard",
    "daily": "Server Daily Leaderboard",
}
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


async def resolve_username(client: commands.Bot, guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    if member:
        return member.name
    user = client.get_user(user_id)
    if user:
        return user.name
    try:
        user = await client.fetch_user(user_id)
        return user.name
    except discord.HTTPException:
        return "unknown-user"


class LeaderboardView(BlacklistGate, discord.ui.LayoutView):
    def __init__(
        self,
        *,
        guild_name: str = "-",
        scope: str = "global",
        page: int = 0,
        total_pages: int = 1,
        stats_text: str = "-",
        rankings_text: str = "-",
        total_members: int = 0,
        updated_ts: int | None = None,
    ):
        super().__init__(timeout=None)
        self.page = page
        updated_ts = updated_ts or int(time.time())

        scope_select = discord.ui.Select(
            custom_id="lb:scope",
            options=[
                discord.SelectOption(label=label, value=value, default=(value == scope))
                for value, label in SCOPE_LABELS.items()
            ],
        )
        scope_select.callback = self.scope_callback

        my_rank_btn = discord.ui.Button(label="My Rank", emoji="👤", style=discord.ButtonStyle.secondary, custom_id="lb:myrank")
        my_rank_btn.callback = self.my_rank_callback

        top_btn = discord.ui.Button(label="Top 10", emoji="📶", style=discord.ButtonStyle.secondary, custom_id="lb:top10")
        top_btn.callback = self.top_callback

        first_btn = discord.ui.Button(label="⏪", style=discord.ButtonStyle.secondary, custom_id="lb:first")
        first_btn.callback = self.first_callback
        prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, custom_id="lb:prev")
        prev_btn.callback = self.prev_callback
        page_btn = discord.ui.Button(label=f"{page + 1}/{total_pages}", style=discord.ButtonStyle.secondary, custom_id="lb:page", disabled=True)
        next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, custom_id="lb:next")
        next_btn.callback = self.next_callback
        last_btn = discord.ui.Button(label="⏩", style=discord.ButtonStyle.secondary, custom_id="lb:last")
        last_btn.callback = self.last_callback

        if scope == "global":
            scope_line = "**Scope:** *every server* - permanent history, never resets"
        else:
            scope_line = f"**Server:** *{guild_name}*'s server"

        container = discord.ui.Container(
            discord.ui.ActionRow(scope_select),
            discord.ui.TextDisplay(
                "Leaderboard for the number of times you helped.\n\n"
                f"{scope_line}"
            ),
            discord.ui.Separator(),
            discord.ui.Section(discord.ui.TextDisplay("## 📊 Your Current Stats"), accessory=my_rank_btn),
            discord.ui.TextDisplay(stats_text),
            discord.ui.Separator(),
            discord.ui.Section(discord.ui.TextDisplay("## 🏆 Rankings"), accessory=top_btn),
            discord.ui.TextDisplay(rankings_text),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# Last updated: <t:{updated_ts}:R> • Total members: {total_members:,}"),
            discord.ui.ActionRow(first_btn, prev_btn, page_btn, next_btn, last_btn),
        )
        self.add_item(container)

    # ---- callbacks -------------------------------------------------

    async def _update(self, interaction: discord.Interaction, *, scope=None, page=None, delta=0):
        state = leaderboard_state.setdefault(interaction.message.id, {"scope": "global", "page": 0})
        if scope is not None:
            state["scope"] = scope
            state["page"] = 0
        if page is not None:
            state["page"] = page
        state["page"] += delta

        await interaction.response.defer()
        view = await make_leaderboard_view(
            interaction.client, interaction.guild, state["scope"], state["page"], interaction.user
        )
        state["page"] = view.page
        await interaction.edit_original_response(view=view)

    async def scope_callback(self, interaction: discord.Interaction):
        await self._update(interaction, scope=interaction.data["values"][0])

    async def my_rank_callback(self, interaction: discord.Interaction):
        await self._update(interaction)

    async def top_callback(self, interaction: discord.Interaction):
        await self._update(interaction, page=0)

    async def first_callback(self, interaction: discord.Interaction):
        await self._update(interaction, page=0)

    async def prev_callback(self, interaction: discord.Interaction):
        await self._update(interaction, delta=-1)

    async def next_callback(self, interaction: discord.Interaction):
        await self._update(interaction, delta=1)

    async def last_callback(self, interaction: discord.Interaction):
        await self._update(interaction, page=10 ** 9)  # clamped to the last page


async def make_leaderboard_view(client: commands.Bot, guild: discord.Guild, scope: str, page: int, viewer=None):
    entries = leaderboard_entries(guild.id, scope)
    total_pages = max(1, math.ceil(len(entries) / LEADERBOARD_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    start = page * LEADERBOARD_PAGE_SIZE
    page_entries = entries[start:start + LEADERBOARD_PAGE_SIZE]

    if page_entries:
        rows = []
        for i, (uid, count) in enumerate(page_entries):
            rank = start + i + 1
            name = await resolve_username(client, guild, uid)
            prefix = MEDALS.get(rank, f"**{rank}.**")
            rows.append(f"{prefix} `@{name}`\n-# Assists: `{count:,}`")
        rankings_text = "\n".join(rows)
    else:
        rankings_text = "No raids recorded yet."

    if viewer is None:
        stats_text = "> Press **My Rank** to see your stats."
    else:
        rank, count, total = get_rank(guild.id, viewer.id, scope)
        if rank is None:
            rank_text = "Unranked"
        else:
            percent = (rank / total * 100) if total else 0
            rank_text = f"#{rank} (Top {percent:.2f}%)"
        stats_text = (
            f"> **User:** `@{viewer.name}`\n"
            f"> **Rank:** {rank_text}\n"
            f"> **Assist(s):** {count:,}"
        )

    return LeaderboardView(
        guild_name=guild.name,
        scope=scope,
        page=page,
        total_pages=total_pages,
        stats_text=stats_text,
        rankings_text=rankings_text,
        total_members=len(entries),
    )


async def refresh_leaderboard(channel: discord.TextChannel):
    gid = str(channel.guild.id)
    lock = _refresh_locks.setdefault(f"lb:{gid}", asyncio.Lock())
    async with lock:
        # Removes the stored leaderboard AND any other leftover leaderboard in this channel.
        await purge_old_messages(channel, "leaderboard")

        view = await make_leaderboard_view(bot, channel.guild, "global", 0, None)
        new_msg = await channel.send(view=view)
        leaderboard_state[new_msg.id] = {"scope": "global", "page": 0}
        DATA["leaderboard_messages"][gid] = new_msg.id
        DATA["leaderboard_channels"][gid] = channel.id
        save_data()
        return new_msg


# ======================================================
# BOT
# ======================================================

class CruxerTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await blacklist_gate(interaction)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            return  # blacklist gate already replied
        await super().on_error(interaction, error)


class CruxerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # needed for /anti-nuke and member names
        super().__init__(command_prefix=commands.when_mentioned_or(*PREFIXES), intents=intents, tree_cls=CruxerTree)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()

        self.add_view(PanelView())
        self.add_view(LeaderboardView())

        for channel_id_str, ticket in DATA["tickets"].items():
            channel_id = int(channel_id_str)
            profile_url = roblox_profile_url(ticket.get("roblox_id"))

            if ticket["status"] == "open":
                self.add_view(TicketView(channel_id, profile_url))
            elif ticket["status"] == "ended":
                self.add_view(DeleteTicketView(channel_id))
            elif ticket["status"] == "deleted":
                self.add_view(ReopenTicketView(channel_id))

        # Slash commands are synced per server in on_ready (see sync_guild_commands).

        duration_updater.start()
        tracker_loop.start()
        join_watch_loop.start()
        backup_loop.start()

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()


bot = CruxerBot()


@bot.check
async def prefix_blacklist_check(ctx: commands.Context) -> bool:
    """Blacklisted members cannot use prefix commands either (silent)."""
    if ctx.author.id == DEVELOPER_ID:
        return True
    return str(ctx.author.id) not in DATA["blacklist"]


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    # ".something" in normal chat is not a command - never spam the console or the channel.
    if isinstance(error, (commands.CommandNotFound, commands.CheckFailure, commands.CommandOnCooldown)):
        return
    print(f"[{BOT_NAME}] Prefix command error in {getattr(ctx.command, 'name', '?')}: {error!r}")


@tasks.loop(seconds=DURATION_UPDATE_SECONDS)
async def duration_updater():
    now = int(time.time())
    for channel_id_str, ticket in list(DATA["tickets"].items()):
        if ticket["status"] != "open":
            continue
        channel = bot.get_channel(int(channel_id_str))
        if channel is None:
            continue
        # Edits the tracked info message directly, so it keeps working in busy tickets (50+ messages).
        await refresh_ticket_message(channel, ticket)


@duration_updater.before_loop
async def before_duration_updater():
    await bot.wait_until_ready()


# ======================================================
# BUSY TICKET NOTICE  ("Ah, this is a real mess.")
# ======================================================

@bot.listen("on_message")
async def ticket_busy_watcher(message: discord.Message):
    if message.guild is None:
        return
    if message.author.id == bot.user.id and (message.content or "").startswith(MESS_PREFIX):
        return  # never count our own notice

    ticket = get_ticket(message.channel.id)
    if not ticket or ticket["status"] == "deleted":
        return

    ticket["message_count"] = ticket.get("message_count", 0) + 1
    n = ticket["message_count"]

    if n > TICKET_MESS_THRESHOLD and (n - 1) % TICKET_MESS_THRESHOLD == 0:
        info_id = await locate_info_message_id(message.channel, ticket)
        view = None
        if info_id:
            url = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{info_id}"
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Jump to Information", style=discord.ButtonStyle.link, url=url))
        try:
            await message.channel.send(
                f"{MESS_PREFIX}\n-# {n} messages in this ticket now. Jump back to the request information below.",
                view=view,
            )
        except discord.HTTPException:
            pass
        save_ticket(message.channel.id, ticket)
    elif n % 10 == 0:
        save_ticket(message.channel.id, ticket)


async def upgrade_open_tickets():
    """Tickets opened before this update get the new Edit button on their information message (once)."""
    for cid, ticket in list(DATA["tickets"].items()):
        if ticket["status"] != "open" or ticket.get("edit_btn"):
            continue
        channel = bot.get_channel(int(cid))
        if channel is None:
            continue
        profile_url = roblox_profile_url(ticket.get("roblox_id"))
        if await refresh_ticket_message(channel, ticket, view=TicketView(int(cid), profile_url)):
            ticket["edit_btn"] = True
            save_ticket(int(cid), ticket)


# ======================================================
# /setup_panel, /leaderboard, /say, /ping, /nuke
# ======================================================

@bot.tree.command(name="setup_panel", description="Post/refresh the raid request panel in this channel")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_panel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await refresh_panel(interaction.channel)
    await interaction.followup.send("Panel posted. Old panels were removed.", ephemeral=True)


@bot.tree.command(name="leaderboard", description="Post/refresh the raid leaderboard in this channel")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def leaderboard_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await refresh_leaderboard(interaction.channel)
    await interaction.followup.send("Leaderboard posted.", ephemeral=True)


@bot.tree.command(name="say", description="[OWN] Make Cruxer send a message")
@app_commands.describe(message="The message Cruxer should send")
@app_commands.guild_only()
async def say_command(interaction: discord.Interaction, message: str):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(message)
        await interaction.followup.send("Message sent.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to send messages here.", ephemeral=True)


@bot.tree.command(name="ping", description="Check Cruxer's latency and connection")
@app_commands.guild_only()
async def ping_command(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"{BOT_NAME} is online.\nLatency: {latency_ms} ms")


@bot.tree.command(name="nuke", description="Go on. Press it.")
async def nuke_command(interaction: discord.Interaction):
    await interaction.response.send_message("This guy thought he's him lol.")


# ======================================================
# /raid panel  and  /backup panel  (open the ticket-making process directly)
# ======================================================

raid_group = app_commands.Group(name="raid", description="Raid requests", guild_only=True)
backup_group = app_commands.Group(name="backup", description="Backup requests", guild_only=True)


@raid_group.command(name="panel", description="Open the Raid ticket-making form")
async def raid_panel_command(interaction: discord.Interaction):
    await start_request_flow(interaction, "Raid")


@backup_group.command(name="panel", description="Open the Backup ticket-making form")
async def backup_panel_command(interaction: discord.Interaction):
    await start_request_flow(interaction, "Backup")


bot.tree.add_command(raid_group)
bot.tree.add_command(backup_group)


# ======================================================
# .av  (prefix command: avatar + profile of a user)
# ======================================================

async def resolve_prefix_target(ctx: commands.Context, text: str | None):
    """A mention, an ID, a name, or - with no text - the author of the replied-to message, or yourself."""
    if not text:
        ref = ctx.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            author = ref.resolved.author
            return (ctx.guild.get_member(author.id) if ctx.guild else None) or author
        return ctx.author

    text = text.strip()
    for converter in (commands.MemberConverter, commands.UserConverter):
        try:
            return await converter().convert(ctx, text)
        except commands.CommandError:
            continue
    if text.isdigit():
        try:
            return await bot.fetch_user(int(text))
        except discord.HTTPException:
            pass
    return None


@bot.command(name="av", aliases=["avatar", "pfp"])
@commands.cooldown(1, 2.0, commands.BucketType.user)
async def av_command(ctx: commands.Context, *, target: str = None):
    """.av [user]  - avatar (all formats), banner and profile of a user."""
    if not mark_prefix_message_seen(ctx.message.id):
        return
    user = await resolve_prefix_target(ctx, target)
    if user is None:
        return await ctx.reply("I could not find that user. Mention them or give their ID.", mention_author=False)

    try:
        full = await bot.fetch_user(user.id)  # needed for the banner / accent colour
    except discord.HTTPException:
        full = user
    member = user if isinstance(user, discord.Member) else (ctx.guild.get_member(user.id) if ctx.guild else None)

    shown = (member or full).display_avatar
    formats = ["png", "jpg", "webp"] + (["gif"] if shown.is_animated() else [])
    links = []
    for fmt in formats:
        try:
            links.append(f"[{fmt.upper()}]({shown.replace(size=1024, format=fmt).url})")
        except (ValueError, TypeError):
            continue

    color = full.accent_colour or discord.Colour(EMBED_COLOR)
    name = (member or full).display_name

    av_embed = discord.Embed(title=f"Avatar - {name}", color=color)
    av_embed.set_image(url=shown.replace(size=1024).url)
    desc = " | ".join(links)
    if member and member.guild_avatar and full.avatar and member.guild_avatar.key != full.avatar.key:
        desc += f"\n**Global avatar:** [Open]({full.avatar.replace(size=1024).url})"
        av_embed.set_thumbnail(url=full.avatar.replace(size=256).url)
    av_embed.description = desc

    pr = discord.Embed(title=f"Profile - {name}", color=color)
    created = int(full.created_at.timestamp())
    pr.add_field(
        name="Account",
        value=(
            f"**Username:** `{full.name}`\n**ID:** `{full.id}`\n**Mention:** <@{full.id}>\n"
            f"**Bot:** {'Yes' if full.bot else 'No'}\n"
            f"**Created:** <t:{created}:F> (<t:{created}:R>)"
        ),
        inline=False,
    )
    badges = [f.name.replace("_", " ").title() for f in full.public_flags.all()]
    if badges:
        pr.add_field(name="Badges", value=", ".join(badges), inline=False)
    if member:
        joined = int(member.joined_at.timestamp()) if member.joined_at else None
        pr.add_field(
            name="In This Server",
            value=(
                f"**Nickname:** {member.nick or 'None'}\n"
                f"**Joined:** {f'<t:{joined}:R>' if joined else 'Unknown'}\n"
                f"**Top role:** {member.top_role.mention}"
            ),
            inline=False,
        )
    link = DATA["links"].get(str(full.id))
    assists = DATA["stats"]["global"].get(str(full.id), 0)
    if link or assists:
        roblox_text = f"[{link['roblox_username']}]({roblox_profile_url(link['roblox_id'])})" if link else "Not linked"
        pr.add_field(name=BOT_NAME, value=f"**Roblox:** {roblox_text}\n**Global assists:** {assists:,}", inline=False)
    if full.banner:
        pr.set_image(url=full.banner.replace(size=1024).url)
    pr.set_footer(text=f"Requested by {ctx.author.display_name}")

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Open Avatar", style=discord.ButtonStyle.link, url=shown.replace(size=1024).url))
    if full.banner:
        view.add_item(discord.ui.Button(label="Open Banner", style=discord.ButtonStyle.link, url=full.banner.replace(size=1024).url))

    await ctx.reply(embeds=[av_embed, pr], view=view, mention_author=False, allowed_mentions=discord.AllowedMentions.none())


# ======================================================
# /purge  (keep only the newest working panel / leaderboard)
# ======================================================

@bot.tree.command(name="purge", description="Keep only the latest panel and delete all the older ones")
@app_commands.describe(item="What to clean up (default: Backup Panel)", server_wide="Also clean the other channels of this server")
@app_commands.choices(item=[
    app_commands.Choice(name="Backup Panel", value="panel"),
    app_commands.Choice(name="Leaderboard", value="leaderboard"),
])
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def purge_command(interaction: discord.Interaction, item: app_commands.Choice[str] = None, server_wide: bool = False):
    if not await require_admin(interaction):
        return
    kind = item.value if item else "panel"
    guild = interaction.guild

    if server_wide:
        channels = [c for c in guild.text_channels if c.permissions_for(guild.me).read_message_history]
        depth = 150
    else:
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Use this in a normal text channel.", ephemeral=True)
        channels = [interaction.channel]
        depth = 500

    await interaction.response.defer(ephemeral=True)

    candidates = []
    for ch in channels:
        try:
            async for msg in ch.history(limit=depth):
                if msg.author.id == bot.user.id and msg_looks_like(msg, kind):
                    candidates.append(msg)
        except (discord.Forbidden, discord.HTTPException):
            continue

    functional = [m for m in candidates if msg_is_functional(m, kind)]
    keep = max(functional, key=lambda m: m.id) if functional else None

    removed = 0
    for m in candidates:
        if keep is not None and m.id == keep.id:
            continue
        try:
            await m.delete()
            leaderboard_state.pop(m.id, None)
            removed += 1
        except discord.HTTPException:
            continue

    msg_key, chan_key = PURGE_KEYS[kind]
    label = "panel" if kind == "panel" else "leaderboard"

    if keep is None:
        # nothing working was left, so post a fresh one here
        if isinstance(interaction.channel, discord.TextChannel):
            if kind == "panel":
                await refresh_panel(interaction.channel)
            else:
                await refresh_leaderboard(interaction.channel)
        return await interaction.followup.send(
            f"No working {label} was found, so I removed {removed} old/legacy one(s) and posted a fresh one here.",
            ephemeral=True,
        )

    DATA[msg_key][str(guild.id)] = keep.id
    DATA[chan_key][str(guild.id)] = keep.channel.id
    save_data()
    await interaction.followup.send(
        f"Kept the latest {label}: {keep.jump_url}\nRemoved {removed} older one(s).", ephemeral=True
    )


# ======================================================
# /member-stats
# ======================================================

@bot.tree.command(name="member-stats", description="Check how many raids a member has assisted in")
@app_commands.describe(member="Leave empty to check yourself")
@app_commands.guild_only()
async def member_stats_command(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    count = get_raid_count(interaction.guild.id, target.id, "server")
    rank, _, total = get_rank(interaction.guild.id, target.id, "server")
    rank_text = f" (Rank #{rank} of {total})" if rank else ""
    await interaction.response.send_message(f"{target.display_name} has assisted in {count} raid(s){rank_text}.")


# ======================================================
# /authorize, /deauthorize
# ======================================================

@bot.tree.command(name="authorize", description="[OWN] Set up Cruxer in this server")
@app_commands.guild_only()
async def authorize_command(interaction: discord.Interaction):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message(f"Only the {BOT_NAME} developer can use /authorize.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This can only be used in a server.", ephemeral=True)

    lines = "\n".join(f"- {label}" for _, label, _, _ in CHANNEL_TYPES)
    embed = discord.Embed(
        title=f"Authorize {BOT_NAME}",
        description=(
            f"This will create a {BOT_NAME} category in {guild.name} along with:\n{lines}\n\n"
            "Auto Setup creates everything above.\n"
            "Manual Setup lets you choose which channels to create (Snipe is always created)."
        ),
        color=EMBED_COLOR,
    )
    await interaction.response.send_message(embed=embed, view=AuthorizeConfirmView(), ephemeral=True)


@bot.tree.command(name="deauthorize", description="[OWN] Remove Cruxer's setup from this server and leave")
@app_commands.guild_only()
async def deauthorize_command(interaction: discord.Interaction):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message(f"Only the {BOT_NAME} developer can use /deauthorize.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This can only be used in a server.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    channels = await find_bot_channels(guild)
    for ch in channels:
        try:
            await ch.delete(reason=f"{BOT_NAME} deauthorized")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    gid = str(guild.id)

    # Raid numbers restart after a re-authorize, so keep this server's archived tickets under a unique key
    # (otherwise new "#1" tickets would overwrite the old "#1" in the permanent archive).
    stamp = int(time.time())
    for key in [k for k, v in DATA["ticket_archive"].items() if v.get("guild_id") == guild.id and ":pre" not in k]:
        DATA["ticket_archive"][f"{key}:pre{stamp}"] = DATA["ticket_archive"].pop(key)

    DATA["authorized_guilds"] = [g for g in DATA["authorized_guilds"] if g != guild.id]
    DATA["panel_messages"].pop(gid, None)
    DATA["leaderboard_messages"].pop(gid, None)
    DATA["raid_counters"].pop(gid, None)
    DATA["stats"]["guilds"].pop(gid, None)
    DATA["stats"]["daily"].pop(gid, None)
    DATA["request_limits"].pop(gid, None)
    DATA["win_streaks"].pop(gid, None)
    DATA["nuke_snapshots"].pop(gid, None)
    DATA["trackers"] = {k: t for k, t in DATA["trackers"].items() if t.get("guild_id") != guild.id}
    # NOTE: DATA["stats"]["global"], DATA["global_history"] and DATA["ticket_archive"] are intentionally never wiped here.
    DATA["tickets"] = {cid: t for cid, t in DATA["tickets"].items() if t.get("guild_id") != guild.id}
    save_data()
    save_global_file()

    try:
        await interaction.followup.send(f"{BOT_NAME} has been removed from this server. Leaving now.", ephemeral=True)
    except discord.HTTPException:
        pass

    await leave_with_farewell(guild)  # creates the farewell channel + message, then leaves


# ======================================================
# /overview
# ======================================================

@bot.tree.command(name="overview", description="View and manage Cruxer's setup in this server")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def overview_command(interaction: discord.Interaction):
    guild = interaction.guild
    gid = str(guild.id)

    open_tickets = sum(1 for t in DATA["tickets"].values() if t.get("guild_id") == guild.id and t["status"] == "open")
    total_raids = DATA["raid_counters"].get(gid, 0)
    server_assists = sum(DATA["stats"]["guilds"].get(gid, {}).values())
    uptime = format_duration(int(time.time()) - BOT_START_TIME)

    embed = discord.Embed(title=f"{BOT_NAME} Overview - {guild.name}", color=EMBED_COLOR)
    embed.add_field(name="Authorized", value="Yes" if guild.id in DATA["authorized_guilds"] else "No", inline=True)
    embed.add_field(name="Bot Uptime", value=uptime, inline=True)
    embed.add_field(name="Open Tickets", value=str(open_tickets), inline=True)
    embed.add_field(name="Total Raids Logged", value=str(total_raids), inline=True)
    embed.add_field(name="Total Assists (Server)", value=str(server_assists), inline=True)
    embed.set_footer(text=BOT_NAME)

    view = discord.ui.View(timeout=120)

    async def refresh_panel_cb(inter: discord.Interaction):
        panel_channel = guild.get_channel(BATTLE_PANEL_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=BATTLE_PANEL_CHANNEL_NAME)
        if not panel_channel:
            return await inter.response.send_message("Battle panel channel not found.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await refresh_panel(panel_channel)
        await inter.followup.send("Panel refreshed.", ephemeral=True)

    async def refresh_leaderboard_cb(inter: discord.Interaction):
        lb_channel = guild.get_channel(LEADERBOARD_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=LEADERBOARD_CHANNEL_NAME)
        if not lb_channel:
            return await inter.response.send_message("Leaderboard channel not found.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await refresh_leaderboard(lb_channel)
        await inter.followup.send("Leaderboard refreshed.", ephemeral=True)

    refresh_panel_btn = discord.ui.Button(label="Refresh Panel", style=discord.ButtonStyle.secondary)
    refresh_panel_btn.callback = refresh_panel_cb
    view.add_item(refresh_panel_btn)

    refresh_lb_btn = discord.ui.Button(label="Refresh Leaderboard", style=discord.ButtonStyle.secondary)
    refresh_lb_btn.callback = refresh_leaderboard_cb
    view.add_item(refresh_lb_btn)

    logs_channel = guild.get_channel(LOGS_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=LOGS_CHANNEL_NAME)
    if logs_channel:
        view.add_item(discord.ui.Button(label="Open Logs", style=discord.ButtonStyle.link, url=logs_channel.jump_url))

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ======================================================
# /bot stats  (owner only, public message) - view ANY server + remove the bot from any server
# ======================================================

bot_group = app_commands.Group(name="bot", description="[OWN] Cruxer bot management", guild_only=True)

REQUIRED_PERMS = [
    ("manage_channels", "Manage Channels"),
    ("manage_roles", "Manage Roles"),
    ("manage_webhooks", "Manage Webhooks"),
    ("manage_guild", "Manage Server"),
    ("kick_members", "Kick Members"),
    ("view_audit_log", "View Audit Log"),
    ("embed_links", "Embed Links"),
    ("attach_files", "Attach Files"),
    ("read_message_history", "Read Message History"),
    ("create_instant_invite", "Create Invite"),
]


def _truncate_field(lines: list[str], limit: int = 1000) -> str:
    out = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit:
            out.append(f"...and {len(lines) - i} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) if out else "None"


def build_bot_stats_embed(guild: discord.Guild) -> discord.Embed:
    gid = str(guild.id)
    authorized = guild.id in DATA["authorized_guilds"]

    open_tickets = sum(1 for t in DATA["tickets"].values() if t.get("guild_id") == guild.id and t["status"] == "open")
    total_raids = DATA["raid_counters"].get(gid, 0)
    server_assists = sum(DATA["stats"]["guilds"].get(gid, {}).values())
    uptime = format_duration(int(time.time()) - BOT_START_TIME)
    perms = guild.me.guild_permissions

    embed = discord.Embed(title=f"{BOT_NAME} - Bot Stats", description=f"Viewing **{discord.utils.escape_markdown(guild.name)}** (`{guild.id}`)", color=EMBED_COLOR)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Authorized", value="Yes" if authorized else "No", inline=True)
    embed.add_field(name="Administrator", value="Yes" if perms.administrator else "No", inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)} ms", inline=True)

    missing = [label for attr, label in REQUIRED_PERMS if not (perms.administrator or getattr(perms, attr, False))]
    embed.add_field(
        name="Bot Permissions",
        value="All required permissions are granted." if not missing else "**Missing:** " + ", ".join(missing),
        inline=False,
    )

    role_lines = [role.mention for role in reversed(guild.me.roles) if not role.is_default()]
    embed.add_field(name=f"Bot Roles ({len(role_lines)})", value=_truncate_field(role_lines), inline=False)

    created = int(guild.created_at.timestamp())
    bot_joined = int(guild.me.joined_at.timestamp()) if guild.me.joined_at else None
    embed.add_field(
        name="Server Info",
        value=(
            f"Owner: <@{guild.owner_id}> (`{guild.owner_id}`)\n"
            f"Created: <t:{created}:R>\n"
            f"Bot joined: {f'<t:{bot_joined}:R>' if bot_joined else 'Unknown'}\n"
            f"Boost level: `{guild.premium_tier}`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"Server Stats - {guild.name}",
        value=(
            f"Members: `{guild.member_count or 0:,}`\n"
            f"Text channels: `{len(guild.text_channels)}`\n"
            f"Voice channels: `{len(guild.voice_channels)}`\n"
            f"Categories: `{len(guild.categories)}`\n"
            f"Roles: `{len(guild.roles)}`\n"
            f"Open tickets: `{open_tickets}`\n"
            f"Raids logged: `{total_raids}`\n"
            f"Total assists: `{server_assists}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Global (all servers)",
        value=(
            f"Leaderboard members: `{len(DATA['stats']['global']):,}`\n"
            f"Total assists ever: `{sum(DATA['stats']['global'].values()):,}`\n"
            f"Raids in history: `{len(DATA['global_history']):,}`"
        ),
        inline=False,
    )

    server_lines = []
    for g in sorted(bot.guilds, key=lambda x: (x.member_count or 0), reverse=True):
        flag = "Authorized" if g.id in DATA["authorized_guilds"] else "Unauthorized"
        server_lines.append(f"**{discord.utils.escape_markdown(g.name)}** - {g.member_count or 0:,} members - {flag}")
    embed.add_field(name=f"Servers ({len(bot.guilds)})", value=_truncate_field(server_lines), inline=False)

    embed.set_footer(text=f"{BOT_NAME} - Uptime {uptime}")
    return embed


class ConfirmLeaveView(discord.ui.View):
    def __init__(self, target_guild_id: int):
        super().__init__(timeout=60)
        self.target_guild_id = target_guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != DEVELOPER_ID:
            await interaction.response.send_message(f"Only the {BOT_NAME} developer can do this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Remove", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = bot.get_guild(self.target_guild_id)
        if target is None:
            return await interaction.response.edit_message(content=f"{BOT_NAME} is not in that server anymore.", view=None)

        name = target.name
        DATA["authorized_guilds"] = [g for g in DATA["authorized_guilds"] if g != target.id]
        save_data()

        await interaction.response.edit_message(content=f"{BOT_NAME} has been removed from **{name}**.", view=None)
        await leave_with_farewell(target)  # farewell channel + message first, then leave

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class BotStatsView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, current_guild_id: int, page: int = 0):
        super().__init__(timeout=900)
        self.current_guild_id = current_guild_id
        self.page = page
        self._build()

    def _build(self):
        self.clear_items()
        guilds = sorted(bot.guilds, key=lambda g: g.name.lower())
        pages = max(1, math.ceil(len(guilds) / self.PAGE_SIZE))
        self.page = max(0, min(self.page, pages - 1))
        chunk = guilds[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]

        remove_here = discord.ui.Button(label="Remove Bot From Viewed Server", style=discord.ButtonStyle.danger)
        remove_here.callback = self.remove_here_callback
        self.add_item(remove_here)

        if pages > 1:
            prev_btn = discord.ui.Button(label="◀ Servers", style=discord.ButtonStyle.secondary, disabled=self.page <= 0)
            next_btn = discord.ui.Button(label="Servers ▶", style=discord.ButtonStyle.secondary, disabled=self.page >= pages - 1)

            async def prev_cb(interaction: discord.Interaction):
                self.page -= 1
                self._build()
                await interaction.response.edit_message(view=self)

            async def next_cb(interaction: discord.Interaction):
                self.page += 1
                self._build()
                await interaction.response.edit_message(view=self)

            prev_btn.callback = prev_cb
            next_btn.callback = next_cb
            self.add_item(prev_btn)
            self.add_item(next_btn)

        if not chunk:
            return

        def options(mark_current: bool):
            return [
                discord.SelectOption(
                    label=(g.name[:100] or str(g.id)),
                    value=str(g.id),
                    description=f"{g.member_count or 0:,} members"[:100],
                    default=bool(mark_current and g.id == self.current_guild_id),
                )
                for g in chunk
            ]

        view_select = discord.ui.Select(
            placeholder=f"View stats of a server (page {self.page + 1}/{pages})", options=options(True)
        )

        async def view_cb(interaction: discord.Interaction):
            target = bot.get_guild(int(view_select.values[0]))
            if target is None:
                return await interaction.response.send_message("The bot is not in that server anymore.", ephemeral=True)
            self.current_guild_id = target.id
            self._build()
            await interaction.response.edit_message(embed=build_bot_stats_embed(target), view=self)

        view_select.callback = view_cb
        self.add_item(view_select)

        remove_select = discord.ui.Select(placeholder="Remove the bot from a server", options=options(False))

        async def remove_cb(interaction: discord.Interaction):
            await self._ask_confirm(interaction, int(remove_select.values[0]))

        remove_select.callback = remove_cb
        self.add_item(remove_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != DEVELOPER_ID:
            await interaction.response.send_message(f"Only the {BOT_NAME} developer can use these controls.", ephemeral=True)
            return False
        return True

    async def _ask_confirm(self, interaction: discord.Interaction, guild_id: int):
        target = bot.get_guild(guild_id)
        name = target.name if target else str(guild_id)
        await interaction.response.send_message(
            f"Are you sure you want to remove {BOT_NAME} from **{name}**?",
            view=ConfirmLeaveView(guild_id),
            ephemeral=True,
        )

    async def remove_here_callback(self, interaction: discord.Interaction):
        await self._ask_confirm(interaction, self.current_guild_id)


@bot_group.command(name="stats", description="[OWN] Bot roles, servers, server stats and authorization (any server)")
async def bot_stats_command(interaction: discord.Interaction):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message(f"Only the {BOT_NAME} developer can use /bot stats.", ephemeral=True)

    # Not ephemeral - everyone in the channel can see the result.
    await interaction.response.defer()
    embed = build_bot_stats_embed(interaction.guild)
    await interaction.followup.send(embed=embed, view=BotStatsView(interaction.guild.id))


bot.tree.add_command(bot_group)


# ======================================================
# /anti-nuke  +  /nuke-restore
#   /anti-nuke writes down everything it changes, /nuke-restore puts it back.
# ======================================================

def serialize_overwrites(channel: discord.abc.GuildChannel):
    out = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        out.append({
            "id": target.id,
            "type": "role" if isinstance(target, discord.Role) else "member",
            "allow": allow.value,
            "deny": deny.value,
        })
    return out


def snapshot_channel(ch: discord.abc.GuildChannel):
    """Everything needed to rebuild a channel (not its messages - Discord cannot give those back)."""
    snap = {
        "id": ch.id,
        "name": ch.name,
        "position": ch.position,
        "category_id": getattr(ch, "category_id", None),
        "overwrites": serialize_overwrites(ch),
    }
    if isinstance(ch, discord.CategoryChannel):
        snap["kind"] = "category"
    elif isinstance(ch, discord.TextChannel):
        snap.update(kind="text", topic=ch.topic, nsfw=ch.nsfw, slowmode_delay=ch.slowmode_delay)
    elif isinstance(ch, discord.StageChannel):
        snap.update(kind="stage")
    elif isinstance(ch, discord.VoiceChannel):
        snap.update(kind="voice", bitrate=ch.bitrate, user_limit=ch.user_limit)
    elif isinstance(ch, discord.ForumChannel):
        snap.update(kind="forum", topic=getattr(ch, "topic", None), nsfw=ch.nsfw, slowmode_delay=ch.slowmode_delay)
    else:
        return None
    return snap


def fix_locked_snapshot(snap_ch: dict, previous, everyone_id: int):
    """/anti-nuke locks channels BEFORE deleting new ones, so put @everyone's send-messages back to what it was."""
    items = snap_ch["overwrites"]
    entry = next((i for i in items if i["type"] == "role" and i["id"] == everyone_id), None)
    ow = discord.PermissionOverwrite.from_pair(
        discord.Permissions(entry["allow"]) if entry else discord.Permissions.none(),
        discord.Permissions(entry["deny"]) if entry else discord.Permissions.none(),
    )
    ow.send_messages = previous
    if entry:
        items.remove(entry)
    if not ow.is_empty():
        allow, deny = ow.pair()
        items.append({"id": everyone_id, "type": "role", "allow": allow.value, "deny": deny.value})


def rebuild_overwrites(guild: discord.Guild, items: list):
    result = {}
    for it in items:
        target = guild.get_role(it["id"]) if it["type"] == "role" else guild.get_member(it["id"])
        if target is None:
            continue
        result[target] = discord.PermissionOverwrite.from_pair(
            discord.Permissions(it["allow"]), discord.Permissions(it["deny"])
        )
    return result


async def recreate_channel(guild: discord.Guild, s: dict, id_map: dict):
    overwrites = rebuild_overwrites(guild, s.get("overwrites", []))
    reason = f"{BOT_NAME} /nuke-restore"
    kind = s["kind"]

    if kind == "category":
        return await guild.create_category(s["name"], overwrites=overwrites, reason=reason)

    kwargs = {"overwrites": overwrites, "reason": reason}
    parent = None
    if s.get("category_id"):
        parent = id_map.get(s["category_id"]) or guild.get_channel(s["category_id"])
    if isinstance(parent, discord.CategoryChannel):
        kwargs["category"] = parent

    if kind == "text":
        return await guild.create_text_channel(
            s["name"], topic=s.get("topic"), nsfw=bool(s.get("nsfw")), slowmode_delay=s.get("slowmode_delay") or 0, **kwargs
        )
    if kind == "voice":
        bitrate = min(s.get("bitrate") or 64000, int(guild.bitrate_limit))
        return await guild.create_voice_channel(s["name"], bitrate=bitrate, user_limit=s.get("user_limit") or 0, **kwargs)
    if kind == "stage":
        return await guild.create_stage_channel(s["name"], **kwargs)
    if kind == "forum":
        return await guild.create_forum(
            s["name"], topic=s.get("topic"), nsfw=bool(s.get("nsfw")), slowmode_delay=s.get("slowmode_delay") or 0, **kwargs
        )
    return None


@bot.tree.command(name="anti-nuke", description="Lock the server, remove recent bots/webhooks, revoke invites, and undo recent channel creations")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def antinuke_command(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Administrator permission required.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    gid = str(guild.id)
    now = discord.utils.utcnow()
    cutoff = now.timestamp() - ANTINUKE_WINDOW_SECONDS

    protected_channels = {ch.id for ch in await find_bot_channels(guild)}

    # Keep one snapshot per server and MERGE into it, so running /anti-nuke twice never loses the originals.
    snap = DATA["nuke_snapshots"].get(gid) or {
        "started_at": int(time.time()), "by": interaction.user.id,
        "locked": {}, "deleted": [], "invites": [], "kicked_bots": [], "webhooks": [],
    }

    results = []

    locked = 0
    for channel in guild.text_channels:
        try:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.send_messages is not False:
                previous = overwrite.send_messages  # None (neutral) or True
                overwrite.send_messages = False
                await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"{BOT_NAME} /anti-nuke")
                snap["locked"].setdefault(str(channel.id), previous)
                locked += 1
        except (discord.Forbidden, discord.HTTPException):
            continue
    results.append(f"Locked {locked} text channel(s).")

    kicked = 0
    try:
        async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=25):
            if entry.created_at.timestamp() < cutoff:
                continue
            member = guild.get_member(entry.target.id) if entry.target else None
            if member:
                try:
                    await member.kick(reason=f"{BOT_NAME} /anti-nuke - recently added bot")
                    snap["kicked_bots"].append({"id": member.id, "name": member.name})
                    kicked += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue
    except discord.Forbidden:
        results.append("Missing View Audit Log permission - could not check for recently added bots.")
    else:
        results.append(f"Removed {kicked} recently added bot(s).")

    removed_webhooks = 0
    try:
        webhooks = await guild.webhooks()
        for wh in webhooks:
            if wh.created_at and wh.created_at.timestamp() >= cutoff:
                try:
                    info = {"name": wh.name, "channel_id": wh.channel_id}
                    await wh.delete(reason=f"{BOT_NAME} /anti-nuke")
                    snap["webhooks"].append(info)
                    removed_webhooks += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue
    except discord.Forbidden:
        results.append("Missing Manage Webhooks permission - could not check webhooks.")
    else:
        results.append(f"Removed {removed_webhooks} recently created webhook(s).")

    revoked_invites = 0
    try:
        invites = await guild.invites()
        for invite in invites:
            try:
                info = {
                    "channel_id": invite.channel.id if invite.channel else None,
                    "max_age": invite.max_age or 0,
                    "max_uses": invite.max_uses or 0,
                    "temporary": bool(invite.temporary),
                    "code": invite.code,
                }
                await invite.delete(reason=f"{BOT_NAME} /anti-nuke")
                snap["invites"].append(info)
                revoked_invites += 1
            except (discord.Forbidden, discord.HTTPException):
                continue
    except discord.Forbidden:
        results.append("Missing Manage Guild permission - could not revoke invites.")
    else:
        results.append(f"Revoked {revoked_invites} invite(s).")

    deleted_channels = 0
    try:
        async for entry in guild.audit_logs(action=discord.AuditLogAction.channel_create, limit=25):
            if entry.created_at.timestamp() < cutoff:
                continue
            channel = guild.get_channel(entry.target.id) if entry.target else None
            if channel and channel.id not in protected_channels:
                try:
                    snap_ch = snapshot_channel(channel)
                    if snap_ch is not None:
                        prev = snap["locked"].get(str(channel.id), "missing")
                        if prev != "missing":
                            fix_locked_snapshot(snap_ch, prev, guild.default_role.id)
                    await channel.delete(reason=f"{BOT_NAME} /anti-nuke - recently created channel")
                    if snap_ch is not None:
                        snap["deleted"].append(snap_ch)
                    deleted_channels += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue
    except discord.Forbidden:
        results.append("Missing View Audit Log permission - could not check for recently created channels.")
    else:
        results.append(f"Deleted {deleted_channels} recently created channel(s).")

    if snap["locked"] or snap["deleted"] or snap["invites"] or snap["kicked_bots"] or snap["webhooks"]:
        DATA["nuke_snapshots"][gid] = snap
        save_data()
        results.append("\nEverything was recorded. Run **/nuke-restore** to put it back.")

    embed = discord.Embed(title="Anti-Nuke Sweep Complete", description="\n".join(results), color=EMBED_COLOR)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="nuke-restore", description="Undo the last /anti-nuke: unlock channels, recreate deleted channels and invites")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def nuke_restore_command(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    guild = interaction.guild
    gid = str(guild.id)
    snap = DATA["nuke_snapshots"].get(gid)
    if not snap:
        return await interaction.response.send_message(
            "There is nothing to restore. Nothing was recorded by /anti-nuke in this server.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    lines = []

    # 1) unlock the paused channels
    unlocked = failed_unlock = 0
    for cid, previous in snap["locked"].items():
        channel = guild.get_channel(int(cid))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            overwrite = channel.overwrites_for(guild.default_role)
            overwrite.send_messages = previous
            if overwrite.is_empty():
                await channel.set_permissions(guild.default_role, overwrite=None, reason=f"{BOT_NAME} /nuke-restore")
            else:
                await channel.set_permissions(guild.default_role, overwrite=overwrite, reason=f"{BOT_NAME} /nuke-restore")
            unlocked += 1
        except (discord.Forbidden, discord.HTTPException):
            failed_unlock += 1
    lines.append(f"**Unlocked:** {unlocked} channel(s)" + (f" ({failed_unlock} failed)" if failed_unlock else "") + ".")

    # 2) recreate deleted channels (categories first so their children can be placed back in them)
    id_map: dict[int, discord.abc.GuildChannel] = {}
    created, failed = [], []
    ordered = sorted(snap["deleted"], key=lambda s: (0 if s["kind"] == "category" else 1, s.get("position", 0)))
    for s in ordered:
        try:
            new = await recreate_channel(guild, s, id_map)
        except (discord.Forbidden, discord.HTTPException):
            new = None
        if new is None:
            failed.append(s["name"])
            continue
        id_map[s["id"]] = new
        created.append(new)
    for s in ordered:
        new = id_map.get(s["id"])
        if new is not None:
            try:
                await new.edit(position=s.get("position", 0), reason=f"{BOT_NAME} /nuke-restore")
            except (discord.Forbidden, discord.HTTPException):
                pass
    if snap["deleted"]:
        lines.append(f"**Recreated channels ({len(created)}/{len(snap['deleted'])}):** " + (_truncate_field([c.mention for c in created], 600) if created else "none"))
        if failed:
            lines.append("**Could not recreate:** " + ", ".join(f"`{n}`" for n in failed))
        lines.append("-# Channel settings, permissions and positions are restored. Messages cannot be - Discord does not keep them.")

    # 3) recreate revoked invites (they get NEW codes)
    new_links = []
    for inv in snap["invites"]:
        channel = guild.get_channel(inv["channel_id"]) if inv.get("channel_id") else None
        channel = channel or id_map.get(inv.get("channel_id"))
        if channel is None or not hasattr(channel, "create_invite"):
            continue
        try:
            new_inv = await channel.create_invite(
                max_age=inv.get("max_age", 0), max_uses=inv.get("max_uses", 0),
                temporary=inv.get("temporary", False), reason=f"{BOT_NAME} /nuke-restore",
            )
            new_links.append(new_inv.url)
        except (discord.Forbidden, discord.HTTPException):
            continue
    if snap["invites"]:
        lines.append(f"**Recreated invites:** {len(new_links)}/{len(snap['invites'])} (new codes)")
        if new_links:
            lines.append(_truncate_field(new_links, 500))

    # 4) things that cannot be undone automatically
    if snap["kicked_bots"]:
        lines.append("**Kicked bots (re-invite them yourself):** " + ", ".join(f"{b['name']} (`{b['id']}`)" for b in snap["kicked_bots"]))
    if snap["webhooks"]:
        lines.append("**Deleted webhooks (their URLs cannot come back):** " + ", ".join(f"`{w['name']}`" for w in snap["webhooks"]))

    DATA["nuke_snapshots"].pop(gid, None)
    save_data()

    embed = discord.Embed(title="Nuke Restore Complete", description="\n".join(lines), color=EMBED_COLOR)
    embed.set_footer(text=BOT_NAME)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ======================================================
# SHARED HELPERS FOR OWNER / ADMIN COMMANDS
# ======================================================

def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.id == DEVELOPER_ID:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


async def require_admin(interaction: discord.Interaction) -> bool:
    if is_admin(interaction):
        return True
    await interaction.response.send_message("Administrator permission required.", ephemeral=True)
    return False


async def require_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id == DEVELOPER_ID:
        return True
    await interaction.response.send_message(f"Only the {BOT_NAME} developer can use this command.", ephemeral=True)
    return False


# ======================================================
# ROBLOX TRACKING  (/hitlist alerts)
# ======================================================

def presence_in_game(p) -> bool:
    """Roblox hides placeId/gameId when joins are off, so type 2 alone means 'in a game'."""
    return bool(p) and p.get("userPresenceType") == 2


def presence_sig(p) -> str:
    return f"{p.get('gameId') or 'private'}:{p.get('placeId') or p.get('rootPlaceId') or p.get('lastLocation') or 'unknown'}"


async def roblox_presence(session: aiohttp.ClientSession, user_ids: list[int]):
    """Return {user_id: presence_dict}, retrying transient Roblox failures."""
    global _roblox_csrf, _presence_warned

    if session is None:
        return None

    headers = {"Content-Type": "application/json"}
    if ROBLOX_COOKIE:
        headers["Cookie"] = f".ROBLOSECURITY={ROBLOX_COOKIE}"

    for attempt in range(3):
        if _roblox_csrf:
            headers["X-CSRF-TOKEN"] = _roblox_csrf

        try:
            async with session.post(
                "https://presence.roblox.com/v1/presence/users",
                json={"userIds": user_ids},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 403 and resp.headers.get("x-csrf-token"):
                    _roblox_csrf = resp.headers["x-csrf-token"]
                    continue

                if resp.status == 200:
                    _presence_warned = False
                    data = await resp.json(content_type=None)
                    return {
                        p["userId"]: p
                        for p in data.get("userPresences", [])
                        if "userId" in p
                    }

                if resp.status == 429 or 500 <= resp.status < 600:
                    if attempt < 2:
                        retry_after = resp.headers.get("Retry-After")
                        try:
                            delay = min(float(retry_after), 3.0) if retry_after else 0.8 * (attempt + 1)
                        except (TypeError, ValueError):
                            delay = 0.8 * (attempt + 1)
                        await asyncio.sleep(delay)
                        continue

                if not _presence_warned:
                    _presence_warned = True
                    print(
                        f"[{BOT_NAME}] Roblox presence returned HTTP {resp.status}. "
                        "Set a valid ROBLOX_COOKIE in your .env to enable /hitlist and /see."
                    )
                return None

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < 2:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            print(f"[{BOT_NAME}] Roblox presence request failed: {exc}")
            return None
        except Exception as exc:
            print(f"[{BOT_NAME}] Roblox presence request failed: {exc}")
            return None

    return None


async def roblox_user_details(session: aiohttp.ClientSession, roblox_id: int):
    try:
        async with session.get(
            f"https://users.roblox.com/v1/users/{roblox_id}", timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("displayName"), data.get("name")
    except Exception:
        pass
    return None, None


async def roblox_avatar_url(session: aiohttp.ClientSession, roblox_id: int):
    try:
        async with session.get(
            "https://thumbnails.roblox.com/v1/users/avatar-bust",
            params={"userIds": roblox_id, "size": "150x150", "format": "Png", "isCircular": "false"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                item = (data.get("data") or [None])[0]
                if item and item.get("state") == "Completed":
                    return item.get("imageUrl")
    except Exception:
        pass
    return None


async def roblox_game_name(session: aiohttp.ClientSession, universe_id, fallback: str) -> str:
    if not universe_id:
        return fallback or "Unknown Game"
    if universe_id in GAME_NAME_CACHE:
        return GAME_NAME_CACHE[universe_id]
    try:
        async with session.get(
            "https://games.roblox.com/v1/games",
            params={"universeIds": universe_id},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("data") or []
                if items and items[0].get("name"):
                    GAME_NAME_CACHE[universe_id] = items[0]["name"]
                    return items[0]["name"]
    except Exception:
        pass
    return fallback or "Unknown Game"


def tracker_label(t: dict) -> str:
    display = t.get("display_name")
    username = t["username"]
    if display and display.lower() != username.lower():
        return f"{display} (@{username})"
    return f"@{username}"


def build_track_embed(t: dict, info: dict, state: str) -> discord.Embed:
    hit = t["kind"] == "hitlist"
    found = state == "found"
    private = info.get("private", False)

    if not found:
        color = 0xED4245
        title = "TARGET DISCONNECTED" if hit else "PLAYER LEFT THE GAME"
    elif private:
        color = 0xFEE75C
        title = "TARGET FOUND [Private / Join-Off]" if hit else "PLAYER IS PLAYING"
    else:
        color = 0x57F287
        title = "TARGET FOUND" if hit else "PLAYER IS PLAYING"

    embed = discord.Embed(title=title, color=color)
    profile_url = roblox_profile_url(t["roblox_id"])
    embed.add_field(
        name="Target Info",
        value=(
            f"**User:** `{tracker_label(t)}`\n"
            f"**Profile:** [Open Profile]({profile_url})\n"
            f"**Detected:** <t:{info.get('since', int(time.time()))}:R>"
        ),
        inline=True,
    )

    if hit:
        server_type = "Private / Join-Off" if private else "Public"
        game_text = f"**Game:** {info.get('game', 'Unknown Game')}\n**Server:** {server_type}"
        if info.get("region"):
            game_text += f"\n**Region:** {info['region']}"
        embed.add_field(name="Game Info", value=game_text, inline=True)
        job_id = info.get("job_id")
        embed.add_field(
            name="Server Job ID",
            value=box(job_id if job_id else "Hidden (private or join-off server)"),
            inline=False,
        )
        if found and private:
            embed.add_field(
                name="Warning",
                value=(
                    "This player appears to be in a **private server** or has joins turned off. "
                    "A direct join link is not available."
                ),
                inline=False,
            )
    else:
        embed.add_field(name="Game Info", value=f"**Playing:** {info.get('game', 'Unknown Game')}", inline=True)

    if t.get("avatar_url"):
        embed.set_thumbnail(url=t["avatar_url"])
    embed.set_footer(text=f"{BOT_NAME} Hitlist" if hit else f"{BOT_NAME} See")
    return embed


def find_snipe_channel(guild: discord.Guild):
    channel = guild.get_channel(SNIPE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        channel = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == SNIPE_CHANNEL_NAME,
            guild.text_channels,
        )
    return channel


async def ensure_snipe_channel(guild: discord.Guild):
    """Find #snipe, or create it (inside the Cruxer category) if it is missing."""
    channel = find_snipe_channel(guild)
    if channel is not None:
        return channel
    try:
        category = await get_or_create_category(guild)
        return await guild.create_text_channel(SNIPE_CHANNEL_NAME, category=category, reason=f"{BOT_NAME} snipe channel")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[{BOT_NAME}] Could not create #snipe in {guild.name}: {exc}")
        return None


async def get_tracker_channel(t: dict):
    channel_id = t.get("alert_channel_id")
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return channel


async def resolve_snipe_channel(t: dict):
    """Snipe alerts are posted ONLY in #snipe (created automatically if missing)."""
    guild = bot.get_guild(t["guild_id"])
    channel = await ensure_snipe_channel(guild) if guild else None
    if channel is not None:
        t["alert_channel_id"] = channel.id
    return channel


def track_join_view(info: dict):
    """A Join Server / Open Game Page button for a found player, or None."""
    place_id = info.get("place_id")
    view = discord.ui.View(timeout=None)
    if place_id and info.get("job_id") and not info.get("private"):
        url = f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={info['job_id']}"
        view.add_item(discord.ui.Button(label="Join Server", style=discord.ButtonStyle.link, url=url))
    elif place_id:
        url = f"https://www.roblox.com/games/{place_id}"
        view.add_item(discord.ui.Button(label="Open Game Page", style=discord.ButtonStyle.link, url=url))
    return view if view.children else None


async def fill_region_later(t: dict, info: dict, msg: discord.Message):
    """The alert goes out instantly WITHOUT the region; the region (slow lookup) is added to it afterwards."""
    if not msg or info.get("region") or not info.get("place_id") or not info.get("job_id") or info.get("private"):
        return
    region = await detect_server_region(bot.session, info["place_id"], info["job_id"])
    if not region:
        return
    info["region"] = region
    # only edit if the alert is still the live "found" alert
    if t.get("message_id") != msg.id or not t.get("sig"):
        return
    try:
        await msg.edit(embed=build_track_embed(t, info, "found"))
    except discord.HTTPException:
        pass


async def announce_found(t: dict, info: dict):
    channel = await resolve_snipe_channel(t)
    if channel is None:
        return None

    who = tracker_label(t)
    kwargs = {"allowed_mentions": discord.AllowedMentions(users=True)}

    if t["kind"] == "hitlist":
        content = f"<@{t['added_by']}> **Target Detected:** `{who}` is currently in a server!"
        view = track_join_view(info)
        if view:
            kwargs["view"] = view
    else:
        content = f"<@{t['added_by']}> `{who}` is now playing **{info.get('game', 'Unknown Game')}**."

    try:
        msg = await channel.send(content=content, embed=build_track_embed(t, info, "found"), **kwargs)
    except discord.HTTPException as exc:
        print(f"[{BOT_NAME}] Could not send tracker alert: {exc}")
        return None

    if t["kind"] == "hitlist" and not info.get("region"):
        spawn(fill_region_later(t, info, msg))
    return msg


async def announce_left(t: dict):
    if not t.get("message_id"):
        return
    channel = await get_tracker_channel(t)
    if channel is None:
        return

    info = t.get("info") or {}
    who = tracker_label(t)

    if t["kind"] == "hitlist":
        content = f"**Update:** `{who}` is no longer in this server."
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Target Left Server", style=discord.ButtonStyle.secondary, disabled=True))
    else:
        content = f"`{who}` has stopped playing **{info.get('game', 'Unknown Game')}**."
        view = None

    try:
        msg = channel.get_partial_message(t["message_id"])
        await msg.edit(content=content, embed=build_track_embed(t, info, "left"), view=view)
    except discord.HTTPException:
        pass


async def process_tracker(t: dict, presence: dict | None):
    in_game = presence_in_game(presence)

    if in_game:
        t["miss"] = 0
        sig = presence_sig(presence)
        if t.get("sig") == sig:
            return

        # Moved to a different server/game: close out the previous alert first.
        if t.get("sig"):
            await announce_left(t)

        info = await presence_to_info(presence)
        t["sig"] = sig  # set BEFORE sending so a slow send can never double-fire
        t["info"] = info
        msg = await announce_found(t, info)
        t["message_id"] = msg.id if msg else None
        save_data()
        return

    # Not in a game. Require two misses in a row so one flaky poll does not spam.
    if t.get("sig"):
        t["miss"] = t.get("miss", 0) + 1
        # Joins-off targets have no server id, so a rejoin can only be noticed by re-arming fast.
        needed = 1 if str(t["sig"]).startswith("private:") else 2
        if t["miss"] >= needed:
            await announce_left(t)
            t["sig"] = None
            t["miss"] = 0
            save_data()


@tasks.loop(seconds=TRACKER_POLL_SECONDS)
async def tracker_loop():
    trackers = [t for t in DATA["trackers"].values() if t["kind"] == "hitlist"]
    if not trackers or bot.session is None:
        return

    ids = list({t["roblox_id"] for t in trackers})
    presences: dict = {}
    for i in range(0, len(ids), 50):
        result = await roblox_presence(bot.session, ids[i:i + 50])
        if result is None:
            return  # Roblox refused or failed this tick; try again next time
        presences.update(result)

    await asyncio.gather(
        *(process_tracker(t, presences.get(t["roblox_id"])) for t in trackers),
        return_exceptions=True,
    )


@tracker_loop.before_loop
async def before_tracker_loop():
    await bot.wait_until_ready()


class TrackerAddModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title="Hitlist - Add Target")
        self.kind = kind
        self.username = discord.ui.TextInput(label="Roblox username", max_length=40)
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        session = interaction.client.session
        typed = self.username.value.strip().lstrip("@")

        roblox = await get_roblox_profile(session, typed)
        if not roblox:
            return await interaction.followup.send(f"I could not find a Roblox account named **{typed}**.", ephemeral=True)
        roblox_id, roblox_name = roblox

        in_guild = [t for t in DATA["trackers"].values() if t["guild_id"] == guild.id and t["kind"] == self.kind]
        if len(in_guild) >= MAX_TRACKED_PER_GUILD:
            return await interaction.followup.send(
                f"This server is already tracking {MAX_TRACKED_PER_GUILD} players. Remove one first.", ephemeral=True
            )

        key = f"{self.kind}:{guild.id}:{roblox_id}"
        if key in DATA["trackers"]:
            return await interaction.followup.send(f"**{roblox_name}** is already being tracked.", ephemeral=True)

        # Everything Roblox-related at the same time instead of one after the other.
        (display_name, _), avatar, snap_presences = await asyncio.gather(
            roblox_user_details(session, roblox_id),
            roblox_avatar_url(session, roblox_id),
            roblox_presence(session, [roblox_id]),
        )

        t = {
            "kind": self.kind,
            "guild_id": guild.id,
            "channel_id": interaction.channel_id,
            "roblox_id": roblox_id,
            "username": roblox_name,
            "display_name": display_name,
            "avatar_url": avatar,
            "added_by": interaction.user.id,
            "sig": None,
            "info": None,
            "message_id": None,
            "miss": 0,
        }
        DATA["trackers"][key] = t

        # Already in a game right now? Alert IMMEDIATELY - no waiting for them to join a new server.
        found_now = False
        snap_p = (snap_presences or {}).get(roblox_id)
        if presence_in_game(snap_p):
            info = await presence_to_info(snap_p)
            t["sig"] = presence_sig(snap_p)
            t["info"] = info
            msg = await announce_found(t, info)
            t["message_id"] = msg.id if msg else None
            found_now = msg is not None
        save_data()
        log_audit(f"{self.kind}_add", guild.id, interaction.user.id, roblox_id=roblox_id, roblox_username=roblox_name)

        label = tracker_label(t)
        snipe = await ensure_snipe_channel(guild)
        where = snipe.mention if snipe else "#snipe (I could not create it - give me Manage Channels)"
        text = f"Now tracking **{label}**. Alerts will be posted only in {where}."
        if found_now:
            text += f"\n**{roblox_name}** is already in a game, so the alert was posted in {where} right now."
        elif snap_presences is not None:
            text += f"\n**{roblox_name}** is not in a game at the moment. I check every {TRACKER_POLL_SECONDS}s and alert you the second they join."
        if not ROBLOX_COOKIE:
            text += (
                "\n\n**Warning:** ROBLOX_COOKIE is not set in the bot's .env, so Roblox will probably "
                "refuse presence checks and no alerts will appear until it is set."
            )
        elif snap_presences is None:
            text += "\n\n**Warning:** Roblox refused the presence check just now (bad/expired ROBLOX_COOKIE?). Try `/hitlist debug`."

        # Answer right away, THEN load the heavy full profile and attach it.
        await interaction.followup.send(text, ephemeral=True)
        try:
            profile_embed = await build_roblox_embed(session, roblox_id, roblox_name)
            await interaction.edit_original_response(content=text, embed=profile_embed)
        except discord.HTTPException:
            pass


def find_tracker_key(guild_id: int, kind: str, username: str):
    wanted = username.strip().lstrip("@").lower()
    for key, t in DATA["trackers"].items():
        if t["guild_id"] == guild_id and t["kind"] == kind and t["username"].lower() == wanted:
            return key
    return None


def make_tracker_autocomplete(kind: str):
    async def autocomplete(interaction: discord.Interaction, current: str):
        current = current.lower()
        choices = []
        for t in DATA["trackers"].values():
            if t["guild_id"] == interaction.guild_id and t["kind"] == kind and current in t["username"].lower():
                choices.append(app_commands.Choice(name=t["username"], value=t["username"]))
        return choices[:25]
    return autocomplete


async def remove_tracker(interaction: discord.Interaction, kind: str, username: str):
    key = find_tracker_key(interaction.guild.id, kind, username)
    if key is None:
        return await interaction.response.send_message(f"**{username}** is not being tracked.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    t = DATA["trackers"][key]
    if t.get("sig"):
        await announce_left(t)
    DATA["trackers"].pop(key, None)
    save_data()
    log_audit(f"{kind}_remove", interaction.guild.id, interaction.user.id, roblox_id=t["roblox_id"], roblox_username=t["username"])
    await interaction.followup.send(f"Stopped tracking **{t['username']}**.", ephemeral=True)


hitlist_group = app_commands.Group(
    name="hitlist",
    description="Get alerts with a join button when a Roblox player joins a game",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)


@hitlist_group.command(name="add", description="Start tracking a Roblox player (asks for their username)")
async def hitlist_add(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.send_modal(TrackerAddModal("hitlist"))


@hitlist_group.command(name="remove", description="Stop tracking a Roblox player")
@app_commands.describe(username="Roblox username to stop tracking")
@app_commands.autocomplete(username=make_tracker_autocomplete("hitlist"))
async def hitlist_remove(interaction: discord.Interaction, username: str):
    if not await require_admin(interaction):
        return
    await remove_tracker(interaction, "hitlist", username)


async def roblox_cookie_status(session: aiohttp.ClientSession):
    """Is the bot's Roblox account (ROBLOX_COOKIE) actually logged in? Returns (ok, text)."""
    if not ROBLOX_COOKIE:
        return False, "ROBLOX_COOKIE is not set"
    try:
        async with session.get(
            "https://users.roblox.com/v1/users/authenticated",
            headers={"Cookie": f".ROBLOSECURITY={ROBLOX_COOKIE}"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                return True, f"logged in as {data.get('name')}"
            return False, f"Roblox rejected the cookie (HTTP {resp.status}) - it is expired or invalid"
    except Exception as exc:
        return False, f"could not reach Roblox ({exc!r})"


@hitlist_group.command(name="debug", description="Show exactly what Roblox reports for every tracked target")
async def hitlist_debug(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    session = interaction.client.session

    ok, cookie_text = await roblox_cookie_status(session)
    targets = [t for t in DATA["trackers"].values() if t["guild_id"] == interaction.guild.id and t["kind"] == "hitlist"]
    lines = [
        f"**Bot Roblox account:** {'OK' if ok else 'PROBLEM'} - {cookie_text}",
        f"**Tracked targets:** {len(targets)}",
        f"**Poll interval:** every {TRACKER_POLL_SECONDS}s",
        "",
    ]

    if targets:
        presences = await roblox_presence(session, [t["roblox_id"] for t in targets][:50])
        if presences is None:
            lines.append("**Presence check FAILED.** Roblox refused the request - this is usually a bad or expired ROBLOX_COOKIE.")
        else:
            for t in targets:
                p = presences.get(t["roblox_id"]) or {}
                ptype = p.get("userPresenceType", 0)
                place = p.get("placeId") or p.get("rootPlaceId")
                job = p.get("gameId")
                if ptype == 2 and job and place:
                    verdict = "In a game, joinable. An alert with a join button should fire."
                elif ptype == 2:
                    verdict = "In a game, but Roblox is hiding the server from the bot's account (joins closed to it). The alert fires without a join link."
                elif ptype == 1:
                    verdict = "Online, not in a game."
                else:
                    verdict = "Offline - or hiding their online status from the bot's account."
                tracked = "alert already sent" if t.get("sig") else "waiting for him to join"
                lines.append(
                    f"**{t['username']}** - {PRESENCE_NAMES.get(ptype, 'Offline')}\n"
                    f"-# lastLocation: `{p.get('lastLocation') or 'none'}` | placeId: `{place or 'hidden'}` | gameId: `{job or 'hidden'}` | state: {tracked}\n"
                    f"-# {verdict}"
                )

    embed = discord.Embed(title="Hitlist Debug", description=_truncate_field(lines, 3900), color=EMBED_COLOR)
    embed.set_footer(text=BOT_NAME)
    await interaction.followup.send(embed=embed, ephemeral=True)


bot.tree.add_command(hitlist_group)


# ======================================================
# /blacklist add | remove   (owner only)
# ======================================================

class BlacklistAddModal(discord.ui.Modal, title="Blacklist Member"):
    user_id = discord.ui.TextInput(label="Discord user ID", min_length=15, max_length=22)
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, max_length=300)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_id.value.strip()
        if not raw.isdigit():
            return await interaction.response.send_message("That is not a valid Discord user ID.", ephemeral=True)

        uid = int(raw)
        if uid == DEVELOPER_ID:
            return await interaction.response.send_message("You cannot blacklist the developer.", ephemeral=True)
        if bot.user and uid == bot.user.id:
            return await interaction.response.send_message("You cannot blacklist the bot itself.", ephemeral=True)

        DATA["blacklist"][str(uid)] = {
            "reason": self.reason.value.strip(),
            "by": interaction.user.id,
            "at": int(time.time()),
        }
        save_data()
        log_audit("blacklist_add", interaction.guild.id, interaction.user.id, discord_id=uid, reason=self.reason.value.strip())

        embed = discord.Embed(title="Member Blacklisted", color=EMBED_COLOR)
        embed.add_field(name="User", value=f"<@{uid}> (`{uid}`)", inline=False)
        embed.add_field(name="Reason", value=box(self.reason.value.strip()), inline=False)
        embed.set_footer(text=BOT_NAME)
        await interaction.response.send_message(embed=embed, ephemeral=True)


blacklist_group = app_commands.Group(name="blacklist", description=f"[OWN] Block members from using {BOT_NAME}", guild_only=True)


@blacklist_group.command(name="add", description="[OWN] Blacklist a member (asks for their user ID and a reason)")
async def blacklist_add(interaction: discord.Interaction):
    if not await require_owner(interaction):
        return
    await interaction.response.send_modal(BlacklistAddModal())


async def blacklist_autocomplete(interaction: discord.Interaction, current: str):
    choices = []
    for uid, entry in DATA["blacklist"].items():
        if current in uid:
            label = f"{uid} - {entry.get('reason', '')}"[:100]
            choices.append(app_commands.Choice(name=label, value=uid))
    return choices[:25]


@blacklist_group.command(name="remove", description="[OWN] Remove a member from the blacklist")
@app_commands.describe(user_id="Discord user ID to unblacklist")
@app_commands.autocomplete(user_id=blacklist_autocomplete)
async def blacklist_remove(interaction: discord.Interaction, user_id: str):
    if not await require_owner(interaction):
        return
    uid = user_id.strip()
    if uid not in DATA["blacklist"]:
        return await interaction.response.send_message("That user is not blacklisted.", ephemeral=True)
    old_entry = DATA["blacklist"].pop(uid, None) or {}
    save_data()
    log_audit("blacklist_remove", interaction.guild.id, interaction.user.id, discord_id=uid, reason=old_entry.get("reason", ""))
    await interaction.response.send_message(f"<@{uid}> (`{uid}`) has been removed from the blacklist.", ephemeral=True)


bot.tree.add_command(blacklist_group)


# ======================================================
# /role add | remove   (owner only)
# ======================================================

role_group = app_commands.Group(name="role", description="[OWN] Give or take roles", guild_only=True)


def role_problem(guild: discord.Guild, role: discord.Role):
    if role.is_default():
        return "You cannot manage the @everyone role."
    if role.managed:
        return "That role is managed by an integration and cannot be assigned manually."
    if role >= guild.me.top_role:
        return "That role is higher than (or equal to) my highest role, so I cannot manage it."
    return None


@role_group.command(name="add", description="[OWN] Give a role to a member")
@app_commands.describe(member="Who gets the role", role="The role to give")
async def role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not await require_owner(interaction):
        return
    problem = role_problem(interaction.guild, role)
    if problem:
        return await interaction.response.send_message(problem, ephemeral=True)
    if role in member.roles:
        return await interaction.response.send_message(f"{member.mention} already has {role.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    await interaction.response.defer()
    try:
        await member.add_roles(role, reason=f"{BOT_NAME} /role add by {interaction.user}")
    except discord.Forbidden:
        return await interaction.followup.send("I do not have permission to give that role.", ephemeral=True)
    await interaction.followup.send(f"Added {role.mention} to {member.mention}.", allowed_mentions=discord.AllowedMentions.none())


@role_group.command(name="remove", description="[OWN] Remove a role from a member")
@app_commands.describe(member="Who loses the role", role="The role to remove")
async def role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not await require_owner(interaction):
        return
    problem = role_problem(interaction.guild, role)
    if problem:
        return await interaction.response.send_message(problem, ephemeral=True)
    if role not in member.roles:
        return await interaction.response.send_message(f"{member.mention} does not have {role.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    await interaction.response.defer()
    try:
        await member.remove_roles(role, reason=f"{BOT_NAME} /role remove by {interaction.user}")
    except discord.Forbidden:
        return await interaction.followup.send("I do not have permission to remove that role.", ephemeral=True)
    await interaction.followup.send(f"Removed {role.mention} from {member.mention}.", allowed_mentions=discord.AllowedMentions.none())


bot.tree.add_command(role_group)


# ======================================================
# STATS RESET WHEN A MEMBER LEAVES A SERVER
# (global leaderboard totals are never touched)
# ======================================================

@bot.event
async def on_member_remove(member: discord.Member):
    gid, uid = str(member.guild.id), str(member.id)
    DATA["stats"]["guilds"].get(gid, {}).pop(uid, None)
    for day_stats in DATA["stats"]["daily"].get(gid, {}).values():
        day_stats.pop(uid, None)
    save_data()


# ======================================================
# COMMAND SYNC (per server = instant, no waiting on Discord's global cache)
# ======================================================

_commands_synced = False


async def sync_guild_commands(guild: discord.Guild):
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[{BOT_NAME}] Synced {len(synced)} commands to {guild.name}: " + ", ".join(sorted(c.name for c in synced)))
        return synced
    except discord.Forbidden:
        print(f"[{BOT_NAME}] Cannot sync commands in {guild.name} ({guild.id}). Re-invite the bot with the applications.commands scope.")
    except Exception as exc:
        print(f"[{BOT_NAME}] Command sync FAILED in {guild.name}: {exc!r}")
    return None


async def sync_all_commands():
    # Wipe the old global registrations so commands do not show up twice.
    try:
        await bot.http.bulk_upsert_global_commands(bot.application_id, [])
    except Exception as exc:
        print(f"[{BOT_NAME}] Could not clear global commands: {exc!r}")
    for guild in bot.guilds:
        await sync_guild_commands(guild)


@bot.command(name="sync")
async def sync_prefix_command(ctx: commands.Context):
    if ctx.author.id != DEVELOPER_ID:
        return
    await sync_all_commands()
    names = ", ".join(sorted(c.name for c in bot.tree.get_commands()))
    await ctx.reply(f"Commands re-synced in {len(bot.guilds)} server(s).\nTop-level commands: {names}\nRestart/refresh Discord (Ctrl+R) if they do not show up.")


# ======================================================
# /sync   (owner only)
# ======================================================

@bot.tree.command(name="sync", description=f"[OWN] Re-register {BOT_NAME}'s slash commands in every server")
@app_commands.guild_only()
async def sync_command(interaction: discord.Interaction):
    if not await require_owner(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    await sync_all_commands()
    names = ", ".join(sorted(f"/{c.name}" for c in bot.tree.get_commands()))
    await interaction.followup.send(
        f"Commands re-synced in {len(bot.guilds)} server(s).\n{names}\n"
        "If something is missing, restart/refresh Discord (Ctrl+R).",
        ephemeral=True,
    )


# ======================================================
# /add   (post a fresh backup panel or leaderboard here)
# ======================================================

@bot.tree.command(name="add", description="Post a fresh backup panel or leaderboard here (deletes the old one)")
@app_commands.describe(item="What should be posted in this channel")
@app_commands.choices(item=[
    app_commands.Choice(name="Backup Panel", value="panel"),
    app_commands.Choice(name="Leaderboard", value="leaderboard"),
])
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def add_command(interaction: discord.Interaction, item: app_commands.Choice[str]):
    if not await require_admin(interaction):
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Use this in a normal text channel.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        if item.value == "panel":
            await refresh_panel(interaction.channel)
            await interaction.followup.send("Old backup panels removed. New panel posted here.", ephemeral=True)
        else:
            await refresh_leaderboard(interaction.channel)
            await interaction.followup.send("Old leaderboards removed. New leaderboard posted here.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I do not have permission to send messages in this channel.", ephemeral=True)


# ======================================================
# AUDIT LOG  (/audit)
# ======================================================

def log_audit(action: str, guild_id: int, by: int, **fields):
    entry = {"ts": int(time.time()), "action": action, "guild_id": guild_id, "by": by}
    entry.update(fields)
    DATA["audit"].append(entry)
    del DATA["audit"][:-1000]  # keep the newest 1000
    save_data()


AUDIT_LABELS = {
    "hitlist_add": "Hitlist - Added",
    "hitlist_remove": "Hitlist - Removed",
    "see_add": "See - Added",
    "see_remove": "See - Removed",
    "blacklist_add": "Blacklist - Added",
    "blacklist_remove": "Blacklist - Removed",
}
AUDIT_FILTERS = {
    "all": None,
    "hitlist": ("hitlist_add", "hitlist_remove"),
    "see": ("see_add", "see_remove"),
    "blacklist": ("blacklist_add", "blacklist_remove"),
}
AUDIT_PAGE_SIZE = 6


class AuditView(discord.ui.View):
    def __init__(self, guild_id: int, filt: str, page: int = 0):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.filt = filt
        self.page = page

        self.prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
        self.prev_btn.callback = self.prev_cb
        self.next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
        self.next_btn.callback = self.next_cb
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def entries(self):
        wanted = AUDIT_FILTERS[self.filt]
        rows = [
            e for e in DATA["audit"]
            if (e.get("guild_id") == self.guild_id) and (wanted is None or e["action"] in wanted)
        ]
        rows.reverse()  # newest first
        return rows

    def build(self) -> discord.Embed:
        rows = self.entries()
        pages = max(1, math.ceil(len(rows) / AUDIT_PAGE_SIZE))
        self.page = max(0, min(self.page, pages - 1))
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= pages - 1

        chunk = rows[self.page * AUDIT_PAGE_SIZE:(self.page + 1) * AUDIT_PAGE_SIZE]
        embed = discord.Embed(title=f"{BOT_NAME} Audit Log - {self.filt.title()}", color=EMBED_COLOR)

        if not chunk:
            embed.description = "Nothing logged yet."
        for e in chunk:
            label = AUDIT_LABELS.get(e["action"], e["action"])
            lines = [f"**By:** <@{e['by']}>"]

            if e["action"].startswith("blacklist"):
                lines.append(f"**User:** <@{e['discord_id']}> (`{e['discord_id']}`)")
                if e.get("reason"):
                    lines.append(f"**Reason:** {e['reason'][:200]}")
                link = DATA["links"].get(str(e["discord_id"]))
                if link:
                    lines.append(f"**Roblox:** [{link['roblox_username']}]({roblox_profile_url(link['roblox_id'])})")
            else:
                url = roblox_profile_url(e.get("roblox_id"))
                lines.append(f"**Roblox:** [{e.get('roblox_username', 'Unknown')}]({url})" if url else f"**Roblox:** {e.get('roblox_username', 'Unknown')}")

            embed.add_field(name=f"{label}  -  <t:{e['ts']}:f>", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"Page {self.page + 1} of {pages} - {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != DEVELOPER_ID:
            await interaction.response.send_message(f"Only the {BOT_NAME} developer can use this.", ephemeral=True)
            return False
        return True

    async def prev_cb(self, interaction: discord.Interaction):
        self.page -= 1
        await interaction.response.edit_message(embed=self.build(), view=self)

    async def next_cb(self, interaction: discord.Interaction):
        self.page += 1
        await interaction.response.edit_message(embed=self.build(), view=self)


@bot.tree.command(name="audit", description="[OWN] See hitlist / blacklist additions and removals")
@app_commands.describe(filter="Which entries to show")
@app_commands.choices(filter=[
    app_commands.Choice(name="Everything", value="all"),
    app_commands.Choice(name="Hitlist", value="hitlist"),
    app_commands.Choice(name="See (old entries)", value="see"),
    app_commands.Choice(name="Blacklist", value="blacklist"),
])
@app_commands.guild_only()
async def audit_command(interaction: discord.Interaction, filter: app_commands.Choice[str] = None):
    if not await require_owner(interaction):
        return
    view = AuditView(interaction.guild.id, filter.value if filter else "all")
    await interaction.response.send_message(embed=view.build(), view=view, ephemeral=True)


# ======================================================
# /view  (tickets created by a member - reads the permanent archive)
# ======================================================

@bot.tree.command(name="view", description="See how many tickets a member created (raid vs backup)")
@app_commands.describe(member="The member to look up")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def view_command(interaction: discord.Interaction, member: discord.Member):
    if not await require_admin(interaction):
        return

    tickets = [
        t for t in DATA["ticket_archive"].values()
        if t.get("guild_id") == interaction.guild.id and t.get("requester_id") == member.id
    ]
    tickets.sort(key=lambda t: t["raid_number"], reverse=True)

    raids = sum(1 for t in tickets if t["type"] == "Raid")
    backups = sum(1 for t in tickets if t["type"] == "Backup")
    won = sum(1 for t in tickets if t.get("result") == "Won")
    lost = sum(1 for t in tickets if t.get("result") == "Lost")
    voided = sum(1 for t in tickets if t.get("result") == "Voided")
    open_now = sum(1 for t in tickets if t["status"] == "open")

    embed = discord.Embed(title=f"Ticket History - {member.display_name}", color=EMBED_COLOR)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name="Tickets Created",
        value=f"**Total:** {len(tickets)}\n**Raid:** {raids}\n**Backup:** {backups}",
        inline=True,
    )
    embed.add_field(
        name="Results",
        value=f"**Won:** {won}\n**Lost:** {lost}\n**Voided:** {voided}\n**Open now:** {open_now}",
        inline=True,
    )
    embed.add_field(
        name="Assists (this server)",
        value=str(get_raid_count(interaction.guild.id, member.id, "server")),
        inline=True,
    )

    link = DATA["links"].get(str(member.id))
    if link:
        embed.add_field(
            name="Linked Roblox",
            value=f"[{link['roblox_username']}]({roblox_profile_url(link['roblox_id'])})",
            inline=False,
        )
    elif tickets:
        t = tickets[0]
        url = roblox_profile_url(t.get("roblox_id"))
        embed.add_field(
            name="Last Roblox Used",
            value=f"[{t['roblox_username']}]({url})" if url else t["roblox_username"],
            inline=False,
        )

    if tickets:
        lines = []
        for t in tickets[:10]:
            result = t.get("result") or ("Open" if t["status"] == "open" else "No result")
            lines.append(f"`#{t['raid_number']}` {t['type']} - {result} - <t:{t['started_at']}:d>")
        embed.add_field(name="Latest Tickets", value="\n".join(lines), inline=False)
    else:
        embed.description = "This member has not created any tickets here."

    embed.set_footer(text=BOT_NAME)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ======================================================
# /link  (safe Roblox account linking -> auto-fill server link / region)
# ======================================================

async def roblox_description(session: aiohttp.ClientSession, roblox_id: int):
    try:
        async with session.get(
            f"https://users.roblox.com/v1/users/{roblox_id}", timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("description") or ""
    except Exception:
        pass
    return None


async def live_server_link(user_id: int):
    """If the member linked Roblox and is in a joinable game right now, return a join link."""
    link = DATA["links"].get(str(user_id))
    if not link or not ROBLOX_COOKIE or bot.session is None:
        return None
    result = await roblox_presence(bot.session, [link["roblox_id"]])
    p = (result or {}).get(link["roblox_id"])
    if not presence_in_game(p):
        return None
    place_id = p.get("placeId") or p.get("rootPlaceId")
    job_id = p.get("gameId")
    if place_id and job_id:
        return f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={job_id}"
    return None


class LinkVerifyView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This is not your link request.", ephemeral=True)
            return False
        return await blacklist_gate(interaction)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = DATA["pending_links"].get(str(interaction.user.id))
        if not pending or pending["expires"] < time.time():
            return await interaction.response.send_message("This request expired. Run /link again.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        description = await roblox_description(interaction.client.session, pending["roblox_id"])
        if description is None:
            return await interaction.followup.send("Could not reach Roblox. Try again in a moment.", ephemeral=True)
        if pending["code"].lower() not in description.lower():
            return await interaction.followup.send(
                f"I could not find `{pending['code']}` in the About section of **{pending['username']}**. "
                "Save your profile, wait a few seconds, and press Verify again.",
                ephemeral=True,
            )

        uid = str(interaction.user.id)
        other = linked_discord_for_roblox(pending["roblox_id"], exclude=interaction.user.id)
        if other:
            DATA["pending_links"].pop(uid, None)
            save_data()
            return await interaction.followup.send(
                f"**{pending['username']}** is already linked to another Discord account. "
                "Ask the developer if that is a mistake.",
                ephemeral=True,
            )
        DATA["links"][uid] = {
            "roblox_id": pending["roblox_id"],
            "roblox_username": pending["username"],
            "linked_at": int(time.time()),
        }
        DATA["configs"].setdefault(uid, {})["roblox_username"] = pending["username"]
        DATA["pending_links"].pop(uid, None)
        save_data()

        await interaction.followup.send(
            f"Linked to **{pending['username']}**. You can now remove the code from your Roblox profile.\n"
            "Your username is autofilled, and when you request help the server link and region are taken "
            "from the server you are in - you will not be asked for them.",
            ephemeral=True,
        )


class LinkModal(discord.ui.Modal, title="Link Roblox Account"):
    username = discord.ui.TextInput(label="Your Roblox username", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        roblox = await get_roblox_profile(interaction.client.session, self.username.value.strip().lstrip("@"))
        if not roblox:
            return await interaction.followup.send("I could not find that Roblox account.", ephemeral=True)

        roblox_id, roblox_name = roblox
        other = linked_discord_for_roblox(roblox_id, exclude=interaction.user.id)
        if other:
            return await interaction.followup.send(
                f"**{roblox_name}** is already linked to another Discord account.", ephemeral=True
            )
        code = "CRUXER-" + secrets.token_hex(3).upper()
        DATA["pending_links"][str(interaction.user.id)] = {
            "roblox_id": roblox_id,
            "username": roblox_name,
            "code": code,
            "expires": int(time.time()) + 900,
        }
        save_data()

        await interaction.followup.send(
            f"To prove **{roblox_name}** is yours:\n"
            f"1. Open your Roblox profile and edit the **About** section.\n"
            f"2. Paste this code anywhere in it: `{code}`\n"
            f"3. Save, then press **Verify** below (valid for 15 minutes).\n\n"
            "I never ask for your password or cookie.",
            view=LinkVerifyView(interaction.user.id),
            ephemeral=True,
        )


@bot.tree.command(name="link", description="Safely link your Roblox account to autofill your requests")
@app_commands.guild_only()
async def link_command(interaction: discord.Interaction):
    await interaction.response.send_modal(LinkModal())


@bot.tree.command(name="unlink", description=f"Unlink your Roblox account from {BOT_NAME}")
@app_commands.guild_only()
async def unlink_command(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    if uid not in DATA["links"]:
        return await interaction.response.send_message("You do not have a linked Roblox account.", ephemeral=True)
    DATA["links"].pop(uid, None)
    save_data()
    await interaction.response.send_message("Your Roblox account has been unlinked.", ephemeral=True)


# ======================================================
# ROBLOX HELPERS (join check, /see, /whois, /frnd)
# ======================================================

PRESENCE_NAMES = {0: "Offline", 1: "Online (not in a game)", 2: "In a game", 3: "In Roblox Studio"}


def iso_to_ts(value: str):
    try:
        value = re.sub(r"\.\d+", "", value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


async def roblox_json(session: aiohttp.ClientSession, url: str, **kwargs):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), **kwargs) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception:
        pass
    return None


def linked_discord_for_roblox(roblox_id: int, exclude: int | None = None):
    for uid, link in DATA["links"].items():
        if link.get("roblox_id") == roblox_id and (exclude is None or int(uid) != exclude):
            return int(uid)
    return None


async def presence_to_info(presence: dict, with_region: bool = False) -> dict:
    place_id = presence.get("placeId") or presence.get("rootPlaceId")
    job_id = presence.get("gameId")
    game = await roblox_game_name(bot.session, presence.get("universeId"), presence.get("lastLocation"))
    info = {"game": game, "place_id": place_id, "job_id": job_id, "private": not (job_id and place_id), "since": int(time.time())}
    if with_region and place_id and job_id:
        info["region"] = await detect_server_region(bot.session, place_id, job_id)
    return info


async def resolve_roblox_for_member(session: aiohttp.ClientSession, member: discord.abc.User):
    """Best guess at a member's Roblox account: linked, then saved config, then last ticket. (id, name) or (None, None)."""
    link = DATA["links"].get(str(member.id))
    if link:
        return link["roblox_id"], link["roblox_username"]
    name = saved_roblox_name(member.id)
    if not name:
        mine = [t for t in DATA["ticket_archive"].values() if t.get("requester_id") == member.id and t.get("roblox_username")]
        mine.sort(key=lambda t: t.get("started_at", 0), reverse=True)
        name = mine[0]["roblox_username"] if mine else None
    found = await get_roblox_profile(session, name) if name else None
    return found if found else (None, None)


def _clean(text: str, limit: int) -> str:
    return (text or "").replace("`", "'")[:limit]


async def build_roblox_embed(session: aiohttp.ClientSession, roblox_id: int, roblox_name: str) -> discord.Embed:
    """The whole Roblox profile (used by /whois, /see and /hitlist add)."""
    user_json, friends, followers, followings, history, groups, avatar, presences = await asyncio.gather(
        roblox_json(session, f"https://users.roblox.com/v1/users/{roblox_id}"),
        roblox_json(session, f"https://friends.roblox.com/v1/users/{roblox_id}/friends/count"),
        roblox_json(session, f"https://friends.roblox.com/v1/users/{roblox_id}/followers/count"),
        roblox_json(session, f"https://friends.roblox.com/v1/users/{roblox_id}/followings/count"),
        roblox_json(session, f"https://users.roblox.com/v1/users/{roblox_id}/username-history", params={"limit": 10}),
        roblox_json(session, f"https://groups.roblox.com/v2/users/{roblox_id}/groups/roles"),
        roblox_avatar_url(session, roblox_id),
        roblox_presence(session, [roblox_id]),
    )
    user_json = user_json or {}
    created = iso_to_ts(user_json.get("created") or "")
    url = roblox_profile_url(roblox_id)
    created_text = f"<t:{created}:F> (<t:{created}:R>)" if created else "Unknown"

    r = discord.Embed(title=f"Roblox - {user_json.get('name', roblox_name)}", url=url, color=EMBED_COLOR)
    if avatar:
        r.set_thumbnail(url=avatar)
    r.add_field(
        name="Profile",
        value=(
            f"**Username:** `{user_json.get('name', roblox_name)}`\n"
            f"**Display name:** `{user_json.get('displayName', 'Unknown')}`\n"
            f"**User ID:** `{roblox_id}`\n"
            f"**Created:** {created_text}\n"
            f"**Verified badge:** {'Yes' if user_json.get('hasVerifiedBadge') else 'No'}\n"
            f"**Banned:** {'Yes' if user_json.get('isBanned') else 'No'}\n"
            f"**Profile:** [Open Profile]({url})"
        ),
        inline=False,
    )
    r.add_field(
        name="Social",
        value=(
            f"**Friends:** {(friends or {}).get('count', '?')}\n"
            f"**Followers:** {(followers or {}).get('count', '?')}\n"
            f"**Following:** {(followings or {}).get('count', '?')}\n"
            f"**Groups:** {len((groups or {}).get('data', [])) if groups else '?'}"
        ),
        inline=True,
    )

    # Presence + join setting (Roblox does not expose privacy settings, so joins are inferred)
    p = (presences or {}).get(roblox_id)
    if presences is None:
        status_text = "Unknown (set a valid ROBLOX_COOKIE to see this)"
        joins_text = "Unknown"
    else:
        p = p or {}
        ptype = p.get("userPresenceType", 0)
        status_text = PRESENCE_NAMES.get(ptype, "Offline")
        if ptype == 2:
            game = await roblox_game_name(session, p.get("universeId"), p.get("lastLocation"))
            status_text += f" - {game}"
            joins_text = "Everyone / allowed (joinable server)" if p.get("gameId") else "Off or private (no join link available)"
        else:
            joins_text = "Only visible while the player is in a game"
    last = iso_to_ts((p or {}).get("lastOnline") or "")
    last_text = f"<t:{last}:R>" if last else "Unknown"
    r.add_field(
        name="Status",
        value=(
            f"**Now:** {status_text}\n"
            f"**Last online:** {last_text}\n"
            f"**Joins:** {joins_text}"
        ),
        inline=True,
    )

    old_names = [h["name"] for h in (history or {}).get("data", [])]
    if old_names:
        r.add_field(name="Previous Usernames", value=_clean(", ".join(old_names), 300), inline=False)

    about = _clean(user_json.get("description", ""), 400)
    r.add_field(name="About", value=box(about or "No description"), inline=False)

    linked_to = linked_discord_for_roblox(roblox_id)
    r.add_field(name="Linked Discord", value=f"<@{linked_to}>" if linked_to else "Not linked to any Discord account", inline=False)
    r.set_footer(text=f"{BOT_NAME} - Roblox privacy settings are not public, joins are inferred from presence")
    return r


# ======================================================
# "JOINS OFF OR NOT IN-GAME" CHECK (shown before a request form)
# ======================================================

async def join_check(user_id: int):
    """Returns ("skip" | "ok" | "blocked", server_link_or_None)."""
    if not ROBLOX_COOKIE or bot.session is None:
        return "skip", None  # cannot check presence without a cookie
    link = DATA["links"].get(str(user_id))
    if not link:
        return ("blocked", None) if REQUIRE_LINK else ("skip", None)

    result = await roblox_presence(bot.session, [link["roblox_id"]])
    if result is None:
        return "skip", None  # Roblox failed; do not block the member
    p = result.get(link["roblox_id"])
    if presence_in_game(p):
        place_id = p.get("placeId") or p.get("rootPlaceId")
        job_id = p.get("gameId")
        if place_id and job_id:
            return "ok", f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={job_id}"
    return "blocked", None


class JoinCheckView(BlacklistGate, discord.ui.LayoutView):
    def __init__(self, request_type: str, user_id: int):
        super().__init__(timeout=600)
        self.request_type = request_type
        self.user_id = user_id

        privacy_btn = discord.ui.Button(
            label="Privacy Settings", style=discord.ButtonStyle.link, url="https://www.roblox.com/my/account#!/privacy"
        )
        link_btn = discord.ui.Button(label="Link Account", style=discord.ButtonStyle.secondary)
        link_btn.callback = self.link_callback
        retry_btn = discord.ui.Button(label="Retry Join Check", style=discord.ButtonStyle.success)
        retry_btn.callback = self.retry_callback

        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                "## Joins Off Or Not In-Game\n"
                "- You have either joins off for everyone, or you are not in-game right now or both.\n"
                "- To let helpers join you, set your experience joins to **Everyone** in "
                "[Roblox settings](https://www.roblox.com/my/account#!/privacy).\n"
                "- **Not linked yet?** Tap **Link Account** below so I can check your status, "
                "then join your game and press **Retry Join Check**."
            ),
            accent_colour=0xFEE75C,
        ))
        self.add_item(discord.ui.ActionRow(privacy_btn, link_btn, retry_btn))

    async def link_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(LinkModal())

    async def retry_callback(self, interaction: discord.Interaction):
        try:
            state, link = await asyncio.wait_for(join_check(interaction.user.id), timeout=2.2)
        except Exception:
            state, link = "blocked", None
        if state == "blocked":
            return await interaction.response.send_message(
                "Still not detected in a joinable game. Join your game, make sure joins are set to "
                "**Everyone**, link your account if you have not, then retry.",
                ephemeral=True,
            )
        await interaction.response.send_modal(RequestModal(self.request_type, interaction.user.id, link))


# ======================================================
# HELPER WATCHER (offline 5 minutes after joining -> warning)
# ======================================================

@tasks.loop(seconds=JOIN_CHECK_POLL_SECONDS)
async def join_watch_loop():
    if not ROBLOX_COOKIE or bot.session is None:
        return
    now = int(time.time())

    watch = []
    for cid, ticket in list(DATA["tickets"].items()):
        if ticket["status"] != "open":
            continue
        for uid, h in ticket["helpers"].items():
            if h.get("roblox_id") and not h.get("seen_online"):
                watch.append((cid, ticket, uid))
    if not watch:
        return

    ids = list({ticket["helpers"][uid]["roblox_id"] for _, ticket, uid in watch})
    presences: dict = {}
    for i in range(0, len(ids), 50):
        result = await roblox_presence(bot.session, ids[i:i + 50])
        if result is None:
            return
        presences.update(result)

    changed = set()
    for cid, ticket, uid in watch:
        h = ticket["helpers"][uid]
        p = presences.get(h["roblox_id"])
        if not p:
            continue
        if p.get("userPresenceType", 0) != 0:
            h["seen_online"] = True
            changed.add(cid)
            continue
        if not h.get("warned") and now - h.get("joined_at", now) >= JOIN_WARNING_SECONDS:
            h["warned"] = True
            changed.add(cid)
            channel = bot.get_channel(int(cid))
            if channel is None:
                continue
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Open Server", style=discord.ButtonStyle.link, url=ticket["server_link"]))
            try:
                await channel.send(
                    f"Warning: <@{uid}> joined this {ticket['type'].lower()} over {JOIN_WARNING_SECONDS // 60} minutes ago, "
                    f"but their Roblox account **{h['username']}** still shows **Offline**. Please join the server now.",
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass

    for cid in changed:
        save_ticket(int(cid), DATA["tickets"][cid])


@join_watch_loop.before_loop
async def before_join_watch_loop():
    await bot.wait_until_ready()


async def warn_offline_helpers(guild: discord.Guild, ticket: dict):
    """When a raid ends: warn in #logs about helpers who were never seen online on Roblox."""
    if not ROBLOX_COOKIE or bot.session is None or not ticket["helper_order"]:
        return

    check_ids = [h["roblox_id"] for h in ticket["helpers"].values() if h.get("roblox_id") and not h.get("seen_online")]
    presences = await roblox_presence(bot.session, check_ids[:50]) if check_ids else {}
    if presences is None:
        return

    problems = []
    for uid in ticket["helper_order"]:
        h = ticket["helpers"].get(uid, {})
        if not h.get("roblox_id"):
            problems.append(f"<@{uid}> - Roblox account `{h.get('username', 'unknown')}` was not found")
            continue
        if h.get("seen_online"):
            continue
        p = presences.get(h["roblox_id"])
        if p and p.get("userPresenceType", 0) != 0:
            h["seen_online"] = True
            continue
        if p is not None:
            roblox_url = roblox_profile_url(h["roblox_id"])
            problems.append(f"<@{uid}> - [{h['username']}]({roblox_url}) never showed online on Roblox")

    if not problems:
        return
    logs_channel = await get_logs_channel(guild)
    if not logs_channel:
        return
    embed = discord.Embed(
        title=f"Offline Helper Warning - {ticket['type']} #{ticket['raid_number']}",
        description="These helpers joined this raid but were still not online when it ended:\n" + "\n".join(problems),
        color=0xED4245,
    )
    embed.set_footer(text=BOT_NAME)
    await logs_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


# ======================================================
# /see  (instant lookup)
#   not in a game        -> says he is not in a server
#   in game, joins on    -> everything: game, join link, region, full profile (also posted in #snipe)
#   in game, joins off   -> the game he is playing + basic info only
# ======================================================

@bot.tree.command(name="see", description="Find out if a Roblox player is in a server right now (full info when their joins are on)")
@app_commands.describe(username="Roblox username to look up")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def see_command(interaction: discord.Interaction, username: str):
    if not await require_admin(interaction):
        return

    await interaction.response.defer(ephemeral=True)
    session = interaction.client.session
    if session is None:
        return await interaction.followup.send(
            "Roblox lookup is still starting up. Try /see again in a few seconds.",
            ephemeral=True,
        )

    typed_username = username.strip().lstrip("@")

    try:
        roblox = await get_roblox_profile(session, typed_username)
        if not roblox:
            return await interaction.followup.send(
                f"I could not find a Roblox account named **{username}**.",
                ephemeral=True,
            )

        roblox_id, roblox_name = roblox
        presences = await roblox_presence(session, [roblox_id])

        if presences is None:
            return await interaction.followup.send(
                "Roblox did not return a presence result after a few attempts. "
                "Try `/see` again in a moment. If this keeps happening, check ROBLOX_COOKIE.",
                ephemeral=True,
            )

        p = presences.get(roblox_id) or {}

        if not presence_in_game(p):
            status = PRESENCE_NAMES.get(p.get("userPresenceType", 0), "Offline")
            result = f"**{roblox_name}** is **not in a server** right now (status: **{status}**)."
            last = iso_to_ts(p.get("lastOnline") or "")
            if last:
                result += f" Last online <t:{last}:R>."
            result += "\nUse `/hitlist add` to be alerted the moment they join."
            return await interaction.followup.send(result, ephemeral=True)

        display_name, _ = await roblox_user_details(session, roblox_id)
        avatar = await roblox_avatar_url(session, roblox_id)
        t = {
            "kind": "hitlist",
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel_id,
            "roblox_id": roblox_id,
            "username": roblox_name,
            "display_name": display_name,
            "avatar_url": avatar,
            "added_by": interaction.user.id,
        }
        joins_on = bool(p.get("gameId") and (p.get("placeId") or p.get("rootPlaceId")))

        if not joins_on:
            info = await presence_to_info(p)
            url = roblox_profile_url(roblox_id)
            details = await roblox_json(session, f"https://users.roblox.com/v1/users/{roblox_id}") or {}
            created = iso_to_ts(details.get("created") or "")
            embed = discord.Embed(
                title=f"{roblox_name} is in a server (joins off)",
                url=url,
                color=0xFEE75C,
            )
            if avatar:
                embed.set_thumbnail(url=avatar)
            embed.add_field(
                name="Basic Info",
                value=(
                    f"**Username:** `{roblox_name}`\n"
                    f"**Display name:** `{display_name or 'Unknown'}`\n"
                    f"**User ID:** `{roblox_id}`\n"
                    f"**Created:** {f'<t:{created}:R>' if created else 'Unknown'}\n"
                    f"**Profile:** [Open Profile]({url})"
                ),
                inline=False,
            )
            embed.add_field(
                name="Playing",
                value=(
                    f"**Game:** {info['game']}\n"
                    "**Joins:** Off or private server - no join link or server info is available"
                ),
                inline=False,
            )
            embed.set_footer(text=f"{BOT_NAME} See")
            return await interaction.followup.send(
                embed=embed,
                view=track_join_view(info),
                ephemeral=True,
            )

        info = await presence_to_info(p, with_region=True)
        profile_embed = await build_roblox_embed(session, roblox_id, roblox_name)
        found_embed = build_track_embed(t, info, "found")
        if info.get("region") is None:
            found_embed.add_field(name="Region", value="Could not detect", inline=True)

        return await interaction.followup.send(
            content=(
                f"**{roblox_name}** is **in a server** right now and their joins are on. "
                "The full result is shown below."
            ),
            embeds=[found_embed, profile_embed],
            view=track_join_view(info),
            ephemeral=True,
        )

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        print(f"[{BOT_NAME}] /see Roblox request failed for {typed_username}: {exc}")
        return await interaction.followup.send(
            "The Roblox lookup timed out. Please try `/see` again in a moment.",
            ephemeral=True,
        )
    except discord.HTTPException as exc:
        print(f"[{BOT_NAME}] /see Discord response failed: {exc}")
        return await interaction.followup.send(
            "I found the player, but Discord could not send the result. Please try `/see` again.",
            ephemeral=True,
        )
    except Exception as exc:
        print(f"[{BOT_NAME}] /see unexpected error for {typed_username}: {exc!r}")
        return await interaction.followup.send(
            "Something went wrong while checking that player. Please try `/see` again.",
            ephemeral=True,
        )


# ======================================================
# /whois  (owner only, PUBLIC reply)
# ======================================================

@bot.tree.command(name="whois", description="[OWN] Full Roblox profile and Discord info for a member or Roblox user")
@app_commands.describe(member="Discord member to look up", roblox="Roblox username to look up")
@app_commands.guild_only()
async def whois_command(interaction: discord.Interaction, member: discord.Member = None, roblox: str = None):
    if not await require_owner(interaction):
        return
    if member is None and not roblox:
        return await interaction.response.send_message("Give a Discord member, a Roblox username, or both.", ephemeral=True)

    await interaction.response.defer()  # public on purpose
    session = interaction.client.session
    guild = interaction.guild

    # ---- work out which Roblox account to show ----
    roblox_id = roblox_name = None
    if roblox:
        found = await get_roblox_profile(session, roblox.strip().lstrip("@"))
        if not found:
            return await interaction.followup.send(f"I could not find a Roblox account named **{roblox}**.")
        roblox_id, roblox_name = found
        if member is None:
            did = linked_discord_for_roblox(roblox_id)
            if did:
                member = guild.get_member(did)
    else:
        roblox_id, roblox_name = await resolve_roblox_for_member(session, member)

    embeds = []

    # ---- Discord info ----
    if member is not None:
        banner = None
        try:
            fetched = await interaction.client.fetch_user(member.id)
            banner = fetched.banner.url if fetched.banner else None
        except discord.HTTPException:
            pass

        d = discord.Embed(title=f"Discord - {member.display_name}", color=EMBED_COLOR)
        d.set_thumbnail(url=member.display_avatar.url)
        if banner:
            d.set_image(url=banner)
        d.add_field(
            name="Account",
            value=(
                f"**Username:** `{member.name}`\n**ID:** `{member.id}`\n**Mention:** {member.mention}\n"
                f"**Bot:** {'Yes' if member.bot else 'No'}\n"
                f"**Created:** <t:{int(member.created_at.timestamp())}:F> (<t:{int(member.created_at.timestamp())}:R>)"
            ),
            inline=False,
        )
        joined = int(member.joined_at.timestamp()) if member.joined_at else None
        notable = [
            n.replace("_", " ").title()
            for n in ("administrator", "manage_guild", "manage_channels", "manage_roles", "kick_members", "ban_members")
            if getattr(member.guild_permissions, n)
        ]
        flags = [f.name.replace("_", " ").title() for f in member.public_flags.all()]
        joined_text = f"<t:{joined}:F> (<t:{joined}:R>)" if joined else "Unknown"
        boost_text = f"<t:{int(member.premium_since.timestamp())}:R>" if member.premium_since else "No"
        d.add_field(
            name="In This Server",
            value=(
                f"**Joined:** {joined_text}\n"
                f"**Top role:** {member.top_role.mention}\n"
                f"**Key permissions:** {', '.join(notable) or 'None'}\n"
                f"**Timed out:** {'Yes' if member.timed_out_until else 'No'}\n"
                f"**Boosting since:** {boost_text}"
            ),
            inline=False,
        )
        role_lines = [r.mention for r in reversed(member.roles) if not r.is_default()]
        d.add_field(name=f"Roles ({len(role_lines)})", value=_truncate_field(role_lines), inline=False)
        if flags:
            d.add_field(name="Badges", value=", ".join(flags), inline=False)

        tickets = [t for t in DATA["ticket_archive"].values() if t.get("guild_id") == guild.id and t.get("requester_id") == member.id]
        bl = DATA["blacklist"].get(str(member.id))
        bl_text = ("Yes - " + _clean(bl.get("reason", ""), 150)) if bl else "No"
        d.add_field(
            name=BOT_NAME,
            value=(
                f"**Assists (server):** {get_raid_count(guild.id, member.id, 'server')}\n"
                f"**Assists (global):** {get_raid_count(guild.id, member.id, 'global')}\n"
                f"**Tickets created:** {len(tickets)} "
                f"({sum(1 for t in tickets if t['type'] == 'Raid')} raid, {sum(1 for t in tickets if t['type'] == 'Backup')} backup)\n"
                f"**Blacklisted:** {bl_text}"
            ),
            inline=False,
        )
        d.set_footer(text=BOT_NAME)
        embeds.append(d)

    # ---- Roblox info ----
    if roblox_id:
        embeds.append(await build_roblox_embed(session, roblox_id, roblox_name))
    elif member is not None:
        embeds.append(discord.Embed(
            description=f"No Roblox account found for {member.mention}. They have not linked one or made a request yet.",
            color=EMBED_COLOR,
        ))

    await interaction.followup.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())


# ======================================================
# /frnd request  (owner only)
#   Roblox: friend request from the bot's Roblox account (ROBLOX_COOKIE).
#   Discord: bots are not allowed to send friend requests, so the member gets a DM
#            with a button that opens your profile so they can add you.
# ======================================================

frnd_group = app_commands.Group(name="frnd", description="[OWN] Friend requests", guild_only=True)


async def send_roblox_friend_request(session: aiohttp.ClientSession, target_roblox_id: int):
    """Returns (ok, message)."""
    if not ROBLOX_COOKIE:
        return False, "No ROBLOX_COOKIE is set, so I cannot send Roblox friend requests."
    status, data, headers = await roblox_authed_post(
        session,
        f"https://friends.roblox.com/v1/users/{target_roblox_id}/request-friendship",
        {"friendshipOriginSourceType": 0},
    )
    if status is None:
        return False, "Could not reach Roblox."
    if status == 200 and isinstance(data, dict) and data.get("success"):
        return True, "Friend request sent."
    if status == 401:
        return False, "The bot's ROBLOX_COOKIE is invalid or expired."
    if status == 403 and any(k.lower().startswith("rblx-challenge") for k in headers):
        return False, "Roblox asked for a captcha / verification, so it blocked the request. Send it manually from the bot's Roblox account once."
    errors = (data or {}).get("errors") if isinstance(data, dict) else None
    if errors:
        return False, f"Roblox refused: {errors[0].get('message', 'unknown error')}"
    if isinstance(data, dict) and data.get("isCaptchaRequired"):
        return False, "Roblox requires a captcha for this request."
    return False, f"Roblox refused the request (HTTP {status})."


@frnd_group.command(name="request", description="[OWN] Send a friend request on Roblox (and a Discord friend DM) to a person")
@app_commands.describe(member="Discord member (their linked/saved Roblox account is used)", roblox="Roblox username (if no member, or to override)")
async def frnd_request(interaction: discord.Interaction, member: discord.Member = None, roblox: str = None):
    if not await require_owner(interaction):
        return
    if member is None and not roblox:
        return await interaction.response.send_message("Give a Discord member, a Roblox username, or both.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    session = interaction.client.session
    lines = []

    # ---- Roblox ----
    roblox_id = roblox_name = None
    if roblox:
        found = await get_roblox_profile(session, roblox.strip().lstrip("@"))
        if found:
            roblox_id, roblox_name = found
        else:
            lines.append(f"**Roblox:** I could not find an account named **{roblox}**.")
    elif member is not None:
        roblox_id, roblox_name = await resolve_roblox_for_member(session, member)
        if not roblox_id:
            lines.append(f"**Roblox:** no Roblox account is linked or saved for {member.mention}. Give a username in the `roblox` option.")

    if roblox_id:
        ok, msg = await send_roblox_friend_request(session, roblox_id)
        lines.append(f"**Roblox ({roblox_name}):** {msg}")

    # ---- Discord ----
    if member is not None:
        if member.bot:
            lines.append("**Discord:** that member is a bot.")
        else:
            embed = discord.Embed(
                title="Friend request",
                description=(
                    f"<@{interaction.user.id}> from **{interaction.guild.name}** would like to be your friend. "
                    "Press the button below to open their profile and add them."
                ),
                color=EMBED_COLOR,
            )
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Open Profile", style=discord.ButtonStyle.link, url=f"https://discord.com/users/{DEVELOPER_ID}"))
            try:
                await member.send(embed=embed, view=view)
                lines.append(
                    "**Discord:** Discord does not let bots send friend requests, so I DMed them a button that opens your profile. "
                    "They (or you, from their profile) still have to press Add Friend."
                )
            except discord.HTTPException:
                lines.append(
                    "**Discord:** Discord does not let bots send friend requests and their DMs are closed. "
                    f"Add them yourself: <@{member.id}> (`{member.id}`)."
                )

    await interaction.followup.send("\n".join(lines), ephemeral=True)


bot.tree.add_command(frnd_group)


# ======================================================
# /tickets-export  (owner only) - permanent ticket archive
# ======================================================

@bot.tree.command(name="tickets-export", description="[OWN] Download every stored ticket as a JSON file")
@app_commands.describe(all_servers="Include tickets from every server (default: this server only)")
@app_commands.guild_only()
async def tickets_export_command(interaction: discord.Interaction, all_servers: bool = False):
    if not await require_owner(interaction):
        return
    rows = {
        k: v for k, v in DATA["ticket_archive"].items()
        if all_servers or v.get("guild_id") == interaction.guild.id
    }
    payload = json.dumps(rows, indent=2).encode("utf-8")
    await interaction.response.send_message(
        f"{len(rows)} ticket(s) in the archive.",
        file=discord.File(io.BytesIO(payload), filename="cruxer_tickets.json"),
        ephemeral=True,
    )


# ======================================================
# DATA BACKUP / RESTORE  (keeps the global leaderboard history safe, even if the host wipes the disk)
# ======================================================

_last_backup_hash: str | None = None


def data_payload() -> bytes:
    return json.dumps(DATA, indent=2).encode("utf-8")


async def send_backup_to_dev(force: bool = False, reason: str = "Scheduled backup") -> str:
    """DM the developer the full data file. Skips when nothing changed since the last backup (unless forced)."""
    global _last_backup_hash
    payload = data_payload()
    digest = hashlib.sha256(payload).hexdigest()
    if not force and digest == _last_backup_hash:
        return "unchanged"
    if len(payload) > 9_500_000:
        print(f"[{BOT_NAME}] Backup file is {len(payload)} bytes - too big to DM. Use /tickets-export or copy the file by hand.")
        return "too_big"
    try:
        dev = bot.get_user(DEVELOPER_ID) or await bot.fetch_user(DEVELOPER_ID)
        stamp = time.strftime("%Y-%m-%d_%H%M", time.gmtime())
        await dev.send(
            f"**{BOT_NAME} data backup** - {reason}\n"
            f"Global leaderboard members: `{len(DATA['stats']['global']):,}` | raids in history: `{len(DATA['global_history']):,}` | "
            f"archived tickets: `{len(DATA['ticket_archive']):,}`\n"
            "-# If the host ever wipes the bot's files, upload this file with /data-restore.",
            file=discord.File(io.BytesIO(payload), filename=f"cruxer_backup_{stamp}.json"),
        )
        _last_backup_hash = digest
        return "sent"
    except discord.HTTPException as exc:
        print(f"[{BOT_NAME}] Could not DM the backup to the developer (are your DMs open?): {exc}")
        return "failed"


@tasks.loop(hours=BACKUP_INTERVAL_HOURS)
async def backup_loop():
    await send_backup_to_dev(force=False)


@backup_loop.before_loop
async def before_backup_loop():
    await bot.wait_until_ready()


def merge_backup(incoming: dict) -> dict:
    """Merge a backup into the live data WITHOUT ever lowering a number or deleting anything."""
    added = {"leaderboard_users": 0, "archive": 0, "history": 0, "blacklist": 0, "links": 0}

    # a plain /tickets-export file (ticket_key -> ticket) is accepted too
    if "stats" not in incoming and incoming and all(isinstance(v, dict) and "raid_number" in v for v in incoming.values()):
        incoming = {"ticket_archive": incoming}

    g = DATA["stats"]["global"]
    for uid, count in ((incoming.get("stats") or {}).get("global") or {}).items():
        if isinstance(count, int) and count > g.get(uid, 0):
            g[uid] = count
            added["leaderboard_users"] += 1

    for gid, users in ((incoming.get("stats") or {}).get("guilds") or {}).items():
        dst = DATA["stats"]["guilds"].setdefault(gid, {})
        for uid, count in (users or {}).items():
            if isinstance(count, int) and count > dst.get(uid, 0):
                dst[uid] = count

    for key, ticket in (incoming.get("ticket_archive") or {}).items():
        if key not in DATA["ticket_archive"]:
            DATA["ticket_archive"][key] = ticket
            added["archive"] += 1

    known = {(h.get("ts"), h.get("guild_id"), h.get("raid_number")) for h in DATA["global_history"]}
    for h in incoming.get("global_history") or []:
        k = (h.get("ts"), h.get("guild_id"), h.get("raid_number"))
        if k not in known:
            DATA["global_history"].append(h)
            known.add(k)
            added["history"] += 1

    for gid, count in (incoming.get("raid_counters") or {}).items():
        if isinstance(count, int) and count > DATA["raid_counters"].get(gid, 0):
            DATA["raid_counters"][gid] = count

    for uid, entry in (incoming.get("blacklist") or {}).items():
        if uid not in DATA["blacklist"]:
            DATA["blacklist"][uid] = entry
            added["blacklist"] += 1
    for uid, entry in (incoming.get("links") or {}).items():
        if uid not in DATA["links"]:
            DATA["links"][uid] = entry
            added["links"] += 1
    for uid, entry in (incoming.get("configs") or {}).items():
        DATA["configs"].setdefault(uid, entry)
    for gid in incoming.get("authorized_guilds") or []:
        if gid not in DATA["authorized_guilds"]:
            DATA["authorized_guilds"].append(gid)

    save_data()
    merge_global_sources()  # also rebuilds totals from the (now bigger) archive
    return added


@bot.tree.command(name="data-backup", description="[OWN] DM yourself a full backup of Cruxer's data right now")
@app_commands.guild_only()
async def data_backup_command(interaction: discord.Interaction):
    if not await require_owner(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    result = await send_backup_to_dev(force=True, reason="Manual backup")
    texts = {
        "sent": "Backup sent to your DMs.",
        "failed": "I could not DM you. Open your DMs for server members and try again.",
        "too_big": "The data file is too big to DM. Copy cruxer_data.json from the host instead.",
    }
    await interaction.followup.send(texts.get(result, "Done."), ephemeral=True)


@bot.tree.command(name="data-restore", description="[OWN] Merge a Cruxer backup file back in (never lowers any count)")
@app_commands.describe(file="A cruxer_backup_*.json / cruxer_data.json / cruxer_tickets.json file")
@app_commands.guild_only()
async def data_restore_command(interaction: discord.Interaction, file: discord.Attachment):
    if not await require_owner(interaction):
        return
    if file.size > 25 * 1024 * 1024:
        return await interaction.response.send_message("That file is too big.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        incoming = json.loads((await file.read()).decode("utf-8"))
        if not isinstance(incoming, dict):
            raise ValueError("not a JSON object")
    except Exception as exc:
        return await interaction.followup.send(f"That is not a valid backup file ({exc!r}).", ephemeral=True)

    added = merge_backup(incoming)
    await interaction.followup.send(
        "Backup merged. Nothing was deleted or lowered.\n"
        f"- Leaderboard members raised/added: `{added['leaderboard_users']}`\n"
        f"- Archived tickets added: `{added['archive']}`\n"
        f"- Raid history entries added: `{added['history']}`\n"
        f"- Blacklist entries added: `{added['blacklist']}`\n"
        f"- Linked accounts added: `{added['links']}`\n"
        f"Global leaderboard now has `{len(DATA['stats']['global']):,}` members.",
        ephemeral=True,
    )


# ======================================================
# NEW SERVER / REMOVED / READY
# ======================================================

@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"Joined new server: {guild.name} ({guild.id})")
    await sync_guild_commands(guild)
    authorized = guild.id in DATA["authorized_guilds"]
    readme = await create_or_update_readme(guild, authorized=authorized)
    if readme:
        print(f"Created/updated Read-me channel in {guild.name}")
    else:
        print(f"Could not create Read-me channel in {guild.name}")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    """Fires after the bot is gone from a server. If it left on purpose the farewell channel was already
    created. If it was KICKED/BANNED it no longer has any access, so a channel is impossible - the best
    we can do is DM the server owner and the developer."""
    print(f"Removed from server: {guild.name} ({guild.id})")
    if guild.id in _self_leaving:
        _self_leaving.discard(guild.id)
        return

    if guild.owner_id:
        try:
            owner = bot.get_user(guild.owner_id) or await bot.fetch_user(guild.owner_id)
            await owner.send(embed=build_farewell_embed(guild.name))
        except discord.HTTPException:
            pass
    try:
        dev = bot.get_user(DEVELOPER_ID) or await bot.fetch_user(DEVELOPER_ID)
        await dev.send(f"{BOT_NAME} was removed from **{guild.name}** (`{guild.id}`) without using /deauthorize.")
    except discord.HTTPException:
        pass


_ready_done = False


@bot.event
async def on_ready():
    global _commands_synced, _ready_done

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Connected to {len(bot.guilds)} server(s). Global leaderboard entries loaded: {len(DATA['stats']['global'])}")

    # on_ready also fires on every reconnect - only do the setup once.
    if _ready_done:
        return
    _ready_done = True

    cookie_ok, cookie_text = await roblox_cookie_status(bot.session)
    print(f"[{BOT_NAME}] Roblox account check: {'OK' if cookie_ok else 'PROBLEM'} - {cookie_text}")

    if DATA_WAS_FRESH and bot.guilds:
        # The data file did not exist at start-up: either the very first run, or the host wiped the disk.
        try:
            dev = bot.get_user(DEVELOPER_ID) or await bot.fetch_user(DEVELOPER_ID)
            await dev.send(
                f"**{BOT_NAME} started with NO data file.** If this is not the very first run, your host wiped the disk. "
                "Upload your latest `cruxer_backup_*.json` with **/data-restore** and the global leaderboard history comes back. "
                "To stop it happening, set CRUXER_DATA_FILE in your .env to a persistent folder."
            )
        except discord.HTTPException:
            pass

    if not _commands_synced:
        _commands_synced = True
        await sync_all_commands()

    for guild in bot.guilds:
        gid = str(guild.id)

        # Authorized servers always get a #snipe channel (hitlist / see alerts only go there).
        if guild.id in DATA["authorized_guilds"]:
            await ensure_snipe_channel(guild)

        # Prefer the channel where the panel/leaderboard currently lives (it may have been moved with /add).
        # refresh_* removes the previous panel/leaderboard, including old legacy (embed) ones.
        panel_channel = (
            guild.get_channel(DATA["panel_channels"].get(gid, 0))
            or guild.get_channel(BATTLE_PANEL_CHANNEL_ID)
            or discord.utils.get(guild.text_channels, name=BATTLE_PANEL_CHANNEL_NAME)
        )
        if isinstance(panel_channel, discord.TextChannel):
            try:
                await refresh_panel(panel_channel)
            except discord.HTTPException:
                pass

        leaderboard_channel = (
            guild.get_channel(DATA["leaderboard_channels"].get(gid, 0))
            or guild.get_channel(LEADERBOARD_CHANNEL_ID)
            or discord.utils.get(guild.text_channels, name=LEADERBOARD_CHANNEL_NAME)
        )
        if isinstance(leaderboard_channel, discord.TextChannel):
            try:
                await refresh_leaderboard(leaderboard_channel)
            except discord.HTTPException:
                pass

    # Tickets that were open before this update get the new Edit button.
    try:
        await upgrade_open_tickets()
    except Exception as exc:
        print(f"[{BOT_NAME}] Could not upgrade open tickets: {exc!r}")


# ======================================================
# START
# ======================================================

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN. Put it in your .env file:\nDISCORD_TOKEN=your_token_here")
    bot.run(TOKEN)
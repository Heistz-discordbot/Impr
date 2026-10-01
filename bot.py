"""
Imperium Discord Bot - discord.py 2.x

Install (Components V2 layouts need discord.py 2.6 or newer):
  pip install -U "discord.py>=2.6" aiohttp python-dotenv

Requires the "Message Content Intent" and "Server Members Intent" turned ON in
the Discord Developer Portal (Bot tab).

The bot also needs these server permissions to use every feature below:
Manage Channels, Manage Roles, Manage Webhooks, Manage Guild (for invites),
Kick Members, View Audit Log, Embed Links, Attach Files.

Data (raid numbers, ticket info, stats, panel/leaderboard message ids,
rate limits, win streaks, saved configuration) is stored in imperium_data.json
next to this file, so it survives restarts.

Changes in this version:
  1. Request panel rebuilt to match the reference layout, with a Raid/Backup
     select menu instead of the "Request Help" button, plus a working
     Configuration button (saves your Roblox username for autofill).
  2. Leaderboard rebuilt to match the video (Global / Server Total / Server
     Daily select, Your Current Stats + My Rank, Rankings + Top 10, pager).
     Only wording change: Help(s) -> Assist(s).
  3. The "Raid #N is completed" log message no longer has any buttons.
  4. When a member joins a raid/backup, only a link to their Roblox profile
     is shown (no username/bio text).
  5. New owner-only command: /bot stats (public message so everyone can see it).
"""

import asyncio
import json
import math
import os
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ======================= CONFIG =======================

TOKEN = os.getenv("DISCORD_TOKEN")

DEVELOPER_ID = 1455426573741330495

GUILD_ID = 1554169015662936114
TICKET_CATEGORY_ID = 1554174156671549553
BATTLE_PANEL_CHANNEL_ID = 1554169017592451175
RAID_RESULTS_CHANNEL_ID = 1554468736440729650
LEADERBOARD_CHANNEL_ID = 1554469313782743050
LOGS_CHANNEL_ID = 1554469729769365555
MVPS_CHANNEL_ID = 1554551411214131351

VIEWER_ROLE_ID = 0
PING_ROLE_ID = 0

EMBED_COLOR = 0x2B2D31

AUTHORIZE_CATEGORY_NAME = "Imperium"
BATTLE_PANEL_CHANNEL_NAME = "battle-panel"
RAID_RESULTS_CHANNEL_NAME = "raid-results"
LEADERBOARD_CHANNEL_NAME = "leaderboard"
LOGS_CHANNEL_NAME = "logs"
MVPS_CHANNEL_NAME = "mvps"
README_CHANNEL_NAME = "read-me"

SUPPORT_SERVER_INVITE = None

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imperium_data.json")

LEADERBOARD_PAGE_SIZE = 10
DURATION_UPDATE_SECONDS = 30
DAILY_REQUEST_LIMIT = 2
ANTINUKE_WINDOW_SECONDS = 600  # look back 10 minutes for recent activity

# ======================================================


# ======================================================
# DATA STORE
# ======================================================

def load_data():
    data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
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
    return data


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=2)


DATA = load_data()
BOT_START_TIME = int(time.time())


def next_raid_number(guild_id: int) -> int:
    gid = str(guild_id)
    DATA["raid_counters"][gid] = DATA["raid_counters"].get(gid, 0) + 1
    save_data()
    return DATA["raid_counters"][gid]


def get_ticket(channel_id: int):
    return DATA["tickets"].get(str(channel_id))


def save_ticket(channel_id: int, ticket: dict):
    DATA["tickets"][str(channel_id)] = ticket
    save_data()


def today_str() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def add_raid_credit(guild_id: int, user_ids):
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

    save_data()


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


def rate_limit_status(guild_id: int, user_id: int, limit: int = DAILY_REQUEST_LIMIT):
    """Check the limit WITHOUT using up a request."""
    entry = DATA["request_limits"].get(str(guild_id), {}).get(str(user_id))
    count = entry["count"] if entry and entry.get("date") == today_str() else 0
    return count < limit, count


def check_and_increment_rate_limit(guild_id: int, user_id: int, limit: int = DAILY_REQUEST_LIMIT):
    gid, uid = str(guild_id), str(user_id)
    today = today_str()
    guild_limits = DATA["request_limits"].setdefault(gid, {})
    entry = guild_limits.get(uid)

    if not entry or entry.get("date") != today:
        entry = {"date": today, "count": 0}

    if entry["count"] >= limit:
        guild_limits[uid] = entry
        save_data()
        return False, entry["count"]

    entry["count"] += 1
    guild_limits[uid] = entry
    save_data()
    return True, entry["count"]


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


# ======================================================
# READ ME
# ======================================================

async def create_or_update_readme(guild: discord.Guild, authorized: bool):
    existing = discord.utils.find(
        lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == README_CHANNEL_NAME,
        guild.text_channels,
    )

    embed = build_readme_embed(guild, authorized)
    view = discord.ui.View(timeout=None)
    if SUPPORT_SERVER_INVITE:
        view.add_item(discord.ui.Button(label="Support Server", style=discord.ButtonStyle.link, url=SUPPORT_SERVER_INVITE))
    view.add_item(discord.ui.Button(label="Open Developer Profile", style=discord.ButtonStyle.link, url=f"https://discord.com/users/{DEVELOPER_ID}"))

    try:
        if existing:
            async for msg in existing.history(limit=10):
                if msg.author == guild.me and msg.embeds:
                    await msg.edit(embed=embed, view=view)
                    return existing
            await existing.send(embed=embed, view=view)
            return existing

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, manage_channels=True),
        }
        channel = await guild.create_text_channel(
            name=README_CHANNEL_NAME,
            overwrites=overwrites,
            topic="Information about Imperium",
            reason="Imperium Read-me channel",
        )
        await channel.send(embed=embed, view=view)
        return channel
    except discord.Forbidden:
        return None


def build_readme_embed(guild: discord.Guild, authorized: bool) -> discord.Embed:
    if authorized:
        description = (
            f"Thank you for authorizing Imperium in {guild.name}.\n\n"
            "Imperium is a raid coordination bot. It lets members request a raid "
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
            "Imperium is a raid coordination bot. Once authorized, it lets "
            "members request a raid or backup, opens a ticket for the request, "
            "tracks who joins, and records the result once the raid is over."
        )

    embed = discord.Embed(title="Imperium", description=description, color=EMBED_COLOR)
    embed.add_field(name="Developer", value=f"<@{DEVELOPER_ID}>", inline=True)
    embed.add_field(name="Status", value="Authorized" if authorized else "Unauthorized", inline=True)
    embed.set_footer(text="Imperium")
    return embed


# ======================================================
# BATTLE PANEL (Components V2 layout + Raid/Backup select)
# ======================================================

class ConfigModal(discord.ui.Modal, title="Configuration"):
    def __init__(self, user_id: int):
        super().__init__()
        saved = DATA["configs"].get(str(user_id), {}).get("roblox_username")
        self.username = discord.ui.TextInput(
            label="Your Roblox username",
            default=saved,
            placeholder="Saved and autofilled on future requests",
            max_length=40,
        )
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        typed = self.username.value.strip()
        roblox = await get_roblox_profile(interaction.client.session, typed)
        name = roblox[1] if roblox else typed

        DATA["configs"][str(interaction.user.id)] = {"roblox_username": name}
        save_data()

        if roblox:
            await interaction.followup.send(f"Configuration saved. Roblox username: **{name}**.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"Saved **{name}**, but I could not find that account on Roblox. "
                "Press Configuration again to edit it.",
                ephemeral=True,
            )


class PanelView(discord.ui.LayoutView):
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
            allowed, count = rate_limit_status(interaction.guild.id, interaction.user.id)

            if not allowed:
                await interaction.response.send_message(
                    f"Rate limit reached ({count}/{DAILY_REQUEST_LIMIT}). Your raid requests reset in a day.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_modal(RequestModal(request_type, interaction.user.id))

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
                f"- **Limit**: {DAILY_REQUEST_LIMIT} requests per member per day."
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
    old_id = DATA["panel_messages"].get(gid)

    if old_id:
        try:
            old_msg = await channel.fetch_message(old_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_msg = await channel.send(view=PanelView())
    DATA["panel_messages"][gid] = new_msg.id
    save_data()
    return new_msg


# ======================================================
# REQUEST FORM
# ======================================================

class RequestModal(discord.ui.Modal):
    def __init__(self, request_type: str, user_id: int = 0):
        super().__init__(title=f"{request_type} Request")
        self.request_type = request_type

        self.region = discord.ui.TextInput(
            label="Server region",
            placeholder="e.g. NA, EU, AS, OCE",
            max_length=30,
        )
        self.reported_players = discord.ui.TextInput(
            label="Reported players / details",
            style=discord.TextStyle.paragraph,
            placeholder="Names of teamers, what happened, etc.",
            max_length=500,
        )
        self.server_link = discord.ui.TextInput(
            label="Server link",
            placeholder="https://www.roblox.com/games/...",
            max_length=300,
        )
        saved_username = DATA["configs"].get(str(user_id), {}).get("roblox_username")
        self.username = discord.ui.TextInput(label="Your Roblox username", default=saved_username, max_length=40)

        self.add_item(self.region)
        self.add_item(self.reported_players)

        self.clan = None
        if request_type == "Raid":
            self.clan = discord.ui.TextInput(label="Enemy guild / clan", required=False, max_length=60)
            self.add_item(self.clan)

        self.add_item(self.server_link)
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction):
        link = self.server_link.value.strip()
        if not link.lower().startswith(("http://", "https://")):
            return await interaction.response.send_message(
                "Server link must start with https://. Try again.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("This can only be used in a server.", ephemeral=True)

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.find(
                lambda c: isinstance(c, discord.CategoryChannel) and c.name.lower() == AUTHORIZE_CATEGORY_NAME.lower(),
                guild.categories,
            )
        if category is None:
            return await interaction.followup.send(
                "Imperium is not authorized in this server yet. Run /authorize first.", ephemeral=True
            )

        for existing in category.text_channels:
            ticket = get_ticket(existing.id)
            if ticket and ticket["status"] == "open" and ticket["requester_id"] == interaction.user.id:
                return await interaction.followup.send(
                    f"You already have an open request: {existing.mention}", ephemeral=True
                )

        allowed, count = check_and_increment_rate_limit(guild.id, interaction.user.id)
        if not allowed:
            return await interaction.followup.send(
                f"Rate limit reached ({count}/{DAILY_REQUEST_LIMIT}). Your raid requests reset in a day.",
                ephemeral=True,
            )

        roblox = await get_roblox_profile(interaction.client.session, self.username.value.strip())
        roblox_id = roblox[0] if roblox else None
        roblox_name = roblox[1] if roblox else self.username.value.strip()

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
                "I'm missing permissions. Give Imperium Manage Channels and try again.", ephemeral=True
            )

        ticket = {
            "guild_id": guild.id,
            "raid_number": raid_number,
            "type": self.request_type,
            "requester_id": interaction.user.id,
            "roblox_username": roblox_name,
            "roblox_id": roblox_id,
            "region": self.region.value.strip(),
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

        await log_ticket_created(guild, ticket, ticket_msg.jump_url, profile_url)

        await interaction.followup.send(f"Request created: {channel.mention}", ephemeral=True)


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

    elapsed = format_duration(int(time.time()) - ticket["started_at"])
    embed.add_field(name="DURATION", value=box(elapsed), inline=False)

    if ticket["helper_order"]:
        helpers_text = "\n".join(helper_line(ticket, uid) for uid in ticket["helper_order"])
    else:
        helpers_text = "No helpers yet."
    embed.add_field(name="HELPERS", value=helpers_text, inline=False)

    embed.set_footer(text="Imperium")
    return embed


# ======================================================
# JOIN RAID MODAL
# ======================================================

class JoinRobloxModal(discord.ui.Modal, title="Join Raid"):
    def __init__(self, channel_id: int, user_id: int = 0):
        super().__init__()
        self.channel_id = channel_id
        saved_username = DATA["configs"].get(str(user_id), {}).get("roblox_username")
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
        ticket["helpers"][uid] = {"username": roblox_name, "roblox_id": roblox_id}
        if uid not in ticket["helper_order"]:
            ticket["helper_order"].append(uid)
        save_ticket(self.channel_id, ticket)

        channel = interaction.channel
        try:
            await channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        except discord.Forbidden:
            pass

        try:
            async for msg in channel.history(limit=20):
                if msg.author == interaction.client.user and msg.embeds:
                    await msg.edit(embed=build_ticket_embed(ticket))
                    break
        except discord.HTTPException:
            pass

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
# TICKET VIEW (Join Raid / End)
# ======================================================

class TicketView(discord.ui.View):
    def __init__(self, channel_id: int, profile_url: str | None = None):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        join_btn = discord.ui.Button(label="Join Raid", style=discord.ButtonStyle.success, custom_id=f"tsb:join:{channel_id}")
        join_btn.callback = self.join_callback
        self.add_item(join_btn)

        end_btn = discord.ui.Button(label="End", style=discord.ButtonStyle.danger, custom_id=f"tsb:end:{channel_id}")
        end_btn.callback = self.end_callback
        self.add_item(end_btn)

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


# ======================================================
# END FLOW: RESULT -> EXPERIENCE -> GANK -> FINISH -> DELETE
# ======================================================

class RaidResultView(discord.ui.View):
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


class PostEndView(discord.ui.View):
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
        add_raid_credit(guild.id, helper_ids)

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
    embed.set_footer(text="Imperium")

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

class DeleteTicketView(discord.ui.View):
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

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.find(
                lambda c: isinstance(c, discord.CategoryChannel) and c.name.lower() == AUTHORIZE_CATEGORY_NAME.lower(),
                guild.categories,
            )

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
        save_ticket(new_channel.id, ticket)
        DATA["tickets"].pop(str(self.channel_id), None)
        save_data()

        profile_url = roblox_profile_url(ticket["roblox_id"])
        await new_channel.send(embed=build_ticket_embed(ticket), view=TicketView(new_channel.id, profile_url))
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
    ("readme", "Read-me", 0, README_CHANNEL_NAME),
]


async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    category = guild.get_channel(TICKET_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        category = discord.utils.find(
            lambda c: isinstance(c, discord.CategoryChannel) and c.name.lower() == AUTHORIZE_CATEGORY_NAME.lower(),
            guild.categories,
        )
    if category is None:
        category = await guild.create_category(AUTHORIZE_CATEGORY_NAME, reason="Imperium /authorize setup")
    return category


async def run_setup(guild: discord.Guild, selected_keys: set):
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
            channel = await guild.create_text_channel(name, category=category, reason="Imperium /authorize setup")
        created[key] = channel

    if guild.id not in DATA["authorized_guilds"]:
        DATA["authorized_guilds"].append(guild.id)
        save_data()

    if created.get("battle_panel"):
        await refresh_panel(created["battle_panel"])
    if created.get("leaderboard"):
        await refresh_leaderboard(created["leaderboard"])

    return category, created


async def find_imperium_channels(guild: discord.Guild):
    found = []
    category = guild.get_channel(TICKET_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        category = discord.utils.find(
            lambda c: isinstance(c, discord.CategoryChannel) and c.name.lower() == AUTHORIZE_CATEGORY_NAME.lower(),
            guild.categories,
        )
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
        await interaction.followup.send("Imperium authorized (manual setup).\n" + "\n".join(lines), ephemeral=True)


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
        await interaction.followup.send("Imperium authorized (auto setup).\n" + "\n".join(lines), ephemeral=True)

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


class LeaderboardView(discord.ui.LayoutView):
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

        container = discord.ui.Container(
            discord.ui.ActionRow(scope_select),
            discord.ui.TextDisplay(
                "Leaderboard for the number of times you helped.\n\n"
                f"**Server:** *{guild_name}*'s server"
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
    old_id = DATA["leaderboard_messages"].get(gid)

    if old_id:
        try:
            old_msg = await channel.fetch_message(old_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    view = await make_leaderboard_view(bot, channel.guild, "global", 0, None)
    new_msg = await channel.send(view=view)
    leaderboard_state[new_msg.id] = {"scope": "global", "page": 0}
    DATA["leaderboard_messages"][gid] = new_msg.id
    save_data()
    return new_msg


# ======================================================
# BOT
# ======================================================

class ImperiumBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # needed for /antinuke and member names
        super().__init__(command_prefix="!", intents=intents)
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

        # Sync globally first. This is safe even when the configured GUILD_ID
        # is wrong, stale, or the bot is no longer in that server.
        try:
            await self.tree.sync()
        except discord.HTTPException as exc:
            print(f"[Imperium] Global slash-command sync failed: {exc}")

        # Guild sync is only an optimization for instant command updates.
        # A bad/inaccessible GUILD_ID must never prevent the bot from starting.
        if GUILD_ID:
            try:
                guild = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"[Imperium] Guild slash commands synced to {GUILD_ID}.")
            except discord.Forbidden:
                print(
                    f"[Imperium] WARNING: Cannot access GUILD_ID {GUILD_ID}. "
                    "Skipping guild command sync; global commands remain available."
                )
            except discord.HTTPException as exc:
                print(f"[Imperium] Guild command sync failed: {exc}. Continuing startup.")

        duration_updater.start()

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()


bot = ImperiumBot()


@tasks.loop(seconds=DURATION_UPDATE_SECONDS)
async def duration_updater():
    now = int(time.time())
    for channel_id_str, ticket in list(DATA["tickets"].items()):
        if ticket["status"] != "open":
            continue
        channel = bot.get_channel(int(channel_id_str))
        if channel is None:
            continue

        elapsed = box(format_duration(now - ticket["started_at"]))
        try:
            async for msg in channel.history(limit=20):
                if msg.author != bot.user or not msg.embeds:
                    continue
                embed = msg.embeds[0]
                for i, field in enumerate(embed.fields):
                    if field.name == "DURATION":
                        embed.set_field_at(i, name="DURATION", value=elapsed, inline=False)
                        await msg.edit(embed=embed)
                        break
                break
        except (discord.HTTPException, discord.NotFound):
            continue


@duration_updater.before_loop
async def before_duration_updater():
    await bot.wait_until_ready()


# ======================================================
# /setup_panel, /leaderboard, /say, /ping
# ======================================================

@bot.tree.command(name="setup_panel", description="Post/refresh the raid request panel in this channel")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_panel(interaction: discord.Interaction):
    await refresh_panel(interaction.channel)
    await interaction.response.send_message("Panel posted.", ephemeral=True)


@bot.tree.command(name="leaderboard", description="Post/refresh the raid leaderboard in this channel")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def leaderboard_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await refresh_leaderboard(interaction.channel)
    await interaction.followup.send("Leaderboard posted.", ephemeral=True)


@bot.tree.command(name="say", description="Make Imperium send a message")
@app_commands.describe(message="The message Imperium should send")
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


@bot.tree.command(name="ping", description="Check Imperium's latency and connection")
@app_commands.guild_only()
async def ping_command(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Imperium is online.\nLatency: {latency_ms} ms")


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

@bot.tree.command(name="authorize", description="Set up Imperium in this server")
@app_commands.guild_only()
async def authorize_command(interaction: discord.Interaction):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message("Only the Imperium developer can use /authorize.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This can only be used in a server.", ephemeral=True)

    lines = "\n".join(f"- {label}" for _, label, _, _ in CHANNEL_TYPES)
    embed = discord.Embed(
        title="Authorize Imperium",
        description=(
            f"This will create an Imperium category in {guild.name} along with:\n{lines}\n\n"
            "Auto Setup creates everything above.\n"
            "Manual Setup lets you choose which channels to create."
        ),
        color=EMBED_COLOR,
    )
    await interaction.response.send_message(embed=embed, view=AuthorizeConfirmView(), ephemeral=True)


@bot.tree.command(name="deauthorize", description="Remove Imperium's setup from this server and leave")
@app_commands.guild_only()
async def deauthorize_command(interaction: discord.Interaction):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message("Only the Imperium developer can use /deauthorize.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This can only be used in a server.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    channels = await find_imperium_channels(guild)
    for ch in channels:
        try:
            await ch.delete(reason="Imperium deauthorized")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    gid = str(guild.id)
    DATA["authorized_guilds"] = [g for g in DATA["authorized_guilds"] if g != guild.id]
    DATA["panel_messages"].pop(gid, None)
    DATA["leaderboard_messages"].pop(gid, None)
    DATA["raid_counters"].pop(gid, None)
    DATA["stats"]["guilds"].pop(gid, None)
    DATA["stats"]["daily"].pop(gid, None)
    DATA["request_limits"].pop(gid, None)
    DATA["win_streaks"].pop(gid, None)
    DATA["tickets"] = {cid: t for cid, t in DATA["tickets"].items() if t.get("guild_id") != guild.id}
    save_data()

    try:
        await interaction.followup.send("Imperium has been removed from this server. Leaving now.", ephemeral=True)
    except discord.HTTPException:
        pass

    await guild.leave()


# ======================================================
# /overview
# ======================================================

@bot.tree.command(name="overview", description="View and manage Imperium's setup in this server")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def overview_command(interaction: discord.Interaction):
    guild = interaction.guild
    gid = str(guild.id)

    open_tickets = sum(1 for t in DATA["tickets"].values() if t.get("guild_id") == guild.id and t["status"] == "open")
    total_raids = DATA["raid_counters"].get(gid, 0)
    server_assists = sum(DATA["stats"]["guilds"].get(gid, {}).values())
    uptime = format_duration(int(time.time()) - BOT_START_TIME)

    embed = discord.Embed(title=f"Imperium Overview - {guild.name}", color=EMBED_COLOR)
    embed.add_field(name="Authorized", value="Yes" if guild.id in DATA["authorized_guilds"] else "No", inline=True)
    embed.add_field(name="Bot Uptime", value=uptime, inline=True)
    embed.add_field(name="Open Tickets", value=str(open_tickets), inline=True)
    embed.add_field(name="Total Raids Logged", value=str(total_raids), inline=True)
    embed.add_field(name="Total Assists (Server)", value=str(server_assists), inline=True)
    embed.set_footer(text="Imperium")

    view = discord.ui.View(timeout=120)

    async def refresh_panel_cb(inter: discord.Interaction):
        panel_channel = guild.get_channel(BATTLE_PANEL_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=BATTLE_PANEL_CHANNEL_NAME)
        if not panel_channel:
            return await inter.response.send_message("Battle panel channel not found.", ephemeral=True)
        await refresh_panel(panel_channel)
        await inter.response.send_message("Panel refreshed.", ephemeral=True)

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
# /bot stats  (owner only, public message)
# ======================================================

bot_group = app_commands.Group(name="bot", description="Imperium bot management", guild_only=True)


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

    embed = discord.Embed(title="Imperium - Bot Stats", color=EMBED_COLOR)
    embed.add_field(name="Authorized in this server", value="Yes" if authorized else "No", inline=True)
    embed.add_field(name="Administrator", value="Yes" if guild.me.guild_permissions.administrator else "No", inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)} ms", inline=True)

    role_lines = [role.mention for role in reversed(guild.me.roles) if not role.is_default()]
    embed.add_field(name=f"Bot Roles ({len(role_lines)})", value=_truncate_field(role_lines), inline=False)

    embed.add_field(
        name=f"Server Stats - {guild.name}",
        value=(
            f"Members: `{guild.member_count or 0:,}`\n"
            f"Text channels: `{len(guild.text_channels)}`\n"
            f"Roles: `{len(guild.roles)}`\n"
            f"Open tickets: `{open_tickets}`\n"
            f"Raids logged: `{total_raids}`\n"
            f"Total assists: `{server_assists}`"
        ),
        inline=False,
    )

    server_lines = []
    for g in sorted(bot.guilds, key=lambda x: (x.member_count or 0), reverse=True):
        flag = "Authorized" if g.id in DATA["authorized_guilds"] else "Unauthorized"
        server_lines.append(f"**{discord.utils.escape_markdown(g.name)}** - {g.member_count or 0:,} members - {flag}")
    embed.add_field(name=f"Servers ({len(bot.guilds)})", value=_truncate_field(server_lines), inline=False)

    embed.set_footer(text=f"Imperium - Uptime {uptime}")
    return embed


class ConfirmLeaveView(discord.ui.View):
    def __init__(self, target_guild_id: int):
        super().__init__(timeout=60)
        self.target_guild_id = target_guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != DEVELOPER_ID:
            await interaction.response.send_message("Only the Imperium developer can do this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Remove", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = bot.get_guild(self.target_guild_id)
        if target is None:
            return await interaction.response.edit_message(content="Imperium is not in that server anymore.", view=None)

        name = target.name
        DATA["authorized_guilds"] = [g for g in DATA["authorized_guilds"] if g != target.id]
        save_data()

        await interaction.response.edit_message(content=f"Imperium has been removed from **{name}**.", view=None)
        try:
            await target.leave()
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class BotStatsView(discord.ui.View):
    def __init__(self, current_guild_id: int):
        super().__init__(timeout=600)
        self.current_guild_id = current_guild_id

        remove_here = discord.ui.Button(label="Remove Bot From This Server", style=discord.ButtonStyle.danger)
        remove_here.callback = self.remove_here_callback
        self.add_item(remove_here)

        guilds = sorted(bot.guilds, key=lambda g: g.name.lower())[:25]
        if guilds:
            options = [
                discord.SelectOption(
                    label=g.name[:100] or str(g.id),
                    value=str(g.id),
                    description=f"{g.member_count or 0:,} members"[:100],
                )
                for g in guilds
            ]
            remove_select = discord.ui.Select(placeholder="Remove the bot from another server", options=options)

            async def select_callback(interaction: discord.Interaction):
                await self._ask_confirm(interaction, int(remove_select.values[0]))

            remove_select.callback = select_callback
            self.add_item(remove_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != DEVELOPER_ID:
            await interaction.response.send_message("Only the Imperium developer can use these controls.", ephemeral=True)
            return False
        return True

    async def _ask_confirm(self, interaction: discord.Interaction, guild_id: int):
        target = bot.get_guild(guild_id)
        name = target.name if target else str(guild_id)
        await interaction.response.send_message(
            f"Are you sure you want to remove Imperium from **{name}**?",
            view=ConfirmLeaveView(guild_id),
            ephemeral=True,
        )

    async def remove_here_callback(self, interaction: discord.Interaction):
        await self._ask_confirm(interaction, self.current_guild_id)


@bot_group.command(name="stats", description="Owner only: bot roles, servers, server stats and authorization")
async def bot_stats_command(interaction: discord.Interaction):
    if interaction.user.id != DEVELOPER_ID:
        return await interaction.response.send_message("Only the Imperium developer can use /bot stats.", ephemeral=True)

    # Not ephemeral - everyone in the channel can see the result.
    await interaction.response.defer()
    embed = build_bot_stats_embed(interaction.guild)
    await interaction.followup.send(embed=embed, view=BotStatsView(interaction.guild.id))


bot.tree.add_command(bot_group)


# ======================================================
# /antinuke
# ======================================================

@bot.tree.command(name="antinuke", description="Lock the server, remove recent bots/webhooks, revoke invites, and undo recent channel creations")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def antinuke_command(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Administrator permission required.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    now = discord.utils.utcnow()
    cutoff = now.timestamp() - ANTINUKE_WINDOW_SECONDS

    protected_channels = {ch.id for ch in await find_imperium_channels(guild)}

    results = []

    locked = 0
    for channel in guild.text_channels:
        try:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.send_messages is not False:
                overwrite.send_messages = False
                await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Imperium /antinuke")
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
                    await member.kick(reason="Imperium /antinuke - recently added bot")
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
                    await wh.delete(reason="Imperium /antinuke")
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
                await invite.delete(reason="Imperium /antinuke")
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
                    await channel.delete(reason="Imperium /antinuke - recently created channel")
                    deleted_channels += 1
                except (discord.Forbidden, discord.HTTPException):
                    continue
    except discord.Forbidden:
        results.append("Missing View Audit Log permission - could not check for recently created channels.")
    else:
        results.append(f"Deleted {deleted_channels} recently created channel(s).")

    embed = discord.Embed(title="Antinuke Sweep Complete", description="\n".join(results), color=EMBED_COLOR)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ======================================================
# NEW SERVER / READY
# ======================================================

@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"Joined new server: {guild.name} ({guild.id})")
    authorized = guild.id in DATA["authorized_guilds"]
    readme = await create_or_update_readme(guild, authorized=authorized)
    if readme:
        print(f"Created/updated Read-me channel in {guild.name}")
    else:
        print(f"Could not create Read-me channel in {guild.name}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Connected to {len(bot.guilds)} server(s).")

    for guild in bot.guilds:
        panel_channel = guild.get_channel(BATTLE_PANEL_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=BATTLE_PANEL_CHANNEL_NAME)
        if panel_channel:
            try:
                await refresh_panel(panel_channel)
            except discord.HTTPException:
                pass

        leaderboard_channel = guild.get_channel(LEADERBOARD_CHANNEL_ID) or discord.utils.get(guild.text_channels, name=LEADERBOARD_CHANNEL_NAME)
        if leaderboard_channel:
            try:
                await refresh_leaderboard(leaderboard_channel)
            except discord.HTTPException:
                pass


# ======================================================
# START
# ======================================================

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN. Put it in your .env file:\nDISCORD_TOKEN=your_token_here")
    bot.run(TOKEN)
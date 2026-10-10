import os, uuid, aiohttp

COOKIE = os.getenv("ROBLOX_COOKIE")
HEADERS = {"User-Agent": "Roblox/WinInet"}


async def get_user_id(session, username):
    async with session.post(
        "https://users.roblox.com/v1/usernames/users",
        json={"usernames": [username], "excludeBannedUsers": False},
    ) as r:
        data = (await r.json()).get("data", [])
        return data[0]["id"] if data else None


async def get_presence(session, user_id):
    async with session.post(
        "https://presence.roblox.com/v1/presence/users",
        json={"userIds": [user_id]},
        cookies={".ROBLOSECURITY": COOKIE},
    ) as r:
        p = (await r.json())["userPresences"][0]
    in_game = p["userPresenceType"] == 2
    return {
        "in_game": in_game,
        "place_id": p.get("placeId"),
        "server_id": p.get("gameId"),  # None if joins are off for the bot's account
    }


def make_join_link(place_id, server_id):
    return f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={server_id}"


async def get_region(session, place_id, server_id):
    cookies = {".ROBLOSECURITY": COOKIE}
    body = {
        "placeId": place_id,
        "gameId": server_id,
        "gameJoinAttemptId": str(uuid.uuid4()),
    }
    url = "https://gamejoin.roblox.com/v1/join-game-instance"
    headers = dict(HEADERS)

    # first call fails with 403 and hands back the CSRF token
    async with session.post(url, json=body, headers=headers, cookies=cookies) as r:
        token = r.headers.get("x-csrf-token")
    headers["x-csrf-token"] = token

    async with session.post(url, json=body, headers=headers, cookies=cookies) as r:
        data = await r.json()

    js = data.get("joinScript") or {}
    ip = None
    if js.get("UdmuxEndpoints"):
        ip = js["UdmuxEndpoints"][0].get("Address")
    ip = ip or js.get("MachineAddress")
    if not ip:
        return "Unknown"

    async with session.get(
        f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city"
    ) as r:
        g = await r.json()
    if g.get("status") != "success":
        return "Unknown"
    return f"{g['city']}, {g['regionName']}, {g['country']}"


async def snipe_info(username):
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        uid = await get_user_id(session, username)
        if not uid:
            return {"status": "not_found"}
        pres = await get_presence(session, uid)
        if not pres["in_game"]:
            return {"status": "offline"}
        if not pres["server_id"]:
            return {"status": "joins_off"}
        return {
            "status": "ok",
            "link": make_join_link(pres["place_id"], pres["server_id"]),
            "region": await get_region(session, pres["place_id"], pres["server_id"]),
        }
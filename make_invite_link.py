"""One named invite link per partner/source, so joins can be counted per source.

    python3 make_invite_link.py sfs-cryptonewsdaily x-bio ig-bio ig-reels --dry-run   # shows what it would create
    python3 make_invite_link.py sfs-cryptonewsdaily x-bio ig-bio ig-reels             # creates the missing ones
    python3 make_invite_link.py --list                                          # name -> link from the local map
    python3 make_invite_link.py --stats                                         # joins per link (Telethon, see below)

Creates links with Bot API createChatInviteLink (env TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID; the bot needs
can_invite_users). Names are max 32 chars. The name -> link map goes to invite_links.json (gitignored: run this
locally, not in Actions, because public-repo logs and artifacts are readable by anyone).

Join counts: the Bot API can't read them. Either
  - Telegram app: channel > Manage channel > Invite links > tap a link ("N joined"), or
  - --stats: uses your own Telethon session (kuoala_user.session, TG_API_ID/TG_API_HASH, as in sfs_prospects.py)
    and messages.getExportedChatInvites, which returns each link's usage count. Read-only.
"""
import json
import os
import re
import sys

import requests

MAP = "invite_links.json"
TOKEN, CHAT = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHANNEL_ID", "")
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def load():
    try:
        return json.load(open(MAP))
    except (OSError, ValueError):
        return {}


def create(names, dry):
    bad = [n for n in names if not NAME_RX.match(n)]
    if bad:
        sys.exit(f"names must be lowercase a-z0-9_- and <=32 chars: {bad}")
    links = load()
    todo = [n for n in names if n not in links]
    for n in names:
        if n in links:
            print(f"exists  {n:32} {links[n]}")
    if dry:
        print("dry run, would create:", todo or "nothing")
        return
    if todo and not (TOKEN and CHAT):
        sys.exit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID")
    for n in todo:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/createChatInviteLink",
                          json={"chat_id": CHAT, "name": n}, timeout=15).json()
        if not r.get("ok"):
            sys.exit(f"{n}: {r.get('description')}")
        links[n] = r["result"]["invite_link"]
        json.dump(links, open(MAP, "w"), indent=1)          # save after each, so a failure loses nothing
        print(f"created {n:32} {links[n]}")


def stats():
    import asyncio
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetExportedChatInvitesRequest

    async def go():
        c = TelegramClient("kuoala_user", int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
        await c.start()
        bot = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10).json()["result"]["username"]
        r = await c(GetExportedChatInvitesRequest(peer="KuoalaOfficial", admin_id=bot, limit=100))
        mine = set(load().values())
        for i in r.invites:
            if i.link in mine or i.title:
                print(f"{(i.title or '-'):32} joined={i.usage or 0:5}  {i.link}")
        await c.disconnect()
    asyncio.run(go())


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--list" in a:
        for k, v in load().items():
            print(f"{k:32} {v}")
    elif "--stats" in a:
        stats()
    elif not [x for x in a if not x.startswith("--")]:
        sys.exit(__doc__)
    else:
        create([x for x in a if not x.startswith("--")], "--dry-run" in a)

"""Set the Telegram channel's display title and description via the Bot API. Rerunnable.

    python3 set_channel_info.py --title "Kuoala | ..." --description "..."            # dry run: shows current vs new
    python3 set_channel_info.py --title "..." --description "..." --apply              # actually changes it

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID. The bot must be a channel admin with can_change_info.
Limits (Bot API): title 1-128 chars, description 0-255 chars.
"""
import argparse
import os
import sys

import requests

API = f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_BOT_TOKEN', '')}"
CHAT = os.environ.get("TELEGRAM_CHANNEL_ID", "")


def call(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=15).json()
    if not r.get("ok"):
        sys.exit(f"{method} failed: {r.get('description')}")
    return r["result"]


def validate(title, desc):
    if title is not None and not 1 <= len(title) <= 128:
        sys.exit(f"title must be 1-128 chars (got {len(title)})")
    if desc is not None and len(desc) > 255:
        sys.exit(f"description must be <=255 chars (got {len(desc)})")
    if desc is not None and "not financial advice" not in desc.lower():
        sys.exit("description must contain 'Not financial advice'")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title")
    ap.add_argument("--description")
    ap.add_argument("--apply", action="store_true", help="without this flag nothing is changed")
    a = ap.parse_args()
    validate(a.title, a.description)
    if not CHAT or API.endswith("bot"):
        sys.exit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID")

    chat = call("getChat", chat_id=CHAT)
    me = call("getMe")
    perm = call("getChatMember", chat_id=CHAT, user_id=me["id"])
    print(f"current title: {chat.get('title')!r}\ncurrent desc:  {chat.get('description')!r}")
    print(f"new title:     {a.title!r}\nnew desc:      {a.description!r} ({len(a.description or '')}/255)")
    if not (perm.get("status") == "creator" or perm.get("can_change_info")):
        sys.exit("bot lacks can_change_info: Channel > Administrators > the bot > enable 'Change channel info'")
    if not a.apply:
        print("dry run: nothing changed (add --apply)")
        return
    if a.title and a.title != chat.get("title"):
        call("setChatTitle", chat_id=CHAT, title=a.title)
    if a.description is not None and a.description != chat.get("description"):
        call("setChatDescription", chat_id=CHAT, description=a.description)
    print("applied")


if __name__ == "__main__":
    main()

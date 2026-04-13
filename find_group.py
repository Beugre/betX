#!/usr/bin/env python3
"""Récupère le chat_id du groupe/channel Telegram (sans purge)."""
import httpx, os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

token = os.getenv("TELEGRAM_BOT_TOKEN")
base = f"https://api.telegram.org/bot{token}"

# Récupérer TOUTES les updates sans purger
r = httpx.get(f"{base}/getUpdates", timeout=15)
updates = r.json().get("result", [])
print(f"Nouveaux updates: {len(updates)}")

seen = set()
for u in updates:
    for key in ["message", "my_chat_member", "chat_member", "channel_post"]:
        if key in u:
            obj = u[key]
            chat = obj.get("chat", obj) if isinstance(obj, dict) else {}
            cid = chat.get("id", "")
            if cid and cid not in seen:
                seen.add(cid)
                print(f"  [{key}] type={chat.get('type','?')} | id={cid} | {chat.get('title', chat.get('first_name','?'))}")

if not updates:
    print("\nAucun nouveau message.")
    print("Envoie un message dans le groupe (ex: /start@BetX_goat_bot)")
    print("Puis relance: .venv/bin/python find_group.py")

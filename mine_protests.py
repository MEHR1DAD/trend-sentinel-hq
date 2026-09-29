import os
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
import json
import re

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

KEYWORDS = ["اعتراض", "اعتصاب", "تجمع", "تظاهرات", "شعار", "درگیری", "تحصن", "بستن خیابان", "گاز اشک"]

async def fetch_messages():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Missing credentials in ENV.")
        return

    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    await client.connect()
    
    tasks = [
        ("VahidOnline", 68821),
        ("iliaen", 1933)
    ]
    
    results = []
    
    for channel, min_id in tasks:
        print(f"Fetching from {channel} starting from {min_id}...")
        count = 0
        match_count = 0
        async for msg in client.iter_messages(channel, min_id=min_id, reverse=True):
            count += 1
            if not msg.text:
                continue
                
            text = msg.text
            # Skip short texts
            if len(text) < 20:
                continue
                
            # Check keywords
            if any(kw in text for kw in KEYWORDS):
                match_count += 1
                # Extract some context around the keywords to see the actual phrasing
                # Or just save the whole text
                results.append({
                    "channel": channel,
                    "id": msg.id,
                    "date": msg.date.isoformat(),
                    "text": text
                })
                
            if count % 1000 == 0:
                print(f"Processed {count} messages from {channel}... found {match_count} matches.")
                
            # Cap at 30,000 messages total per channel to not take forever
            if count >= 30000:
                break
                
        print(f"Finished {channel}. Total processed: {count}. Matches: {match_count}.")
        
    with open("protest_data.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print(f"Saved {len(results)} matching messages to protest_data.json")

if __name__ == "__main__":
    asyncio.run(fetch_messages())

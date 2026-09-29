import os
import json
import asyncio
import re
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

KEYWORDS = ["دلار", "طلا", "تتر", "پوند", "سکه", "یورو", "درهم", "بورس", "ارز"]

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Missing credentials.")
        return
        
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    await client.start()
    
    now = datetime.now(timezone.utc)
    one_year_ago = now - timedelta(days=365)
    
    results = {k: [] for k in KEYWORDS}
    
    print("Fetching VahidOnline messages...")
    count = 0
    try:
        async for msg in client.iter_messages('VahidOnline', offset_date=now):
            if msg.date < one_year_ago:
                break
                
            if not msg.text:
                continue
                
            count += 1
            if count % 1000 == 0:
                print(f"Processed {count} messages... (current date: {msg.date})")
                
            lines = msg.text.split('\n')
            for line in lines:
                for kw in KEYWORDS:
                    if kw in line:
                        # Extract the line or a portion of it to analyze patterns
                        clean_line = line.strip()
                        if len(clean_line) > 10 and clean_line not in results[kw]:
                            results[kw].append(clean_line)
                            
    except Exception as e:
        print(f"Error: {e}")
        
    await client.disconnect()
    
    # Save the sample lines for analysis
    with open("economy_patterns.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print("\nExtraction complete!")
    for kw, lines in results.items():
        print(f"{kw}: {len(lines)} unique mentions found")

if __name__ == "__main__":
    asyncio.run(main())

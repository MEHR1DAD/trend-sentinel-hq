import os
import json
import asyncio
from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.sessions import StringSession

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Missing credentials.")
        return
        
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    await client.start()
    print("Client connected.")
    
    with open('backend/sentinel_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    nodes = config.get('nodes', [])
    print(f"Attempting to join {len(nodes)} channels...")
    
    joined_count = 0
    for node in nodes:
        try:
            print(f"Joining {node}...")
            await client(JoinChannelRequest(node))
            joined_count += 1
            print(f"✅ Successfully joined {node}.")
            await asyncio.sleep(8)  # Anti-flood delay
        except Exception as e:
            if "USER_ALREADY_PARTICIPANT" in str(e):
                print(f"ℹ️ Already a member of {node}.")
            else:
                print(f"❌ Failed to join {node}: {e}")
                
    print(f"Finished. Joined {joined_count} new channels.")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, errors
from telethon.tl.types import InputPeerChannel

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PEER_CACHE_FILE = 'backend/telegram_peer_cache.json'

CHANNELS = ["@DidebanJang", "@DidebanEterazat", "@DidehbanEghtesad"]

def load_peer_cache():
    if os.path.exists(PEER_CACHE_FILE):
        try:
            with open(PEER_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'node_peers': {}, 'id_to_node': {}}

def save_peer_cache(cache):
    try:
        with open(PEER_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

async def resolve_target_entity(client, channel_name, peer_cache):
    clean_name = channel_name.lstrip('@')
    node_peers = peer_cache.get('node_peers', {})
    
    # 1. Lookup in permanent disk cache to avoid ResolveUsernameRequest
    for key in (channel_name, clean_name, f"@{clean_name}"):
        if key in node_peers:
            cdata = node_peers[key]
            cid = cdata.get('channel_id')
            ah = cdata.get('access_hash')
            if cid and ah:
                return InputPeerChannel(channel_id=int(cid), access_hash=int(ah))
    
    # 2. Fallback to Telegram entity resolution and persist to cache
    try:
        entity = await client.get_entity(channel_name)
        cid = getattr(entity, 'id', None)
        ah = getattr(entity, 'access_hash', None)
        if cid and ah:
            peer_cache.setdefault('node_peers', {})[clean_name] = {
                'channel_id': cid,
                'access_hash': ah,
                'peer_id': int(f"-100{cid}")
            }
            save_peer_cache(peer_cache)
            return InputPeerChannel(channel_id=int(cid), access_hash=int(ah))
        return entity
    except Exception as e:
        print(f"⚠️ Could not resolve entity for {channel_name}: {e}")
        return channel_name

async def main():
    if not API_ID or not API_HASH or not BOT_TOKEN:
        print("Missing credentials.")
        return
        
    client = TelegramClient('daily_report_session', int(API_ID), API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    peer_cache = load_peer_cache()
    
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    
    try:
        for channel in CHANNELS:
            count = 0
            try:
                target_peer = await resolve_target_entity(client, channel, peer_cache)
                # Fetch messages from the last 24 hours using cached peer
                async for msg in client.iter_messages(target_peer, limit=100):
                    if msg.date < yesterday:
                        break
                    if msg.text and any(h in msg.text for h in ["هشدار فوری", "خبر فوری", "هشدار منطقه‌ای", "گزارش مردمی", "گزارش اقتصادی"]):
                        count += 1
                        
                print(f"Channel {channel} had {count} alerts today.")
                
                # Post heartbeat ONLY if the channel was completely quiet
                if count == 0:
                    report = (
                        "🌙 **گزارش شبانه رادار دیده‌بان**\n\n"
                        "وضعیت: 🟢 آرامش نسبی\n\n"
                        "در ۲۴ ساعت گذشته هیچ‌گونه فعالیت غیرعادی یا هشدار مهمی در این رادار ثبت نشده است. "
                        "سیستم به صورت ۲۴ ساعته در حال پایش لحظه‌ای منابع می‌باشد.\n\n"
                        "🤖 *Powered by Sentinel AI*"
                    )
                    await client.send_message(target_peer, report, silent=True)
                    print(f"Sent 'all quiet' report to {channel}")
                else:
                    print(f"Skipping daily report for {channel} since it had {count} alerts.")
                    
            except errors.FloodWaitError as fwe:
                print(f"⚠️ FloodWaitError on {channel}! Sleeping for {fwe.seconds}s...")
                await asyncio.sleep(fwe.seconds + 2)
            except Exception as e:
                print(f"Error processing {channel}: {e}")
            
            await asyncio.sleep(1.0)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

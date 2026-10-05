import os
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

CHANNELS = ["@DidebanJang", "@DidebanEterazat", "@DidehbanEghtesad"]

async def main():
    if not API_ID or not API_HASH or not BOT_TOKEN:
        print("Missing credentials.")
        return
        
    client = TelegramClient('daily_report_session', int(API_ID), API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    
    for channel in CHANNELS:
        count = 0
        try:
            # Fetch messages from the last 24 hours
            async for msg in client.iter_messages(channel):
                if msg.date < yesterday:
                    break
                if msg.text and ("هشدار فوری" in msg.text or "گزارش مردمی" in msg.text or "گزارش اقتصادی" in msg.text):
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
                await client.send_message(channel, report, silent=True)
                print(f"Sent 'all quiet' report to {channel}")
            else:
                print(f"Skipping daily report for {channel} since it had {count} alerts.")
                
        except Exception as e:
            print(f"Error processing {channel}: {e}")
            
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

CHANNELS = ["@DidebanJang", "@DidebanEterazat"]

async def main():
    if not API_ID or not API_HASH or not BOT_TOKEN:
        print("Missing credentials.")
        return
        
    client = TelegramClient('daily_report_session', int(API_ID), API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    
    total_alerts = 0
    channel_counts = {}
    
    for channel in CHANNELS:
        count = 0
        try:
            # We fetch messages from the last 24 hours
            async for msg in client.iter_messages(channel, offset_date=now):
                if msg.date < yesterday:
                    break
                if msg.text and ("هشدار فوری" in msg.text or "گزارش مردمی" in msg.text):
                    count += 1
                    
            channel_counts[channel] = count
            total_alerts += count
        except Exception as e:
            print(f"Error fetching {channel}: {e}")
            
    # Formulate report
    if total_alerts == 0:
        report = (
            "🌙 **گزارش شبانه رادار دیده‌بان**\n\n"
            "وضعیت: 🟢 آرامش نسبی\n\n"
            "در ۲۴ ساعت گذشته هیچ‌گونه فعالیت غیرعادی یا هشدار مهمی در رادارهای دیده‌بان ثبت نشده است. "
            "سیستم به صورت ۲۴ ساعته در حال پایش لحظه‌ای منابع می‌باشد.\n\n"
            "🤖 *Powered by Sentinel AI*"
        )
    else:
        report = (
            "📊 **گزارش شبانه رادار دیده‌بان**\n\n"
            f"مجموع هشدارهای ۲۴ ساعت گذشته: **{total_alerts} حادثه**\n\n"
            f"🔴 دیده‌بان جنگ: {channel_counts.get('@DidebanJang', 0)} هشدار\n"
            f"🟠 دیده‌بان اعتراضات: {channel_counts.get('@DidebanEterazat', 0)} هشدار\n\n"
            "سیستم به صورت ۲۴ ساعته در حال پایش لحظه‌ای منابع می‌باشد.\n\n"
            "🤖 *Powered by Sentinel AI*"
        )
        
    # Broadcast report silently to both channels
    for channel in CHANNELS:
        try:
            await client.send_message(channel, report, silent=True)
            print(f"Sent daily report to {channel}")
        except Exception as e:
            print(f"Failed to send to {channel}: {e}")
            
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

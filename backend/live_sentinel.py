import os
import json
import time
import asyncio
import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from collections import deque

# --- Config ---
CONFIG_FILE = 'backend/sentinel_config.json'
BASELINE_FILE = 'backend/trend_baselines.json'
MAX_RUNTIME_SEC = 5 * 3600 + 40 * 60  # 5 hours 40 minutes (safe margin before 350m GitHub timeout)

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

class LiveSentinel:
    def __init__(self, bot_client):
        self.bot = bot_client
        self.config = self.load_json(CONFIG_FILE)
        self.nodes = self.config.get('nodes', [])
        
        self.baselines = self.fetch_remote_baselines()
        
        # Buffer for messages in the last 3 minutes
        self.recent_messages = deque()
        self.last_alert_time = {} # pat -> timestamp
        
        # Metrics
        self.start_time = time.time()
        self.total_msgs_processed = 0
        self.last_msg_text = "No messages yet"
        self.last_msg_time = "N/A"
        
    def fetch_remote_baselines(self):
        remote_url = "https://mehr1dad.github.io/python-utils-collection/data/trend_history.json"
        try:
            import requests
            print(f"📥 Fetching Master Baselines from {remote_url}...")
            resp = requests.get(remote_url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                print(f"✅ Loaded {len(data.get('baselines', {}))} baseline records.")
                return data.get('baselines', {})
        except Exception as e:
            print(f"⚠️ Failed to fetch remote baselines: {e}")
            
        print("⚠️ Using local baseline fallback.")
        return self.load_json(BASELINE_FILE).get('baselines', {})

    def load_json(self, path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return {}

    def is_old_news(self, text):
        if re.search(r'(۱۳۹\d|۱۴۰[۰-۳])', text): return True
        months_fa = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
        current_month_idx = 10 # Bahman
        for i, month in enumerate(months_fa):
            if month in text and i < current_month_idx: return True
        return False

    def match_pattern(self, text, pattern):
        try:
            esc_pattern = re.escape(pattern)
            if re.search(r'\b' + esc_pattern + r'\b', text):
                return True
        except:
            if pattern in text:
                return True
        return False

    def calculate_jaccard(self, text1, text2):
        set1 = set(text1.split())
        set2 = set(text2.split())
        if not set1 or not set2: return 0.0
        return len(set1.intersection(set2)) / len(set1.union(set2))

    def purge_old_messages(self):
        now = time.time()
        while self.recent_messages and (now - self.recent_messages[0]['timestamp']) > 180: # 3 minutes
            self.recent_messages.popleft()

    async def process_message(self, text, node, msg_id):
        self.purge_old_messages()
        
        if not text: return
        self.total_msgs_processed += 1
        self.last_msg_text = text[:100] + "..." if len(text) > 100 else text
        self.last_msg_time = time.strftime("%Y-%m-%d %H:%M:%S")
        
        text = text.replace('ي', 'ی').replace('ك', 'ک')
        
        if self.is_old_news(text): return
        
        # Fuzzy Deduplication against messages in the last 3 minutes
        is_syndicated = False
        for rm in self.recent_messages:
            if self.calculate_jaccard(text, rm['text']) > 0.75:
                is_syndicated = True
                break
                
        if is_syndicated: return
        
        # Add to buffer
        msg_obj = {
            'text': text,
            'node': node,
            'timestamp': time.time(),
            'link': f"https://t.me/{node}/{msg_id}"
        }
        self.recent_messages.append(msg_obj)
        
        await self.detect_anomalies()

    async def detect_anomalies(self):
        config_patterns = self.config.get('patterns', {})
        incidents = config_patterns.get('incidents', [])
        locations = config_patterns.get('locations', [])
        status = config_patterns.get('status', [])
        
        counts = {}
        msg_pool = {} # pat -> list of links
        
        for msg in self.recent_messages:
            text = msg['text']
            
            found_incidents = [i for i in incidents if self.match_pattern(text, i)]
            found_locations = [l for l in locations if self.match_pattern(text, l)]
            found_status = [s for s in status if self.match_pattern(text, s)]
            
            patterns_in_msg = []
            
            for loc in found_locations:
                for inc in found_incidents:
                    composite = f"{inc} در {loc}"
                    patterns_in_msg.append(composite)
            
            for s in found_status:
                patterns_in_msg.append(s)
                
            for pat in patterns_in_msg:
                counts[pat] = counts.get(pat, 0) + 1
                if pat not in msg_pool: msg_pool[pat] = []
                msg_pool[pat].append(f"- [{msg['node']}]({msg['link']})")
                
        now = time.time()
        for pat, count in counts.items():
            if count < 4: continue
            
            normal_rate = self.baselines.get(pat, 0.1)
            
            if count > (normal_rate * 10):
                # Throttle alerts (1 alert per pattern per 30 minutes)
                if pat in self.last_alert_time and (now - self.last_alert_time[pat]) < 1800:
                    continue
                    
                self.last_alert_time[pat] = now
                await self.send_alert(pat, count, normal_rate, msg_pool[pat][:3])

    async def send_alert(self, pat, count, baseline, links):
        if not BOT_TOKEN: return
        
        msg_text = (
            f"🚨 **SENTINEL ALERT: {pat}**\n\n"
            f"🔥 Velocity: {count} hits (last 3m)\n"
            f"📊 Normal Baseline: {baseline:.2f}/hr\n\n"
            f"Sources:\n" + "\n".join(links) + "\n\n"
            f"#TrendSentinel"
        )
        
        # Load subscribers dynamically
        subs_data = self.load_json('backend/subscribers.json')
        subs = set(subs_data.get('subscribers', []))
        if CHAT_ID: subs.add(int(CHAT_ID))
        
        for sub in subs:
            try:
                await self.bot.send_message(sub, msg_text, link_preview=False)
                print(f"🚨 SENT ALERT for {pat} to {sub}")
            except Exception as e:
                print(f"Failed to send alert to {sub}: {e}")

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING or not BOT_TOKEN:
        print("Error: Missing Telegram API credentials.")
        return
        
    print("👁️ Sentinel Eye (LIVE MODE): Initializing...")
    
    bot = TelegramClient(StringSession(), int(API_ID), API_HASH)
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    
    sentinel = LiveSentinel(bot)
    
    @bot.on(events.NewMessage(pattern='/start'))
    async def bot_start_handler(event):
        chat_id = event.chat_id
        data = sentinel.load_json('backend/subscribers.json')
        subs = data.get('subscribers', [])
        
        if chat_id not in subs:
            subs.append(chat_id)
            with open('backend/subscribers.json', 'w', encoding='utf-8') as f:
                json.dump({'subscribers': subs}, f, indent=2)
            
            await event.respond("✅ شما به سیستم هشدار فوری Sentinel اضافه شدید. از این پس هشدارهای اخبار فوری برای شما ارسال خواهد شد.")
            print(f"➕ New subscriber added: {chat_id}")
            
            # Commit to GitHub
            os.system('git config --global user.email "bot@sentinel.local"')
            os.system('git config --global user.name "Sentinel Bot"')
            os.system('git add backend/subscribers.json')
            os.system('git commit -m "[skip ci] add new subscriber"')
            os.system('git push')
        else:
            await event.respond("شما قبلاً در سیستم هشدار ثبت‌نام کرده‌اید. 🛡️")

    @bot.on(events.NewMessage(pattern='/ping'))
    async def bot_ping_handler(event):
        uptime_mins = int((time.time() - sentinel.start_time) / 60)
        await event.respond(f"✅ ربات بیدار است و در حال رصد اخبار می‌باشد.\nزمان فعال بودن سرور: {uptime_mins} دقیقه")

    @bot.on(events.NewMessage(pattern='/status'))
    async def bot_status_handler(event):
        uptime_mins = int((time.time() - sentinel.start_time) / 60)
        msg = (
            f"📊 **گزارش زنده Sentinel**\n\n"
            f"⏱️ **مدت زمان بیداری:** {uptime_mins} دقیقه\n"
            f"📡 **منابع فعال:** {len(sentinel.nodes)} کانال\n"
            f"📥 **پیام‌های پردازش شده:** {sentinel.total_msgs_processed} پیام\n"
            f"آخرین پیام: {sentinel.last_msg_time}\n"
            f"متن: {sentinel.last_msg_text}\n"
        )
        await event.respond(msg)

    @client.on(events.NewMessage(chats=sentinel.nodes))
    async def user_handler(event):
        sender = await event.get_chat()
        node_username = getattr(sender, 'username', 'unknown')
        text = event.message.message
        msg_id = event.message.id
        await sentinel.process_message(text, node_username, msg_id)

    async def active_poller():
        last_ids = {}
        while True:
            for node in sentinel.nodes:
                try:
                    messages = await client.get_messages(node, limit=1)
                    if messages:
                        msg = messages[0]
                        if node not in last_ids or msg.id > last_ids[node]:
                            last_ids[node] = msg.id
                            await sentinel.process_message(msg.message, node, msg.id)
                except Exception as e:
                    pass
                await asyncio.sleep(1.5)
            await asyncio.sleep(15)
        
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 Bot listener started.")
    
    await client.start()
    print("✅ Live listening started on", len(sentinel.nodes), "nodes (Active Polling).")
    
    poller_task = asyncio.create_task(active_poller())
    
    # Run until time limit
    await asyncio.sleep(MAX_RUNTIME_SEC)
    
    print("⏰ Max runtime reached. Exiting gracefully to allow restart.")
    poller_task.cancel()
    
    # Generate and push session report
    uptime_mins = int((time.time() - sentinel.start_time) / 60)
    report_content = (
        f"# Sentinel Session Report\n\n"
        f"- **Uptime:** {uptime_mins} minutes\n"
        f"- **Messages Processed:** {sentinel.total_msgs_processed}\n"
        f"- **Last Message Text:** {sentinel.last_msg_text}\n"
        f"- **Last Message Time:** {sentinel.last_msg_time}\n"
    )
    with open('session_report.md', 'w', encoding='utf-8') as f:
        f.write(report_content)
    os.system('git config --global user.email "bot@sentinel.local"')
    os.system('git config --global user.name "Sentinel Bot"')
    os.system('git add session_report.md')
    os.system('git commit -m "[skip ci] save session report"')
    os.system('git push')
    
    await client.disconnect()
    await bot.disconnect()

if __name__ == "__main__":
    asyncio.run(main())


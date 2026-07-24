import os
import json
import time
import asyncio
import re
import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from collections import deque
from datetime import datetime

# --- Config ---
CONFIG_FILE = 'backend/sentinel_config.json'
BASELINE_FILE = 'backend/trend_baselines.json'
MAX_RUNTIME_SEC = 5 * 3600 + 50 * 60  # 5 hours 50 minutes

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

class LiveSentinel:
    def __init__(self):
        self.config = self.load_json(CONFIG_FILE)
        self.nodes = self.config.get('nodes', [])
        
        self.baselines = self.fetch_remote_baselines()
        
        # Buffer for messages in the last 3 minutes
        self.recent_messages = deque()
        self.last_alert_time = {} # pat -> timestamp
        
    def fetch_remote_baselines(self):
        remote_url = "https://mehr1dad.github.io/python-utils-collection/data/trend_history.json"
        try:
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

    def process_message(self, text, node, msg_id):
        self.purge_old_messages()
        
        if not text: return
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
        
        self.detect_anomalies()

    def detect_anomalies(self):
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
                self.send_alert(pat, count, normal_rate, msg_pool[pat][:3])

    def send_alert(self, pat, count, baseline, links):
        if not BOT_TOKEN or not CHAT_ID: return
        
        msg_text = (
            f"🚨 **SENTINEL ALERT: {pat}**\n\n"
            f"🔥 Velocity: {count} hits (last 3m)\n"
            f"📊 Normal Baseline: {baseline:.2f}/hr\n\n"
            f"Sources:\n" + "\n".join(links) + "\n\n"
            f"#TrendSentinel"
        )
        
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={'chat_id': CHAT_ID, 'text': msg_text, 'parse_mode': 'Markdown', 'disable_web_page_preview': True}
            )
            print(f"🚨 SENT ALERT for {pat}")
        except Exception as e:
            print(f"Failed to send alert: {e}")

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Error: Missing Telegram API credentials.")
        return
        
    print("👁️ Sentinel Eye (LIVE MODE): Initializing...")
    sentinel = LiveSentinel()
    
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    
    @client.on(events.NewMessage(chats=sentinel.nodes))
    async def handler(event):
        sender = await event.get_chat()
        node_username = getattr(sender, 'username', 'unknown')
        text = event.message.message
        msg_id = event.message.id
        sentinel.process_message(text, node_username, msg_id)
        
    await client.start()
    print("✅ Live listening started on", len(sentinel.nodes), "nodes.")
    
    # Run until time limit
    await asyncio.sleep(MAX_RUNTIME_SEC)
    
    print("⏰ Max runtime reached. Exiting gracefully to allow restart.")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

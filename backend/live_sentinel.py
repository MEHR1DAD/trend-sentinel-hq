import os
import json
import time
import asyncio
import re
from datetime import datetime, timezone
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
        self.incident_severities = self.config.get('patterns', {}).get('incident_severities', {})
        
        self.baselines = self.fetch_remote_baselines()
        self.lock = asyncio.Lock()
        
        # Buffer for messages in the last 3 minutes
        self.recent_messages = deque()
        self.last_alert_time = {} # pat -> timestamp
        self.alerted_msg_patterns = {} # "node_msgid" -> set of patterns
        self.recent_alert_sources = deque() # (timestamp, set_of_sources)
        
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
        
        # Strong indicators of live citizen reports that override any news stopwords
        citizen_indicators = [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ]
        if any(ind in text for ind in citizen_indicators):
            return False
        
        # Filter out formal journalistic/recap language and news channel forwards
        news_stopwords = [
            "vahidheadline", "vahidoonline", "خبر داد", "اعلام کرد", "گزارش داد", 
            "منتشر کرد", "به گزارش", "خبرگزاری", "ایسنا", "فارس", 
            "تسنیم", "رویترز", "روز گذشته", "صبح امروز", "تأکید کرد", "تاکید کرد",
            "هشدار داد", "افزود", "در پاسخ به", "در گفت‌وگو", "در گفتوگو", "مجری",
            "بیان کرد", "اشاره کرد", "ترجمه ماشین", "ترجمه ماشینی", "به نقل از", 
            "نیویورک تایمز", "آسوشیتدپرس", "وال استریت", "کاخ سفید", "پنتاگون",
            
            # Political figures and titles (to ignore quotes and diplomatic news)
            "ترامپ", "نتانیاهو", "بایدن", "پوتین", "خامنه‌ای", "پزشکیان", "عراقچی", 
            "قالیباف", "ظریف", "سلامی", "قاآنی", "کامالا هریس", "بلینکن", "لوید آستین", 
            "جیک سالیوان", "گالانت", "کاتس", "هرتزوگ", "مکرون", "اردوغان", "بن سلمان", 
            "بشار اسد", "زلنسکی", "جوزپ بورل", "آنتونیو گوترش", "رافائل گروسی", "شولتز",
            "کیر استارمر",
            
            "وزیر خارجه", "وزیر امور خارجه", "وزیر دفاع", "سخنگوی", 
            "رئیس‌جمهور", "رییس‌جمهور", "رئیس جمهور", "رییس جمهور", 
            "نخست‌وزیر", "نخست وزیر", "پادشاه", "رئیس مجلس", "رییس مجلس", "رهبر انقلاب"
        ]
        
        text_lower = text.lower()
        for word in news_stopwords:
            if word in text_lower:
                return True
                
        return False

    def match_pattern(self, text, pattern):
        try:
            # Using negative lookbehind/lookahead for Persian letters and ZWNJ (\u200c)
            # to prevent matching substrings inside words (like شنبه in پنج‌شنبه)
            esc_pattern = re.escape(pattern)
            regex = r'(?<![آ-یa-zA-Z0-9_\u200c\u200d])' + esc_pattern + r'(?![آ-یa-zA-Z0-9_\u200c\u200d])'
            return bool(re.search(regex, text))
        except:
            return pattern in text

    def calculate_jaccard(self, text1, text2):
        set1 = set(text1.split())
        set2 = set(text2.split())
        if not set1 or not set2: return 0.0
        return len(set1.intersection(set2)) / len(set1.union(set2))

    def purge_old_messages(self):
        now = time.time()
        while self.recent_messages and (now - self.recent_messages[0]['timestamp']) > 180: # 3 minutes
            self.recent_messages.popleft()

    def get_message_patterns(self, text):
        config_patterns = self.config.get('patterns', {})
        incidents = config_patterns.get('incidents', [])
        locations = config_patterns.get('locations', [])
        status = config_patterns.get('status', [])
        
        incident_mappings = config_patterns.get('incident_mappings', {})
        generic_locations = config_patterns.get('generic_locations', [])
        
        found_incidents = [i for i in incidents if self.match_pattern(text, i)]
        found_locations = [l for l in locations if self.match_pattern(text, l)]
        found_status = [s for s in status if self.match_pattern(text, s)]
        
        # 1. Semantic Resolution for Incidents
        resolved_incidents = set()
        for inc in found_incidents:
            mapped = False
            for canonical, synonyms in incident_mappings.items():
                if inc in synonyms or inc == canonical:
                    resolved_incidents.add(canonical)
                    mapped = True
                    break
            if not mapped:
                resolved_incidents.add(inc)
                
        resolved_status = set()
        for s in found_status:
            mapped = False
            for canonical, synonyms in incident_mappings.items():
                if s in synonyms or s == canonical:
                    resolved_status.add(canonical)
                    mapped = True
                    break
            if not mapped:
                resolved_status.add(s)

        # 2. Context-Aware Location Grouping
        specific_cities = [l for l in found_locations if l not in generic_locations]
        generic_locs = [l for l in found_locations if l in generic_locations]
        
        if specific_cities:
            merged_cities = "، ".join(specific_cities)
            if generic_locs:
                merged_generics = " و ".join(generic_locs)
                final_loc_str = f"{merged_generics} {merged_cities}"
            else:
                final_loc_str = merged_cities
        elif generic_locs:
            final_loc_str = "، ".join(generic_locs)
        else:
            final_loc_str = ""
            
        patterns = []
        if final_loc_str:
            if resolved_incidents:
                # Sort incidents by severity: URGENT first
                def inc_priority(inc):
                    sev = self.incident_severities.get(inc, "IMPORTANT")
                    return 0 if sev == "URGENT" else 1
                sorted_incidents = sorted(list(resolved_incidents), key=inc_priority)
                
                # Combine up to 3 incidents into one unified description to avoid multi-alert spam
                if len(sorted_incidents) == 1:
                    inc_title = sorted_incidents[0]
                elif len(sorted_incidents) == 2:
                    inc_title = f"{sorted_incidents[0]} و {sorted_incidents[1]}"
                else:
                    inc_title = f"{sorted_incidents[0]}، {sorted_incidents[1]} و {sorted_incidents[2]}"
                    
                patterns.append(f"{inc_title} در {final_loc_str}")
        elif resolved_incidents:
            def inc_priority(inc):
                sev = self.incident_severities.get(inc, "IMPORTANT")
                return 0 if sev == "URGENT" else 1
            sorted_incidents = sorted(list(resolved_incidents), key=inc_priority)
            if len(sorted_incidents) == 1:
                inc_title = sorted_incidents[0]
            else:
                inc_title = " و ".join(sorted_incidents[:2])
            patterns.append(inc_title)
            
        for s in resolved_status:
            patterns.append(s)
            
        return list(set(patterns))

    async def process_message(self, text, node, msg_id, is_edit=False, msg_date=None):
        async with self.lock:
            # Ignore messages older than 10 minutes to prevent spam on bot restart (catch-up)
            if msg_date:
                now_utc = datetime.now(timezone.utc)
                if (now_utc - msg_date).total_seconds() > 600:
                    return
                    
            self.purge_old_messages()
            
            if not text: return
            
            self.total_msgs_processed += 1
            self.last_msg_text = text[:100] + "..." if len(text) > 100 else text
            self.last_msg_time = time.strftime("%Y-%m-%d %H:%M:%S")
            
            text = text.replace('ي', 'ی').replace('ك', 'ک')
            
            if self.is_old_news(text): return
            
            patterns_in_msg = self.get_message_patterns(text)
            link = f"https://t.me/{node}/{msg_id}"
            
            # --- VIP CHANNELS LOGIC ---
            if node in ['VahidOnline', 'iliaen'] and patterns_in_msg:
                msg_key = f"{node}_{msg_id}"
                if msg_key not in self.alerted_msg_patterns:
                    self.alerted_msg_patterns[msg_key] = set()
                    
                for pat in patterns_in_msg:
                    if pat not in self.alerted_msg_patterns[msg_key]:
                        self.alerted_msg_patterns[msg_key].add(pat)
                        # Immediate VIP Alert!
                        baseline = self.baselines.get(pat, 0.1)
                        
                        is_silent = True
                        for inc, sev in self.incident_severities.items():
                            if inc in pat and sev == "URGENT":
                                is_silent = False
                                break
                                
                        await self.send_alert(pat, "VIP_IMMEDIATE", baseline, [f"- [{node}]({link}) (VIP Alert{' - Edited' if is_edit else ''})"], is_silent=is_silent)

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
        counts = {}
        msg_pool = {} # pat -> list of links
        
        for msg in self.recent_messages:
            text = msg['text']
            patterns_in_msg = self.get_message_patterns(text)
            
            for pat in patterns_in_msg:
                counts[pat] = counts.get(pat, 0) + 1
                if pat not in msg_pool: msg_pool[pat] = []
                msg_pool[pat].append(f"- [{msg['node']}]({msg['link']})")
                
        now = time.time()
        # Clean up old alerted sources older than 15 minutes
        while self.recent_alert_sources and (now - self.recent_alert_sources[0][0]) > 900:
            self.recent_alert_sources.popleft()
            
        for pat, count in counts.items():
            if count < 4: continue
            
            normal_rate = self.baselines.get(pat, 0.1)
            
            if count > (normal_rate * 10):
                # 1. Throttle alerts (1 alert per pattern per 30 minutes)
                if pat in self.last_alert_time and (now - self.last_alert_time[pat]) < 1800:
                    continue
                    
                # 2. Source Overlap Deduplication (check against alerts in last 15 minutes)
                current_sources = set(msg_pool[pat])
                is_duplicate_story = False
                for prev_time, prev_sources in self.recent_alert_sources:
                    common_sources = current_sources.intersection(prev_sources)
                    # If 2 or more sources are identical, it's the same syndicated news story!
                    if len(common_sources) >= 2 or (len(current_sources) > 0 and len(common_sources) / len(current_sources) >= 0.5):
                        is_duplicate_story = True
                        break
                        
                if is_duplicate_story:
                    continue
                    
                self.last_alert_time[pat] = now
                self.recent_alert_sources.append((now, current_sources))
                
                is_silent = True
                for inc, sev in self.incident_severities.items():
                    if inc in pat and sev == "URGENT":
                        is_silent = False
                        break
                        
                await self.send_alert(pat, count, normal_rate, msg_pool[pat][:3], is_silent=is_silent)

    async def send_alert(self, pattern, count, normal_rate, context_msgs, is_silent=False):
        if not BOT_TOKEN: return
        
        icon = "🔕" if is_silent else "🚨"
        alert_text = (
            f"{icon} **SENTINEL ALERT: {pattern}**\n\n"
            f"🔥 Velocity: {count} hits (last 3m)\n"
            f"📊 Normal Baseline: {normal_rate:.2f}/hr\n\n"
            f"Sources:\n" + "\n".join(context_msgs) + "\n\n"
            f"#TrendSentinel"
        )
        
        data = self.load_json('backend/subscribers.json')
        subs = set(data.get('subscribers', []))
        if CHAT_ID: subs.add(int(CHAT_ID))
        
        for sub in subs:
            try:
                await self.bot.send_message(sub, alert_text, link_preview=False, silent=is_silent)
                print(f"{icon} SENT ALERT for {pattern} to {sub}")
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
        msg_date = event.message.date
        
        await sentinel.process_message(text, node_username, msg_id, msg_date=msg_date)

    @client.on(events.MessageEdited(chats=sentinel.nodes))
    async def edit_handler(event):
        sender = await event.get_chat()
        node_username = getattr(sender, 'username', 'unknown')
        text = event.message.message
        msg_id = event.message.id
        msg_date = event.message.date
        
        await sentinel.process_message(text, node_username, msg_id, is_edit=True, msg_date=msg_date)

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
                            await sentinel.process_message(msg.message, node, msg.id, msg_date=msg.date)
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


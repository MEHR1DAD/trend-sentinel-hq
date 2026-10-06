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
MAX_RUNTIME_SEC = 30 * 60  # 30 minutes (Rotate before the 45-minute hard crash)

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

class LiveSentinel:
    def __init__(self, bot):
        self.bot = bot
        self.config = self.load_json(CONFIG_FILE)
        self.nodes = self.config.get('nodes', [])
        
        # Load severities
        self.incident_severities = self.config.get('patterns', {}).get('incident_severities', {})
        
        self.baselines = self.fetch_remote_baselines()
        self.lock = asyncio.Lock()
        
        # Buffer for messages in the last 3 minutes
        self.recent_messages = deque()
        self.last_alert_time = {} # pat -> timestamp
        self.alerted_msg_patterns = {} # "node_msgid" -> set of patterns
        self.vip_alert_history = {} # "node_msgid" -> dict(time, pattern, text)
        self.recent_alert_sources = deque() # (timestamp, set_of_sources)
        self.vahid_ai_posted = {} # msg_id -> posted_msg_id in @VahidOnlineAI
        
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

    def is_old_news(self, text, node=None):
        # Strong indicators of live citizen reports that override any news stopwords
        citizen_indicators = [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ]
        if any(ind in text for ind in citizen_indicators):
            return False
            
        # Catch historical news (e.g. 13xx or 1400-1404)
        if re.search(r'(۱۳\d{2}|۱۴۰[۰-۴]|13\d{2}|140[0-4])', text): return True
        
        # Approximate mapping from Gregorian to Jalali month (Oct is Mehr/7th)
        import datetime
        now = datetime.datetime.now()
        month_map = {1: 10, 2: 11, 3: 12, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7, 11: 8, 12: 9}
        current_month_idx = month_map.get(now.month, 7) - 1
        
        months_fa = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
        for i, month in enumerate(months_fa):
            # Use lookbehind/lookahead to match whole words and prevent substrings like "دیجیتال" matching "دی"
            if re.search(r'(?<![\u0600-\u06FF])' + month + r'(?![\u0600-\u06FF])', text) and i < current_month_idx: 
                return True
        
        # Filter out formal journalistic/recap language and news channel forwards
        news_stopwords = [
            "vahidheadline", "vahidoonline", "به گزارش", "خبرگزاری", "ایسنا", "فارس", 
            "تسنیم", "مهر", "ایرنا", "ایلنا", "همشهری", "شرق", "رویترز", "روز گذشته", "صبح امروز", 
            "در پاسخ به", "در گفت‌وگو", "در گفتوگو", "مجری", "مصاحبه", "تلویزیون",
            "ترجمه ماشین", "ترجمه ماشینی", "به نقل از", 
            "یادبود", "سالگرد", "خاطره",
            "نیویورک تایمز", "آسوشیتدپرس", "وال استریت", "کاخ سفید", "پنتاگون",
            "تست شد", "آزمایش شد", "آزمایش موفق", "با موفقیت", "رزمایش",
            
            # Political and military figures and titles (to ignore quotes, speeches and diplomatic news)
            "ترامپ", "نتانیاهو", "بایدن", "پوتین", "خامنه‌ای", "پزشکیان", "عراقچی", 
            "قالیباف", "ظریف", "سلامی", "قاآنی", "کامالا هریس", "بلینکن", "لوید آستین", 
            "جیک سالیوان", "گالانت", "کاتس", "هرتزوگ", "مکرون", "اردوغان", "بن سلمان", 
            "بشار اسد", "زلنسکی", "جوزپ بورل", "آنتونیو گوترش", "رافائل گروسی", "شولتز",
            "کیر استارمر", "رضایی", "محسن رضایی", "باقری", "موسوی", "شمخانی", "کوثری",
            
            "وزیر خارجه", "وزیر امور خارجه", "وزیر دفاع", "سخنگوی", "نماینده مجلس",
            "رئیس‌جمهور", "رییس‌جمهور", "رئیس جمهور", "رییس جمهور", 
            "نخست‌وزیر", "نخست وزیر", "پادشاه", "رئیس مجلس", "رییس مجلس", "رهبر انقلاب",
            "سرلشکر", "سردار", "امیر", "دریادار", "سرتیپ", "فرمانده کل"
        ]
        
        text_lower = text.lower()
        for word in news_stopwords:
            if word in text_lower:
                return True
                
        # Detect official/speaker quotes with colon (e.g. "سرلشکر رضایی:", "سخنگوی دولت:", etc.)
        if re.search(r'(?:سرلشکر|سردار|فرمانده|امیر|دریادار|سرتیپ|سخنگوی|وزیر|رئیس|رییس|پزشکیان|عراقچی|قالیباف|نتانیاهو|ترامپ|بایدن|پوتین|رضایی|باقری)\s*[\w‌]*:', text):
            return True
                
        return False

    def match_pattern(self, text, pattern):
        try:
            # Strip Arabic/Persian diacritics
            diacritics_regex = re.compile(r'[\u064B-\u065F\u0670]')
            clean_text = diacritics_regex.sub('', text)
            clean_pattern = diacritics_regex.sub('', pattern)
            
            # Replace ZWNJ with space for more flexible matching
            clean_text = clean_text.replace('\u200c', ' ')
            clean_pattern = clean_pattern.replace('\u200c', ' ')
            
            # Prevent false positives for medical conditions (e.g. حمله قلبی)
            if clean_pattern == 'حمله':
                if re.search(r'حمله\s+(قلبی|عصبی|تنفسی|پانیک)', clean_text):
                    non_medical = re.sub(r'حمله\s+(قلبی|عصبی|تنفسی|پانیک)', '', clean_text)
                    if not re.search(r'(?<![آ-یa-zA-Z0-9_])حمله(?![آ-یa-zA-Z0-9_])', non_medical):
                        return False

            # Using negative lookbehind/lookahead for Persian letters
            # to prevent matching substrings inside words (like شنبه in پنج‌شنبه)
            esc_pattern = re.escape(clean_pattern)
            regex = r'(?<![آ-یa-zA-Z0-9_])' + esc_pattern + r'(?![آ-یa-zA-Z0-9_])'
            return bool(re.search(regex, clean_text))
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

    def get_message_patterns(self, text, is_citizen=False):
        config_patterns = self.config.get('patterns', {})
        incidents = config_patterns.get('incidents', [])
        economy = config_patterns.get('economy', [])
        locations = config_patterns.get('locations', [])
        status = config_patterns.get('status', [])
        
        incident_mappings = config_patterns.get('incident_mappings', {})
        generic_locations = config_patterns.get('generic_locations', [])
        
        found_incidents = [i for i in incidents if self.match_pattern(text, i)]
        found_economy = [e for e in economy if self.match_pattern(text, e)]
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
        
        foreign_keywords = ['اسرائیل', 'لبنان', 'غزه', 'فلسطین', 'سوریه', 'عراق', 'اربیل', 'یمن', 'عربستان', 'کویت', 'قطر', 'امارات', 'عمان', 'ترکیه', 'پاکستان', 'افغانستان', 'تل آویو', 'حیفا', 'آمریکا']
        has_foreign = any(fk in text for fk in foreign_keywords)
        
        if specific_cities:
            merged_cities = "، ".join(specific_cities)
            if generic_locs:
                merged_generics = " و ".join(generic_locs)
                final_loc_str = f"{merged_generics} {merged_cities}"
            else:
                final_loc_str = merged_cities
        elif generic_locs:
            # Allow standalone generic locations only for citizen reports
            if is_citizen:
                final_loc_str = "، ".join(generic_locs)
            else:
                final_loc_str = ""
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
                    
                pat = f"{inc_title} در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in foreign_keywords):
                    pat += "||FOREIGN||"
                patterns.append(pat)
            elif is_citizen:
                pat = f"گزارش شهروندی در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in foreign_keywords):
                    pat += "||FOREIGN||"
                patterns.append(pat)
        elif is_citizen:
            if resolved_incidents:
                inc_title = " و ".join(list(resolved_incidents)[:2])
                pat = f"گزارش شهروندی: {inc_title}"
            else:
                pat = "گزارش فوری دریافتی شهروندان"
            
            if has_foreign:
                pat += "||FOREIGN||"
            patterns.append(pat)
            
        for s in resolved_status:
            pat = s
            if has_foreign:
                pat += "||FOREIGN||"
            patterns.append(pat)
            
        for e in found_economy:
            pat = e + "||ECONOMY||"
            patterns.append(pat)
            
        return list(set(patterns))

    async def process_message(self, text, node, msg_id, is_edit=False, msg_date=None, raw_msg=None):
        async with self.lock:
            # Ignore messages older than 3 minutes to prevent spam on bot restart (catch-up)
            if msg_date:
                now_utc = datetime.now(timezone.utc)
                if (now_utc - msg_date).total_seconds() > 180:
                    return
                    
            self.purge_old_messages()
            
            # --- @VahidOnlineAI Channel Pipeline ---
            node_key = (node or '').lower()
            if node_key == 'vahidonline':
                asyncio.create_task(self.handle_vahid_online_ai(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
            
            if not text: return
            
            self.total_msgs_processed += 1
            self.last_msg_text = text[:100] + "..." if len(text) > 100 else text
            self.last_msg_time = time.strftime("%Y-%m-%d %H:%M:%S")
            
            text = text.replace('ي', 'ی').replace('ك', 'ک')
            
            is_citizen_report = any(ind in text for ind in [
                "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
                "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
            ])
            
            patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
            has_economy = any("||ECONOMY||" in p for p in patterns_in_msg)
            
            if not has_economy and self.is_old_news(text, node): return
            
            # --- VIP CHANNELS EXCLUSIVE PROTESTS LOGIC ---
            vip_map = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen'}
            node_key = (node or '').lower()
            
            if node_key not in vip_map:
                # If not VIP, drop any protest/strike/rights patterns
                patterns_in_msg = [p for p in patterns_in_msg if "اعتراض" not in p and "اعتصاب" not in p and "اعدام" not in p]
                
            link = f"https://t.me/{node}/{msg_id}"
            
            # --- VIP CHANNELS LOGIC ---
            vip_map = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen'}
            node_key = (node or '').lower()
            if node_key in vip_map and patterns_in_msg:
                canonical_node = vip_map[node_key]
                msg_key = f"{canonical_node}_{msg_id}"
                now = time.time()
                
                # Check if this message was previously alerted and is now updated within 15 minutes (900s)
                if msg_key in self.vip_alert_history:
                    prev = self.vip_alert_history[msg_key]
                    time_diff = now - prev['time']
                    
                    if is_edit and time_diff <= 900 and text != prev['text']:
                        target_ch = prev.get('target_channel')
                        self.vip_alert_history[msg_key] = {'time': now, 'pattern': patterns_in_msg[0], 'text': text, 'target_channel': target_ch}
                        clean_pats = [p.replace("||FOREIGN||", "") for p in patterns_in_msg]
                        combined_pat = "، ".join(dict.fromkeys(clean_pats)) # remove duplicates
                        
                        # Use the first pattern's metadata for routing/baseline
                        base_pat = patterns_in_msg[0]
                        baseline = self.baselines.get(base_pat.replace("||FOREIGN||", ""), 0.1)
                        
                        # Preserve prefixes for send_alert logic
                        prefix = ""
                        if "||ECONOMY||" in base_pat: prefix = "||ECONOMY||"
                        elif "||FOREIGN||" in base_pat: prefix = "||FOREIGN||"
                        
                        display_pat = prefix + combined_pat
                        
                        sent_msg, target_channel, alert_text = await self.send_alert(
                            f"{display_pat} (به‌روزرسانی خبر)", 
                            "VIP_UPDATE", 
                            baseline, 
                            [f"- [{canonical_node}]({link}) (VIP Update)"], 
                            is_silent=True,
                            target_channel=target_ch
                        )
                        if sent_msg:
                            asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, [text]))
                else:
                    self.vip_alert_history[msg_key] = {'time': now, 'pattern': patterns_in_msg[0], 'text': text}
                    if msg_key not in self.alerted_msg_patterns:
                        self.alerted_msg_patterns[msg_key] = set()
                        
                    # Filter to only patterns we haven't alerted for this exact message
                    new_pats = [p for p in patterns_in_msg if p.replace("||FOREIGN||", "") not in self.alerted_msg_patterns[msg_key]]
                    
                    if new_pats:
                        clean_pats = [p.replace("||FOREIGN||", "") for p in new_pats]
                        for p in clean_pats:
                            self.alerted_msg_patterns[msg_key].add(p)
                            
                        # 1. Try VIP AI Classifier first
                        ai_class = await self.classify_vip_message(text)
                        if ai_class:
                            category = ai_class.get('category')
                            topic_title = ai_class.get('topic_title', '').strip()
                            
                            if category == 'OTHER':
                                print(f"ℹ️ VIP AI Classifier filtered out message as OTHER: '{topic_title}' (Msg {msg_id})")
                                return
                                
                            if category == 'ECONOMY':
                                target_channel = "@DidehbanEghtesad"
                                alert_title = f"گزارش اقتصادی: {topic_title}"
                                custom_icon = "📈"
                            elif category == 'PROTEST_RIGHTS':
                                target_channel = "@DidebanEterazat"
                                is_rights = any(w in text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت", "محبوس", "قوه قضائیه"])
                                alert_title = f"گزارش حقوق بشری: {topic_title}" if is_rights else f"گزارش مردمی: {topic_title}"
                                custom_icon = "⚖️" if any(w in text for w in ["اعدام", "حکم", "طناب دار", "دادگاه"]) else "📢"
                            elif category == 'WAR':
                                target_channel = "@DidebanJang"
                                alert_title = f"هشدار فوری: {topic_title}"
                                custom_icon = "🚨"
                            else:
                                target_channel = None
                                alert_title = None
                                custom_icon = None
                                
                            self.vip_alert_history[msg_key]['target_channel'] = target_channel
                            
                            is_silent = False if (canonical_node in ['VahidOnline', 'iliaen'] and not is_edit) else True
                            baseline = self.baselines.get(clean_pats[0], 0.1) if clean_pats else 0.1
                            
                            sent_msg, target_channel, alert_text = await self.send_alert(
                                topic_title, 
                                "VIP_IMMEDIATE", 
                                baseline, 
                                [f"- [{canonical_node}]({link}) (VIP Alert{' - Edited' if is_edit else ''})"], 
                                is_silent=is_silent,
                                target_channel=target_channel,
                                alert_title=alert_title,
                                custom_icon=custom_icon
                            )
                            if sent_msg:
                                asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, [text]))
                        else:
                            # 2. Fallback to keyword-based logic if AI is unreachable
                            combined_pat = "، ".join(dict.fromkeys(clean_pats))
                            base_pat = new_pats[0]
                            is_foreign = "||FOREIGN||" in base_pat
                            baseline = self.baselines.get(base_pat.replace("||FOREIGN||", ""), 0.1)
                            
                            # Preserve prefixes
                            prefix = ""
                            if "||ECONOMY||" in base_pat: prefix = "||ECONOMY||"
                            elif "||FOREIGN||" in base_pat: prefix = "||FOREIGN||"
                            
                            display_pat = prefix + combined_pat
                            
                            is_silent = True
                            if not is_edit and not is_foreign:
                                if is_citizen_report or canonical_node == 'VahidOnline':
                                    is_silent = False
                                else:
                                    for inc, sev in self.incident_severities.items():
                                        if inc in clean_pats[0] and sev == "URGENT":
                                            is_silent = False
                                            break
                                            
                            sent_msg, target_channel, alert_text = await self.send_alert(
                                display_pat, 
                                "VIP_IMMEDIATE", 
                                baseline, 
                                [f"- [{canonical_node}]({link}) (VIP Alert{' - Edited' if is_edit else ''})"], 
                                is_silent=is_silent
                            )
                            if sent_msg:
                                self.vip_alert_history[msg_key]['target_channel'] = target_channel
                                asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, [text]))

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
                'link': f"https://t.me/{node}/{msg_id}",
                'is_citizen_report': is_citizen_report
            }
            self.recent_messages.append(msg_obj)
            
            await self.detect_anomalies()

    async def detect_anomalies(self):
        channel_pools = {} # pat -> dict of node -> link (ensuring 1 link per distinct channel)
        
        for msg in self.recent_messages:
            text = msg['text']
            is_citizen_report = msg.get('is_citizen_report', False)
            patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
            
            for pat in patterns_in_msg:
                if pat not in channel_pools:
                    channel_pools[pat] = {}
                # Keep latest message per channel for this pattern
                channel_pools[pat][msg['node']] = msg['link']
                
        now = time.time()
        # Clean up old alerted sources older than 15 minutes
        while self.recent_alert_sources and (now - self.recent_alert_sources[0][0]) > 900:
            self.recent_alert_sources.popleft()
            
        for pat, channels in channel_pools.items():
            is_foreign = "||FOREIGN||" in pat
            clean_pat = pat.replace("||FOREIGN||", "")
            
            distinct_channel_count = len(channels)
            # Rule: MUST be confirmed by at least 3 distinct channels!
            if distinct_channel_count < 3:
                continue
            
            normal_rate = self.baselines.get(clean_pat, 0.1)
            
            # 1. Throttle alerts (1 alert per pattern per 30 minutes)
            if clean_pat in self.last_alert_time and (now - self.last_alert_time[clean_pat]) < 1800:
                continue
                
            # 2. Source Overlap Deduplication (check against alerts in last 15 minutes)
            source_links = [f"- [{node}]({link})" for node, link in channels.items()]
            current_sources = set(channels.values())
            is_duplicate_story = False
            for prev_time, prev_sources in self.recent_alert_sources:
                common_sources = current_sources.intersection(prev_sources)
                # If 2 or more sources are identical, it's the same syndicated news story!
                if len(common_sources) >= 2 or (len(current_sources) > 0 and len(common_sources) / len(current_sources) >= 0.5):
                    is_duplicate_story = True
                    break
                    
            if is_duplicate_story:
                continue
                
            self.last_alert_time[clean_pat] = now
            self.recent_alert_sources.append((now, current_sources))
            
            is_silent = True
            if not is_foreign:
                for inc, sev in self.incident_severities.items():
                    if inc in clean_pat and sev == "URGENT":
                        is_silent = False
                        break
                    
            raw_texts = [msg['text'] for msg in self.recent_messages if msg['node'] in channels and msg['link'] == channels[msg['node']]]
            combined_text = "\n---\n".join(raw_texts[:3])
            
            # Use AI Classifier for Anomaly Alert to verify, clean title, and route to correct channel
            ai_class = await self.classify_message(combined_text)
            if ai_class:
                category = ai_class.get('category')
                topic_title = ai_class.get('topic_title', '').strip()
                
                if category == 'OTHER':
                    print(f"ℹ️ Anomaly AI Classifier rejected alert as OTHER: '{topic_title}' ({clean_pat})")
                    continue
                    
                if category == 'ECONOMY':
                    target_ch = "@DidehbanEghtesad"
                    title = f"گزارش اقتصادی: {topic_title}"
                    icon = "📈"
                    is_silent = False
                elif category == 'PROTEST_RIGHTS':
                    target_ch = "@DidebanEterazat"
                    is_rights = any(w in combined_text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت", "محبوس", "قوه قضائیه"])
                    title = f"گزارش حقوق بشری: {topic_title}" if is_rights else f"گزارش مردمی: {topic_title}"
                    icon = "⚖️" if any(w in combined_text for w in ["اعدام", "حکم", "طناب دار", "دادگاه"]) else "📢"
                    is_silent = False
                elif category == 'WAR':
                    target_ch = "@DidebanJang"
                    title = f"هشدار فوری: {topic_title}"
                    icon = "🚨"
                else:
                    target_ch = None
                    title = None
                    icon = None
                    
                sent_msg, target_channel, alert_text = await self.send_alert(
                    topic_title, 
                    distinct_channel_count, 
                    normal_rate, 
                    source_links[:3], 
                    is_silent=is_silent,
                    target_channel=target_ch,
                    alert_title=title,
                    custom_icon=icon
                )
                if sent_msg:
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, raw_texts[:3]))
            else:
                sent_msg, target_channel, alert_text = await self.send_alert(clean_pat, distinct_channel_count, normal_rate, source_links[:3], is_silent=is_silent)
                if sent_msg:
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, raw_texts[:3]))

    async def send_alert(self, pattern, count, normal_rate, context_msgs, is_silent=False, target_channel=None, alert_title=None, custom_icon=None):
        if not BOT_TOKEN: return
        
        is_economy = "||ECONOMY||" in pattern or target_channel == "@DidehbanEghtesad"
        pattern = pattern.replace("||ECONOMY||", "")
        
        is_protest = ("اعتراض" in pattern or "اعتصاب" in pattern or "اعدام" in pattern or "حقوق بشری" in pattern) or target_channel == "@DidebanEterazat"
        
        # Determine icon and channel
        if custom_icon:
            icon = custom_icon
        elif is_silent:
            icon = "🔕"
        elif is_economy:
            icon = "📈"
        elif is_protest:
            if "اعدام" in pattern or "حقوق بشری" in pattern:
                icon = "⚖️"
            else:
                icon = "🛑" if "اعتصاب" in pattern else "📢"
        else:
            icon = "🚨"
            
        if not alert_title:
            if is_economy:
                alert_title = f"گزارش اقتصادی: {pattern}"
            elif is_protest:
                if "اعدام" in pattern or "حقوق بشری" in pattern:
                    alert_title = f"گزارش حقوق بشری: {pattern}"
                else:
                    alert_title = f"گزارش مردمی: {pattern}"
            else:
                alert_title = f"هشدار فوری: {pattern}"
            
        alert_text = (
            f"{icon} **{alert_title}**\n\n"
            f"⚡️ سرعت انتشار: {count if isinstance(count, str) else str(count) + ' گزارش'} (در ۳ دقیقه گذشته)\n"
            f"📊 وضعیت عادی: {normal_rate:.2f} گزارش در ساعت\n\n"
            f"🔗 **منابع خبر:**\n" + "\n".join(context_msgs).replace('VIP Alert', 'هشدار ویژه').replace('VIP Update', 'به‌روزرسانی ویژه').replace('Edited', 'ویرایش شده') + "\n\n"
        )
        
        # Add tags and channel signature
        if not target_channel:
            if is_economy:
                target_channel = "@DidehbanEghtesad"
            elif is_protest:
                target_channel = "@DidebanEterazat"
            else:
                target_channel = "@DidebanJang"
                
        if target_channel == "@DidehbanEghtesad":
            alert_text += f"#دیده‌بان_اقتصاد\n\n{target_channel}"
        elif target_channel == "@DidebanEterazat":
            alert_text += f"#دیده‌بان_اعتراضات\n\n{target_channel}"
        else:
            alert_text += f"#دیده‌بان_جنگ\n\n{target_channel}"
            
        # Send only to the public channel (as requested by user)
        subs = [target_channel]
        
        sent_msg = None
        for sub in subs:
            try:
                sent_msg = await self.bot.send_message(sub, alert_text, link_preview=False, silent=is_silent)
                print(f"{icon} SENT ALERT for {pattern} to {sub}")
            except Exception as e:
                print(f"Failed to send alert to {sub}: {e}")
                
        return sent_msg, target_channel, alert_text

    async def classify_message(self, text):
        if not GEMINI_API_KEY:
            return None
            
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = "gemini-flash-lite-latest"
            
        prompt = (
            "تو یک تحلیلگر و دروازه‌بان هوشمند خبر برای یک سیستم دیده‌بان و مانیتورینگ تلگرام هستی.\n"
            "یک رویداد خبری مهم دریافت شده است. موضوع این خبر را تحلیل کن و مشخص کن آیا این خبر باید در یکی از ۳ کانال تخصصی زیر منتشر شود:\n\n"
            "دسته‌بندی‌های مجاز:\n"
            "1. WAR: اخبار جنگ، تنش‌های نظامی، حملات هوایی/موشکی/پهپادی، بمباران، پدافند هوایی، درگیری‌های مسلحانه، آژیر خطر، انفجارهای نظامی.\n"
            "2. ECONOMY: اخبار مهم اقتصادی، نوسانات شدید نرخ ارز (دلار، تتر، یورو)، طلا و سکه، بازار بورس، سقوط ریال، تصمیمات کلیدی ارزی و شوک‌های معیشتی.\n"
            "3. PROTEST_RIGHTS: اخبار اعتراضات مردمی، اعتصابات، تجمعات خیابانی، سرکوب معترضان، بازداشت‌ها، احکام دادگاه‌ها و پرونده‌های معترضان و فعالان، اجرای احکام اعدام، وضعیت زندانیان سیاسی.\n"
            "4. OTHER: اخبار متفرقه که در هیچ‌کدام از ۳ دسته بالا قرار نمی‌گیرد (مانند اخبار پزشکی، حمله قلبی، حوادث روزمره، اخبار فرهنگی/ورزشی، روابط دیپلماتیک عادی بدون جنگ، هواشناسی).\n\n"
            f"متن خبر:\n{text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب یک شیء JSON با این دو فیلد بنویس و هیچ کلمه یا توضیح دیگری قبل یا بعد از آن ننویس:\n"
            "{\n"
            '  "category": "WAR" | "ECONOMY" | "PROTEST_RIGHTS" | "OTHER",\n'
            '  "topic_title": "یک عنوان کوتاه و دقیق فارسی (حداکثر ۵ تا ۶ کلمه) متناسب با واقعه"\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 100,
                "temperature": 0.1
            }
        }
        
        max_retries = 2
        for attempt in range(max_retries):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.current_gemini_model}:generateContent?key={GEMINI_API_KEY}"
            try:
                import urllib.request
                import urllib.error
                import json
                
                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                loop = asyncio.get_running_loop()
                def make_req():
                    with urllib.request.urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode('utf-8'))
                        
                data = await loop.run_in_executor(None, make_req)
                raw_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if match:
                    res = json.loads(match.group(0))
                    category = str(res.get("category", "")).upper().strip()
                    topic_title = str(res.get("topic_title", "")).strip()
                    if category in ["WAR", "ECONOMY", "PROTEST_RIGHTS", "OTHER"]:
                        print(f"🎯 VIP AI Classification: category={category}, topic='{topic_title}' (Model: {self.current_gemini_model})")
                        return {"category": category, "topic_title": topic_title}
                break
            except urllib.error.HTTPError as e:
                if e.code == 404 and attempt < max_retries - 1:
                    print(f"⚠️ Model {self.current_gemini_model} not found (404). Seeking fallback model...")
                    self.current_gemini_model = await self.get_fallback_gemini_model()
                else:
                    print(f"⚠️ VIP AI Classifier HTTP Error: {e.code} - {e.reason}")
                    break
            except Exception as e:
                print(f"⚠️ VIP AI Classifier failed: {e}")
                break
                
        return None

    classify_vip_message = classify_message

    async def get_fallback_gemini_model(self):
        import urllib.request
        import json
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
        print(f"DEBUG: get_fallback url length: {len(url)}")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        loop = asyncio.get_running_loop()
        def make_req():
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode('utf-8'))
                
        try:
            data = await loop.run_in_executor(None, make_req)
            models = data.get('models', [])
            
            # Filter models that support generateContent
            valid_models = [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
            
            # Priority: 1. flash-lite, 2. flash, 3. pro
            for priority in ["flash-lite", "flash", "pro"]:
                for m in valid_models:
                    if priority in m['name'].lower():
                        return m['name'].split('models/')[1]
            
            # Ultimate fallback if nothing matches
            if valid_models:
                return valid_models[0]['name'].split('models/')[1]
                
        except Exception as e:
            print(f"⚠️ Failed to list Gemini models: {e}")
        
        return "gemini-pro" # safe fallback

    async def append_ai_summary(self, target_channel, message_id, original_text, raw_texts):
        if not GEMINI_API_KEY:
            return
            
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = "gemini-flash-lite-latest"
        
        print(f"🤖 Starting AI summary injection using model: {self.current_gemini_model}")
            
        prompt = (
            "تو یک دستیار هوشمند و بی‌طرف برای یک ربات خبری (دیده‌بان) هستی. "
            "متن خبرهای زیر از چند منبع مختلف (یا یک منبع طولانی) جمع‌آوری شده است. "
            "لطفاً یک چکیده دقیق، بی‌طرفانه، بدون قضاوت و بسیار کوتاه (حداکثر ۲ خط) از مهم‌ترین اتفاق این اخبار بنویس. "
            "هیچگونه تیتر، مقدمه، سلام، هشتگ یا توضیحات اضافه‌ای ننویس و فقط خودِ چکیده را ارائه بده.\n\nاخبار:\n"
            + "\n---\n".join(raw_texts)
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 150,
                "temperature": 0.3
            }
        }
        
        max_retries = 2
        
        for attempt in range(max_retries):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.current_gemini_model}:generateContent?key={GEMINI_API_KEY}"
            
            try:
                import urllib.request
                import urllib.error
                import json
                
                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                loop = asyncio.get_running_loop()
                def make_req():
                    with urllib.request.urlopen(req, timeout=15) as response:
                        return json.loads(response.read().decode('utf-8'))
                        
                try:
                    data = await loop.run_in_executor(None, make_req)
                    summary = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    
                    if summary:
                        parts = original_text.split("🔗 **منابع خبر:**")
                        if len(parts) == 2:
                            new_text = f"{parts[0]}🤖 **چکیده هوشمند:**\n{summary}\n\n🔗 **منابع خبر:**{parts[1]}"
                            await self.bot.edit_message(target_channel, message_id, new_text, link_preview=False)
                            print(f"✅ AI Summary added to message {message_id} in {target_channel} (Model: {self.current_gemini_model})")
                            return # Success, exit function
                            
                except urllib.error.HTTPError as e:
                    if e.code == 404 and attempt < max_retries - 1:
                        print(f"⚠️ Model {self.current_gemini_model} not found (404). Seeking fallback model...")
                        self.current_gemini_model = await self.get_fallback_gemini_model()
                        print(f"🔄 Retrying with model: {self.current_gemini_model}")
                    else:
                        print(f"⚠️ Gemini API HTTP Error: {e.code} - {e.reason}")
                        return
                        
            except Exception as e:
                print(f"⚠️ Failed to append AI summary: {e}")
                return

    async def summarize_vahid_post(self, text):
        if not GEMINI_API_KEY:
            return text[:250] + "..." if len(text) > 250 else text
            
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = "gemini-flash-lite-latest"
            
        # If text is already very brief (under 60 chars), keep it as is
        if len(text.strip()) < 60:
            return text.strip()
            
        prompt = (
            "تو دستیار هوشمند و خلاصه‌ساز خبر برای کانال تلگرام 'وحیدآنلاین هوشمند' (VahidOnlineAI) هستی.\n"
            "پست زیر از کانال تلگرام وحیدآنلاین منتشر شده است. "
            "لطفاً پیام اصلی، مهم‌ترین اتفاق و نکات کلیدی این متن را در ۲ الی ۴ خط بسیار روان، دقیق، رسا و بدون قضاوت خلاصه کن.\n"
            "دستورالعمل‌های الزامی:\n"
            "- هیچ مقدمه، سلام، توضیح اضافی یا عباراتی مثل 'خلاصه:' یا 'این خبر درباره...' ننویس.\n"
            "- هیچ هشتگی اضافه نکن.\n"
            "- فقط و فقط خودِ متن چکیده را بنویس.\n\n"
            f"متن پست:\n{text}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 200,
                "temperature": 0.2
            }
        }
        
        max_retries = 2
        for attempt in range(max_retries):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.current_gemini_model}:generateContent?key={GEMINI_API_KEY}"
            try:
                import urllib.request
                import urllib.error
                import json
                
                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                loop = asyncio.get_running_loop()
                def make_req():
                    with urllib.request.urlopen(req, timeout=12) as response:
                        return json.loads(response.read().decode('utf-8'))
                        
                data = await loop.run_in_executor(None, make_req)
                summary = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                if summary:
                    return summary
                break
            except urllib.error.HTTPError as e:
                if e.code == 404 and attempt < max_retries - 1:
                    print(f"⚠️ Model {self.current_gemini_model} not found (404). Seeking fallback model...")
                    self.current_gemini_model = await self.get_fallback_gemini_model()
                else:
                    print(f"⚠️ Gemini summarize HTTP Error: {e.code} - {e.reason}")
                    break
            except Exception as e:
                print(f"⚠️ Gemini summarize failed: {e}")
                break
                
        return text[:250] + "..." if len(text) > 250 else text

    async def handle_vahid_online_ai(self, text, msg_id, raw_msg=None, is_edit=False):
        target_channel = "@VahidOnlineAI"
        if not text and not (raw_msg and getattr(raw_msg, 'media', None)):
            return
            
        clean_text = (text or "").strip()
        if not clean_text:
            summary = "📷 [رسانه بدون متن منتشر شده در کانال وحیدآنلاین]"
        else:
            summary = await self.summarize_vahid_post(clean_text)
            
        link = f"https://t.me/VahidOnline/{msg_id}"
        
        post_content = (
            f"⚡️ **چکیده پست وحیدآنلاین:**\n\n"
            f"{summary}\n\n"
            f"🔗 [مشاهده پست اصلی در کانال وحیدآنلاین]({link})\n"
            f"📡 {target_channel}"
        )
        
        try:
            if is_edit and msg_id in self.vahid_ai_posted:
                posted_id = self.vahid_ai_posted[msg_id]
                await self.bot.edit_message(target_channel, posted_id, post_content, link_preview=False)
                print(f"✏️ Edited post {posted_id} in {target_channel} for VahidOnline msg {msg_id}")
            elif not is_edit and msg_id not in self.vahid_ai_posted:
                sent = None
                has_media = raw_msg and getattr(raw_msg, 'media', None)
                if has_media:
                    try:
                        sent = await self.bot.send_message(
                            target_channel, 
                            post_content, 
                            file=raw_msg.media, 
                            link_preview=False
                        )
                    except Exception as media_err:
                        print(f"⚠️ Could not send media to {target_channel} ({media_err}). Sending text with link.")
                        sent = await self.bot.send_message(
                            target_channel, 
                            post_content, 
                            link_preview=False
                        )
                else:
                    sent = await self.bot.send_message(
                        target_channel, 
                        post_content, 
                        link_preview=False
                    )
                    
                if sent:
                    self.vahid_ai_posted[msg_id] = sent.id
                    print(f"🚀 Published AI summary to {target_channel} for VahidOnline msg {msg_id}")
        except Exception as e:
            print(f"❌ Error publishing to {target_channel} for msg {msg_id}: {e}")

import signal
import sys

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING or not BOT_TOKEN:
        print("Error: Missing Telegram API credentials.")
        return
        
    print("👁️ Sentinel Eye (LIVE MODE): Initializing...")
    
    bot = TelegramClient(StringSession(), int(API_ID), API_HASH)
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    
    sentinel = LiveSentinel(bot)
    
    # Setup graceful shutdown on SIGTERM
    def handle_sigterm(sig, frame):
        print(f"🛑 Received signal {sig}. Raising KeyboardInterrupt...")
        raise KeyboardInterrupt()
        
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)
    
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

    def resolve_node_username(chat_obj):
        if not chat_obj: return 'unknown'
        u = getattr(chat_obj, 'username', None)
        if u: return u
        title = getattr(chat_obj, 'title', None)
        if title: return title
        return 'unknown'

    @client.on(events.NewMessage(chats=sentinel.nodes))
    async def user_handler(event):
        sender = await event.get_chat()
        node_username = resolve_node_username(sender)
        text = event.message.message
        msg_id = event.message.id
        msg_date = event.message.date
        
        await sentinel.process_message(text, node_username, msg_id, msg_date=msg_date, raw_msg=event.message)

    @client.on(events.MessageEdited(chats=sentinel.nodes))
    async def edit_handler(event):
        sender = await event.get_chat()
        node_username = resolve_node_username(sender)
        text = event.message.message
        msg_id = event.message.id
        msg_date = getattr(event.message, 'edit_date', None) or event.message.date
        
        await sentinel.process_message(text, node_username, msg_id, is_edit=True, msg_date=msg_date, raw_msg=event.message)

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
                            await sentinel.process_message(msg.message, node, msg.id, msg_date=msg.date, raw_msg=msg)
                except Exception as e:
                    pass
                await asyncio.sleep(1.5)
            await asyncio.sleep(15)
        
    try:
        await bot.start(bot_token=BOT_TOKEN)
        print("🤖 Bot listener started.")
        await client.start()
        print("✅ Live listening started on", len(sentinel.nodes), "nodes (Active Polling).")
    except Exception as e:
        if hasattr(e, 'seconds'):
            print(f"⚠️ FloodWaitError! Sleeping for {e.seconds} seconds before retrying...")
            await asyncio.sleep(e.seconds + 5)
            await bot.start(bot_token=BOT_TOKEN)
            await client.start()
        else:
            raise
    
    poller_task = asyncio.create_task(active_poller())
    
    try:
        # Run until time limit
        await asyncio.sleep(MAX_RUNTIME_SEC)
        
        print("⏰ Max runtime reached. Exiting gracefully to allow restart.")
    except asyncio.CancelledError:
        print("🛑 Task cancelled. Shutting down...")
    except KeyboardInterrupt:
        print("🛑 KeyboardInterrupt received. Shutting down...")
    except Exception as e:
        import traceback
        print("❌ UNHANDLED FATAL ERROR:")
        traceback.print_exc()
        sentinel.last_msg_text = f"FATAL ERROR: {str(e)}"
    finally:
        poller_task.cancel()
        print("🔌 Disconnecting Telegram sessions...")
        
        async def safe_disconnect():
            try:
                await client.disconnect()
                await bot.disconnect()
            except: pass
            
        try:
            await asyncio.wait_for(safe_disconnect(), timeout=10.0)
        except asyncio.TimeoutError:
            print("⚠️ Disconnect timed out, forcing exit.")
        
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

if __name__ == "__main__":
    asyncio.run(main())


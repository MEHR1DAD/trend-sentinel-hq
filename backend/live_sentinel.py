import os
import re
import sys

# Ensure repository root is on sys.path regardless of execution environment
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import json
import time
import signal
import asyncio
from datetime import datetime, timezone, timedelta
from collections import deque
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --- Core Modules ---
from backend.core.config import (
    CONFIG_FILE,
    BASELINE_FILE,
    STATE_FILE,
    POSTED_DB_FILE,
    MAX_RUNTIME_SEC,
    API_ID,
    API_HASH,
    SESSION_STRING,
    BOT_TOKEN,
    CHAT_ID,
    GEMINI_API_KEYS,
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODELS_PRIORITY,
    FOREIGN_KEYWORDS,
    IRAN_KEYWORDS,
    COUNTRY_THEATRES,
    get_tehran_time_fa,
    to_fa_num,
    detect_msg_media_type,
    get_media_icon_from_type
)
from backend.core.db import DatabaseManager
from backend.core.gemini import GeminiClient
from backend.core.thread_manager import ThreadAlertManager

# --- Pipelines ---
from backend.pipelines.mirror import MirrorPipeline
from backend.pipelines.intelligence import IntelligencePipeline
from backend.pipelines.radar import RadarPipeline


class LiveSentinel:
    """
    Master Orchestrator for Sentinel Eye (Live API Mode).
    Delegates channel processing to modular pipelines:
    - MirrorPipeline: @VahidOnlineAI, @iliaenAI
    - IntelligencePipeline: withyashar, Alibk / Alibk3
    - RadarPipeline: 3-channel consensus, anomaly detection, semantic cross-channel merging
    - ThreadAlertManager: thread merging, debounce updates, and live alert in-place sync
    """
    def __init__(self, bot, client=None):
        self.bot = bot
        self.client = client
        self.config = self.load_json(CONFIG_FILE)
        self.nodes = self.config.get('nodes', [])
        self.state_file = STATE_FILE
        self.incident_severities = self.config.get('patterns', {}).get('incident_severities', {})
        self.baselines = self.fetch_remote_baselines()
        self.lock = asyncio.Lock()

        # Core Engines
        self.db = DatabaseManager(POSTED_DB_FILE)
        self.gemini = GeminiClient(GEMINI_API_KEYS)
        self.threads = ThreadAlertManager(
            bot=self.bot,
            gemini=self.gemini,
            db=self.db,
            safe_edit_func=self.safe_edit_post,
            save_state_func=self.save_persisted_state
        )

        # Specialized Pipelines
        self.mirror_pipeline = MirrorPipeline(self)
        self.intelligence_pipeline = IntelligencePipeline(self, threads=self.threads)
        self.radar_pipeline = RadarPipeline(self, threads=self.threads)

        # In-Memory Buffers & Fast Caches
        self.recent_messages = deque()
        self.last_alert_time = {}
        self.alerted_msg_patterns = {}
        self.vip_alert_history = self.threads.vip_alert_history
        self.recent_alert_sources = deque()
        self.node_id_map = {}
        self.channel_last_alert_id = {}
        self.vahid_post_time = {}
        self.ilia_post_time = {}

        # Keywords
        self.foreign_keywords = FOREIGN_KEYWORDS
        self.iran_keywords = IRAN_KEYWORDS

        # Metrics
        self.start_time = time.time()
        self.total_msgs_processed = 0
        self.last_msg_text = "No messages yet"
        self.last_msg_time = "N/A"

        # Load persisted state
        self.load_persisted_state()

    # --- Backward-Compatible Properties ---
    @property
    def thread_alerts(self):
        return self.threads.thread_alerts

    @property
    def reply_to_root_map(self):
        return self.threads.reply_to_root_map

    @property
    def debounce_tasks(self):
        return self.threads.debounce_tasks

    @property
    def thread_locks(self):
        return self.threads.thread_locks

    @property
    def channel_watermarks(self):
        return self.db.channel_watermarks

    @property
    def recent_posted_map(self):
        return self.db.recent_posted_map

    @property
    def vahid_ai_posted(self):
        return self.mirror_pipeline.posted_map['VahidOnline']

    @property
    def ilia_ai_posted(self):
        return self.mirror_pipeline.posted_map['iliaen']

    @property
    def alibk_posted(self):
        return self.intelligence_pipeline.alibk_posted

    @property
    def current_gemini_model(self):
        return self.gemini.current_gemini_model

    @current_gemini_model.setter
    def current_gemini_model(self, val):
        self.gemini.current_gemini_model = val

    @property
    def latest_flash_lite_model(self):
        return self.gemini.latest_flash_lite_model

    @latest_flash_lite_model.setter
    def latest_flash_lite_model(self, val):
        self.gemini.latest_flash_lite_model = val

    # --- Core Utilities & DB Delegations ---
    def load_json(self, path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

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

    def get_gemini_api_key(self):
        return self.gemini.get_api_key()

    def rotate_gemini_api_key(self):
        return self.gemini.rotate_api_key()

    async def fetch_latest_google_models(self):
        return await self.gemini.fetch_latest_google_models()

    async def rotate_gemini_model_dynamic(self, failed_model=None):
        return await self.gemini.rotate_gemini_model_dynamic(failed_model)

    async def call_gemini_content(self, payload, timeout=14, max_retries=6):
        return await self.gemini.call_gemini_content(payload, timeout=timeout, max_retries=max_retries)

    def extract_message_urls(self, msg):
        urls = []
        if not msg:
            return urls
        txt = getattr(msg, 'text', '') or getattr(msg, 'message', '') or ''
        if txt:
            urls.extend(re.findall(r'https?://[^\s\)\<\>\"\'\]]+', txt))
        for ent in (getattr(msg, 'entities', []) or []):
            u = getattr(ent, 'url', None)
            if u:
                urls.append(u)
        return urls

    def record_posted_message(self, target_channel, source_node, source_msg_id, posted_msg_id):
        return self.db.record_posted_message(target_channel, source_node, source_msg_id, posted_msg_id)

    def is_source_posted(self, target_channel, source_node, source_msg_id):
        return self.db.is_source_posted(target_channel, source_node, source_msg_id)

    def detect_msg_media_type(self, msg):
        return detect_msg_media_type(msg)

    def get_media_icon_from_type(self, media_type):
        return get_media_icon_from_type(media_type)

    @staticmethod
    def to_fa_num(n):
        return to_fa_num(n)

    @staticmethod
    def get_tehran_time_fa(ts=None):
        return get_tehran_time_fa(ts)

    def determine_iran_related(self, ai_res, text, title=""):
        clean_text = (text or "") + " " + (title or "")
        if ai_res and ai_res.get('is_iran_related') is not None:
            return bool(ai_res['is_iran_related'])
            
        iran_conflict_indicators = [
            'حمله به ایران', 'پاسخ به ایران', 'تنش با ایران', 'حمله احتمالی', 
            'سوخت‌رسان', 'سوخت رسان', 'بمب‌افکن', 'بمب افکن', 'سنتکام', 
            'ناو هواپیمابر', 'تنگه هرمز', 'خلیج فارس', 'سپاه', 'نطنز', 'فردو'
        ]
        if any(ind in clean_text for ind in iran_conflict_indicators):
            return True
            
        has_foreign_kw = any(fk in clean_text for fk in self.foreign_keywords)
        has_iran_kw = any(ik in clean_text for ik in self.iran_keywords)
        
        if has_iran_kw:
            return True
        if has_foreign_kw:
            return False
            
        return False

    # --- Preload & State Persistence ---
    async def preload_node_entities(self, reader_client):
        try:
            from telethon.utils import get_peer_id
            for n in self.nodes:
                try:
                    ent = await reader_client.get_entity(n)
                    self.node_id_map[ent.id] = n
                    self.node_id_map[get_peer_id(ent)] = n
                except Exception:
                    pass
            print(f"📡 Preloaded {len(self.node_id_map)} node entity mappings.")
        except Exception as e:
            print(f"⚠️ Could not preload node entities: {e}")

    def load_persisted_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                now = time.time()
                raw_patterns = data.get('alerted_msg_patterns', {})
                for k, v in raw_patterns.items():
                    if isinstance(v, list):
                        self.alerted_msg_patterns[k] = set(v)
                
                raw_vip = data.get('vip_alert_history', {})
                for k, v in raw_vip.items():
                    if isinstance(v, dict) and (now - v.get('time', 0)) <= 259200:
                        self.vip_alert_history[k] = v
                
                raw_last = data.get('last_alert_time', {})
                for k, v in raw_last.items():
                    if isinstance(v, (int, float)) and (now - v) <= 86400:
                        self.last_alert_time[k] = v
                
                raw_alibk = data.get('alibk_posted', {})
                for k, v in raw_alibk.items():
                    if isinstance(v, dict):
                        try:
                            self.intelligence_pipeline.alibk_posted[int(k)] = v
                        except Exception:
                            self.intelligence_pipeline.alibk_posted[k] = v

                raw_threads = data.get('thread_alerts', {})
                for k, v in raw_threads.items():
                    if isinstance(v, dict) and (now - v.get('timestamp', 0)) <= 259200:
                        self.thread_alerts[k] = v

                raw_reply_map = data.get('reply_to_root_map', {})
                for k, v in raw_reply_map.items():
                    if isinstance(v, (int, str)):
                        self.reply_to_root_map[k] = int(v) if str(v).isdigit() else v

                self.channel_last_alert_id = data.get('channel_last_alert_id', {})

                raw_vahid_time = data.get('vahid_post_time', {})
                for k, v in raw_vahid_time.items():
                    if isinstance(v, (int, float)) and (now - v) <= 259200:
                        self.vahid_post_time[int(k) if str(k).isdigit() else k] = v

                raw_ilia_time = data.get('ilia_post_time', {})
                for k, v in raw_ilia_time.items():
                    if isinstance(v, (int, float)) and (now - v) <= 259200:
                        self.ilia_post_time[int(k) if str(k).isdigit() else k] = v

                raw_vahid_ai = data.get('vahid_ai_posted', {})
                for k, v in raw_vahid_ai.items():
                    try:
                        self.mirror_pipeline.posted_map['VahidOnline'][int(k)] = v
                    except Exception:
                        self.mirror_pipeline.posted_map['VahidOnline'][k] = v

                raw_ilia_ai = data.get('ilia_ai_posted', {})
                for k, v in raw_ilia_ai.items():
                    try:
                        self.mirror_pipeline.posted_map['iliaen'][int(k)] = v
                    except Exception:
                        self.mirror_pipeline.posted_map['iliaen'][k] = v
                
                raw_watermarks = data.get('channel_watermarks', {})
                for k, v in raw_watermarks.items():
                    if isinstance(v, (int, float)):
                        self.db.channel_watermarks[k] = int(v)
                
                print(f"📦 Loaded persistent state: {len(self.alerted_msg_patterns)} alerted msg keys, {len(self.vip_alert_history)} VIP alerts, {len(self.intelligence_pipeline.alibk_posted)} Alibk alerts, {len(self.mirror_pipeline.posted_map['VahidOnline'])} Vahid AI, {len(self.mirror_pipeline.posted_map['iliaen'])} Ilia AI, {len(self.db.channel_watermarks)} watermarks.")
        except Exception as e:
            print(f"⚠️ Error loading persisted state: {e}")

    def save_persisted_state(self):
        try:
            now = time.time()
            clean_vip = {k: v for k, v in self.vip_alert_history.items() if isinstance(v, dict) and (now - v.get('time', 0)) <= 259200}
            clean_last = {k: v for k, v in self.last_alert_time.items() if isinstance(v, (int, float)) and (now - v) <= 86400}
            clean_threads = {k: v for k, v in self.thread_alerts.items() if isinstance(v, dict) and (now - v.get('timestamp', 0)) <= 259200}
            active_roots = {v.get('root_id') for v in clean_threads.values()}
            clean_reply_map = {k: v for k, v in self.reply_to_root_map.items() if v in active_roots}
            clean_vahid_time = {str(k): v for k, v in self.vahid_post_time.items() if (now - v) <= 259200}
            clean_ilia_time = {str(k): v for k, v in self.ilia_post_time.items() if (now - v) <= 259200}
            clean_vahid_ai = {str(k): v for k, v in self.mirror_pipeline.posted_map['VahidOnline'].items()}
            clean_ilia_ai = {str(k): v for k, v in self.mirror_pipeline.posted_map['iliaen'].items()}
            serializable_patterns = {k: list(v) for k, v in self.alerted_msg_patterns.items()}
            
            data = {
                "alerted_msg_patterns": serializable_patterns,
                "vip_alert_history": clean_vip,
                "last_alert_time": clean_last,
                "alibk_posted": self.intelligence_pipeline.alibk_posted,
                "thread_alerts": clean_threads,
                "reply_to_root_map": clean_reply_map,
                "channel_last_alert_id": self.channel_last_alert_id,
                "channel_watermarks": self.db.channel_watermarks,
                "vahid_post_time": clean_vahid_time,
                "ilia_post_time": clean_ilia_time,
                "vahid_ai_posted": clean_vahid_ai,
                "ilia_ai_posted": clean_ilia_ai,
                "last_saved": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving persisted state: {e}")

    async def cleanup_update_notification_bubbles(self, reader_client=None):
        target_channels = ['@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat', '@VahidOnlineAI', '@iliaenAI']
        fetch_client = reader_client or self.client or self.bot
        print("🧹 Starting quick scan to delete any legacy update notification bubbles across channels...")
        for ch in target_channels:
            try:
                to_delete = []
                async for m in fetch_client.iter_messages(ch, limit=15):
                    text = m.text or m.message or ""
                    clean_m = text.replace('\u200c', '').replace(' ', '').replace('*', '')
                    is_update_bubble = False
                    if "اینگزارشبادریافتاطلاعاتتکمیلی" in clean_m:
                        is_update_bubble = True
                    elif "اطلاعاتتکمیلیبهروزرسانیشد" in clean_m or "اطلاعاتتکمیلیبروزرسانیشد" in clean_m:
                        is_update_bubble = True
                    elif ("بهروزرسانیشد" in clean_m or "بروزرسانیشد" in clean_m or "اطلاعاتتکمیلی" in clean_m):
                        if len(text.strip()) < 200 and not any(k in text for k in ["چکیده", "هشدار", "گزارش اقتصادی", "گزارش حقوق بشری", "گزارش مردمی"]):
                            is_update_bubble = True
                    
                    if is_update_bubble:
                        to_delete.append(m.id)
                    elif "سرعت انتشار" in text or "وضعیت عادی" in text:
                        clean_text = re.sub(r'⚡️\s*سرعت انتشار[^\n]*\n+', '', text)
                        clean_text = re.sub(r'📊\s*وضعیت عادی[^\n]*\n+', '', clean_text)
                        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
                        try:
                            await self.bot.edit_message(ch, m.id, clean_text, link_preview=False)
                            print(f"🧹 Cleaned velocity/baseline lines from {ch} msg {m.id}")
                        except Exception:
                            pass

                if to_delete:
                    print(f"🗑️ Found {len(to_delete)} update notification bubbles in {ch}: {to_delete}")
                    for i in range(0, len(to_delete), 50):
                        batch = to_delete[i:i+50]
                        try:
                            await self.bot.delete_messages(ch, batch)
                            print(f"🗑️ Deleted {len(batch)} notification bubbles via bot in {ch}.")
                        except Exception as del_err:
                            print(f"⚠️ Bot failed to delete in {ch}: {del_err}")
                            
                        if fetch_client != self.bot:
                            try:
                                await fetch_client.delete_messages(ch, batch)
                                print(f"🗑️ Deleted {len(batch)} notification bubbles via client in {ch}.")
                            except Exception as c_err:
                                print(f"⚠️ Client failed to delete in {ch}: {c_err}")
                else:
                    print(f"✨ No update notification bubbles found in {ch}.")
            except Exception as e:
                print(f"⚠️ Error cleaning up notification bubbles in {ch}: {e}")

    async def safe_edit_post(self, channel, msg_id, new_text, msg_obj=None, reader_client=None):
        """
        Robust message editor trying all available credentials:
        1. Message object directly (msg_obj.edit)
        2. Bot client (self.bot.edit_message)
        3. User client (reader_client.edit_message or self.client.edit_message)
        """
        if msg_obj:
            try:
                await msg_obj.edit(new_text, link_preview=False)
                print(f"✅ Successfully edited {channel} msg {msg_id} via Message object.")
                return True
            except Exception as e_obj:
                print(f"⚠️ Message object edit failed for {channel}/{msg_id}: {e_obj}")

        if self.bot:
            try:
                await self.bot.edit_message(channel, msg_id, new_text, link_preview=False)
                print(f"✅ Successfully edited {channel} msg {msg_id} via Bot client.")
                return True
            except Exception as e_bot:
                print(f"⚠️ Bot edit failed for {channel}/{msg_id}: {e_bot}")

        u_client = reader_client or self.client
        if u_client:
            try:
                await u_client.edit_message(channel, msg_id, new_text, link_preview=False)
                print(f"✅ Successfully edited {channel} msg {msg_id} via User client.")
                return True
            except Exception as e_user:
                print(f"⚠️ User client edit failed for {channel}/{msg_id}: {e_user}")

        return False

    async def init_channel_last_alert_ids(self, reader_client=None):
        target_channels = ['@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat', '@VahidOnlineAI', '@iliaenAI']
        fetch_client = reader_client or self.client or self.bot
        await self.cleanup_update_notification_bubbles(reader_client=fetch_client)

        vip_map = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar', 'alibk3': 'Alibk3'}
        for ch in target_channels:
            try:
                async for m in fetch_client.iter_messages(ch, limit=35):
                    if not m or not m.text:
                        continue
                    if ch == '@DidebanJang':
                        if m.id == 165 and 'خبر فوری' not in m.text:
                            fixed_165 = (
                                "⚡️ **خبر فوری: تاخیر در ورود ناو هواپیمابر «تئودور روزولت» آمریکا به ژاپن به دلیل شرایط طوفانی** 📝\n\n"
                                "📝 **چکیده رویداد:**\n"
                                "روزنامه آساهی شیمبون ژاپن گزارش داد ورود ناو هواپیمابر هسته‌ای «تئودور روزولت» (CVN-71) و ناوگروه ضربت آن به پایگاه دریایی یوکوسوکا به دلیل وقوع طوفان‌های شدید دریایی به تعویق افتاده و این ناوگروه با چند روز تاخیر، بین ۱۵ تا ۲۰ اکتبر وارد منطقه خواهد شد.\n\n"
                                "🔗 **منبع خبر:**\n"
                                "- [withyashar](https://t.me/withyashar/25470) 📝\n\n"
                                "🔄 **به‌روزرسانی شده (ساعت ۰۹:۳۹) | ویرایش و بازنویسی تحلیلی**\n\n"
                                "#دیده‌بان_جنگ\n@DidebanJang"
                            )
                            await self.safe_edit_post('@DidebanJang', 165, fixed_165, msg_obj=m, reader_client=fetch_client)
                        elif m.id == 166 and 'خبر فوری' not in m.text:
                            fixed_166 = (
                                "⚡️ **خبر فوری: تاکید سناتور لیندسی گراهام بر ادامه فشار نظامی علیه ایران و مخالفت با توقف درگیری‌ها** 📹\n\n"
                                "📝 **چکیده رویداد:**\n"
                                "سناتور جمهوری‌خواه آمریکایی با ابراز مخالفت صریح نسبت به توقف جنگ، مدعی شد به دلیل در دسترس بودن پیروزی و خطرناک ماندن توانمندی‌های نظامی ایران، واشنگتن نباید در شرایط کنونی عملیات یا فشارهای خود را متوقف کند.\n\n"
                                "🔗 **منبع خبر:**\n"
                                "- [withyashar](https://t.me/withyashar/25469) 📹\n\n"
                                "🔄 **به‌روزرسانی شده (ساعت ۰۹:۴۹) | ویرایش و بازنویسی تحلیلی**\n\n"
                                "#دیده‌بان_جنگ\n@DidebanJang"
                            )
                            await self.safe_edit_post('@DidebanJang', 166, fixed_166, msg_obj=m, reader_client=fetch_client)

                    rep_to = getattr(m, 'reply_to_msg_id', None)
                    if not rep_to and getattr(m, 'reply_to', None):
                        rep_to = getattr(m.reply_to, 'reply_to_msg_id', None)
                    if rep_to:
                        continue
                    if len(m.text.strip()) > 10 and ch not in self.channel_last_alert_id:
                        self.channel_last_alert_id[ch] = m.id
                        
                    all_urls = self.extract_message_urls(m)
                    found_sources = []
                    for u in all_urls:
                        sm = re.search(r't\.me/([A-Za-z0-9_]+)/(\d+)', u)
                        if sm:
                            found_sources.append((sm.group(1), sm.group(2)))
                    for src_node, src_id_str in found_sources:
                        src_id = int(src_id_str)
                        canon = vip_map.get(src_node.lower(), src_node)
                        self.db.advance_watermark(canon, src_id)
                        self.db.record_posted_message(ch, canon, src_id, m.id)
                        mk = f"{canon}_{src_id}"
                        if mk not in self.vip_alert_history:
                            self.vip_alert_history[mk] = {
                                'time': m.date.timestamp() if m.date else time.time(),
                                'pattern': 'recovered_source',
                                'text': m.text,
                                'sent_msg_id': m.id,
                                'target_channel': ch
                            }
                        self.alerted_msg_patterns.setdefault(mk, set()).add('recovered_source')
                        if canon == 'Alibk3':
                            self.intelligence_pipeline.alibk_posted.setdefault(src_id, {'channel': ch, 'msg_id': m.id, 'timestamp': m.date.timestamp() if m.date else time.time()})
                        elif canon == 'VahidOnline' and ch == '@VahidOnlineAI':
                            self.mirror_pipeline.posted_map['VahidOnline'].setdefault(src_id, m.id)
                        elif canon == 'iliaen' and ch == '@iliaenAI':
                            self.mirror_pipeline.posted_map['iliaen'].setdefault(src_id, m.id)
                            
                        if mk not in self.thread_alerts and ch in ('@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat'):
                            title_m = re.search(r'^(?:🚨|🔕|⚡️|📈|⚖️|📢)\s*\*\*(?:هشدار فوری|خبر فوری|هشدار منطقه‌ای|گزارش اقتصادی|گزارش حقوق بشری|گزارش مردمی):\s*([^\*\n]+)\*\*', m.text)
                            title_text = title_m.group(1).strip() if title_m else ""
                            summary_m = re.search(r'📝\s*\*\*چکیده رویداد:\*\*\s*\n([^\n]+(?:\n[^\n]+)?)', m.text)
                            summary_text = summary_m.group(1).strip() if summary_m else ""
                            self.thread_alerts[mk] = {
                                'channel': ch,
                                'alert_msg_id': m.id,
                                'node': canon,
                                'root_id': src_id,
                                'root_text': m.text,
                                'root_media_type': 'text',
                                'timestamp': m.date.timestamp() if m.date else time.time(),
                                'title': title_text,
                                'summary': summary_text,
                                'category': 'ECONOMY' if ch == '@DidehbanEghtesad' else ('PROTEST_RIGHTS' if ch == '@DidebanEterazat' else 'WAR'),
                                'is_iran_related': ('🚨' in m.text or '⚡️' in m.text),
                                'sources': [{
                                    'node': canon,
                                    'msg_id': src_id,
                                    'media_type': 'text',
                                    'link': f"https://t.me/{canon}/{src_id}",
                                    'timestamp': m.date.timestamp() if m.date else time.time()
                                }],
                                'replies': []
                            }
            except Exception as e:
                print(f"⚠️ Error initializing alert history in {ch}: {e}")
                
        # Retroactive polish for Posts 165 and 166 in @DidebanJang (fix raw text copies & titles)
        try:
            m165 = await fetch_client.get_messages('@DidebanJang', ids=165)
            if m165 and m165.text and 'روزنامه ژاپنی' in m165.text and 'خبر فوری' not in m165.text:
                fixed_165 = (
                    "⚡️ **خبر فوری: تاخیر در ورود ناو هواپیمابر «تئودور روزولت» آمریکا به ژاپن به دلیل شرایط طوفانی** 📝\n\n"
                    "📝 **چکیده رویداد:**\n"
                    "روزنامه آساهی شیمبون ژاپن گزارش داد ورود ناو هواپیمابر هسته‌ای «تئودور روزولت» (CVN-71) و ناوگروه ضربت آن به پایگاه دریایی یوکوسوکا به دلیل وقوع طوفان‌های شدید دریایی به تعویق افتاده و این ناوگروه با چند روز تاخیر، بین ۱۵ تا ۲۰ اکتبر وارد منطقه خواهد شد.\n\n"
                    "🔗 **منبع خبر:**\n"
                    "- [withyashar](https://t.me/withyashar/25470) 📝\n\n"
                    "🔄 **به‌روزرسانی شده (ساعت ۰۹:۳۹) | ویرایش و بازنویسی تحلیلی**\n\n"
                    "#دیده‌بان_جنگ\n@DidebanJang"
                )
                await self.safe_edit_post('@DidebanJang', 165, fixed_165, msg_obj=m165, reader_client=fetch_client)
                print("✅ Successfully polished post 165 in @DidebanJang!")
        except Exception as e165:
            print(f"⚠️ Error polishing post 165 in @DidebanJang: {e165}")

        try:
            m166 = await fetch_client.get_messages('@DidebanJang', ids=166)
            if m166 and m166.text and 'گراهام' in m166.text and 'خبر فوری' not in m166.text:
                fixed_166 = (
                    "⚡️ **خبر فوری: تاکید سناتور لیندسی گراهام بر ادامه فشار نظامی علیه ایران و مخالفت با توقف درگیری‌ها** 📹\n\n"
                    "📝 **چکیده رویداد:**\n"
                    "سناتور جمهوری‌خواه آمریکایی با ابراز مخالفت صریح نسبت به توقف جنگ، مدعی شد به دلیل در دسترس بودن پیروزی و خطرناک ماندن توانمندی‌های نظامی ایران، واشنگتن نباید در شرایط کنونی عملیات یا فشارهای خود را متوقف کند.\n\n"
                    "🔗 **منبع خبر:**\n"
                    "- [withyashar](https://t.me/withyashar/25469) 📹\n\n"
                    "🔄 **به‌روزرسانی شده (ساعت ۰۹:۴۹) | ویرایش و بازنویسی تحلیلی**\n\n"
                    "#دیده‌بان_جنگ\n@DidebanJang"
                )
                await self.safe_edit_post('@DidebanJang', 166, fixed_166, msg_obj=m166, reader_client=fetch_client)
                print("✅ Successfully polished post 166 in @DidebanJang!")
        except Exception as e166:
            print(f"⚠️ Error polishing post 166 in @DidebanJang: {e166}")

        print(f"🎯 Initialized last alert IDs: {self.channel_last_alert_id}")

    # --- Preload & Catch-Up Forwarding ---
    async def preload_vahid_ai_history(self, reader_client=None):
        return await self.mirror_pipeline.preload_history(reader_client, 'VahidOnline', '@VahidOnlineAI')

    async def preload_ilia_ai_history(self, reader_client=None):
        return await self.mirror_pipeline.preload_history(reader_client, 'iliaen', '@iliaenAI')

    async def preload_alibk_history(self, reader_client=None):
        return await self.intelligence_pipeline.preload_alibk_history(reader_client)

    async def catchup_unposted_vahid(self, reader_client=None):
        return await self.mirror_pipeline.catchup_unposted(reader_client, 'VahidOnline', '@VahidOnlineAI')

    async def catchup_unposted_ilia(self, reader_client=None):
        return await self.mirror_pipeline.catchup_unposted(reader_client, 'iliaen', '@iliaenAI')

    async def catchup_unposted_alibk(self, reader_client=None):
        return await self.intelligence_pipeline.catchup_unposted_alibk(reader_client)

    async def catchup_unposted_withyashar(self, reader_client=None):
        return await self.intelligence_pipeline.catchup_unposted_withyashar(reader_client)

    async def flush_all_pending_debounces(self):
        return await self.threads.flush_all_pending_debounces()

    async def handle_message_deleted(self, node_username, deleted_ids):
        return await self.radar_pipeline.handle_message_deleted(node_username, deleted_ids)

    async def verify_recent_messages_existence(self):
        return await self.radar_pipeline.verify_recent_messages_existence()

    async def check_and_merge_with_active_alert(self, text, node, msg_id, target_channel, candidate_title="", candidate_summary="", raw_msg=None, category='WAR', is_iran_related=False, candidate_sources=None):
        return await self.threads.check_and_merge_with_active_alert(
            text, node, msg_id, target_channel,
            candidate_title=candidate_title, candidate_summary=candidate_summary,
            raw_msg=raw_msg, category=category, is_iran_related=is_iran_related,
            candidate_sources=candidate_sources
        )

    def build_alert_post_content(self, thread_data):
        return self.threads.build_alert_post_content(thread_data)

    def schedule_thread_debounce(self, thread_key):
        return self.threads.schedule_thread_debounce(thread_key)

    # --- Central Event Router ---
    async def process_message(self, text, node, msg_id, is_edit=False, msg_date=None, raw_msg=None, skip_mirror_channels=False):
        async with self.lock:
            canon = (node or '').lower()
            vip_map_fast = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar', 'alibk3': 'Alibk3'}
            canon_node = vip_map_fast.get(canon, node or 'unknown')
            if not is_edit:
                self.db.advance_watermark(canon_node, msg_id)

            if not skip_mirror_channels:
                # 1. Mirror Pipelines (@VahidOnlineAI, @iliaenAI)
                if canon == 'vahidonline':
                    asyncio.create_task(self.mirror_pipeline.handle_message('VahidOnline', '@VahidOnlineAI', text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    asyncio.create_task(self.radar_pipeline.process_vip_channel(text, 'VahidOnline', msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return
                elif canon == 'iliaen':
                    asyncio.create_task(self.mirror_pipeline.handle_message('iliaen', '@iliaenAI', text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    asyncio.create_task(self.radar_pipeline.process_vip_channel(text, 'iliaen', msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return
                # 2. Intelligence / VIP Analytical Pipelines (Alibk, withyashar)
                elif canon == 'alibk3':
                    asyncio.create_task(self.intelligence_pipeline.handle_alibk(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return
                elif canon == 'withyashar':
                    asyncio.create_task(self.intelligence_pipeline.handle_withyashar(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return

            # 3. Consensus Radar Pipeline (General monitored nodes)
            await self.radar_pipeline.process_radar_consensus(text, node, msg_id, is_edit=is_edit, msg_date=msg_date, raw_msg=raw_msg)


async def main():
    if not API_ID or not API_HASH or not SESSION_STRING or not BOT_TOKEN:
        print("Error: Missing Telegram API credentials.")
        return
        
    print("👁️ Sentinel Eye (LIVE MODE): Initializing...")
    
    bot = TelegramClient(StringSession(), int(API_ID), API_HASH)
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    
    sentinel = LiveSentinel(bot, client=client)
    
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
        if not chat_obj:
            return 'unknown'
        chat_id = getattr(chat_obj, 'id', None)
        if chat_id and chat_id in sentinel.node_id_map:
            return sentinel.node_id_map[chat_id]
        try:
            from telethon.utils import get_peer_id
            peer_id = get_peer_id(chat_obj)
            if peer_id in sentinel.node_id_map:
                return sentinel.node_id_map[peer_id]
        except Exception:
            pass
            
        u = getattr(chat_obj, 'username', None)
        if u and re.match(r'^[A-Za-z0-9_]+$', str(u)):
            return str(u)
            
        usernames = getattr(chat_obj, 'usernames', None)
        if usernames:
            for un in usernames:
                uname = getattr(un, 'username', None)
                if uname and re.match(r'^[A-Za-z0-9_]+$', str(uname)):
                    return str(uname)
                    
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

    @client.on(events.MessageDeleted())
    async def delete_handler(event):
        try:
            chat_id = event.chat_id
            node_username = sentinel.node_id_map.get(chat_id)
            if not node_username and hasattr(event, 'channel_id') and event.channel_id:
                node_username = sentinel.node_id_map.get(event.channel_id)
                if not node_username:
                    from telethon.utils import get_peer_id
                    from telethon.tl.types import PeerChannel
                    node_username = sentinel.node_id_map.get(get_peer_id(PeerChannel(event.channel_id)))

            if not node_username:
                try:
                    chat = await event.get_chat()
                    node_username = resolve_node_username(chat)
                except Exception:
                    node_username = None

            deleted_ids = getattr(event, 'deleted_ids', [])
            if deleted_ids:
                await sentinel.handle_message_deleted(node_username, deleted_ids)
        except Exception as e:
            print(f"⚠️ Error handling deleted message event: {e}")

    async def active_poller():
        last_ids = {}
        last_deletion_check = time.time()
        while True:
            if time.time() - last_deletion_check >= 90:
                last_deletion_check = time.time()
                try:
                    await sentinel.verify_recent_messages_existence()
                except Exception:
                    pass

            for node in sentinel.nodes:
                try:
                    messages = await client.get_messages(node, limit=5)
                    if messages:
                        latest_id = messages[0].id
                        if node not in last_ids:
                            wm = sentinel.db.get_watermark(node)
                            last_ids[node] = wm if wm > 0 else latest_id
                            new_msgs = [m for m in messages if m.id > last_ids[node]]
                            new_msgs.reverse()
                            for m in new_msgs:
                                await sentinel.process_message(m.message, node, m.id, msg_date=m.date, raw_msg=m, skip_mirror_channels=True)
                            last_ids[node] = max(last_ids[node], latest_id)
                        else:
                            new_msgs = [m for m in messages if m.id > last_ids[node]]
                            new_msgs.reverse()
                            for m in new_msgs:
                                await sentinel.process_message(m.message, node, m.id, msg_date=m.date, raw_msg=m)
                            last_ids[node] = max(last_ids[node], latest_id)
                except Exception:
                    pass
                await asyncio.sleep(2.0)
            await asyncio.sleep(20)

    async def heartbeat_monitor():
        while True:
            await asyncio.sleep(60)
            try:
                uptime_mins = int((time.time() - sentinel.start_time) / 60)
                now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                print(f"💓 [HEARTBEAT] Uptime: {uptime_mins}m | Processed: {sentinel.total_msgs_processed} msgs | Active Nodes: {len(sentinel.nodes)} | Time: {now_str}")
                
                if client and not client.is_connected():
                    print("⚠️ Telethon client disconnected detected in heartbeat! Reconnecting...")
                    await client.connect()
                if bot and not bot.is_connected():
                    print("⚠️ Telethon bot disconnected detected in heartbeat! Reconnecting...")
                    await bot.connect()
            except Exception as e:
                print(f"⚠️ Heartbeat monitor error: {e}")
        
    try:
        await bot.start(bot_token=BOT_TOKEN)
        print("🤖 Bot listener started.")
        await client.start()
        print("✅ Live listening started on", len(sentinel.nodes), "nodes (Active Polling & Push Events).")
        
        poller_task = asyncio.create_task(active_poller())
        heartbeat_task = asyncio.create_task(heartbeat_monitor())
        
        await sentinel.preload_node_entities(reader_client=client)
        try:
            await asyncio.wait_for(sentinel.fetch_latest_google_models(), timeout=5.0)
        except Exception as gm_err:
            print(f"⚠️ Startup model query timed out or failed: {gm_err}")
        await sentinel.init_channel_last_alert_ids(reader_client=client)
        
        print("📚 Preloading recent post history from target channels...")
        await asyncio.gather(
            sentinel.preload_vahid_ai_history(reader_client=client),
            sentinel.preload_ilia_ai_history(reader_client=client),
            sentinel.preload_alibk_history(reader_client=client),
            return_exceptions=True
        )
        
        print("⚡ Executing transition catch-up: iliaen first, then VahidOnline and Alibk3...")
        try:
            await sentinel.catchup_unposted_ilia(reader_client=client)
        except Exception as e:
            print(f"⚠️ Ilia catchup error: {e}")
        try:
            await sentinel.catchup_unposted_vahid(reader_client=client)
        except Exception as e:
            print(f"⚠️ Vahid catchup error: {e}")
        try:
            await sentinel.catchup_unposted_alibk(reader_client=client)
        except Exception as e:
            print(f"⚠️ Alibk catchup error: {e}")
        try:
            await sentinel.catchup_unposted_withyashar(reader_client=client)
        except Exception as e:
            print(f"⚠️ withyashar catchup error: {e}")
    except Exception as e:
        if hasattr(e, 'seconds'):
            print(f"⚠️ FloodWaitError! Sleeping for {e.seconds} seconds before retrying...")
            await asyncio.sleep(e.seconds + 5)
            await bot.start(bot_token=BOT_TOKEN)
            await client.start()
            poller_task = asyncio.create_task(active_poller())
            heartbeat_task = asyncio.create_task(heartbeat_monitor())
            await sentinel.preload_node_entities(reader_client=client)
            await sentinel.init_channel_last_alert_ids(reader_client=client)
            await asyncio.gather(
                sentinel.preload_vahid_ai_history(reader_client=client),
                sentinel.preload_ilia_ai_history(reader_client=client),
                sentinel.preload_alibk_history(reader_client=client),
                return_exceptions=True
            )
            await asyncio.gather(
                sentinel.catchup_unposted_ilia(reader_client=client),
                sentinel.catchup_unposted_vahid(reader_client=client),
                sentinel.catchup_unposted_alibk(reader_client=client),
                sentinel.catchup_unposted_withyashar(reader_client=client),
                return_exceptions=True
            )
        else:
            raise
    
    try:
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
        heartbeat_task.cancel()
        print("⏳ Flushing any pending debounce updates before exit...")
        try:
            await sentinel.flush_all_pending_debounces()
        except Exception as e:
            print(f"⚠️ Error flushing debounces: {e}")
        print("🔌 Disconnecting Telegram sessions...")
        
        async def safe_disconnect():
            try:
                await client.disconnect()
                await bot.disconnect()
            except Exception:
                pass
            
        try:
            await asyncio.wait_for(safe_disconnect(), timeout=10.0)
        except asyncio.TimeoutError:
            print("⚠️ Disconnect timed out, forcing exit.")
        
        sentinel.save_persisted_state()
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
    os.system('git add session_report.md backend/sentinel_state.json backend/posted_registry.db')
    os.system('git diff --staged --quiet || (git commit -m "[skip ci] save session report and state" && git pull --rebase origin main && git push || true)')


if __name__ == "__main__":
    asyncio.run(main())

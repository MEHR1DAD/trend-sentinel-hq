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
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession

# --- Core Modules ---
from backend.core.config import (
    CONFIG_FILE,
    BASELINE_FILE,
    STATE_FILE,
    POSTED_DB_FILE,
    PEER_CACHE_FILE,
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
from backend.pipelines.radar import RadarPipeline
from backend.pipelines.vip_multi_agent import VipMultiAgentEditorialPipeline

class _LegacyMirrorStub:
    def __init__(self, posted_map):
        self.posted_map = posted_map
    async def preload_history(self, *args, **kwargs):
        return 0

class _LegacyIntelligenceStub:
    def __init__(self, alibk_posted):
        self.alibk_posted = alibk_posted
    async def preload_alibk_history(self, *args, **kwargs):
        return 0

class _LegacyYasharStub:
    pass


class LiveSentinel:
    """
    Master Orchestrator for Sentinel Eye (Live API Mode).
    Delegates channel processing to modular pipelines:
    - MirrorPipeline: @VahidOnlineAI, @iliaenAI
    - YasharAutonomousPipeline: 100% Autonomous AI Editor for withyashar
    - IntelligencePipeline: Alibk / Alibk3
    - RadarPipeline: 3-channel consensus, anomaly detection, semantic cross-channel merging
    - ThreadAlertManager: thread merging, debounce updates, and live alert in-place sync
    """
    def __init__(self, bot, client=None):
        self.bot = bot
        self.client = client
        self.config = self.load_json(CONFIG_FILE)
        self.nodes = self.config.get('nodes', [])
        self.state_file = STATE_FILE
        self.peer_cache_file = PEER_CACHE_FILE
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

        # Decoupled Legacy Maps (replacing heavy ghost modules)
        self.mirror_posted_map = {'VahidOnline': {}, 'iliaen': {}}
        self.alibk_posted_map = {}
        self.mirror_pipeline = _LegacyMirrorStub(self.mirror_posted_map)
        self.intelligence_pipeline = _LegacyIntelligenceStub(self.alibk_posted_map)
        self.yashar_pipeline = _LegacyYasharStub()

        # Active Pipelines
        self.radar_pipeline = RadarPipeline(self, threads=self.threads)
        self.vip_pipeline = VipMultiAgentEditorialPipeline(self, threads=self.threads)

        # In-Memory Buffers & Fast Caches
        self.recent_messages = deque()
        self.last_alert_time = {}
        self.alerted_msg_patterns = {}
        self.vip_alert_history = self.threads.vip_alert_history
        self.recent_alert_sources = deque()
        self.node_id_map = {}
        self.node_input_peers = {}
        self.node_peer_cache = {'node_peers': {}, 'id_to_node': {}}
        self.channel_last_alert_id = {}
        self.vahid_post_time = {}
        self.ilia_post_time = {}

        # Load permanent peer cache from disk
        self.load_peer_cache()

        # Keywords
        self.foreign_keywords = FOREIGN_KEYWORDS
        self.iran_keywords = IRAN_KEYWORDS

        # Metrics
        self.start_time = time.time()
        self.total_msgs_processed = 0
        self.last_msg_text = "No messages yet"
        self.last_msg_time = "N/A"

        # Smart Queue for Unprocessed Messages during API Cooldown/Outages (TTL: 45m / 2700s)
        self.smart_queue = []
        self.smart_queue_raw_msgs = {}
        self.smart_queue_lock = asyncio.Lock()
        self.smart_queue_task = None

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
        return self.mirror_posted_map['VahidOnline']

    @property
    def ilia_ai_posted(self):
        return self.mirror_posted_map['iliaen']

    @property
    def alibk_posted(self):
        return self.alibk_posted_map

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

    # --- Permanent Peer Cache & Node Resolution ---
    def load_peer_cache(self):
        """
        بارگذاری شناسه‌های عددی کانال‌ها از دیسک برای جلوگیری قطعی از ResolveUsernameRequest و ارور FloodWait
        """
        data = self.load_json(self.peer_cache_file)
        if not data:
            data = {'node_peers': {}, 'id_to_node': {}}
        self.node_peer_cache = data

        peers = data.get('node_peers', {})
        for node, info in peers.items():
            if isinstance(info, dict):
                cid = info.get('channel_id')
                pid = info.get('peer_id')
                ah = info.get('access_hash')
                clean_n = node.lstrip('@')
                at_n = f"@{clean_n}"
                aliases = {node, clean_n, at_n, node.lower(), clean_n.lower(), at_n.lower()}
                for n_alias in aliases:
                    if cid:
                        self.node_id_map[cid] = node
                        self.node_id_map[str(cid)] = node
                    if pid:
                        self.node_id_map[pid] = node
                        self.node_id_map[str(pid)] = node
                    if cid and ah:
                        from telethon.tl.types import InputPeerChannel
                        inp = InputPeerChannel(channel_id=int(cid), access_hash=int(ah))
                        self.node_input_peers[n_alias] = inp

        id_map = data.get('id_to_node', {})
        for k, v in id_map.items():
            try:
                self.node_id_map[int(k)] = v
            except Exception:
                pass
            self.node_id_map[str(k)] = v

        if peers:
            print(f"📡 [PEER CACHE] Loaded {len(peers)} node entities from {self.peer_cache_file}.")

    def save_peer_cache(self):
        """
        ذخیره دائمی کش متادیتا و شناسه‌های عددی کانال‌ها روی دیسک
        """
        try:
            with open(self.peer_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.node_peer_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving peer cache to {self.peer_cache_file}: {e}")

    def record_node_peer(self, node, ent):
        """
        ثبت متادیتای عددی کانال تلگرام در کش دیسک برای عدم نیاز به استعلام مجدد
        """
        if not ent or not node:
            return
        from telethon.utils import get_peer_id
        cid = getattr(ent, 'id', None)
        ah = getattr(ent, 'access_hash', 0)
        try:
            pid = get_peer_id(ent)
        except Exception:
            pid = None

        if cid:
            clean_n = node.lstrip('@')
            at_n = f"@{clean_n}"
            self.node_peer_cache.setdefault('node_peers', {})[node] = {
                'channel_id': cid,
                'access_hash': ah,
                'peer_id': pid
            }
            self.node_peer_cache.setdefault('id_to_node', {})[str(cid)] = node
            if pid:
                self.node_peer_cache.setdefault('id_to_node', {})[str(pid)] = node
            aliases = {node, clean_n, at_n, node.lower(), clean_n.lower(), at_n.lower()}
            for n_alias in aliases:
                self.node_id_map[cid] = node
                self.node_id_map[str(cid)] = node
                if pid:
                    self.node_id_map[pid] = node
                    self.node_id_map[str(pid)] = node
                if ah:
                    from telethon.tl.types import InputPeerChannel
                    inp = InputPeerChannel(channel_id=int(cid), access_hash=int(ah))
                    self.node_input_peers[n_alias] = inp
            self.save_peer_cache()

    def get_node_input_peer(self, node):
        if not node:
            return None
        node_str = str(node)
        clean_n = node_str.lstrip('@')
        at_n = f"@{clean_n}"
        return (
            self.node_input_peers.get(node_str)
            or self.node_input_peers.get(node_str.lower())
            or self.node_input_peers.get(clean_n)
            or self.node_input_peers.get(clean_n.lower())
            or self.node_input_peers.get(at_n)
            or self.node_input_peers.get(at_n.lower())
        )

    def resolve_event_node(self, event):
        """
        تشخیص هویت کانال مبدا کاملاً درون حافظه RAM بر اساس شناسه‌های عددی بدون هیچ درخواست شبکه به تلگرام
        """
        if not event:
            return None

        chat_id = getattr(event, 'chat_id', None)
        if chat_id is not None:
            if chat_id in self.node_id_map:
                return self.node_id_map[chat_id]
            if str(chat_id) in self.node_id_map:
                return self.node_id_map[str(chat_id)]

        cid = getattr(event, 'channel_id', None)
        if cid is not None:
            if cid in self.node_id_map:
                return self.node_id_map[cid]
            if str(cid) in self.node_id_map:
                return self.node_id_map[str(cid)]

        msg = getattr(event, 'message', None)
        if msg:
            peer = getattr(msg, 'peer_id', None)
            if peer:
                from telethon.utils import get_peer_id
                try:
                    pid = get_peer_id(peer)
                    if pid in self.node_id_map:
                        return self.node_id_map[pid]
                    if str(pid) in self.node_id_map:
                        return self.node_id_map[str(pid)]
                except Exception:
                    pass
        return None

    async def preload_node_entities(self, reader_client):
        self.load_peer_cache()
        cached_nodes = set(self.node_peer_cache.get('node_peers', {}).keys())

        # Include all radar/source nodes AND destination target channels
        dest_channels = ['@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat', '@VahidOnlineAI', '@iliaenAI']
        all_targets = list(self.nodes)
        for d in dest_channels:
            if d not in all_targets and d.lstrip('@') not in all_targets:
                all_targets.append(d)

        missing_nodes = []
        for n in all_targets:
            clean_n = n.lstrip('@')
            at_n = f"@{clean_n}"
            if n not in cached_nodes and clean_n not in cached_nodes and at_n not in cached_nodes:
                missing_nodes.append(n)

        if not missing_nodes:
            print(f"📡 [PEER CACHE] All {len(all_targets)} source & destination entities verified in local disk cache. ZERO Telegram resolve requests made!")
            return

        print(f"📡 [PEER CACHE] {len(missing_nodes)} entity/entities missing from disk cache. Resolving with anti-flood delay (1.2s)...")
        for n in missing_nodes:
            try:
                ent = await reader_client.get_entity(n)
                self.record_node_peer(n, ent)
                print(f"   ✅ Cached entity: {n} (ID: {ent.id})")
                await asyncio.sleep(1.2)
            except Exception as e:
                print(f"⚠️ [PEER CACHE] Could not resolve entity for {n}: {e}")
        self.save_peer_cache()

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
                            self.alibk_posted_map[int(k)] = v
                        except Exception:
                            self.alibk_posted_map[k] = v

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
                        self.mirror_posted_map['VahidOnline'][int(k)] = v
                    except Exception:
                        self.mirror_posted_map['VahidOnline'][k] = v

                raw_ilia_ai = data.get('ilia_ai_posted', {})
                for k, v in raw_ilia_ai.items():
                    try:
                        self.mirror_posted_map['iliaen'][int(k)] = v
                    except Exception:
                        self.mirror_posted_map['iliaen'][k] = v
                
                raw_watermarks = data.get('channel_watermarks', {})
                for k, v in raw_watermarks.items():
                    if isinstance(v, (int, float)):
                        self.db.channel_watermarks[k] = int(v)
                
                raw_stats = data.get('gemini_model_stats', {})
                if isinstance(raw_stats, dict):
                    self.gemini.model_usage_stats.update(raw_stats)

                raw_queue = data.get('smart_queue', [])
                now = time.time()
                self.smart_queue = [q for q in raw_queue if isinstance(q, dict) and (now - q.get('enqueued_at', 0)) <= 2700]
                queue_log = f", {len(self.smart_queue)} queued items (45m TTL)" if self.smart_queue else ""
                
                print(f"📦 Loaded persistent state: {len(self.alerted_msg_patterns)} alerted msg keys, {len(self.vip_alert_history)} VIP alerts, {len(self.alibk_posted_map)} Alibk alerts, {len(self.mirror_posted_map['VahidOnline'])} Vahid AI, {len(self.mirror_posted_map['iliaen'])} Ilia AI, {len(self.db.channel_watermarks)} watermarks{queue_log}.")
        except Exception as e:
            print(f"⚠️ Error loading persisted state: {e}")

    def save_persisted_state(self):
        try:
            now = time.time()
            clean_vip = {k: v for k, v in self.vip_alert_history.items() if isinstance(v, dict) and (now - v.get('time', 0)) <= 86400}
            clean_last = {k: v for k, v in self.last_alert_time.items() if isinstance(v, (int, float)) and (now - v) <= 86400}
            clean_threads = {k: v for k, v in self.thread_alerts.items() if isinstance(v, dict) and (now - v.get('timestamp', 0)) <= 86400}
            active_roots = {v.get('root_id') for v in clean_threads.values()}
            clean_reply_map = {k: v for k, v in self.reply_to_root_map.items() if v in active_roots}
            clean_vahid_time = {str(k): v for k, v in self.vahid_post_time.items() if (now - v) <= 259200}
            clean_ilia_time = {str(k): v for k, v in self.ilia_post_time.items() if (now - v) <= 259200}

            # هرس دیکشنری‌های آینه به حداکثر ۱۵۰ مورد اخیر
            vahid_ai_items = list(self.mirror_posted_map['VahidOnline'].items())[-150:]
            clean_vahid_ai = {str(k): v for k, v in vahid_ai_items}
            ilia_ai_items = list(self.mirror_posted_map['iliaen'].items())[-150:]
            clean_ilia_ai = {str(k): v for k, v in ilia_ai_items}

            # هرس پست‌های علی‌بک با TTL سه روزه (یا حداکثر ۱۵۰ مورد اخیر)
            clean_alibk = {
                k: v for k, v in self.alibk_posted_map.items()
                if isinstance(v, dict) and (now - v.get('timestamp', 0)) <= 259200
            }
            if len(clean_alibk) < 50:
                clean_alibk = dict(list(self.alibk_posted_map.items())[-150:])

            clean_queue = [q for q in self.smart_queue if isinstance(q, dict) and (now - q.get('enqueued_at', 0)) <= 2700]

            # هرس الگوهای پیام برای سبک‌سازی شدید sentinel_state.json از ۶۵۰ کیلوبایت به زیر ۸۰ کیلوبایت
            active_keys = set(clean_vip.keys()) | set(clean_threads.keys())
            recent_pattern_items = list(self.alerted_msg_patterns.items())[-150:]
            clean_patterns = {k: list(v) for k, v in self.alerted_msg_patterns.items() if k in active_keys}
            for k, v in recent_pattern_items:
                clean_patterns[k] = list(v)

            data = {
                "alerted_msg_patterns": clean_patterns,
                "vip_alert_history": clean_vip,
                "last_alert_time": clean_last,
                "alibk_posted": clean_alibk,
                "thread_alerts": clean_threads,
                "reply_to_root_map": clean_reply_map,
                "channel_last_alert_id": self.channel_last_alert_id,
                "channel_watermarks": self.db.channel_watermarks,
                "vahid_post_time": clean_vahid_time,
                "ilia_post_time": clean_ilia_time,
                "vahid_ai_posted": clean_vahid_ai,
                "ilia_ai_posted": clean_ilia_ai,
                "gemini_model_stats": self.gemini.model_usage_stats,
                "smart_queue": clean_queue,
                "last_saved": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving persisted state: {e}")

    async def cleanup_update_notification_bubbles(self, reader_client=None):
        """
        Consolidated into init_channel_last_alert_ids for zero redundant message scans.
        """
        pass

    async def safe_edit_post(self, channel, msg_id, new_text, msg_obj=None, reader_client=None):
        """
        Robust message editor trying all available credentials:
        1. Message object directly (msg_obj.edit)
        2. Bot client (self.bot.edit_message)
        3. User client (reader_client.edit_message or self.client.edit_message)
        """
        target_peer = self.get_node_input_peer(channel) or channel
        if msg_obj and getattr(msg_obj, 'out', False):
            try:
                await msg_obj.edit(new_text, link_preview=False)
                print(f"✅ Successfully edited {channel} msg {msg_id} via Message object.")
                return True
            except Exception as e_obj:
                print(f"⚠️ Message object edit failed for {channel}/{msg_id}: {e_obj}")

        if self.bot:
            try:
                await self.bot.edit_message(target_peer, msg_id, new_text, link_preview=False)
                print(f"✅ Successfully edited {channel} msg {msg_id} via Bot client.")
                return True
            except Exception as e_bot:
                print(f"⚠️ Bot edit failed for {channel}/{msg_id}: {e_bot}")

        u_client = reader_client or self.client
        if u_client:
            try:
                await u_client.edit_message(target_peer, msg_id, new_text, link_preview=False)
                print(f"✅ Successfully edited {channel} msg {msg_id} via User client.")
                return True
            except Exception as e_user:
                print(f"⚠️ User client edit failed for {channel}/{msg_id}: {e_user}")

        return False

    async def safe_delete_post(self, channel, msg_id, reader_client=None):
        """
        چتر نجات: حذف قطعی و امن پیام در صورت ایجاد دابل‌پست تصادفی یا دستور پاکسازی
        تلاش با هر دو کلاینت: ربات ادمین و کلاینت کاربر با استفاده از شناسه عددی (Peer ID)
        """
        deleted = False
        target_peer = self.get_node_input_peer(channel) or channel
        target_ids = [msg_id] if isinstance(msg_id, int) else list(msg_id)
        if self.bot:
            try:
                await self.bot.delete_messages(target_peer, target_ids)
                print(f"🗑️ [SAFE DELETE] Successfully deleted msg #{msg_id} in {channel} via Bot client.")
                deleted = True
            except Exception as e_bot:
                print(f"⚠️ Bot failed to delete msg #{msg_id} in {channel}: {e_bot}")

        u_client = reader_client or self.client
        if u_client:
            try:
                await u_client.delete_messages(target_peer, target_ids)
                print(f"🗑️ [SAFE DELETE] Successfully deleted msg #{msg_id} in {channel} via User client.")
                deleted = True
            except Exception as e_user:
                print(f"⚠️ User client failed to delete msg #{msg_id} in {channel}: {e_user}")

        return deleted

    async def init_channel_last_alert_ids(self, reader_client=None):
        """
        اسکن تجمیعی و سبک شروع کار:
        تنها یک بار پیمایش ۴۰ پیام اخیر هر کانال دیده‌بان (به جای ۳ اسکن جداگانه).
        وظایف در همین تک‌پیمایش:
        ۱. پاکسازی حباب‌های اعلان و خطوط اضافه
        ۲. استخراج و کش منابع (Alibk3, withyashar, VahidOnline, iliaen)
        ۳. تنظیم آخرین ID معتبر کانال (channel_last_alert_id)
        ۴. چتر نجات: حذف پست ۲۱۲ تکراری در صورت وجود
        """
        target_channels = ['@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat']
        fetch_client = reader_client or self.client or self.bot
        vip_map = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar', 'alibk3': 'Alibk3'}

        for ch in target_channels:
            target_peer = self.get_node_input_peer(ch) or ch
            to_delete_bubbles = []
            try:
                async for m in fetch_client.iter_messages(target_peer, limit=40):
                    if not m:
                        continue
                    text = m.text or m.message or ""
                    
                    # الف) بررسی حباب‌های اعلان قدیمی جهت پاکسازی
                    clean_m = text.replace('\u200c', '').replace(' ', '').replace('*', '')
                    is_update_bubble = False
                    if "اینگزارشبادریافتاطلاعاتتکمیلی" in clean_m or "اطلاعاتتکمیلیبهروزرسانیشد" in clean_m or "اطلاعاتتکمیلیبروزرسانیشد" in clean_m:
                        is_update_bubble = True
                    elif ("بهروزرسانیشد" in clean_m or "بروزرسانیشد" in clean_m or "اطلاعاتتکمیلی" in clean_m):
                        if len(text.strip()) < 200 and not any(k in text for k in ["چکیده", "هشدار", "گزارش اقتصادی", "گزارش حقوق بشری", "گزارش مردمی"]):
                            is_update_bubble = True

                    if is_update_bubble:
                        to_delete_bubbles.append(m.id)
                        continue

                    if not text:
                        continue

                    # ب) پالایش خطوط سرعت انتشار در صورت وجود
                    if "سرعت انتشار" in text or "وضعیت عادی" in text:
                        clean_text = re.sub(r'⚡️\s*سرعت انتشار[^\n]*\n+', '', text)
                        clean_text = re.sub(r'📊\s*وضعیت عادی[^\n]*\n+', '', clean_text)
                        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
                        try:
                            await self.bot.edit_message(target_peer, m.id, clean_text, link_preview=False)
                        except Exception:
                            pass

                    # ج) تنظیم channel_last_alert_id
                    rep_to = getattr(m, 'reply_to_msg_id', None)
                    if not rep_to and getattr(m, 'reply_to', None):
                        rep_to = getattr(m.reply_to, 'reply_to_msg_id', None)
                    if not rep_to and len(text.strip()) > 10 and ch not in self.channel_last_alert_id:
                        self.channel_last_alert_id[ch] = m.id

                    # د) استخراج منابع و لینک‌های کانال‌های VIP
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
                                'text': text,
                                'sent_msg_id': m.id,
                                'target_channel': ch
                            }
                        self.alerted_msg_patterns.setdefault(mk, set()).add('recovered_source')
                        if canon == 'Alibk3':
                            self.alibk_posted_map.setdefault(src_id, {'channel': ch, 'msg_id': m.id, 'timestamp': m.date.timestamp() if m.date else time.time()})
                        elif canon == 'VahidOnline' and ch == '@VahidOnlineAI':
                            self.mirror_posted_map['VahidOnline'].setdefault(src_id, m.id)
                        elif canon == 'iliaen' and ch == '@iliaenAI':
                            self.mirror_posted_map['iliaen'].setdefault(src_id, m.id)

                        if mk not in self.thread_alerts and ch in ('@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat'):
                            title_m = re.search(r'^(?:🚨|🔕|⚡️|📈|⚖️|📢)\s*\*\*(?:هشدار فوری|خبر فوری|هشدار منطقه‌ای|گزارش اقتصادی|گزارش حقوق بشری|گزارش مردمی):\s*([^\*\n]+)\*\*', text)
                            title_text = title_m.group(1).strip() if title_m else ""
                            summary_m = re.search(r'📝\s*\*\*چکیده رویداد:\*\*\s*\n([^\n]+(?:\n[^\n]+)?)', text)
                            summary_text = summary_m.group(1).strip() if summary_m else ""
                            self.thread_alerts[mk] = {
                                'channel': ch,
                                'alert_msg_id': m.id,
                                'node': canon,
                                'root_id': src_id,
                                'root_text': text,
                                'root_media_type': 'text',
                                'timestamp': m.date.timestamp() if m.date else time.time(),
                                'title': title_text,
                                'summary': summary_text,
                                'category': 'ECONOMY' if ch == '@DidehbanEghtesad' else ('PROTEST_RIGHTS' if ch == '@DidebanEterazat' else 'WAR'),
                                'is_iran_related': ('🚨' in text or '⚡️' in text),
                                'sources': [{
                                    'node': canon,
                                    'msg_id': src_id,
                                    'media_type': 'text',
                                    'link': f"https://t.me/{canon}/{src_id}",
                                    'timestamp': m.date.timestamp() if m.date else time.time()
                                }],
                                'replies': []
                            }

                if to_delete_bubbles:
                    print(f"🗑️ Found {len(to_delete_bubbles)} update notification bubbles in {ch}. Deleting...")
                    await self.safe_delete_post(ch, to_delete_bubbles, reader_client=fetch_client)

            except Exception as e:
                print(f"⚠️ Error initializing alert history in {ch}: {e}")

        print(f"🎯 Initialized last alert IDs: {self.channel_last_alert_id}")

    # --- Preload & Catch-Up Forwarding ---
    async def preload_vahid_ai_history(self, reader_client=None):
        return await self.mirror_pipeline.preload_history(reader_client, 'VahidOnline', '@VahidOnlineAI')

    async def preload_ilia_ai_history(self, reader_client=None):
        return await self.mirror_pipeline.preload_history(reader_client, 'iliaen', '@iliaenAI')

    async def preload_alibk_history(self, reader_client=None):
        return await self.intelligence_pipeline.preload_alibk_history(reader_client)

    async def catchup_unposted_vahid(self, reader_client=None):
        return await self.vip_pipeline.catchup_unposted(reader_client, 'VahidOnline')

    async def catchup_unposted_ilia(self, reader_client=None):
        return await self.vip_pipeline.catchup_unposted(reader_client, 'iliaen')

    async def catchup_unposted_alibk(self, reader_client=None):
        return await self.vip_pipeline.catchup_unposted(reader_client, 'Alibk3')

    async def catchup_unposted_withyashar(self, reader_client=None):
        return await self.vip_pipeline.catchup_unposted(reader_client, 'withyashar')

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

    # ==========================================
    # --- SMART RETRY QUEUE (PLAN B / 45m TTL) ---
    # ==========================================

    async def enqueue_unprocessed_message(self, pipeline, node, msg_id, text, raw_msg=None, is_edit=False):
        item_id = f"{pipeline}_{node}_{msg_id}"
        async with self.smart_queue_lock:
            if any(item.get('id') == item_id for item in self.smart_queue):
                print(f"ℹ️ [SMART QUEUE] Item {item_id} is already in the queue. Skipping duplicate enqueue.")
                return

            now = time.time()
            item = {
                "id": item_id,
                "pipeline": pipeline,
                "node": node,
                "msg_id": msg_id,
                "text": text,
                "is_edit": is_edit,
                "enqueued_at": now,
                "retries": 0,
                "last_retry_at": 0
            }
            self.smart_queue.append(item)
            if raw_msg:
                self.smart_queue_raw_msgs[item_id] = raw_msg
            print(f"📥 [SMART QUEUE: ENQUEUED] Added {item_id} to queue (Queue size: {len(self.smart_queue)}, TTL: 45m / 2700s). Waiting for API availability...")
            self.save_persisted_state()

    async def run_smart_queue_worker(self):
        print("🚀 [SMART QUEUE] Background Retry Worker started (TTL: 45m / 2700s, Check Interval: 15s).")
        while True:
            try:
                await asyncio.sleep(15)
                await self.process_smart_queue_cycle()
            except asyncio.CancelledError:
                print("🛑 [SMART QUEUE] Background Retry Worker stopped.")
                break
            except Exception as e:
                print(f"⚠️ [SMART QUEUE] Worker loop error: {e}")

    async def process_smart_queue_cycle(self):
        now = time.time()
        ttl_seconds = 2700  # 45 minutes

        async with self.smart_queue_lock:
            if not self.smart_queue:
                return

            remaining_items = []
            for item in self.smart_queue:
                age = now - item.get('enqueued_at', now)
                item_id = item.get('id', 'unknown')
                if age > ttl_seconds:
                    age_min = age / 60.0
                    print(f"⏰ [SMART QUEUE: EXPIRED] Item {item_id} (msg #{item.get('msg_id')} from {item.get('node')}) exceeded TTL of 45m (Age: {age_min:.1f}m). Dropping without publishing.")
                    self.smart_queue_raw_msgs.pop(item_id, None)
                    continue
                remaining_items.append(item)

            self.smart_queue = remaining_items
            if not self.smart_queue:
                return

            oldest_age_min = (now - min(q.get('enqueued_at', now) for q in self.smart_queue)) / 60.0
            print(f"📊 [SMART QUEUE: STATUS] {len(self.smart_queue)} pending message(s) waiting in queue | Oldest age: {oldest_age_min:.1f}m.")

        # Process pending items
        for item in list(self.smart_queue):
            item_id = item.get('id')
            now = time.time()
            retries = item.get('retries', 0)
            backoff_delay = min(15 * (2 ** min(retries, 3)), 90)
            if (now - item.get('last_retry_at', 0)) < backoff_delay:
                continue

            item['retries'] = retries + 1
            item['last_retry_at'] = now
            age = now - item.get('enqueued_at', now)
            print(f"🔄 [SMART QUEUE: RETRYING] Attempt #{item['retries']} for {item_id} (Backoff: {backoff_delay}s | Age: {age:.1f}s / {ttl_seconds}s)...")

            raw_msg = self.smart_queue_raw_msgs.get(item_id)
            if not raw_msg and self.client:
                try:
                    target_peer = self.get_node_input_peer(item.get('node')) or item.get('node')
                    fetched = await self.client.get_messages(target_peer, ids=[item['msg_id']])
                    if fetched:
                        raw_msg = fetched[0] if isinstance(fetched, list) else fetched
                        self.smart_queue_raw_msgs[item_id] = raw_msg
                except Exception:
                    pass

            success = False
            try:
                if item.get('pipeline') in ('vip_multi_agent', 'vip', 'yashar', 'alibk'):
                    success = await self.vip_pipeline.process_queue_item(item, raw_msg=raw_msg)
                elif item.get('pipeline') == 'radar':
                    # Future radar queue extensions
                    success = False
            except Exception as proc_err:
                print(f"⚠️ [SMART QUEUE: ERROR] Processing failed for {item_id}: {proc_err}")
                success = False

            if success:
                async with self.smart_queue_lock:
                    self.smart_queue = [q for q in self.smart_queue if q.get('id') != item_id]
                    self.smart_queue_raw_msgs.pop(item_id, None)
                total_wait = time.time() - item.get('enqueued_at', now)
                print(f"✅ [SMART QUEUE: SUCCESS] Item {item_id} processed successfully after {total_wait:.1f}s in queue ({item['retries']} retries).")
                self.save_persisted_state()
                await asyncio.sleep(2.0)
            else:
                print(f"⏳ [SMART QUEUE: REMAINING] Item {item_id} still pending AI availability. Will retry in next worker cycle (attempt #{item.get('retries')}).")

    # --- Central Event Router ---
    async def process_message(self, text, node, msg_id, is_edit=False, msg_date=None, raw_msg=None, skip_mirror_channels=False):
        async with self.lock:
            canon = (node or '').lower()
            vip_map_fast = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar', 'alibk3': 'Alibk3'}
            canon_node = vip_map_fast.get(canon, node or 'unknown')

            if not skip_mirror_channels:
                # VIP Channels: 4-Soldier Multi-Agent Pipeline (withyashar, Alibk3, VahidOnline, iliaen)
                if canon in ('vahidonline', 'iliaen', 'withyashar', 'alibk3'):
                    asyncio.create_task(self.vip_pipeline.handle_message(text, canon_node, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return

            # 4. Consensus Radar Pipeline (General monitored nodes)
            await self.radar_pipeline.process_radar_consensus(text, node, msg_id, is_edit=is_edit, msg_date=msg_date, raw_msg=raw_msg)
            if not is_edit:
                self.db.advance_watermark(canon_node, msg_id)


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

    @client.on(events.NewMessage())
    async def user_handler(event):
        node_username = sentinel.resolve_event_node(event)
        if not node_username:
            return
        text = event.message.message
        msg_id = event.message.id
        msg_date = event.message.date
        await sentinel.process_message(text, node_username, msg_id, msg_date=msg_date, raw_msg=event.message)

    @client.on(events.MessageEdited())
    async def edit_handler(event):
        node_username = sentinel.resolve_event_node(event)
        if not node_username:
            return
        text = event.message.message
        msg_id = event.message.id
        msg_date = getattr(event.message, 'edit_date', None) or event.message.date
        await sentinel.process_message(text, node_username, msg_id, is_edit=True, msg_date=msg_date, raw_msg=event.message)

    @client.on(events.MessageDeleted())
    async def delete_handler(event):
        try:
            node_username = sentinel.resolve_event_node(event)
            if not node_username:
                return
            deleted_ids = getattr(event, 'deleted_ids', [])
            if deleted_ids:
                await sentinel.handle_message_deleted(node_username, deleted_ids)
        except Exception as e:
            print(f"⚠️ Error handling deleted message event: {e}")

    async def active_poller():
        last_ids = {}
        last_deletion_check = time.time()
        general_idx = 0
        vip_nodes = [n for n in sentinel.nodes if (n or '').lower() in ('withyashar', 'alibk3', 'vahidonline', 'iliaen')]
        general_nodes = [n for n in sentinel.nodes if n not in vip_nodes]

        while True:
            if time.time() - last_deletion_check >= 180:
                last_deletion_check = time.time()
                try:
                    await sentinel.verify_recent_messages_existence()
                except Exception as del_err:
                    print(f"⚠️ [POLLER] Error verifying recent messages: {del_err}")

            # ترکیب کانال‌های VIP (همیشه) با یک دسته چرخشی از ۵ کانال عمومی
            cycle_nodes = list(vip_nodes)
            if general_nodes:
                batch_size = 5
                batch = [general_nodes[(general_idx + i) % len(general_nodes)] for i in range(min(batch_size, len(general_nodes)))]
                cycle_nodes.extend(batch)
                general_idx = (general_idx + batch_size) % len(general_nodes)

            for node in cycle_nodes:
                try:
                    target_peer = sentinel.get_node_input_peer(node) or node
                    messages = await client.get_messages(target_peer, limit=5)
                    if messages:
                        latest_id = messages[0].id
                        wm = sentinel.db.get_watermark(node)
                        effective_last_id = max(last_ids.get(node, 0), wm)

                        if node not in last_ids:
                            last_ids[node] = effective_last_id if effective_last_id > 0 else latest_id
                            new_msgs = [m for m in messages if m.id > last_ids[node]]
                            new_msgs.reverse()
                            for m in new_msgs:
                                await sentinel.process_message(m.message, node, m.id, msg_date=m.date, raw_msg=m, skip_mirror_channels=True)
                            last_ids[node] = max(last_ids[node], latest_id)
                        else:
                            new_msgs = [m for m in messages if m.id > effective_last_id]
                            new_msgs.reverse()
                            for m in new_msgs:
                                await sentinel.process_message(m.message, node, m.id, msg_date=m.date, raw_msg=m)
                            last_ids[node] = max(effective_last_id, latest_id)
                except errors.FloodWaitError as fwe:
                    print(f"⚠️ [POLLER: FLOOD WAIT] Telegram requested {fwe.seconds}s pause on node {node}. Sleeping...")
                    await asyncio.sleep(fwe.seconds + 5)
                except Exception as e:
                    print(f"⚠️ [POLLER: ERROR] Error polling {node}: {e}")
                await asyncio.sleep(1.5)
            await asyncio.sleep(45)

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
        smart_queue_task = asyncio.create_task(sentinel.run_smart_queue_worker())
        
        await sentinel.preload_node_entities(reader_client=client)
        try:
            await asyncio.wait_for(sentinel.fetch_latest_google_models(), timeout=5.0)
        except Exception as gm_err:
            print(f"⚠️ Startup model query timed out or failed: {gm_err}")
        await sentinel.init_channel_last_alert_ids(reader_client=client)
        
        print("⚡ Executing transition catch-up for all 4 VIP channels...")
        for catchup_coro in (
            sentinel.catchup_unposted_alibk(reader_client=client),
            sentinel.catchup_unposted_withyashar(reader_client=client),
            sentinel.catchup_unposted_vahid(reader_client=client),
            sentinel.catchup_unposted_ilia(reader_client=client)
        ):
            try:
                await catchup_coro
            except Exception as e:
                print(f"⚠️ VIP catchup error: {e}")
    except Exception as e:
        if hasattr(e, 'seconds'):
            print(f"⚠️ FloodWaitError! Sleeping for {e.seconds} seconds before retrying...")
            await asyncio.sleep(e.seconds + 5)
            await bot.start(bot_token=BOT_TOKEN)
            await client.start()
            poller_task = asyncio.create_task(active_poller())
            heartbeat_task = asyncio.create_task(heartbeat_monitor())
            smart_queue_task = asyncio.create_task(sentinel.run_smart_queue_worker())
            await sentinel.preload_node_entities(reader_client=client)
            await sentinel.init_channel_last_alert_ids(reader_client=client)
            await asyncio.gather(
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
        if 'smart_queue_task' in locals() and not smart_queue_task.done():
            smart_queue_task.cancel()
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
        try:
            sentinel.gemini.save_cached_stacks()
        except Exception as e_mod:
            print(f"⚠️ Could not save gemini model cache: {e_mod}")
        try:
            sentinel.db.optimize()
        except Exception:
            pass
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
    os.system('git add session_report.md backend/sentinel_state.json backend/posted_registry.db backend/telegram_peer_cache.json backend/model_cache.json')
    os.system('git diff --staged --quiet || (git commit -m "[skip ci] save session report and state" && git pull --rebase origin main && git push || true)')


if __name__ == "__main__":
    asyncio.run(main())

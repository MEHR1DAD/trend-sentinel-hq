import os
import json
import time
import asyncio
import re
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from collections import deque

# --- Config ---
CONFIG_FILE = 'backend/sentinel_config.json'
BASELINE_FILE = 'backend/trend_baselines.json'
STATE_FILE = 'backend/sentinel_state.json'
POSTED_DB_FILE = 'backend/posted_registry.db'
MAX_RUNTIME_SEC = 35 * 60  # 35 minutes graceful rotation

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

GEMINI_API_KEY_RAW = os.environ.get('GEMINI_API_KEY', '')
GEMINI_API_KEYS = [k.strip() for k in GEMINI_API_KEY_RAW.split(',') if k.strip()]
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else None
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash-lite"
GEMINI_MODELS_PRIORITY = [
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-2.0-pro-exp-02-05",
    "gemini-1.5-pro"
]

COUNTRY_THEATRES = {
    'عربستان': [
        'عربستان', 'السعودية', 'السعوديه', 'سعودی', 'سعودیه', 'سعودیة', 'ریاض', 'الرياض', 
        'دمام', 'الدمام', 'جده', 'جدة', 'ینبع', 'جازان', 'جیزان', 'بقیق', 'خریص', 
        'راس تنوره', 'رأس تنورة', 'شدقم', 'نجران', 'ابها', 'أبها', 'تبوک', 'ظهران', 
        'الظهران', 'خمیس مشیط', 'خميس مشيط', 'آرامکو', 'ارامکو', 'ارامكو', 'آرامكو', 
        'فرودگاه ملک خالد', 'قاعده ملک خالد', 'فرودگاه ریاض'
    ],
    'اسرائیل': [
        'اسرائیل', 'إسرائيل', 'اسرائيل', 'صهیونیست', 'الصهيوني', 'تل آویو', 'تل ابیب', 'تل أبيب', 'حیفا', 'حيفا', 
        'قدس', 'القدس', 'اورشلیم', 'أورشليم', 'ایلات', 'إيلات', 'صفد', 'طبریا', 'طبريا', 
        'عکا', 'عكا', 'دیمونا', 'ديمونا', 'بئرشبع', 'بئر السبع', 'عسقلان', 'اسدود', 'أشدود', 
        'کریات شمونه', 'كريات شمونة', 'رمات گان', 'رمات غان', 'نتانیا', 'نتانيا', 'هرتزلیا', 'هرتسليا', 
        'غلاف غزه', 'غلاف غزة', 'نواتیم', 'نفاطيم', 'نواتيم', 'هاتزریم', 'رامات دیوید', 'بن گوریون', 'بن غوريون'
    ],
    'لبنان': [
        'لبنان', 'بیروت', 'بيروت', 'ضاحیه', 'الضاحية', 'ضاحية', 'بقاع', 'البقاع', 
        'صور', 'صیدا', 'صيدا', 'بعلبک', 'بعلبك', 'نبطیه', 'النبطية', 'نبطية', 
        'الناقوره', 'الناقورة', 'خیام', 'الخيام', 'مرجعیون', 'حزب الله', 'حزب‌الله'
    ],
    'یمن': [
        'یمن', 'اليمن', 'صنعا', 'صنعاء', 'عدن', 'حدیده', 'الحديدة', 'حديدة', 
        'صعده', 'صعدة', 'مارب', 'مأرب', 'تعز', 'حجه', 'حجة', 'شبوه', 'شبوة', 
        'ابین', 'أبين', 'انصارالله', 'انصار الله', 'حوثی', 'الحوثي', 'الحوثيين'
    ],
    'سوریه': [
        'سوریه', 'سوريا', 'دمشق', 'حلب', 'حمص', 'حماه', 'حماة', 'لاذقیه', 'اللاذقية', 
        'طرطوس', 'دیرالزور', 'دير الزور', 'بوکمال', 'البوكمال', 'تدمر', 'قنیطره', 'القنيطرة', 
        'سویداء', 'السويداء', 'جولان', 'الجولان', 'التنف'
    ],
    'عراق': [
        'عراق', 'العراق', 'بغداد', 'اربیل', 'أربيل', 'سلیمانیه', 'السليمانية', 
        'دهوک', 'بصره', 'البصرة', 'نجف', 'النجف', 'کربلا', 'كربلاء', 'سامرا', 'سامراء', 
        'کرکوک', 'كركوك', 'موصل', 'الموصل', 'الانبار', 'الأنبار', 'عین الاسد', 'عين الأسد', 
        'التاجی', 'بلد', 'جرف الصخر', 'القائم', 'الحریر'
    ],
    'ایران': [
        'ایران', 'إيران', 'تهران', 'طهران', 'اصفهان', 'شیراز', 'تبریز', 'مشهد', 
        'اهواز', 'کرج', 'قم', 'کرمانشاه', 'ارومیه', 'رشت', 'زاهدان', 'همدان', 
        'کرمان', 'یزد', 'اردبیل', 'بندرعباس', 'اراک', 'زنجان', 'سنندج', 'قزوین', 
        'خرم‌آباد', 'گرگان', 'ساری', 'بوشهر', 'بیرجند', 'ایلام', 'شهرکرد', 'یاسوج', 
        'سمنان', 'کیش', 'قشم', 'چابهار', 'نطنز', 'فردو', 'پارس جنوبی', 'عسلویه', 
        'خارگ', 'خارک', 'سپاه', 'الحرس الثوري', 'ارتش', 'تنگه هرمز', 'خلیج فارس'
    ],
    'امارات': [
        'امارات', 'الإمارات', 'دبی', 'دبي', 'ابوظبی', 'أبوظبي', 'شارجه', 'الشارقة', 
        'فجیره', 'الفجيرة', 'جبل علی', 'الرویس', 'عجمان', 'راس الخیمه', 'رأس الخيمة'
    ],
    'اوکراین': [
        'اوکراین', 'أوكرانيا', 'کی‌یف', 'کیف', 'كييف', 'خارکیف', 'خاركيف', 'اودسا', 'أوديسا', 
        'زاپروژیا', 'دنیپرو', 'لویو', 'کریمه', 'دونباس'
    ],
    'روسیه': [
        'روسیه', 'روسيا', 'مسکو', 'موسكو', 'کرملین', 'کورسک', 'بلگورود', 'روستوف'
    ]
}

class LiveSentinel:
    def __init__(self, bot, client=None):
        self.bot = bot
        self.client = client
        self.config = self.load_json(CONFIG_FILE)
        self.nodes = self.config.get('nodes', [])
        self.state_file = STATE_FILE
        self.gemini_api_keys = GEMINI_API_KEYS
        self.current_key_idx = 0
        self.latest_flash_lite_model = DEFAULT_GEMINI_MODEL
        self.current_gemini_model = DEFAULT_GEMINI_MODEL
        
        # Load severities
        self.incident_severities = self.config.get('patterns', {}).get('incident_severities', {})
        
        self.baselines = self.fetch_remote_baselines()
        self.lock = asyncio.Lock()
        
        # Buffer for messages in the last 3 minutes
        self.recent_messages = deque()
        self.last_alert_time = {} # pat -> timestamp
        self.alerted_msg_patterns = {} # "node_msgid" -> set of patterns
        self.vip_alert_history = {} # "node_msgid" -> dict(time, pattern, text, sent_msg_id, target_channel)
        self.recent_alert_sources = deque() # (timestamp, set_of_sources)
        self.vahid_ai_posted = {} # msg_id -> posted_msg_id in @VahidOnlineAI
        self.vahid_grouped_posted = set() # set of grouped_id to prevent multi-media album duplicates
        self.vahid_msg_locks = {} # msg_id -> asyncio.Lock() for sequential edit ordering
        self.vahid_needs_refresh = set() # set of (vahid_id, posted_id) needing AI re-summary
        self.ilia_ai_posted = {} # msg_id -> posted_msg_id in @iliaenAI
        self.ilia_grouped_posted = set() # set of grouped_id to prevent multi-media album duplicates
        self.ilia_msg_locks = {} # msg_id -> asyncio.Lock() for sequential edit ordering
        self.ilia_needs_refresh = set() # set of (ilia_id, posted_id) needing AI re-summary
        self.alibk_posted = {} # msg_id -> {'channel': target_channel, 'msg_id': posted_msg_id}
        self.alibk_grouped_posted = set() # set of grouped_id to prevent multi-media album duplicates
        self.alibk_msg_locks = {} # msg_id -> asyncio.Lock() for sequential edit ordering
        self.withyashar_msg_locks = {} # msg_id -> asyncio.Lock() for sequential edit ordering
        self.vip_grouped_posted = set() # set of grouped_id to prevent multi-media album duplicates
        self.node_id_map = {} # chat_id -> clean node username
        self.channel_last_alert_id = {} # channel_username -> last_alert_msg_id
        self.channel_watermarks = {} # canonical_node -> highest processed msg_id
        self.recent_posted_map = {} # (target_channel, canonical_node, source_msg_id) -> posted_msg_id
        self.vahid_post_time = {} # msg_id -> timestamp
        self.ilia_post_time = {} # msg_id -> timestamp
        
        # Unified Thread and Supplementary Updates Tracking (72-hour window)
        self.thread_alerts = {} # "node_rootid" -> dict(channel, alert_msg_id, node, root_id, root_text, root_media_type, timestamp, title, summary, is_iran_related, replies)
        self.reply_to_root_map = {} # "node_replyid" -> root_id
        self.debounce_tasks = {} # "node_rootid" -> asyncio.Task
        self.thread_locks = {} # "node_rootid" -> asyncio.Lock
        
        # Metrics
        self.start_time = time.time()
        self.total_msgs_processed = 0
        self.last_msg_text = "No messages yet"
        self.last_msg_time = "N/A"
        
        # Comprehensive list of foreign & regional countries, cities, and strategic targets (silent alert)
        self.foreign_keywords = [
            # Countries & territories
            'اسرائیل', 'لبنان', 'غزه', 'فلسطین', 'سوریه', 'عراق', 'یمن', 'عربستان', 
            'کویت', 'قطر', 'امارات', 'عمان', 'بحرین', 'ترکیه', 'پاکستان', 'افغانستان', 
            'آمریکا', 'اردن', 'مصر', 'آذربایجان', 'ارمنستان', 'اوکراین', 'روسیه',
            # Saudi Arabia
            'ریاض', 'جده', 'دمام', 'ینبع', 'جازان', 'بقیق', 'خریص', 'راس تنوره', 'شدقم', 
            'نجران', 'ابها', 'تبوک', 'ظهران', 'خمیس مشیط',
            # Israel / Palestine
            'تل آویو', 'حیفا', 'قدس', 'اورشلیم', 'ایلات', 'صفد', 'طبریا', 'عکا', 'دیمونا', 
            'بئرشبع', 'عسقلان', 'اسدود', 'کریات شمونه', 'رمات گان', 'نتانیا', 'هرتزلیا', 
            'غلاف غزه', 'خان یونس', 'رفح', 'جنین', 'نابلس', 'طولکرم', 'رام الله',
            # Lebanon
            'بیروت', 'ضاحیه', 'بقاع', 'صور', 'صیدا', 'بعلبک', 'نبطیه', 'الناقوره', 'خیام', 'مرجعیون',
            # Syria
            'دمشق', 'حلب', 'حمص', 'حماه', 'لاذقیه', 'طرطوس', 'دیرالزور', 'بوکمال', 'تدمر', 'قنیطره', 'سویداء',
            # Iraq
            'بغداد', 'اربیل', 'سلیمانیه', 'دهوک', 'بصره', 'نجف', 'کربلا', 'سامرا', 'کرکوک', 
            'موصل', 'الانبار', 'عین الاسد', 'التاجی', 'بلد', 'جرف الصخر', 'القائم',
            # Yemen
            'صنعا', 'عدن', 'حدیده', 'صعده', 'مارب', 'تعز', 'حجه', 'شبوه', 'ابین',
            # Gulf & Emirates
            'دبی', 'ابوظبی', 'شارجه', 'فجیره', 'جبل علی', 'الرویس', 'عجمان', 'راس الخیمه',
            'دوحه', 'راس لفان', 'منامه', 'مسقط', 'صلاله', 'دقم',
            # Strategic & military facilities
            'آرامکو', 'بن گوریون', 'فرودگاه بن گوریون', 'فرودگاه بیروت', 'فرودگاه ریاض', 
            'فرودگاه دمشق', 'فرودگاه بغداد', 'پایگاه نواتیم', 'پایگاه هاتزریم', 'پایگاه رامات دیوید',
            'پایگاه الحریر', 'پایگاه التنف', 'نیروگاه براکه', 'رآکتور دیمونا'
        ]
        
        # Iran-specific keywords (loud siren alert)
        self.iran_keywords = [
            "ایران", "إيران", "طهران", "تهران", "اصفهان", "شیراز", "تبریز", "مشهد", "اهواز", "کرج", "قم", 
            "کرمانشاه", "ارومیه", "رشت", "زاهدان", "همدان", "کرمان", "یزد", "اردبیل", 
            "بندرعباس", "اراک", "زنجان", "سنندج", "قزوین", "خرم‌آباد", "گرگان", "ساری", 
            "بوشهر", "بیرجند", "ایلام", "شهرکرد", "یاسوج", "سمنان", "کیش", "قشم", "چابهار", 
            "نطنز", "فردو", "پارس جنوبی", "عسلویه", "خارگ", "خارک", "لاوان", "سیری", 
            "تنگه هرمز", "خلیج فارس", "دریای عمان",
            "سپاه", "الحرس الثوري", "الحرس_الثوري", "ارتش ایران", "الجيش الإيراني", 
            "پدافند هوایی ایران", "نیروهای مسلح ایران", "خامنه‌ای", "خامنائي", "پزشکیان", "بزشكيان", 
            "قاسم سلیمانی", "قاسم سليماني", "فیلق القدس", "فيلق القدس", "نیروی قدس",
            "إيراني", "ایرانی", "إيرانية", "ایرانیة", "ایرانیه"
        ]
        
        # Load persistent state from disk
        self.load_persisted_state()
        
        # Persistent SQLite Database for posted source messages
        self.db_file = POSTED_DB_FILE
        self.init_posted_db()
        
    def get_gemini_api_key(self):
        if not self.gemini_api_keys:
            return None
        return self.gemini_api_keys[self.current_key_idx % len(self.gemini_api_keys)]

    def rotate_gemini_api_key(self):
        if len(self.gemini_api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.gemini_api_keys)
            print(f"🔄 Rotated to backup Gemini API Key #{self.current_key_idx + 1}/{len(self.gemini_api_keys)}")
            return self.get_gemini_api_key()
        return None

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

    def init_posted_db(self):
        try:
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS posted_sources (
                    target_channel TEXT,
                    source_node TEXT,
                    source_msg_id INTEGER,
                    posted_msg_id INTEGER,
                    created_at REAL,
                    PRIMARY KEY (target_channel, source_node, source_msg_id)
                )
            """)
            con.commit()
            
            cur.execute("SELECT target_channel, source_node, source_msg_id, posted_msg_id FROM posted_sources")
            rows = cur.fetchall()
            for ch, node, src_id, dst_id in rows:
                canon = node or 'unknown'
                self.recent_posted_map[(ch, canon, src_id)] = dst_id
                self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_id)
                if (node and node.lower() == 'vahidonline') or ch == '@VahidOnlineAI':
                    self.vahid_ai_posted[src_id] = dst_id
                elif (node and node.lower() == 'iliaen') or ch == '@iliaenAI':
                    self.ilia_ai_posted[src_id] = dst_id
            con.close()
            print(f"🗄️ Initialized SQLite registry: loaded {len(rows)} posted records into in-memory fast map.")
        except Exception as e:
            print(f"⚠️ Error initializing SQLite DB: {e}")

    def is_source_posted(self, target_channel, source_node, source_msg_id):
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            canon = source_node or 'unknown'
            # 1. Ultra-fast in-memory lookup (O(1))
            if (target_channel, canon, src_int) in self.recent_posted_map:
                return True
            if target_channel == '@VahidOnlineAI' and src_int in self.vahid_ai_posted:
                return True
            if target_channel == '@iliaenAI' and src_int in self.ilia_ai_posted:
                return True
                
            # 2. Durable SQLite fallback
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute(
                "SELECT posted_msg_id FROM posted_sources WHERE target_channel = ? AND source_node = ? AND source_msg_id = ?",
                (target_channel, source_node, src_int)
            )
            row = cur.fetchone()
            con.close()
            if row:
                self.recent_posted_map[(target_channel, canon, src_int)] = row[0]
                if target_channel == '@VahidOnlineAI':
                    self.vahid_ai_posted[src_int] = row[0]
                elif target_channel == '@iliaenAI':
                    self.ilia_ai_posted[src_int] = row[0]
                return True
        except Exception:
            pass
        return False

    def record_posted_message(self, target_channel, source_node, source_msg_id, posted_msg_id):
        try:
            src_int = int(source_msg_id) if str(source_msg_id).isdigit() else source_msg_id
            dst_int = int(posted_msg_id) if str(posted_msg_id).isdigit() else posted_msg_id
            canon = source_node or 'unknown'
            
            # 1. Update fast in-memory map & watermark (bounded to 1000 items)
            self.recent_posted_map[(target_channel, canon, src_int)] = dst_int
            if len(self.recent_posted_map) > 1000:
                oldest_keys = list(self.recent_posted_map.keys())[:200]
                for ok in oldest_keys:
                    self.recent_posted_map.pop(ok, None)
                    
            self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_int)
            
            if target_channel == '@VahidOnlineAI':
                self.vahid_ai_posted[src_int] = dst_int
            elif target_channel == '@iliaenAI':
                self.ilia_ai_posted[src_int] = dst_int
                
            con = sqlite3.connect(self.db_file)
            cur = con.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO posted_sources (target_channel, source_node, source_msg_id, posted_msg_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (target_channel, source_node, src_int, dst_int, time.time()))
            con.commit()
            con.close()
        except Exception as e:
            print(f"⚠️ Error recording to SQLite DB: {e}")

    def determine_iran_related(self, ai_res, text, title=""):
        clean_text = (text or "") + " " + (title or "")
        
        # 1. Primary Authority: AI Semantic Classifier
        if ai_res and ai_res.get('is_iran_related') is not None:
            return bool(ai_res['is_iran_related'])
            
        # 2. Strategic escalation & military preparation indicators concerning Iran conflict
        iran_conflict_indicators = [
            'حمله به ایران', 'پاسخ به ایران', 'تنش با ایران', 'حمله احتمالی', 
            'سوخت‌رسان', 'سوخت رسان', 'بمب‌افکن', 'بمب افکن', 'سنتکام', 
            'ناو هواپیمابر', 'تنگه هرمز', 'خلیج فارس', 'سپاه', 'نطنز', 'فردو'
        ]
        if any(ind in clean_text for ind in iran_conflict_indicators):
            return True
            
        has_foreign_kw = any(fk in clean_text for fk in self.foreign_keywords)
        has_iran_kw = any(ik in clean_text for ik in self.iran_keywords)
        
        # 3. Clear domestic Iran news -> loud alert
        if has_iran_kw:
            return True
            
        # 4. Pure regional/foreign news without Iran context -> silent alert
        if has_foreign_kw:
            return False
            
        return False
        
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
                # 1. Alerted message patterns: key -> set
                raw_patterns = data.get('alerted_msg_patterns', {})
                for k, v in raw_patterns.items():
                    if isinstance(v, list):
                        self.alerted_msg_patterns[k] = set(v)
                
                # 2. VIP Alert History (keep entries from last 72h)
                raw_vip = data.get('vip_alert_history', {})
                for k, v in raw_vip.items():
                    if isinstance(v, dict) and (now - v.get('time', 0)) <= 259200:
                        self.vip_alert_history[k] = v
                
                # 3. Last alert time (keep entries from last 24h)
                raw_last = data.get('last_alert_time', {})
                for k, v in raw_last.items():
                    if isinstance(v, (int, float)) and (now - v) <= 86400:
                        self.last_alert_time[k] = v
                
                # 4. Alibk Alert History (keep entries from last 72h)
                raw_alibk = data.get('alibk_posted', {})
                for k, v in raw_alibk.items():
                    if isinstance(v, dict):
                        try:
                            self.alibk_posted[int(k)] = v
                        except:
                            self.alibk_posted[k] = v

                # 5. Thread Alerts (keep entries from last 72h = 259200s)
                raw_threads = data.get('thread_alerts', {})
                for k, v in raw_threads.items():
                    if isinstance(v, dict) and (now - v.get('timestamp', 0)) <= 259200:
                        self.thread_alerts[k] = v

                # 6. Reply to root map
                raw_reply_map = data.get('reply_to_root_map', {})
                for k, v in raw_reply_map.items():
                    if isinstance(v, (int, str)):
                        self.reply_to_root_map[k] = int(v) if str(v).isdigit() else v

                # 7. Channel last alert ID
                self.channel_last_alert_id = data.get('channel_last_alert_id', {})

                # 8. Post timestamps for Vahid & Ilia
                raw_vahid_time = data.get('vahid_post_time', {})
                for k, v in raw_vahid_time.items():
                    if isinstance(v, (int, float)) and (now - v) <= 259200:
                        self.vahid_post_time[int(k) if str(k).isdigit() else k] = v

                raw_ilia_time = data.get('ilia_post_time', {})
                for k, v in raw_ilia_time.items():
                    if isinstance(v, (int, float)) and (now - v) <= 259200:
                        self.ilia_post_time[int(k) if str(k).isdigit() else k] = v

                # 9. Vahid & Ilia AI posted mapping
                raw_vahid_ai = data.get('vahid_ai_posted', {})
                for k, v in raw_vahid_ai.items():
                    try:
                        self.vahid_ai_posted[int(k)] = v
                    except Exception:
                        self.vahid_ai_posted[k] = v

                raw_ilia_ai = data.get('ilia_ai_posted', {})
                for k, v in raw_ilia_ai.items():
                    try:
                        self.ilia_ai_posted[int(k)] = v
                    except Exception:
                        self.ilia_ai_posted[k] = v
                
                # 10. Channel high-water marks
                raw_watermarks = data.get('channel_watermarks', {})
                for k, v in raw_watermarks.items():
                    if isinstance(v, (int, float)):
                        self.channel_watermarks[k] = int(v)
                
                print(f"📦 Loaded persistent state: {len(self.alerted_msg_patterns)} alerted msg keys, {len(self.vip_alert_history)} VIP alerts, {len(self.alibk_posted)} Alibk alerts, {len(self.vahid_ai_posted)} Vahid AI, {len(self.ilia_ai_posted)} Ilia AI, {len(self.channel_watermarks)} watermarks.")
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
            clean_vahid_ai = {str(k): v for k, v in self.vahid_ai_posted.items()}
            clean_ilia_ai = {str(k): v for k, v in self.ilia_ai_posted.items()}
            serializable_patterns = {k: list(v) for k, v in self.alerted_msg_patterns.items()}
            
            data = {
                "alerted_msg_patterns": serializable_patterns,
                "vip_alert_history": clean_vip,
                "last_alert_time": clean_last,
                "alibk_posted": self.alibk_posted,
                "thread_alerts": clean_threads,
                "reply_to_root_map": clean_reply_map,
                "channel_last_alert_id": self.channel_last_alert_id,
                "channel_watermarks": self.channel_watermarks,
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

    @staticmethod
    def to_fa_num(n):
        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        return "".join(fa_digits[int(d)] if d.isdigit() else d for d in str(n))

    @staticmethod
    def get_tehran_time_fa(ts=None):
        tz = timezone(timedelta(hours=3, minutes=30))
        dt = datetime.fromtimestamp(ts if ts else time.time(), tz=tz)
        fa_digits = "۰۱۲۳۴۵۶۷۸۹"
        time_str = dt.strftime("%H:%M")
        return "".join(fa_digits[int(d)] if d.isdigit() else d for d in time_str)

    def detect_msg_media_type(self, msg):
        if not msg:
            return 'text'
        if getattr(msg, 'video', None) or getattr(msg, 'video_note', None):
            return 'video'
        if getattr(msg, 'photo', None):
            return 'photo'
        if getattr(msg, 'voice', None) or getattr(msg, 'audio', None):
            return 'voice'
        if getattr(msg, 'document', None):
            mime = getattr(msg.file, 'mime_type', '') if getattr(msg, 'file', None) else ''
            if 'video' in mime: return 'video'
            if 'image' in mime: return 'photo'
            if 'audio' in mime: return 'voice'
            return 'file'
        return 'text'

    def get_media_icon_from_type(self, media_type):
        m = (media_type or 'text').lower()
        if m == 'video': return "📹"
        if m == 'photo': return "📸"
        if m == 'voice': return "🎙"
        if m == 'file': return "📄"
        return "📝"

    async def find_root_message(self, node, raw_msg, max_depth=5):
        if not raw_msg:
            return None, []
            
        reply_to_id = getattr(raw_msg, 'reply_to_msg_id', None)
        if not reply_to_id and getattr(raw_msg, 'reply_to', None):
            reply_to_id = getattr(raw_msg.reply_to, 'reply_to_msg_id', None)
            
        if not reply_to_id:
            return raw_msg.id, [raw_msg.id]
            
        cache_key = f"{node}_{reply_to_id}"
        if cache_key in self.reply_to_root_map:
            return self.reply_to_root_map[cache_key], [raw_msg.id]

        thread_key = f"{node}_{reply_to_id}"
        if thread_key in self.thread_alerts or (node == 'Alibk3' and reply_to_id in self.alibk_posted):
            return reply_to_id, [reply_to_id, raw_msg.id]

        curr_id = reply_to_id
        chain = [raw_msg.id]
        depth = 0
        while curr_id and depth < max_depth:
            chain.insert(0, curr_id)
            ck = f"{node}_{curr_id}"
            if ck in self.reply_to_root_map:
                return self.reply_to_root_map[ck], chain
            if ck in self.thread_alerts or (node == 'Alibk3' and curr_id in self.alibk_posted):
                return curr_id, chain
                
            if not self.client:
                break
                
            try:
                parent_msgs = await self.client.get_messages(node, ids=[curr_id])
                if not parent_msgs or not parent_msgs[0]:
                    break
                parent = parent_msgs[0]
                next_reply_id = getattr(parent, 'reply_to_msg_id', None)
                if not next_reply_id:
                    return curr_id, chain
                curr_id = next_reply_id
                depth += 1
            except Exception as e:
                print(f"⚠️ Error tracing parent for {node}_{curr_id}: {e}")
                break
                
        return curr_id, chain

    def build_alert_post_content(self, thread_data):
        """
        Constructs the unified Telegram alert post text for thread_data:
        - Appropriate alert icon & title prefix (🚨 / 🔕 / 📈 / ⚖️ / 🛑 / 📢)
        - Header media icon (📹, 📸, 🎙)
        - Clean Persian summary under 📝 **چکیده رویداد:**
        - Clean sources block under 🔗 **منبع خبر:** or 🔗 **منابع خبر:**
          Formatted cleanly as - [Channel](link) [media_icon]
          If multiple updates from the same channel: (خبر اصلی), (تکمیلی ۱), etc.
        - Proper tag and channel signature.
        """
        category = thread_data.get('category', 'WAR')
        is_iran_related = thread_data.get('is_iran_related', False)
        title = thread_data.get('title', '').strip()
        summary = thread_data.get('summary', '').strip()
        target_channel = thread_data.get('channel', '@DidebanJang')
        tag = thread_data.get('tag', '#دیده‌بان_جنگ')
        
        # Collect sources list and strictly filter out internal artifacts
        raw_sources = list(thread_data.get('sources', []))
        if not raw_sources:
            r_node = thread_data.get('node', 'Alibk3')
            r_id = thread_data.get('root_id')
            if r_id:
                raw_sources.append({
                    'node': r_node,
                    'msg_id': r_id,
                    'media_type': thread_data.get('root_media_type', 'text'),
                    'link': f"https://t.me/{r_node}/{r_id}",
                    'timestamp': thread_data.get('timestamp', time.time())
                })
            for r in thread_data.get('replies', []):
                raw_sources.append({
                    'node': r_node,
                    'msg_id': r.get('msg_id'),
                    'media_type': r.get('media_type', 'text'),
                    'link': f"https://t.me/{r_node}/{r.get('msg_id')}",
                    'timestamp': r.get('timestamp', time.time())
                })

        sources = [
            s for s in raw_sources 
            if s.get('node') and str(s.get('node')).lower() not in ('radar', 'unknown', 'sentinel')
            and 't.me/radar/' not in str(s.get('link', '')).lower()
        ]

        # Header media icon
        all_media_types = [s.get('media_type', 'text') for s in sources]
        header_media_icon = ""
        if 'video' in all_media_types:
            header_media_icon = " 📹"
        elif 'photo' in all_media_types:
            header_media_icon = " 📸"
        elif 'voice' in all_media_types:
            header_media_icon = " 🎙"

        # Determine header
        if category == 'ECONOMY':
            alert_header = f"📈 **گزارش اقتصادی: {title}**{header_media_icon}"
        elif category == 'PROTEST_RIGHTS':
            if "اعدام" in title or "حقوق بشری" in title:
                alert_header = f"⚖️ **گزارش حقوق بشری: {title}**{header_media_icon}"
            elif "اعتصاب" in title:
                alert_header = f"🛑 **گزارش مردمی: {title}**{header_media_icon}"
            else:
                alert_header = f"📢 **گزارش مردمی: {title}**{header_media_icon}"
        else:
            if is_iran_related:
                alert_header = f"🚨 **هشدار فوری: {title}**{header_media_icon}"
            else:
                alert_header = f"🔕 **هشدار منطقه‌ای: {title}**{header_media_icon}"

        # Format sources
        node_counts = {}
        for s in sources:
            n = s.get('node', '')
            node_counts[n] = node_counts.get(n, 0) + 1

        node_seen = {}
        sources_lines = []
        for s in sources:
            n = s.get('node', '')
            link = s.get('link') or f"https://t.me/{n}/{s.get('msg_id')}"
            m_icon = self.get_media_icon_from_type(s.get('media_type'))
            if node_counts.get(n, 0) > 1:
                idx = node_seen.get(n, 0) + 1
                node_seen[n] = idx
                if idx == 1:
                    line = f"- [{n}]({link}) (خبر اصلی) {m_icon}".strip()
                else:
                    line = f"- [{n}]({link}) (تکمیلی {self.to_fa_num(idx - 1)}) {m_icon}".strip()
            else:
                line = f"- [{n}]({link}) {m_icon}".strip()
            sources_lines.append(line)

        source_header = "🔗 **منبع خبر:**" if len(sources) <= 1 else "🔗 **منابع خبر:**"
        sources_block = f"{source_header}\n" + "\n".join(sources_lines)

        parts = [
            alert_header,
            "",
            f"📝 **چکیده رویداد:**\n{summary}",
            "",
            sources_block,
        ]

        update_time = thread_data.get('updated_at')
        if update_time:
            time_fa = self.get_tehran_time_fa(update_time)
            reason = thread_data.get('update_reason', 'افزودن منابع میدانی')
            parts.extend([
                "",
                f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | {reason}**"
            ])

        parts.extend([
            "",
            f"{tag}\n{target_channel}"
        ])
        return "\n".join(parts)

    @staticmethod
    def normalize_text_for_anchors(text):
        if not text:
            return ""
        t = str(text).lower()
        t = re.sub(r'[\u064B-\u065F\u0670]', '', t)
        t = t.replace('ي', 'ی').replace('ك', 'ک').replace('ة', 'ه')
        t = t.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا').replace('ٱ', 'ا')
        t = t.replace('ؤ', 'و').replace('ئ', 'ی')
        t = t.replace('\u200c', ' ')
        return re.sub(r'\s+', ' ', t).strip()

    def extract_message_anchors(self, text, title=""):
        """
        Extracts geographic locations, strategic targets, and event keywords
        from text and title for zero-cost Layer 1 candidate filtering.
        Supports both Persian and Arabic scripts, normalizes variants,
        and maps strategic cities/targets to their country theatre.
        """
        norm_combined = self.normalize_text_for_anchors(f"{title} {text}")
        anchors = set()
        
        # 1. Geographic & regional locations and Country Theatres
        for country, aliases in COUNTRY_THEATRES.items():
            norm_country = self.normalize_text_for_anchors(country)
            for alias in aliases:
                norm_alias = self.normalize_text_for_anchors(alias)
                if norm_alias in norm_combined:
                    anchors.add(norm_alias)
                    anchors.add(norm_country)
                    
        for kw in self.foreign_keywords:
            norm_kw = self.normalize_text_for_anchors(kw)
            if norm_kw in norm_combined:
                anchors.add(norm_kw)
                
        for kw in self.iran_keywords:
            norm_kw = self.normalize_text_for_anchors(kw)
            if norm_kw in norm_combined:
                anchors.add(norm_kw)
                
        # 2. Strategic targets & critical facilities
        facilities = [
            'فرودگاه', 'پایگاه', 'پالایشگاه', 'نیروگاه', 'بندر', 'اسکله', 'تنگه هرمز',
            'باب المندب', 'دریای سرخ', 'کانال سوئز', 'آرامکو', 'دیمونا', 'نواتیم',
            'عین الاسد', 'التاجی', 'التنف', 'هاتزریم', 'رامات دیوید', 'کرملین', 'پنتاگون'
        ]
        for fac in facilities:
            norm_fac = self.normalize_text_for_anchors(fac)
            if norm_fac in norm_combined:
                anchors.add(norm_fac)
                
        # 3. Incident types & critical domains
        actions = [
            'انفجار', 'موشک', 'پهپاد', 'بمباران', 'حمله', 'تیراندازی', 'پدافند',
            'آژیر', 'آتش سوزی', 'آتش‌سوزی', 'سقوط', 'ترور', 'تعطیلی', 'لغو پرواز',
            'توقیف', 'نفتکش', 'اعدام', 'اعتصاب', 'اعتراض', 'بازداشت', 'دادگاه', 'حکم', 'فساد', 'اختلاس', 'دلار', 'تتر', 'طلا', 'سکه', 'بورس'
        ]
        for act in actions:
            norm_act = self.normalize_text_for_anchors(act)
            if norm_act in norm_combined:
                anchors.add(norm_act)
                
        return anchors

    async def evaluate_semantic_relation(self, existing_title, existing_summary, new_text, is_same_source=False, has_country_theatre=False):
        """
        Layer 2: Calls Gemini to evaluate whether new_text refers to SAME_EVENT or a coordinated wave
        as the existing alert, or is a NEW_EVENT (such as a separate incident in a different theatre).
        If SAME_EVENT, synthesizes updated title & summary combining all targets and new facts.
        """
        if not GEMINI_API_KEY:
            return None
            
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = DEFAULT_GEMINI_MODEL
            
        same_source_hint = "نکته بسیار مهم: این گزارش جدید از همان کانال منبع منتشرکننده هشدار قبلی ارسال شده است.\n" if is_same_source else ""
        theatre_hint = "نکته بسیار مهم: هر دو رویداد در یک کشور یا جبهه نبرد مشترک رخ داده‌اند.\n" if has_country_theatre else ""
        
        prompt = (
            "تو یک تحلیلگر ارشد خبر و ناظر هوشمند دیده‌بان هستی.\n"
            "یک هشدار خبری فعال در کانال منتشر شده است و اکنون یک خبر/گزارش جدید دریافت شده است.\n\n"
            f"📌 [هشدار فعال فعلی در کانال]:\n"
            f"عنوان: {existing_title}\n"
            f"چکیده: {existing_summary}\n\n"
            f"📥 [خبر جدید دریافت شده]:\n"
            f"{new_text}\n\n"
            f"{same_source_hint}{theatre_hint}"
            "وظیفه تو این است که مشخص کنی آیا این خبر جدید باید با هشدار فعلی «ادغام» (SAME_EVENT) شود یا یک «رویداد کاملاً مستقل و بی‌ارتباط» (NEW_EVENT) است:\n\n"
            "قوانین بسیار مهم ادغام هوشمند (SAME_EVENT):\n"
            "۱. موج حملات هماهنگ و اهداف همزمان (Coordinated Wave / Multi-Target Attack): اگر خبر جدید درباره حملات موشکی، پهپادی یا انفجار در همان کشور/جبهه جنگ است (مثلاً حمله به فرودگاه ریاض و همزمان یا با فاصله کم اصابت یا دود در الدمام، یا حملات همزمان به تل‌آویو و حیفا، یا چند نقطه در بیروت/لبنان)، این یک موج عملیاتی واحد است و حتماً باید SAME_EVENT باشد!\n"
            "۲. پیگیری، جزییات تکمیلی و بازتاب: اگر خبر جدید ابعاد جدید، تاییدیه، خسارات، لغو پروازها، بیانیه‌های طرفین، یا منبع دیگری از همان تحولات است، حتماً SAME_EVENT است.\n"
            "۳. گزارش از همان منبع (Same-Source): اگر خبر جدید از همان کانال مبدا است و سیر تحولات یک ساعت اخیر را پوشش می‌دهد، نباید دو پست هشدار جداگانه ایجاد شود و باید ادغام شود.\n\n"
            "تنها در صورتی NEW_EVENT است که:\n"
            "- خبر جدید در یک کشور یا جبهه کاملاً بی‌ارتباط رخ داده باشد (مثلاً هشدار قبلی درباره عربستان بوده و این خبر درباره اوکراین یا ایران است).\n"
            "- یا موضوع خبر کاملاً متفاوت باشد (مثلاً هشدار قبلی نظامی بوده و این خبر سقوط ارزش سهام یا یک رویداد ورزشی/پزشکی است).\n\n"
            "اگر SAME_EVENT است:\n"
            "- has_update: مقدار true\n"
            "- updated_title: یک عنوان جامع، رسا و خوش‌خوان فارسی همراه با فعل یا گزاره کامل که تمام ابعاد و اهداف رویداد را منعکس کند (مثلاً: «حملات موشکی همزمان به فرودگاه ریاض و شهر الدمام»).\n"
            "- updated_summary: چکیده تلفیقی منسجم و شیوا حاصل تجمیع اطلاعات قبلی و جدید با اولویت ایجاز و شفاف‌سازی ابعاد ماجرا.\n"
            "- is_iran_related: آیا مستقیماً به خاک، نیروها یا منافع نظامی مستقیم ایران مربوط است؟ (true/false)\n\n"
            "پاسخ را دقیقاً و فقط در قالب شیء JSON زیر بدون هیچ توضیح دیگری ارسال کن:\n"
            "{\n"
            '  "relation": "SAME_EVENT" | "NEW_EVENT",\n'
            '  "has_update": true,\n'
            '  "updated_title": "عنوان جامع و کامل فارسی",\n'
            '  "updated_summary": "چکیده تلفیقی کامل",\n'
            '  "is_iran_related": true | false\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 450,
                "temperature": 0.2
            }
        }
        
        raw_out = await self.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                return parsed
                
        return None

    async def check_and_merge_with_active_alert(self, text, node, msg_id, target_channel, candidate_title="", candidate_summary="", raw_msg=None, category='WAR', is_iran_related=False, candidate_sources=None):
        """
        Cross-channel semantic deduplication and source merging.
        Checks if candidate news matches an active alert in target_channel within 72 hours.
        If matched, appends the source to the existing alert and updates in-place.
        """
        if target_channel not in ('@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat'):
            return False
            
        candidate_anchors = self.extract_message_anchors(text, candidate_title)
        if not candidate_anchors:
            return False
            
        now = time.time()
        candidate_threads = []
        for tk, td in self.thread_alerts.items():
            if not isinstance(td, dict):
                continue
            if td.get('channel') != target_channel:
                continue
            if (now - td.get('timestamp', 0)) > 259200: # 72 hours
                continue
            # Already has this source?
            if any(s.get('node') == node and (s.get('msg_id') == msg_id or str(s.get('msg_id')) == str(msg_id)) for s in td.get('sources', [])):
                return True
            candidate_threads.append((tk, td))
            
        candidate_threads.sort(key=lambda item: item[1].get('timestamp', 0), reverse=True)
        
        for thread_key, thread_data in candidate_threads:
            thread_time = thread_data.get('timestamp', 0)
            time_diff = now - thread_time
            
            existing_text = f"{thread_data.get('title', '')} {thread_data.get('summary', '')} {thread_data.get('root_text', '')}"
            existing_anchors = self.extract_message_anchors(existing_text, thread_data.get('title', ''))
            
            overlap = candidate_anchors.intersection(existing_anchors)
            
            # Check 1: Same Source Proximity Rule
            # If the candidate news is from the EXACT SAME source channel within 45 minutes:
            # We MUST prioritize merging to prevent duplicate alerts from the same source.
            is_same_source = (
                thread_data.get('node') == node or 
                any(s.get('node') == node for s in thread_data.get('sources', []))
            )
            is_recent_same_source = is_same_source and (time_diff <= 2700) # 45 minutes
            
            # Check 2: Substantive Anchor or Theatre Overlap
            has_substantive = False
            for anchor in overlap:
                norm_a = self.normalize_text_for_anchors(anchor)
                if (anchor in COUNTRY_THEATRES or norm_a in [self.normalize_text_for_anchors(c) for c in COUNTRY_THEATRES] or
                    anchor in self.foreign_keywords or anchor in self.iran_keywords or 
                    anchor in ['فرودگاه', 'پایگاه', 'پالایشگاه', 'نیروگاه', 'بندر', 'اسکله', 'تنگه هرمز', 
                               'باب المندب', 'دریای سرخ', 'کانال سوئز', 'آرامکو', 'دیمونا', 'نواتیم', 
                               'عین الاسد', 'التاجی', 'التنف', 'دلار', 'تتر', 'بورس', 'سکه', 'طلا', 'اعدام', 'اعتصاب', 'بازداشت', 'دادگاه', 'حکم', 'فساد', 'اختلاس']):
                    has_substantive = True
                    break
                    
            if not has_substantive and not is_recent_same_source:
                continue
                
            has_country_theatre = any(
                c in overlap or self.normalize_text_for_anchors(c) in [self.normalize_text_for_anchors(o) for o in overlap]
                for c in COUNTRY_THEATRES
            )
                
            existing_title = thread_data.get('title', '') or candidate_title
            existing_summary = thread_data.get('summary', '') or thread_data.get('root_text', '')
            
            print(f"🧠 Checking semantic deduplication: new msg {node}_{msg_id} vs thread {thread_key} (anchors: {overlap}, same_source={is_same_source}, dt={int(time_diff)}s)...")
            ai_eval = await self.evaluate_semantic_relation(
                existing_title, 
                existing_summary, 
                text,
                is_same_source=is_same_source,
                has_country_theatre=has_country_theatre
            )
            if not ai_eval:
                continue
                
            relation = ai_eval.get('relation', 'NEW_EVENT')
            
            # Fallback guardrail: If from SAME source within 45 minutes AND in the same country theatre,
            # enforce SAME_EVENT even if LLM wavered, to honor the user's ironclad rule:
            # "از یک خبر واحد یا یک منبع در یک بازه زمانی نباید بیش از یک عدد هشدار داشته باشیم"
            if relation != 'SAME_EVENT' and is_recent_same_source and has_country_theatre:
                print(f"🛡️ Guardrail triggered: Forcing SAME_EVENT merge for same source {node} in theatre within {int(time_diff)}s.")
                relation = 'SAME_EVENT'
                ai_eval['has_update'] = True
                if not ai_eval.get('updated_title'):
                    clean_cand = re.sub(r'^(?:🚨|🔕)?\s*(?:هشدار فوری|هشدار منطقه‌ای):\s*', '', candidate_title).strip()
                    clean_exist = re.sub(r'^(?:🚨|🔕)?\s*(?:هشدار فوری|هشدار منطقه‌ای):\s*', '', existing_title).strip()
                    ai_eval['updated_title'] = f"{clean_exist} و {clean_cand}" if clean_cand else clean_exist
                if not ai_eval.get('updated_summary'):
                    ai_eval['updated_summary'] = f"{existing_summary}\nهمچنین گزارش‌ها حاکی از دریافت اطلاعات تکمیلی و پوشش تحولات جدید در این زمینه است."
            
            if relation != 'SAME_EVENT':
                print(f"ℹ️ News msg {node}_{msg_id} classified as {relation} relative to thread {thread_key}. Not merging.")
                continue
                
            # Perform merge under thread lock
            lock = self.thread_locks.setdefault(thread_key, asyncio.Lock())
            async with lock:
                media_type = self.detect_msg_media_type(raw_msg)
                sources = thread_data.setdefault('sources', [])
                if not sources:
                    r_node = thread_data.get('node', 'Alibk3')
                    r_id = thread_data.get('root_id')
                    if r_id:
                        sources.append({
                            'node': r_node,
                            'msg_id': r_id,
                            'media_type': thread_data.get('root_media_type', 'text'),
                            'link': f"https://t.me/{r_node}/{r_id}",
                            'timestamp': thread_data.get('timestamp', time.time())
                        })
                    for r in thread_data.get('replies', []):
                        sources.append({
                            'node': r_node,
                            'msg_id': r.get('msg_id'),
                            'media_type': r.get('media_type', 'text'),
                            'link': f"https://t.me/{r_node}/{r.get('msg_id')}",
                            'timestamp': r.get('timestamp', time.time())
                        })
                        
                if candidate_sources:
                    for cs in candidate_sources:
                        cs_node = cs.get('node')
                        cs_mid = cs.get('msg_id')
                        if cs_node and str(cs_node).lower() not in ('radar', 'unknown', 'sentinel') and 't.me/radar/' not in str(cs.get('link', '')).lower():
                            if not any(s.get('node') == cs_node and (s.get('msg_id') == cs_mid or str(s.get('msg_id')) == str(cs_mid)) for s in sources):
                                sources.append(cs)
                elif node and str(node).lower() not in ('radar', 'unknown', 'sentinel'):
                    if not any(s.get('node') == node and (s.get('msg_id') == msg_id or str(s.get('msg_id')) == str(msg_id)) for s in sources):
                        sources.append({
                            'node': node,
                            'msg_id': msg_id,
                            'media_type': media_type,
                            'link': f"https://t.me/{node}/{msg_id}",
                            'timestamp': time.time()
                        })

                # Sanitize sources: strictly prune any dummy/radar entries
                thread_data['sources'] = [
                    s for s in sources 
                    if s.get('node') and str(s.get('node')).lower() not in ('radar', 'unknown', 'sentinel')
                    and 't.me/radar/' not in str(s.get('link', '')).lower()
                ]
                    
                if ai_eval.get('has_update', False):
                    if ai_eval.get('updated_title'):
                        thread_data['title'] = ai_eval['updated_title'].strip()
                    if ai_eval.get('updated_summary'):
                        thread_data['summary'] = ai_eval['updated_summary'].strip()
                    if ai_eval.get('is_iran_related') is not None:
                        thread_data['is_iran_related'] = ai_eval['is_iran_related']
                elif not thread_data.get('title') and candidate_title:
                    thread_data['title'] = candidate_title
                if not thread_data.get('summary') and candidate_summary:
                    thread_data['summary'] = candidate_summary
                    
                alert_msg_id = thread_data.get('alert_msg_id')
                thread_data['updated_at'] = time.time()
                thread_data['update_reason'] = 'دریافت اطلاعات تکمیلی و پوشش حملات' if (has_country_theatre or ai_eval.get('has_update')) else 'افزودن منابع میدانی'
                updated_alert_text = self.build_alert_post_content(thread_data)
                
                try:
                    await self.bot.edit_message(target_channel, alert_msg_id, updated_alert_text, link_preview=False)
                    print(f"🔀 Successfully merged msg {node}_{msg_id} into alert {alert_msg_id} in {target_channel} ({len(sources)} sources). Status line updated in-place.")
                except Exception as edit_err:
                    if "not modified" in str(edit_err).lower():
                        print(f"ℹ️ Merged alert {alert_msg_id} in {target_channel} content unchanged.")
                    else:
                        print(f"❌ Error editing merged alert {alert_msg_id}: {edit_err}")
                        
                msg_key = f"{node}_{msg_id}"
                self.vip_alert_history[msg_key] = {
                    'sent_msg_id': alert_msg_id,
                    'target_channel': target_channel,
                    'time': time.time(),
                    'thread_key': thread_key
                }
                if node == 'Alibk3':
                    self.alibk_posted[msg_id] = {'channel': target_channel, 'msg_id': alert_msg_id, 'timestamp': time.time()}
                    
                self.save_persisted_state()
                return True
                
        return False

    def schedule_thread_debounce(self, thread_key):
        if thread_key not in self.debounce_tasks or self.debounce_tasks[thread_key].done():
            self.debounce_tasks[thread_key] = asyncio.create_task(self.flush_thread_debounce(thread_key, delay=300))
            print(f"⏳ Scheduled 5-minute debounce update for thread {thread_key}.")

    async def flush_thread_debounce(self, thread_key, delay=300):
        if delay > 0:
            await asyncio.sleep(delay)
            
        thread_data = self.thread_alerts.get(thread_key)
        if not thread_data:
            return
            
        lock = self.thread_locks.setdefault(thread_key, asyncio.Lock())
        async with lock:
            node = thread_data['node']
            root_id = thread_data['root_id']
            root_text = thread_data.get('root_text', '')
            replies = thread_data.get('replies', [])
            
            combined_sections = [f"[خبر اصلی]:\n{root_text}"]
            for idx, rep in enumerate(replies, 1):
                rep_text = rep.get('text', '').strip()
                if rep_text:
                    combined_sections.append(f"[گزارش تکمیلی {self.to_fa_num(idx)}]:\n{rep_text}")
                    
            full_text_for_ai = "\n\n".join(combined_sections)
            
            if node == 'Alibk3':
                ai_res = await self.classify_and_translate_alibk_thread(full_text_for_ai)
            else:
                ai_res = await self.summarize_thread_post(full_text_for_ai)
                
            if not ai_res:
                print(f"⚠️ Failed to re-summarize thread {thread_key}")
                return
                
            new_title = ai_res.get('title', '').strip() or thread_data.get('title', '')
            new_summary = ai_res.get('summary', '').strip() or thread_data.get('summary', '')
            is_iran_related = ai_res.get('is_iran_related', thread_data.get('is_iran_related', False))
            
            thread_data['title'] = new_title
            thread_data['summary'] = new_summary
            thread_data['is_iran_related'] = is_iran_related
            
            # Sync sources
            sources = thread_data.setdefault('sources', [])
            if not sources:
                sources.append({
                    'node': node,
                    'msg_id': root_id,
                    'media_type': thread_data.get('root_media_type', 'text'),
                    'link': f"https://t.me/{node}/{root_id}",
                    'timestamp': thread_data.get('timestamp', time.time())
                })
                for r in replies:
                    sources.append({
                        'node': node,
                        'msg_id': r.get('msg_id'),
                        'media_type': r.get('media_type', 'text'),
                        'link': f"https://t.me/{node}/{r.get('msg_id')}",
                        'timestamp': r.get('timestamp', time.time())
                    })
            else:
                for r in replies:
                    r_mid = r.get('msg_id')
                    existing_s = next((s for s in sources if s.get('node') == node and s.get('msg_id') == r_mid), None)
                    if existing_s:
                        existing_s['media_type'] = r.get('media_type', 'text')
                    else:
                        sources.append({
                            'node': node,
                            'msg_id': r_mid,
                            'media_type': r.get('media_type', 'text'),
                            'link': f"https://t.me/{node}/{r_mid}",
                            'timestamp': r.get('timestamp', time.time())
                        })
            
            target_channel = thread_data.get('channel', '@DidebanJang')
            alert_msg_id = thread_data.get('alert_msg_id')
            thread_data['updated_at'] = time.time()
            thread_data['update_reason'] = 'دریافت اطلاعات تکمیلی'
            updated_alert_text = self.build_alert_post_content(thread_data)
            
            try:
                await self.bot.edit_message(target_channel, alert_msg_id, updated_alert_text, link_preview=False)
                print(f"✏️ Successfully updated alert {alert_msg_id} in {target_channel} ({len(replies)} replies attached). Status line updated in-place.")
            except Exception as e:
                if "not modified" in str(e).lower():
                    print(f"ℹ️ Alert {alert_msg_id} in {target_channel} content unchanged.")
                else:
                    print(f"❌ Error editing thread alert {alert_msg_id} in {target_channel}: {e}")
                    
            self.save_persisted_state()

    async def flush_all_pending_debounces(self):
        print("⏳ Flushing all pending debounced updates before exit...")
        active_keys = list(self.debounce_tasks.keys())
        for k in active_keys:
            task = self.debounce_tasks.pop(k, None)
            if task and not task.done():
                task.cancel()
            try:
                await self.flush_thread_debounce(k, delay=0)
            except Exception as e:
                print(f"⚠️ Error flushing thread {k}: {e}")

    async def should_send_update_notification(self, target_channel, alert_msg_id, original_post_timestamp, last_notified_msg_id=None):
        """
        Notifications via new reply messages are disabled in favor of in-place clean edits.
        """
        return False

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
                        # Delete via bot
                        try:
                            await self.bot.delete_messages(ch, batch)
                            print(f"🗑️ Deleted {len(batch)} notification bubbles via bot in {ch}.")
                        except Exception as del_err:
                            print(f"⚠️ Bot failed to delete in {ch}: {del_err}")
                            
                        # Delete via client (user session) as well to guarantee deletion
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

    async def init_channel_last_alert_ids(self, reader_client=None):
        target_channels = ['@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat', '@VahidOnlineAI', '@iliaenAI']
        fetch_client = reader_client or self.client or self.bot
        
        # Comprehensive cleanup of all previous update notification messages across all channels
        await self.cleanup_update_notification_bubbles(reader_client=fetch_client)

        vip_map = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar', 'alibk3': 'Alibk3'}
        # Initialize latest standalone news IDs for each channel and recover alerted source history
        for ch in target_channels:
            try:
                async for m in fetch_client.iter_messages(ch, limit=35):
                    if not m or not m.text:
                        continue
                    rep_to = getattr(m, 'reply_to_msg_id', None)
                    if not rep_to and getattr(m, 'reply_to', None):
                        rep_to = getattr(m.reply_to, 'reply_to_msg_id', None)
                    if rep_to:
                        continue
                    if len(m.text.strip()) > 10 and ch not in self.channel_last_alert_id:
                        self.channel_last_alert_id[ch] = m.id
                        
                    # Reconstruct source links (from plain text and entities)
                    all_urls = self.extract_message_urls(m)
                    found_sources = []
                    for u in all_urls:
                        sm = re.search(r't\.me/([A-Za-z0-9_]+)/(\d+)', u)
                        if sm:
                            found_sources.append((sm.group(1), sm.group(2)))
                    for src_node, src_id_str in found_sources:
                        src_id = int(src_id_str)
                        canon = vip_map.get(src_node.lower(), src_node)
                        self.channel_watermarks[canon] = max(self.channel_watermarks.get(canon, 0), src_id)
                        self.recent_posted_map[(ch, canon, src_id)] = m.id
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
                            self.alibk_posted.setdefault(src_id, {'channel': ch, 'msg_id': m.id, 'timestamp': m.date.timestamp() if m.date else time.time()})
                        elif canon == 'VahidOnline' and ch == '@VahidOnlineAI':
                            self.vahid_ai_posted.setdefault(src_id, m.id)
                            msg_age = (time.time() - m.date.timestamp()) if m.date else 0
                            if msg_age <= 86400 and ("خلاصه خبر" in (m.text or "") or "⚡️" in (m.text or "")):
                                self.vahid_needs_refresh.add((src_id, m.id))
                        elif canon == 'iliaen' and ch == '@iliaenAI':
                            self.ilia_ai_posted.setdefault(src_id, m.id)
                            msg_age = (time.time() - m.date.timestamp()) if m.date else 0
                            if msg_age <= 86400 and ("خلاصه خبر" in (m.text or "") or "⚡️" in (m.text or "")):
                                self.ilia_needs_refresh.add((src_id, m.id))
                            
                        # Reconstruct thread alerts entry for semantic cross-channel merging
                        if mk not in self.thread_alerts and ch in ('@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat'):
                            title_m = re.search(r'^(?:🚨|🔕|📈|⚖️|📢)\s*\*\*(?:هشدار فوری|هشدار منطقه‌ای|گزارش اقتصادی|گزارش حقوق بشری|گزارش مردمی):\s*([^\*\n]+)\*\*', m.text)
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
                                'category': 'WAR' if ch == '@DidebanJang' else ('ECONOMY' if ch == '@DidehbanEghtesad' else 'PROTEST_RIGHTS'),
                                'is_iran_related': '🚨' in m.text,
                                'title': title_text,
                                'summary': summary_text,
                                'tag': '#دیده‌بان_جنگ' if ch == '@DidebanJang' else ('#دیده‌بان_اقتصاد' if ch == '@DidehbanEghtesad' else '#دیده‌بان_اعتراضات'),
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
                print(f"⚠️ Error initializing latest alert ID and recovering sources for {ch}: {e}")
        print(f"📊 Initialized channel latest news IDs: {self.channel_last_alert_id}")
        print(f"🔄 Recovered {len(self.vip_alert_history)} VIP alerts and {len(self.thread_alerts)} thread alerts directly from Telegram channels.")

        # Retroactive polish for Post 163 in @DidebanJang (fix title grammar and incomplete verb)
        try:
            m163 = await self.bot.get_messages('@DidebanJang', ids=163)
            if m163 and m163.text and 'شناسایی نیروهای ارتش ایران' in m163.text and 'منتشر' not in m163.text.split('\n')[0]:
                fixed_title = "🚨 **هشدار فوری: انتشار راهنمای تصویری ارتش آمریکا برای شناسایی تجهیزات و نیروهای ارتش ایران** 📸"
                t163 = re.sub(r'^(?:🚨|🔕)\s*(?:\*\*)?هشدار [^\n]+', fixed_title, m163.text)
                t163 = t163.replace("ایران ، منتشر", "ایران، منتشر")
                await self.bot.edit_message('@DidebanJang', 163, t163, link_preview=False)
                print("✅ Successfully polished title of post 163 in @DidebanJang!")
        except Exception as e163:
            print(f"⚠️ Error polishing post 163 in @DidebanJang: {e163}")

        # Startup initialization complete.

    async def handle_message_deleted(self, node_username, deleted_ids):
        """
        When a message in a monitored source channel is deleted by its admin,
        instantly delete the corresponding alert (and its update notifications) from our channels!
        """
        if not deleted_ids:
            return
            
        print(f"🗑️ Handling MessageDeleted event for node={node_username}: {deleted_ids}")
        for del_id in deleted_ids:
            del_int = int(del_id) if str(del_id).isdigit() else del_id
            del_str = str(del_id)
            
            # 0. Check multi-source thread alerts across all aggregate channels
            for tk, thread_data in list(self.thread_alerts.items()):
                if not isinstance(thread_data, dict):
                    continue
                sources = thread_data.get('sources', [])
                matching = [
                    s for s in sources 
                    if (s.get('msg_id') in (del_int, del_str) or str(s.get('msg_id')) == del_str)
                    and (not node_username or node_username in ('unknown', None) or s.get('node') == node_username)
                ]
                if matching:
                    target_ch = thread_data.get('channel', '@DidebanJang')
                    alert_mid = thread_data.get('alert_msg_id')
                    thread_data['sources'] = [s for s in sources if s not in matching]
                    if 'replies' in thread_data:
                        thread_data['replies'] = [r for r in thread_data['replies'] if r.get('msg_id') not in (del_int, del_str) and str(r.get('msg_id')) != del_str]
                    
                    if not thread_data['sources']:
                        self.thread_alerts.pop(tk, None)
                        task = self.debounce_tasks.pop(tk, None)
                        if task and not task.done():
                            task.cancel()
                        if alert_mid:
                            to_delete = [alert_mid]
                            if thread_data.get('last_notified_msg_id'):
                                to_delete.append(thread_data['last_notified_msg_id'])
                            try:
                                await self.bot.delete_messages(target_ch, to_delete)
                                print(f"🗑️ Successfully deleted alert {alert_mid} in {target_ch} (all source posts deleted by admin).")
                            except Exception as e:
                                print(f"⚠️ Error deleting alert {alert_mid} in {target_ch}: {e}")
                    else:
                        updated_text = self.build_alert_post_content(thread_data)
                        try:
                            await self.bot.edit_message(target_ch, alert_mid, updated_text, link_preview=False)
                            print(f"✏️ Refreshed alert {alert_mid} in {target_ch} after removing deleted source {del_id} ({len(thread_data['sources'])} sources remain).")
                        except Exception as e:
                            print(f"⚠️ Error editing alert after source deletion: {e}")
                    self.save_persisted_state()
            
            # 1. Alibk3 root or reply
            if node_username in ('Alibk3', None, 'unknown'):
                thread_key = f"Alibk3_{del_id}"
                # 1.1 Root post deletion
                if thread_key in self.thread_alerts or del_int in self.alibk_posted or del_str in self.alibk_posted:
                    thread_data = self.thread_alerts.pop(thread_key, None)
                    posted_info = self.alibk_posted.pop(del_int, None) or self.alibk_posted.pop(del_str, None)
                    
                    target_ch = (thread_data and thread_data.get('channel')) or (posted_info and posted_info.get('channel')) or '@DidebanJang'
                    alert_mid = (thread_data and thread_data.get('alert_msg_id')) or (posted_info and posted_info.get('msg_id'))
                    
                    task = self.debounce_tasks.pop(thread_key, None)
                    if task and not task.done():
                        task.cancel()
                        
                    if alert_mid:
                        to_delete = [alert_mid]
                        if thread_data and thread_data.get('last_notified_msg_id'):
                            to_delete.append(thread_data['last_notified_msg_id'])
                        try:
                            await self.bot.delete_messages(target_ch, to_delete)
                            print(f"🗑️ Successfully deleted Alibk3 alert {alert_mid} in {target_ch} (source post {del_id} deleted by admin).")
                        except Exception as e:
                            print(f"⚠️ Error deleting Alibk3 alert {alert_mid} in {target_ch}: {e}")
                            
                    self.reply_to_root_map = {k: v for k, v in self.reply_to_root_map.items() if v not in (del_int, del_str)}
                    self.save_persisted_state()
                    
                # 1.2 Reply deletion
                rep_key = f"Alibk3_{del_id}"
                if rep_key in self.reply_to_root_map:
                    root_id = self.reply_to_root_map.pop(rep_key, None)
                    rk = f"Alibk3_{root_id}"
                    if rk in self.thread_alerts:
                        thread_data = self.thread_alerts[rk]
                        orig_len = len(thread_data.get('replies', []))
                        thread_data['replies'] = [r for r in thread_data.get('replies', []) if r.get('msg_id') not in (del_int, del_str)]
                        if len(thread_data['replies']) != orig_len:
                            print(f"🗑️ Removed deleted reply {del_id} from Alibk3 root {root_id}. Scheduling debounce update...")
                            self.schedule_thread_debounce(rk)
                            self.save_persisted_state()

            # 2. VahidOnline
            if node_username in ('VahidOnline', None, 'unknown'):
                if del_int in self.vahid_ai_posted or del_str in self.vahid_ai_posted:
                    posted_id = self.vahid_ai_posted.pop(del_int, None) or self.vahid_ai_posted.pop(del_str, None)
                    self.vahid_post_time.pop(del_int, None)
                    self.vahid_post_time.pop(del_str, None)
                    if posted_id:
                        try:
                            await self.bot.delete_messages('@VahidOnlineAI', [posted_id])
                            print(f"🗑️ Successfully deleted VahidOnline alert {posted_id} in @VahidOnlineAI (source post {del_id} deleted by admin).")
                        except Exception as e:
                            print(f"⚠️ Error deleting VahidOnline post {posted_id} in @VahidOnlineAI: {e}")
                    self.save_persisted_state()

            # 3. iliaen
            if node_username in ('iliaen', None, 'unknown'):
                if del_int in self.ilia_ai_posted or del_str in self.ilia_ai_posted:
                    posted_id = self.ilia_ai_posted.pop(del_int, None) or self.ilia_ai_posted.pop(del_str, None)
                    self.ilia_post_time.pop(del_int, None)
                    self.ilia_post_time.pop(del_str, None)
                    if posted_id:
                        try:
                            await self.bot.delete_messages('@iliaenAI', [posted_id])
                            print(f"🗑️ Successfully deleted iliaen alert {posted_id} in @iliaenAI (source post {del_id} deleted by admin).")
                        except Exception as e:
                            print(f"⚠️ Error deleting iliaen post {posted_id} in @iliaenAI: {e}")
                    self.save_persisted_state()

            # 4. VIP / Anomaly alerts across all nodes
            for msg_key in list(self.vip_alert_history.keys()):
                if msg_key.endswith(f"_{del_int}") or msg_key.endswith(f"_{del_str}"):
                    if node_username and not msg_key.startswith(f"{node_username}_"):
                        continue
                    info = self.vip_alert_history.pop(msg_key, None)
                    if info and info.get('sent_msg_id'):
                        sent_mid = info['sent_msg_id']
                        target_ch = info.get('target_channel', '@DidebanJang')
                        try:
                            await self.bot.delete_messages(target_ch, [sent_mid])
                            print(f"🗑️ Successfully deleted VIP alert {sent_mid} in {target_ch} (source {msg_key} deleted by admin).")
                        except Exception as e:
                            print(f"⚠️ Error deleting VIP alert {sent_mid} in {target_ch}: {e}")
                    self.save_persisted_state()

    async def verify_recent_messages_existence(self):
        """
        Periodic poller fallback:
        Verifies if any recently alerted source message was deleted by its admin on Telegram.
        """
        if not self.client or not self.client.is_connected():
            return
            
        from telethon.tl.types import MessageEmpty
        now = time.time()
        
        # 1. Alibk3
        alibk_recent_ids = set()
        for k, v in self.thread_alerts.items():
            if k.startswith("Alibk3_") and (now - v.get('timestamp', 0)) <= 7200:
                root_id = v.get('root_id')
                if root_id:
                    alibk_recent_ids.add(int(root_id) if str(root_id).isdigit() else root_id)
                for r in v.get('replies', []):
                    rep_id = r.get('msg_id')
                    if rep_id:
                        alibk_recent_ids.add(int(rep_id) if str(rep_id).isdigit() else rep_id)
        
        if alibk_recent_ids:
            try:
                check_ids = list(alibk_recent_ids)[:20]
                msgs = await self.client.get_messages('Alibk3', ids=check_ids)
                deleted_ids = []
                for cid, m in zip(check_ids, msgs):
                    if m is None or isinstance(m, MessageEmpty) or (not getattr(m, 'message', None) and not getattr(m, 'media', None)):
                        deleted_ids.append(cid)
                if deleted_ids:
                    print(f"🔍 Poller fallback detected deleted messages in Alibk3: {deleted_ids}")
                    await self.handle_message_deleted('Alibk3', deleted_ids)
            except Exception as e:
                pass

        # 2. VahidOnline
        vahid_recent_ids = [int(mid) for mid, t in self.vahid_post_time.items() if (now - t) <= 7200 and str(mid).isdigit()]
        if vahid_recent_ids:
            try:
                check_ids = vahid_recent_ids[:20]
                msgs = await self.client.get_messages('VahidOnline', ids=check_ids)
                deleted_ids = []
                for cid, m in zip(check_ids, msgs):
                    if m is None or isinstance(m, MessageEmpty) or (not getattr(m, 'message', None) and not getattr(m, 'media', None)):
                        deleted_ids.append(cid)
                if deleted_ids:
                    print(f"🔍 Poller fallback detected deleted messages in VahidOnline: {deleted_ids}")
                    await self.handle_message_deleted('VahidOnline', deleted_ids)
            except Exception as e:
                pass

        # 3. iliaen
        ilia_recent_ids = [int(mid) for mid, t in self.ilia_post_time.items() if (now - t) <= 7200 and str(mid).isdigit()]
        if ilia_recent_ids:
            try:
                check_ids = ilia_recent_ids[:20]
                msgs = await self.client.get_messages('iliaen', ids=check_ids)
                deleted_ids = []
                for cid, m in zip(check_ids, msgs):
                    if m is None or isinstance(m, MessageEmpty) or (not getattr(m, 'message', None) and not getattr(m, 'media', None)):
                        deleted_ids.append(cid)
                if deleted_ids:
                    print(f"🔍 Poller fallback detected deleted messages in iliaen: {deleted_ids}")
                    await self.handle_message_deleted('iliaen', deleted_ids)
            except Exception as e:
                pass

    async def summarize_thread_post(self, thread_text):
        if not GEMINI_API_KEY:
            return None
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = DEFAULT_GEMINI_MODEL
            
        prompt = (
            "تو یک تحلیلگر و ویراستار ارشد اخبار در سیستم مانیتورینگ هوشمند هستی.\n"
            "در زیر یک خبر اصلی و گزارش‌های تکمیلی یا ویرایش‌های انجام‌شده قرار دارد که سیر یک رویداد خبری را نشان می‌دهند.\n\n"
            "وظایف الزامی:\n"
            "۱. تمام متن‌ها را با هم تلفیق کن و وضعیت نهایی و به‌روز شده رویداد را استخراج کن.\n"
            "۲. title: یک تیتر رسا، جذاب و صریح به زبان فارسی همراه با فعل کامل که وضعیت نهایی را بدون ابهام بیان کند.\n"
            "۳. summary: یک چکیده روان، گویا و منسجم به زبان فارسی با اولویت ایجاز و کوتاه‌سازی در کنار پوشش کامل وضعیت نهایی رویداد.\n\n"
            f"متن رویداد و پیوست‌ها:\n{thread_text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب شیء JSON با دو کلید 'title' و 'summary' بنویس:\n"
            "{\n"
            '  "title": "تیتر رسا و کامل فارسی",\n'
            '  "summary": "متن چکیده منسجم و شیوا"\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.2}
        }
        
        raw_out = await self.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
                title = res.get("title", "").strip()
                summary = res.get("summary", "").strip()
                if title and summary:
                    return {"title": title, "summary": summary}
        return None

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
        if not text:
            return False
            
        # VIP channels (VahidOnline, iliaen & withyashar) are primary monitored channels
        # that frequently quote news agencies or tag their sub-channels (@VahidHeadline).
        # They must NEVER be dropped by keyword stopwords.
        if (node or '').lower() in ['vahidonline', 'iliaen', 'withyashar']:
            return False

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
            # Only match if preceded by numbers/ordinals (e.g. "۱۵ شهریور") or explicit "ماه گذشته"
            if (re.search(r'(?:\d{1,2}|یکم|دوم|سوم|چهارم|پنجم|سی‌ام)\s+' + month, text) or f"{month}‌ماه گذشته" in text or f"{month} گذشته" in text) and i < current_month_idx: 
                return True
        
        # Filter out formal journalistic/recap language and news channel forwards
        news_stopwords = [
            "vahidheadline", "vahidoonline", "به گزارش", "خبرگزاری مهر", "خبرگزاری فارس", 
            "خبرگزاری تسنیم", "خبرگزاری ایسنا", "خبرگزاری ایرنا", "خبرگزاری ایلنا", "روزنامه همشهری", "روزنامه شرق", "رویترز", "روز گذشته", "صبح امروز", 
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
            "سرلشکر", "سردار", "امیر", "دریادار", "سرتیپ"
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
        
        has_foreign = any(fk in text for fk in self.foreign_keywords)
        
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
                if has_foreign or any(fk in final_loc_str for fk in self.foreign_keywords):
                    pat += "||FOREIGN||"
                patterns.append(pat)
            elif is_citizen:
                pat = f"گزارش شهروندی در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in self.foreign_keywords):
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

    async def process_message(self, text, node, msg_id, is_edit=False, msg_date=None, raw_msg=None, skip_mirror_channels=False):
        async with self.lock:
            # Advance high-water mark for monitored channels
            vip_map_fast = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar', 'alibk3': 'Alibk3'}
            canon_node = vip_map_fast.get((node or '').lower(), node or 'unknown')
            if not is_edit:
                self.channel_watermarks[canon_node] = max(self.channel_watermarks.get(canon_node, 0), msg_id)

            # --- @VahidOnlineAI & @iliaenAI Channel Pipelines ---
            # Dedicated mirror summary channels must NEVER be dropped by radar age filters
            node_key = (node or '').lower()
            if not skip_mirror_channels:
                if node_key == 'vahidonline':
                    asyncio.create_task(self.handle_vahid_online_ai(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                elif node_key == 'iliaen':
                    asyncio.create_task(self.handle_ilia_ai(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                elif node_key == 'alibk3':
                    asyncio.create_task(self.handle_alibk_regional(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return
                elif node_key == 'withyashar':
                    asyncio.create_task(self.handle_withyashar_intelligence(text, msg_id, raw_msg=raw_msg, is_edit=is_edit))
                    return

            # Accept messages up to 15 minutes old (900s) to catch news during runner restarts & handovers
            # This applies to breaking news radar alerts (War / Economy / Protest)
            if msg_date:
                now_utc = datetime.now(timezone.utc)
                if (now_utc - msg_date).total_seconds() > 900:
                    return
                    
            self.purge_old_messages()
            
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
            
            if (node or '').lower() not in ['vahidonline', 'iliaen', 'withyashar'] and not has_economy and self.is_old_news(text, node):
                return
            
            # --- VIP CHANNELS EXCLUSIVE PROTESTS LOGIC ---
            vip_map = {'vahidonline': 'VahidOnline', 'iliaen': 'iliaen', 'withyashar': 'withyashar'}
            node_key = (node or '').lower()
            
            if node_key not in vip_map:
                # If not VIP, drop any protest/strike/rights patterns
                patterns_in_msg = [p for p in patterns_in_msg if "اعتراض" not in p and "اعتصاب" not in p and "اعدام" not in p]
                
            link = f"https://t.me/{node}/{msg_id}"
            
            # --- VIP CHANNELS LOGIC ---
            if node_key in vip_map and (patterns_in_msg or node_key == 'withyashar'):
                canonical_node = vip_map[node_key]
                msg_key = f"{canonical_node}_{msg_id}"
                now = time.time()
                
                # Check if this message was previously alerted
                if msg_key in self.vip_alert_history:
                    prev = self.vip_alert_history[msg_key]
                    time_diff = now - prev.get('time', now)
                    target_ch = prev.get('target_channel')
                    orig_sent_id = prev.get('sent_msg_id')
                    
                    if time_diff <= 259200 and text != prev.get('text') and orig_sent_id and target_ch:
                        self.vip_alert_history[msg_key]['time'] = now
                        self.vip_alert_history[msg_key]['text'] = text
                        
                        ai_class = await self.classify_vip_message(text)
                        if ai_class:
                            topic_title = ai_class.get('topic_title', '').strip()
                            category = ai_class.get('category')
                            is_iran = self.determine_iran_related(ai_class, text, topic_title)
                            
                            icon = "🚨" if is_iran else "🔕"
                            alert_title = f"هشدار فوری: {topic_title}" if is_iran else f"هشدار منطقه‌ای: {topic_title}"
                            if category == 'ECONOMY':
                                alert_title = f"گزارش اقتصادی: {topic_title}"
                                icon = "📈"
                            elif category == 'PROTEST_RIGHTS':
                                alert_title = f"گزارش حقوق بشری: {topic_title}"
                                icon = "⚖️"
                                
                            root_media = self.detect_msg_media_type(raw_msg)
                            root_icon = self.get_media_icon_from_type(root_media)
                            
                            tag = "#دیده‌بان_جنگ" if target_ch == "@DidebanJang" else ("#دیده‌بان_اقتصاد" if target_ch == "@DidehbanEghtesad" else "#دیده‌بان_اعتراضات")
                            
                            time_fa = self.get_tehran_time_fa()
                            updated_alert = (
                                f"{icon} **{alert_title}**\n\n"
                                f"🔗 **منبع خبر:**\n- [{canonical_node}]({link}) {root_icon}\n\n"
                                f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | دریافت اطلاعات تکمیلی**\n\n"
                                f"{tag}\n\n{target_ch}"
                            )
                            try:
                                await self.bot.edit_message(target_ch, orig_sent_id, updated_alert, link_preview=False)
                                asyncio.create_task(self.append_ai_summary(target_ch, orig_sent_id, updated_alert, [text]))
                                print(f"✏️ Updated VIP alert {orig_sent_id} in {target_ch} with in-place status line.")
                            except Exception as edit_err:
                                print(f"⚠️ Failed to edit VIP alert {orig_sent_id} in {target_ch}: {edit_err}")
                        else:
                            time_fa = self.get_tehran_time_fa()
                            try:
                                curr_msg = await self.bot.get_messages(target_ch, ids=orig_sent_id)
                                if curr_msg and curr_msg.text:
                                    status_line = f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | دریافت اطلاعات تکمیلی**"
                                    if '🔄 **به‌روزرسانی شده' in curr_msg.text:
                                        new_t = re.sub(r'🔄\s*\*\*به‌روزرسانی شده[^\n]+', status_line, curr_msg.text)
                                    else:
                                        tag = "#دیده‌بان_جنگ" if target_ch == "@DidebanJang" else ("#دیده‌بان_اقتصاد" if target_ch == "@DidehbanEghtesad" else "#دیده‌بان_اعتراضات")
                                        new_t = curr_msg.text.replace(tag, f"{status_line}\n\n{tag}")
                                    await self.bot.edit_message(target_ch, orig_sent_id, new_t, link_preview=False)
                                    print(f"✏️ Fallback updated VIP alert {orig_sent_id} in {target_ch}.")
                            except Exception as fb_err:
                                print(f"⚠️ Failed to fallback edit VIP alert {orig_sent_id} in {target_ch}: {fb_err}")
                        self.save_persisted_state()
                    # In ALL cases where msg_key is already in vip_alert_history, return immediately to guarantee no duplicate alert!
                    return
                else:
                    self.vip_alert_history[msg_key] = {'time': now, 'pattern': patterns_in_msg[0] if patterns_in_msg else "withyashar_report", 'text': text}
                    if msg_key not in self.alerted_msg_patterns:
                        self.alerted_msg_patterns[msg_key] = set()
                        
                    # Filter to only patterns we haven't alerted for this exact message
                    if patterns_in_msg:
                        new_pats = [p for p in patterns_in_msg if p.replace("||FOREIGN||", "") not in self.alerted_msg_patterns[msg_key]]
                    else:
                        new_pats = ["withyashar_report"] if "withyashar_report" not in self.alerted_msg_patterns[msg_key] else []
                    
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
                                is_silent = False if (canonical_node in ['VahidOnline', 'iliaen', 'withyashar'] and not is_edit) else True
                            elif category == 'PROTEST_RIGHTS':
                                target_channel = "@DidebanEterazat"
                                is_rights = any(w in text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت", "محبوس", "قوه قضائیه", "فساد", "تخلف"])
                                alert_title = f"گزارش حقوق بشری: {topic_title}" if is_rights else f"گزارش مردمی: {topic_title}"
                                custom_icon = "⚖️" if any(w in text for w in ["اعدام", "حکم", "طناب دار", "دادگاه", "قوه قضائیه", "بازداشت"]) else "📢"
                                is_silent = False if (canonical_node in ['VahidOnline', 'iliaen', 'withyashar'] and not is_edit) else True
                            elif category == 'WAR':
                                target_channel = "@DidebanJang"
                                is_iran = self.determine_iran_related(ai_class, text, topic_title)
                                if is_iran:
                                    alert_title = f"هشدار فوری: {topic_title}"
                                    custom_icon = "🚨"
                                    is_silent = False if (canonical_node in ['VahidOnline', 'iliaen', 'withyashar'] and not is_edit) else True
                                else:
                                    alert_title = f"هشدار منطقه‌ای: {topic_title}"
                                    custom_icon = "🔕"
                                    is_silent = True
                            else:
                                target_channel = None
                                alert_title = None
                                custom_icon = None
                                return
                                
                            # Check cross-channel semantic deduplication / merging
                            is_iran = self.determine_iran_related(ai_class, text, topic_title) if category == 'WAR' else False
                            merged = await self.check_and_merge_with_active_alert(
                                text=text,
                                node=canonical_node,
                                msg_id=msg_id,
                                target_channel=target_channel,
                                candidate_title=topic_title,
                                candidate_summary="",
                                raw_msg=raw_msg,
                                category=category,
                                is_iran_related=is_iran
                            )
                            if merged:
                                return

                            self.vip_alert_history[msg_key]['target_channel'] = target_channel
                            baseline = self.baselines.get(clean_pats[0], 0.1) if clean_pats else 0.1
                            
                            topic_summary = ai_class.get('summary', '')
                            sent_msg, target_channel, alert_text = await self.send_alert(
                                topic_title, 
                                "VIP_IMMEDIATE", 
                                baseline, 
                                [f"- [{canonical_node}]({link})"], 
                                is_silent=is_silent,
                                target_channel=target_channel,
                                alert_title=alert_title,
                                custom_icon=custom_icon,
                                summary=topic_summary
                            )
                            if sent_msg:
                                self.vip_alert_history[msg_key]['sent_msg_id'] = sent_msg.id
                                thread_key = f"{canonical_node}_{msg_id}"
                                self.thread_alerts[thread_key] = {
                                    'channel': target_channel,
                                    'alert_msg_id': sent_msg.id,
                                    'node': canonical_node,
                                    'root_id': msg_id,
                                    'root_text': text,
                                    'root_media_type': self.detect_msg_media_type(raw_msg),
                                    'timestamp': time.time(),
                                    'category': category,
                                    'is_iran_related': is_iran,
                                    'title': topic_title,
                                    'summary': topic_summary,
                                    'tag': '#دیده‌بان_اقتصاد' if target_channel == '@DidehbanEghtesad' else ('#دیده‌بان_اعتراضات' if target_channel == '@DidebanEterazat' else '#دیده‌بان_جنگ'),
                                    'sources': [{
                                        'node': canonical_node,
                                        'msg_id': msg_id,
                                        'media_type': self.detect_msg_media_type(raw_msg),
                                        'link': link,
                                        'timestamp': time.time()
                                    }],
                                    'replies': []
                                }
                                self.save_persisted_state()
                                if not topic_summary:
                                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, [text]))
                            return
                        else:
                            # 2. Intelligent Humanized Fallback for VIP Channels if AI API is temporarily unreachable
                            first_line = text.split('\n')[0].strip()
                            clean_headline = re.sub(r'^[🚨⚡️📢🛑🔕⚖️📈📸📹🎙\s\*\-]+', '', first_line).strip()
                            parts = re.split(r'[.؛:\n]', clean_headline)
                            candidate_title = parts[0].strip() if parts else clean_headline
                            if len(candidate_title) < 15 and len(parts) > 1:
                                candidate_title = (parts[0] + ": " + parts[1]).strip()
                            clean_headline = candidate_title if candidate_title else clean_headline[:80]
                            clean_headline = re.sub(r'\s+', ' ', clean_headline).strip()
                            
                            if not clean_headline or len(clean_headline) < 5:
                                clean_headline = "، ".join(dict.fromkeys(clean_pats)) if clean_pats else "گزارش فوری میدانی"
                                
                            clean_headline = clean_headline.replace("||FOREIGN||", "").replace("||ECONOMY||", "").strip()
                            
                            # Heuristic category determination
                            is_economy = any(w in text for w in ['دلار', 'تتر', 'ارز', 'طلا', 'سکه', 'بورس', 'تورم', 'قیمت'])
                            is_protest = any(w in text for w in ['اعتراض', 'اعتصاب', 'اعدام', 'تجمع', 'زندان', 'دادگاه', 'بازداشت', 'حقوق بشری'])
                            has_war_keyword = any(w in text for w in [
                                'حمله', 'موشک', 'پهپاد', 'انفجار', 'بمباران', 'آژیر', 'پدافند', 'جنگنده', 
                                'سپاه', 'ارتش', 'درگیری', 'نظامی', 'سقوط', 'کشته', 'مجروح', 'تلفات', 'تیراندازی', 'عملیات',
                                'آرامکو', 'پایگاه', 'ناو', 'بمب‌افکن', 'سوخت‌رسان', 'رادار', 'سنتکام', 'جبهه', 'اصابت', 'شهید'
                            ])
                            
                            # If from VIP and has NEITHER war patterns NOR war keywords, it is general/diplomatic commentary (like Trump/Zelensky) -> drop it from war channel
                            if not patterns_in_msg and not has_war_keyword and not is_economy and not is_protest:
                                print(f"ℹ️ VIP Fallback dropped off-topic non-war message (Msg {msg_id}): '{clean_headline}'")
                                return
                                
                            if is_economy:
                                category = 'ECONOMY'
                                target_channel = "@DidehbanEghtesad"
                                alert_title = f"گزارش اقتصادی: {clean_headline}"
                                custom_icon = "📈"
                                is_silent = False
                            elif is_protest:
                                category = 'PROTEST_RIGHTS'
                                target_channel = "@DidebanEterazat"
                                alert_title = f"گزارش مردمی: {clean_headline}"
                                custom_icon = "📢"
                                is_silent = False
                            else:
                                category = 'WAR'
                                target_channel = "@DidebanJang"
                                is_iran = self.determine_iran_related(None, text, clean_headline)
                                if is_iran:
                                    alert_title = f"هشدار فوری: {clean_headline}"
                                    custom_icon = "🚨"
                                    is_silent = False
                                else:
                                    alert_title = f"هشدار منطقه‌ای: {clean_headline}"
                                    custom_icon = "🔕"
                                    is_silent = True
                                    
                            # Check semantic deduplication even for fallback
                            merged = await self.check_and_merge_with_active_alert(
                                text=text,
                                node=canonical_node,
                                msg_id=msg_id,
                                target_channel=target_channel,
                                candidate_title=clean_headline,
                                candidate_summary="",
                                raw_msg=raw_msg,
                                category=category,
                                is_iran_related=is_iran
                            )
                            if merged:
                                return

                            baseline = self.baselines.get(clean_pats[0], 0.1) if clean_pats else 0.1
                            sent_msg, target_channel, alert_text = await self.send_alert(
                                clean_headline, 
                                "VIP_IMMEDIATE", 
                                baseline, 
                                [f"- [{canonical_node}]({link})"], 
                                is_silent=is_silent,
                                target_channel=target_channel,
                                alert_title=alert_title,
                                custom_icon=custom_icon,
                                summary=text.strip()
                            )
                            if sent_msg:
                                self.vip_alert_history[msg_key]['target_channel'] = target_channel
                                self.vip_alert_history[msg_key]['sent_msg_id'] = sent_msg.id
                                thread_key = f"{canonical_node}_{msg_id}"
                                self.thread_alerts[thread_key] = {
                                    'channel': target_channel,
                                    'alert_msg_id': sent_msg.id,
                                    'node': canonical_node,
                                    'root_id': msg_id,
                                    'root_text': text,
                                    'root_media_type': self.detect_msg_media_type(raw_msg),
                                    'timestamp': time.time(),
                                    'category': category,
                                    'is_iran_related': is_iran,
                                    'title': clean_headline,
                                    'summary': text.strip(),
                                    'tag': '#دیده‌بان_اقتصاد' if target_channel == '@DidehbanEghtesad' else ('#دیده‌بان_اعتراضات' if target_channel == '@DidebanEterazat' else '#دیده‌بان_جنگ'),
                                    'sources': [{
                                        'node': canonical_node,
                                        'msg_id': msg_id,
                                        'media_type': self.detect_msg_media_type(raw_msg),
                                        'link': link,
                                        'timestamp': time.time()
                                    }],
                                    'replies': []
                                }
                                self.save_persisted_state()
                                asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, [text]))
                            return

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
            source_links = [f"- [{node}]({link})" for node, link in channels.items() if node and node != 'unknown' and re.match(r'^[A-Za-z0-9_]+$', str(node))]
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
                    is_iran = self.determine_iran_related(ai_class, combined_text, topic_title)
                    if is_iran:
                        title = f"هشدار فوری: {topic_title}"
                        icon = "🚨"
                        is_silent = False
                    else:
                        title = f"هشدار منطقه‌ای: {topic_title}"
                        icon = "🔕"
                        is_silent = True
                else:
                    target_ch = None
                    title = None
                    icon = None
                    continue
                    
                parsed_anomaly_sources = []
                for ch_node, ch_link in channels.items():
                    if not ch_node or str(ch_node).lower() in ('radar', 'unknown', 'sentinel'):
                        continue
                    m_sl = re.search(r'https://t\.me/([^/]+)/(\d+)', ch_link)
                    if m_sl:
                        parsed_anomaly_sources.append({
                            'node': m_sl.group(1),
                            'msg_id': int(m_sl.group(2)),
                            'media_type': 'text',
                            'link': ch_link,
                            'timestamp': time.time()
                        })

                first_source_node = parsed_anomaly_sources[0]['node'] if parsed_anomaly_sources else 'Sentinel'
                first_source_id = parsed_anomaly_sources[0]['msg_id'] if parsed_anomaly_sources else int(time.time())

                is_iran = self.determine_iran_related(ai_class, combined_text, topic_title) if category == 'WAR' else False
                merged = await self.check_and_merge_with_active_alert(
                    text=combined_text,
                    node=first_source_node,
                    msg_id=first_source_id,
                    target_channel=target_ch,
                    candidate_title=topic_title,
                    candidate_summary="",
                    raw_msg=None,
                    category=category,
                    is_iran_related=is_iran,
                    candidate_sources=parsed_anomaly_sources
                )
                if merged:
                    continue

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
                    thread_key = f"{first_source_node}_{first_source_id}"
                    self.thread_alerts[thread_key] = {
                        'channel': target_channel,
                        'alert_msg_id': sent_msg.id,
                        'node': first_source_node,
                        'root_id': first_source_id,
                        'root_text': combined_text,
                        'root_media_type': 'text',
                        'timestamp': time.time(),
                        'category': category,
                        'is_iran_related': is_iran,
                        'title': topic_title,
                        'summary': '',
                        'tag': '#دیده‌بان_اقتصاد' if target_channel == '@DidehbanEghtesad' else ('#دیده‌بان_اعتراضات' if target_channel == '@DidebanEterazat' else '#دیده‌بان_جنگ'),
                        'sources': parsed_anomaly_sources,
                        'replies': []
                    }
                    self.save_persisted_state()
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, raw_texts[:3]))
            else:
                sent_msg, target_channel, alert_text = await self.send_alert(clean_pat, distinct_channel_count, normal_rate, source_links[:3], is_silent=is_silent)
                if sent_msg:
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, raw_texts[:3]))

    async def send_alert(self, pattern, count, normal_rate, context_msgs, is_silent=False, target_channel=None, alert_title=None, custom_icon=None, summary=None):
        if not BOT_TOKEN: return
        
        is_economy = "||ECONOMY||" in str(pattern) or target_channel == "@DidehbanEghtesad"
        clean_pat_str = str(pattern).replace("||ECONOMY||", "").replace("||FOREIGN||", "").strip()
        
        is_protest = ("اعتراض" in clean_pat_str or "اعتصاب" in clean_pat_str or "اعدام" in clean_pat_str or "حقوق بشری" in clean_pat_str) or target_channel == "@DidebanEterazat"
        
        # Determine icon and channel
        if custom_icon:
            icon = custom_icon
        elif is_silent:
            icon = "🔕"
        elif is_economy:
            icon = "📈"
        elif is_protest:
            if "اعدام" in clean_pat_str or "حقوق بشری" in clean_pat_str:
                icon = "⚖️"
            else:
                icon = "🛑" if "اعتصاب" in clean_pat_str else "📢"
        else:
            icon = "🚨"
            
        if alert_title:
            alert_title = str(alert_title).replace("||ECONOMY||", "").replace("||FOREIGN||", "").strip()
        else:
            if is_economy:
                alert_title = f"گزارش اقتصادی: {clean_pat_str}"
            elif is_protest:
                if "اعدام" in clean_pat_str or "حقوق بشری" in clean_pat_str:
                    alert_title = f"گزارش حقوق بشری: {clean_pat_str}"
                else:
                    alert_title = f"گزارش مردمی: {clean_pat_str}"
            else:
                if is_silent:
                    alert_title = f"هشدار منطقه‌ای: {clean_pat_str}"
                else:
                    alert_title = f"هشدار فوری: {clean_pat_str}"
            
        source_header = "🔗 **منبع خبر:**" if len(context_msgs) == 1 else "🔗 **منابع خبر:**"
        clean_context = [
            m.replace(' (VIP Alert - Edited)', '').replace(' (VIP Alert)', '').replace(' (VIP Update)', '')
             .replace(' (هشدار ویژه)', '').replace(' (به‌روزرسانی ویژه)', '').strip()
            for m in context_msgs
        ]
        
        if summary:
            alert_text = (
                f"{icon} **{alert_title}**\n\n"
                f"📝 **چکیده رویداد:**\n{summary}\n\n"
                f"{source_header}\n" + "\n".join(clean_context) + "\n\n"
            )
        else:
            alert_text = (
                f"{icon} **{alert_title}**\n\n"
                f"{source_header}\n" + "\n".join(clean_context) + "\n\n"
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
                if sent_msg:
                    self.channel_last_alert_id[sub] = sent_msg.id
            except Exception as e:
                print(f"Failed to send alert to {sub}: {e}")
                
        if sent_msg:
            self.save_persisted_state()
            
        return sent_msg, target_channel, alert_text

    async def classify_message(self, text):
        if not GEMINI_API_KEY:
            return None
            
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = DEFAULT_GEMINI_MODEL
            
        prompt = (
            "تو یک تحلیلگر و دروازه‌بان هوشمند خبر برای یک سیستم دیده‌بان و مانیتورینگ تلگرام هستی.\n"
            "یک رویداد خبری مهم دریافت شده است. موضوع این خبر را تحلیل کن و مشخص کن آیا این خبر باید در یکی از ۳ کانال تخصصی زیر منتشر شود:\n\n"
            "دسته‌بندی‌های مجاز (category):\n"
            "1. WAR: اخبار جنگ، تنش‌های نظامی، حملات هوایی/موشکی/پهپادی، بمباران، پدافند هوایی، درگیری‌های مسلحانه، آژیر خطر، انفجارهای نظامی، حوادث و آسیب به تاسیسات نفتی/گازی و پالایشگاهی ناشی از جنگ در منطقه.\n"
            "2. ECONOMY: اخبار مهم اقتصادی، نوسانات شدید نرخ ارز (دلار، تتر، یورو)، طلا و سکه، بازار بورس، سقوط ریال، تصمیمات کلیدی ارزی و شوک‌های معیشتی.\n"
            "3. PROTEST_RIGHTS: اخبار اعتراضات مردمی، اعتصابات، تجمعات خیابانی، سرکوب معترضان، بازداشت‌ها، احکام دادگاه‌ها و پرونده‌های معترضان و فعالان، اجرای احکام اعدام، وضعیت زندانیان سیاسی.\n"
            "4. OTHER: اخبار متفرقه که در هیچ‌کدام از ۳ دسته بالا قرار نمی‌گیرد (مانند اخبار پزشکی، حمله قلبی، حوادث روزمره، اخبار فرهنگی/ورزشی، روابط دیپلماتیک عادی بدون جنگ، هواشناسی).\n\n"
            "تشخیص ارتباط با ایران و آمادگی‌های جنگی (is_iran_related):\n"
            "- مقدار true: اگر رویداد مستقیماً به خاک ایران، اهداف نظامی یا پدافندی ایران، سپاه یا ارتش، مقامات ایران مربوط است، یا اگر خبر درباره «آمادگی‌ها، آرایش جنگی، جابجایی تانکرهای سوخت‌رسان، بمب‌افکن‌ها و تسلیحات توسط آمریکا یا اسرائیل برای پشتیبانی از حمله احتمالی به ایران یا رویارویی مستقیم با ایران» است.\n"
            "- مقدار false: اگر صرفاً یک رویداد داخلی در کشوری دوردست (مثل اوکراین) یا درگیری بین سایر طرف‌های منطقه‌ای بدون هیچ ربطی به جبهه رویارویی با ایران است.\n\n"
            f"متن خبر:\n{text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب یک شیء JSON با این فیلدها بنویس و هیچ کلمه یا توضیح دیگری قبل یا بعد از آن ننویس:\n"
            "{\n"
            '  "category": "WAR" | "ECONOMY" | "PROTEST_RIGHTS" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "topic_title": "یک عنوان رسا، جذاب و صریح فارسی همراه با فعل کامل که اصل رویداد را بدون ابهام بیان کند",\n'
            '  "summary": "چکیده شفاف و شیوا از رویداد با اولویت ایجاز و کوتاه‌سازی در کنار ابهام‌زدایی و توضیحات تکمیلی لازم"\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 400,
                "temperature": 0.2
            }
        }
        
        raw_text = await self.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_text:
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
                category = str(res.get("category", "")).upper().strip()
                topic_title = str(res.get("topic_title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                is_iran_related = bool(res.get("is_iran_related", False))
                if category in ["WAR", "ECONOMY", "PROTEST_RIGHTS", "OTHER"]:
                    print(f"🎯 VIP/Anomaly AI Classification: category={category}, is_iran={is_iran_related}, topic='{topic_title}' (Model: {self.current_gemini_model})")
                    return {"category": category, "is_iran_related": is_iran_related, "topic_title": topic_title, "summary": summary}
                    
        return None

    classify_vip_message = classify_message

    def parse_model_version_score(self, model_name):
        """
        Calculates a sort score so the latest (highest version) models rank first.
        e.g. gemini-2.5 > gemini-2.0 > gemini-1.5 > gemini-1.0
        """
        clean = model_name.lower().replace("models/", "").strip()
        nums = re.findall(r'(\d+(?:\.\d+)?)', clean)
        score = float(nums[0]) if nums else 0.0
        has_date = 1 if re.search(r'\d{2}-\d{2}|\d{4}', clean) else 0
        is_stable = 0 if ('preview' in clean or 'exp' in clean) else 1
        return (score, is_stable, has_date, clean)

    async def fetch_latest_google_models(self):
        """
        Live-queries Google API for available models supporting generateContent.
        Categorizes and sorts into strict user-defined priority:
          Priority 1: Latest flash-lite (newest version first)
          Priority 2: Latest flash (newest version first)
          Priority 3: Latest pro (newest version first)
        """
        active_key = self.get_gemini_api_key()
        if not active_key:
            return []
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={active_key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        loop = asyncio.get_running_loop()
        
        def make_req():
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.loads(response.read().decode('utf-8'))
                
        try:
            data = await loop.run_in_executor(None, make_req)
            models = data.get('models', [])
            
            # Exclude non-text/specialized modalities (TTS audio, image, custom tools, deep-research)
            valid = [
                m['name'].replace('models/', '').strip() 
                for m in models 
                if "generateContent" in m.get("supportedGenerationMethods", [])
                and not any(bad in m['name'].lower() for bad in ['tts', 'image', 'audio', 'embed', 'customtools', 'deep-research', 'realtime', 'live'])
            ]
            
            flash_lite = [m for m in valid if 'flash-lite' in m or 'flash_lite' in m]
            flash = [m for m in valid if ('flash' in m) and ('flash-lite' not in m and 'flash_lite' not in m)]
            pro = [m for m in valid if 'pro' in m and 'flash' not in m]
            
            flash_lite.sort(key=self.parse_model_version_score, reverse=True)
            flash.sort(key=self.parse_model_version_score, reverse=True)
            pro.sort(key=self.parse_model_version_score, reverse=True)
            
            if flash_lite:
                self.latest_flash_lite_model = flash_lite[0]
            elif flash:
                self.latest_flash_lite_model = flash[0]
                
            sorted_candidates = flash_lite + flash + pro
            print(f"📡 Dynamic Google Models Queried ({len(sorted_candidates)} candidates):")
            print(f"   ⚡️ Tier 1 (Flash-Lite): {flash_lite[:3]}")
            print(f"   ⚡️ Tier 2 (Flash): {flash[:3]}")
            print(f"   ⚡️ Tier 3 (Pro): {pro[:3]}")
            if sorted_candidates:
                self.current_gemini_model = sorted_candidates[0]
                return sorted_candidates
        except Exception as e:
            print(f"⚠️ Failed to query latest Google models: {e}")
            
        fallback_candidates = [
            "gemini-2.0-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-1.5-flash", 
            "gemini-1.5-flash-8b",
            "gemini-2.0-pro-exp-02-05",
            "gemini-1.5-pro"
        ]
        return fallback_candidates

    async def rotate_gemini_model_dynamic(self, failed_model=None):
        """
        Dynamically queries Google API for the newest models and escalates across tiers:
        Priority 1: Latest flash-lite
        Priority 2: Latest flash (if flash-lite fails/429)
        Priority 3: Latest pro (if flash fails/429)
        Fallback: Stable flash
        """
        candidates = await self.fetch_latest_google_models()
        if not candidates:
            candidates = [
                "gemini-2.0-flash-lite", 
                "gemini-2.0-flash", 
                "gemini-2.5-flash", 
                "gemini-1.5-flash", 
                "gemini-1.5-flash-8b"
            ]
            
        curr = failed_model or getattr(self, 'current_gemini_model', None) or getattr(self, 'latest_flash_lite_model', candidates[0])
        
        flash_lite = [m for m in candidates if 'flash-lite' in m or 'flash_lite' in m]
        flash = [m for m in candidates if ('flash' in m) and ('flash-lite' not in m and 'flash_lite' not in m)]
        pro = [m for m in candidates if 'pro' in m and 'flash' not in m]
        stable_flash = [m for m in flash if any(v in m for v in ['2.5', '1.5'])]

        next_model = None
        # Strict cross-tier escalation on failure:
        if curr in flash_lite:
            # Tier 1 failed (e.g. 429 quota) -> Escalate to Tier 2 (Top Flash)
            if flash:
                next_model = flash[0]
            elif pro:
                next_model = pro[0]
        elif curr == (flash[0] if flash else None):
            # Top flash preview failed (503/timeout) -> Try rock-solid stable workhorse flash
            if stable_flash and stable_flash[0] != curr:
                next_model = stable_flash[0]
            elif len(flash) > 1:
                next_model = flash[1]
            elif pro:
                next_model = pro[0]
        elif curr in flash:
            # Flash failed -> Escalate to Tier 3 (Pro)
            if pro:
                next_model = pro[0]
            elif stable_flash and stable_flash[0] != curr:
                next_model = stable_flash[0]
        elif curr in pro:
            # Tier 3 failed -> Fallback to rock-solid stable Flash
            if stable_flash:
                next_model = stable_flash[0]
            elif flash:
                next_model = flash[-1]
                
        if not next_model:
            if curr in candidates:
                idx = candidates.index(curr)
                for offset in range(1, len(candidates)):
                    cand = candidates[(idx + offset) % len(candidates)]
                    if cand != curr:
                        next_model = cand
                        break
        if not next_model:
            next_model = [c for c in candidates if c != curr][0] if len(candidates) > 1 else candidates[0]
            
        print(f"🔄 Dynamic Model Rotation (Cross-Tier Escalation): {curr} ➡️ {next_model}")
        self.current_gemini_model = next_model
        return next_model

    async def rotate_gemini_model(self, failed_model=None):
        return await self.rotate_gemini_model_dynamic(failed_model)

    async def get_fallback_gemini_model(self):
        return await self.rotate_gemini_model_dynamic()

    async def call_gemini_content(self, payload, timeout=7, max_retries=6):
        """
        Central resilient caller for Google Gemini with Dynamic Model Rotation:
        - Defaults to latest flash-lite in memory for every new request.
        - If it fails, queries Google API live for the latest models:
          Priority 1: latest flash-lite
          Priority 2: latest flash
          Priority 3: latest pro
        - Keeps the latest working flash-lite in memory for future requests.
        """
        active_key = self.get_gemini_api_key()
        if not active_key:
            return None

        if not hasattr(self, 'gemini_lock'):
            self.gemini_lock = asyncio.Lock()
        if not hasattr(self, 'last_gemini_call_time'):
            self.last_gemini_call_time = 0.0

        # Ensure relaxed safety settings so defense/war/security/nuclear news is never blocked by filters
        payload.setdefault("safetySettings", [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ])
            
        # Every new request defaults to the latest flash-lite stored in memory
        model = getattr(self, 'latest_flash_lite_model', None) or getattr(self, 'current_gemini_model', DEFAULT_GEMINI_MODEL)
        
        for attempt in range(max_retries):
            # Special escalation: on attempt 2, immediately use high-quota rock-solid production model
            if attempt == 2:
                model = "gemini-1.5-flash"
            elif attempt == 3:
                model = "gemini-1.5-flash-8b"
            elif attempt == 4:
                model = "gemini-2.5-flash"
            elif attempt == 5:
                model = "gemini-1.5-pro"
                
            cur_key = self.get_gemini_api_key()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={cur_key}"
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
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        return json.loads(response.read().decode('utf-8'))
                        
                # Strictly serialize and rate-limit HTTP requests to Google API across all tasks
                async with self.gemini_lock:
                    gap = time.time() - self.last_gemini_call_time
                    if gap < 2.0:
                        await asyncio.sleep(2.0 - gap)
                    self.last_gemini_call_time = time.time()
                    data = await loop.run_in_executor(None, make_req)
                    
                candidates = data.get("candidates", [])
                raw_text = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    raw_text = "\n".join([p.get("text", "") for p in parts if p.get("text")]).strip()

                if raw_text:
                    self.current_gemini_model = model
                    if "flash-lite" in model or "flash_lite" in model:
                        self.latest_flash_lite_model = model
                    return raw_text
                
                finish_reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
                print(f"⚠️ Gemini empty/blocked response from model {model} (finishReason: {finish_reason}). Attempt {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    model = await self.rotate_gemini_model_dynamic(failed_model=model)
                else:
                    break
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode('utf-8', errors='ignore')
                except Exception:
                    pass
                print(f"⚠️ Gemini API HTTP Error {e.code} ({e.reason}) on model {model} (Attempt {attempt+1}/{max_retries}): {err_body[:200]}")
                if e.code == 429 and "quota" in err_body.lower():
                    self.rotate_gemini_api_key()
                if attempt < max_retries - 1:
                    model = await self.rotate_gemini_model_dynamic(failed_model=model)
                    if e.code in (429, 503):
                        await asyncio.sleep(1.5)
                else:
                    break
            except Exception as e:
                print(f"⚠️ Gemini API error on model {model}: {e} (Attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    model = await self.rotate_gemini_model_dynamic(failed_model=model)
                else:
                    break
                    
        return None

    async def append_ai_summary(self, target_channel, message_id, original_text, raw_texts):
        if not GEMINI_API_KEY:
            return
            
        print(f"🤖 Starting AI summary injection (Default model: {getattr(self, 'latest_flash_lite_model', 'gemini-2.0-flash-lite')})")
            
        prompt = (
            "تو دستیار ارشد و همکار هوشمند تحریریه دیده‌بان هستی. "
            "متن خبرهای زیر از منابع مختلف جمع‌آوری شده است. "
            "لطفاً یک چکیده شفاف، دقیق، بی‌طرفانه و شیوا از رویداد بنویس. "
            "اولویت اصلی ایجاز و خلاصه‌سازی در کنار بیان تمام نکات مهم و در صورت لزوم ابهام‌زدایی از رویداد است. "
            "هیچگونه تیتر، مقدمه، سلام، هشتگ یا توضیحات اضافه‌ای ننویس و منحصراً خودِ متن چکیده را ارائه بده.\n\nاخبار:\n"
            + "\n---\n".join(raw_texts)
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 300,
                "temperature": 0.25
            }
        }
        
        summary = await self.call_gemini_content(payload, timeout=14, max_retries=5)
        if summary:
            delimiter = "🔗 **منابع خبر:**" if "🔗 **منابع خبر:**" in original_text else "🔗 **منبع خبر:**"
            parts = original_text.split(delimiter)
            if len(parts) == 2:
                clean_part0 = re.sub(r'⚡️\s*سرعت انتشار[^\n]*\n+', '', parts[0])
                clean_part0 = re.sub(r'📊\s*وضعیت عادی[^\n]*\n+', '', clean_part0)
                clean_part0 = clean_part0.rstrip() + "\n\n"
                new_text = f"{clean_part0}📝 **چکیده رویداد:**\n{summary}\n\n{delimiter}{parts[1]}"
                try:
                    await self.bot.edit_message(target_channel, message_id, new_text, link_preview=False)
                    for tk, td in self.thread_alerts.items():
                        if td.get('alert_msg_id') == message_id and td.get('channel') == target_channel:
                            td['summary'] = summary
                            break
                    self.save_persisted_state()
                    print(f"✅ AI Summary added to message {message_id} in {target_channel} (Model: {self.current_gemini_model})")
                except Exception as edit_err:
                    print(f"⚠️ Failed to edit AI summary message: {edit_err}")

    def generate_heuristic_summary(self, text, channel_name=""):
        clean = (text or "").strip()
        # 1. Advisory: Avoiding military / nuclear / defense facilities
        is_military_nuclear = any(k in clean for k in ['مراکز نظامی', 'تاسیسات هسته‌ای', 'پایگاه‌های نظامی', 'پایگاه هوایی', 'سایت موشکی', 'پایگاه موشکی', 'اهداف نظامی'])
        is_avoidance = any(k in clean for k in ['اجتناب بفرمایید', 'اجتناب کنید', 'دوری کنید', 'پرهیز کنید', 'نزدیک نشوید', 'فاصله بگیرید'])
        is_shelter = any(k in clean for k in ['پناهگاه', 'پناه بگیرید', 'تخلیه', 'محل امن'])
        
        if is_avoidance and is_military_nuclear:
            return {
                "title": "⚠️ توصیه امنیتی و هشدار دوری از مراکز حساس نظامی و هسته‌ای",
                "summary": "با توجه به تشدید تنش‌های منطقه‌ای و احتمال وقوع حملات هوایی و موشکی به اهداف راهبردی در روزهای پیش‌رو، این توصیه حفاظتی به منظور پیشگیری از تلفات غیرنظامی و در امان ماندن شهروندان از خطرات مستقیم و ترکش در مجاورت مراکز نظامی و تاسیسات اتمی مطرح شده است.",
                "is_ai": False
            }
        elif is_shelter:
            return {
                "title": "⚠️ هشدار اضطراری و توصیه به استقرار در نقاط امن",
                "summary": "با توجه به وضعیت بحرانی و احتمال درگیری‌های هوایی یا موشکی، هشدارهای حفاظتی لازم جهت حفظ جان شهروندان و استقرار در پناهگاه‌ها یا نقاط امن صادر شده است.",
                "is_ai": False
            }
            
        lines = [l.strip() for l in clean.split('\n') if l.strip()]
        first_line = lines[0] if lines else clean
        title = first_line[:50] + "..." if len(first_line) > 50 else first_line
        title = re.sub(r'^[⚡️🚨⚠️🔴📌]+', '', title).strip()
        
        return {
            "title": title or "چکیده رویداد",
            "summary": clean[:300] + "..." if len(clean) > 300 else clean,
            "is_ai": False
        }

    async def summarize_channel_post(self, text, channel_name="وحیدآنلاین", bot_name="وحیدآنلاین هوشمند (VahidOnlineAI)"):
        if not self.get_gemini_api_key():
            return self.generate_heuristic_summary(text, channel_name)
            
        prompt = (
            f"تو دستیار، تحلیلگر و خلاصه‌ساز هوشمند خبر برای کانال تلگرام '{bot_name}' هستی.\n"
            f"پست زیر از کانال {channel_name} منتشر شده است.\n\n"
            "وظیفه تو این است که دو فیلد هوشمندانه، دقیق، بدون قضاوت و با ارزش افزوده بالا برای مخاطب تولید کنی:\n"
            "۱. title: یک عنوان خبری یا هشداری بسیار جذاب، برجسته، صریح و کوتاه (حداکثر ۷ کلمه) که اصل ماهیت رویداد یا هشدار را بیان کند.\n"
            "۲. summary: \n"
            "   - اگر متن پست یک هشدار، توصیه امنیتی یا پیام کوتاه/فوری است: هرگز متن پست را صرفاً کپی یا بازنویسی سطحی نکن! "
            "بلکه مفهوم، ابعاد رویداد و دلیل اصلی این پیشنهاد یا هشدار را برای مخاطب به صورت هوشمندانه و تحلیلی شرح بده "
            "(مانند اشاره به افزایش تنش‌های نظامی، خطرات بالقوه ناشی از هدف قرار گرفتن مراکز دفاعی و تاسیسات اتمی در روزهای آتی، و دلایل لزوم رعایت هشدارهای حفاظتی توسط شهروندان).\n"
            "   - اگر متن پست یک گزارش خبری طولانی یا چندقسمتی است: یک چکیده دقیق، روان، رسا و منسجم در ۲ الی ۴ خط بنویس که جان کلام و آخرین وضعیت نهایی رویداد را منعکس کند.\n\n"
            "دستورالعمل‌های الزامی:\n"
            "- از تکرار سطحی و واژه به واژه متن اصلی اجتناب کن؛ وظیفه تو شرح و تبیین هوشمندانه است تا کاربر علت و اهمیت خبر را به وضوح درک کند.\n"
            "- هیچ مقدمه، سلام، توضیح فنی، هشتگ یا ایموجی اضافی قبل یا بعد از JSON ننویس.\n"
            "- پاسخ را دقیقاً و منحصراً در قالب یک شیء JSON با دو کلید 'title' و 'summary' بنویس:\n"
            "{\n"
            '  "title": "تیتر کوتاه و دقیق",\n'
            '  "summary": "شرح و چکیده هوشمندانه"\n'
            "}\n\n"
            f"متن پست:\n{text}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 350,
                "temperature": 0.2
            }
        }
        
        raw_out = await self.call_gemini_content(payload, timeout=8, max_retries=6)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                try:
                    res = json.loads(match.group(0))
                    title = res.get("title", "").strip()
                    summary = res.get("summary", "").strip()
                    if title and summary:
                        return {"title": title, "summary": summary, "is_ai": True}
                except Exception:
                    pass
                    
            lines = [l.strip() for l in raw_out.split('\n') if l.strip()]
            if len(lines) >= 2:
                return {"title": lines[0].replace('#', '').strip(), "summary": "\n".join(lines[1:]), "is_ai": True}
            return {"title": "تحلیل خبر", "summary": raw_out, "is_ai": True}
            
        return self.generate_heuristic_summary(text, channel_name)


    async def summarize_vahid_post(self, text):
        return await self.summarize_channel_post(text, channel_name="وحیدآنلاین", bot_name="وحیدآنلاین هوشمند (VahidOnlineAI)")

    async def summarize_ilia_post(self, text):
        return await self.summarize_channel_post(text, channel_name="ایلیا (کانال تحلیلی/امنیتی iliaen)", bot_name="ایلیا هوشمند (iliaenAI)")

    async def preload_vahid_ai_history(self, reader_client=None):
        target_channel = "@VahidOnlineAI"
        client_to_use = reader_client or self.bot
        try:
            count = 0
            async for msg in client_to_use.iter_messages(target_channel, limit=100):
                txt = msg.text or msg.message or ""
                urls = self.extract_message_urls(msg)
                for u in urls:
                    match = re.search(r'Vahid(?:Online|Headline|OOnLine)/(\d+)', u, re.IGNORECASE)
                    if match:
                        vahid_id = int(match.group(1))
                        if vahid_id not in self.vahid_ai_posted:
                            self.vahid_ai_posted[vahid_id] = msg.id
                            count += 1
                        self.record_posted_message(target_channel, "VahidOnline", vahid_id, msg.id)
                        if "خلاصه خبر" in txt and "⚡️" in txt:
                            self.vahid_needs_refresh.add((vahid_id, msg.id))
                        break
            if count > 0:
                print(f"🔄 Preloaded {count} historical posts for @VahidOnlineAI edit tracking.")
        except Exception as e:
            print(f"⚠️ Could not preload @VahidOnlineAI history: {e}")

    async def catchup_unposted_vahid(self, reader_client=None):
        if not reader_client:
            return
        try:
            # Fast scan of last 10 messages in @VahidOnlineAI to detect any fallback posts needing AI upgrade
            try:
                async for m in reader_client.iter_messages("@VahidOnlineAI", limit=10):
                    txt = m.text or m.message or ""
                    if "خلاصه خبر" in txt and "⚡️" in txt:
                        urls = self.extract_message_urls(m)
                        for u in urls:
                            match = re.search(r'Vahid(?:Online|Headline|OOnLine)/(\d+)', u, re.IGNORECASE)
                            if match:
                                src_id = int(match.group(1))
                                self.vahid_ai_posted[src_id] = m.id
                                self.record_posted_message("@VahidOnlineAI", "VahidOnline", src_id, m.id)
                                self.vahid_needs_refresh.add((src_id, m.id))
                                break
            except Exception as scan_err:
                print(f"⚠️ Quick scan @VahidOnlineAI failed: {scan_err}")

            now_utc = datetime.now(timezone.utc)
            recent_vahid = []
            async for msg in reader_client.iter_messages("VahidOnline", limit=5):
                if msg.date and (now_utc - msg.date).total_seconds() > 300:
                    continue
                recent_vahid.append(msg)
            recent_vahid.reverse()
            
            for msg in recent_vahid:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 5:
                    if msg.id not in self.vahid_ai_posted and not self.is_source_posted("@VahidOnlineAI", "VahidOnline", msg.id):
                        print(f"⚡ Catching up missed VahidOnline post {msg.id}...")
                        await self.handle_vahid_online_ai(clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
                    # Feed into process_message with skip_mirror_channels=True so it doesn't trigger handle_vahid_online_ai again
                    await self.process_message(clean_text, "VahidOnline", msg.id, msg_date=msg.date, raw_msg=msg, skip_mirror_channels=True)

            # Re-summarize any posts that previously fell back to generic "خلاصه خبر"
            for vahid_id, posted_id in list(self.vahid_needs_refresh):
                try:
                    orig_msgs = await reader_client.get_messages("VahidOnline", ids=[vahid_id])
                    if orig_msgs and orig_msgs[0]:
                        orig_m = orig_msgs[0]
                        clean_text = (orig_m.message or orig_m.text or "").strip()
                        if clean_text:
                            print(f"🔄 Upgrading fallback post {vahid_id} (msg {posted_id}) with full AI summary...")
                            upgraded_ok = await self.handle_vahid_online_ai(clean_text, vahid_id, raw_msg=orig_m, is_edit=False, is_upgrade=True)
                            if upgraded_ok:
                                self.vahid_needs_refresh.discard((vahid_id, posted_id))
                            await asyncio.sleep(4)
                except Exception as ref_err:
                    print(f"⚠️ Failed to upgrade fallback post {vahid_id}: {ref_err}")
        except Exception as e:
            print(f"⚠️ Catchup for VahidOnline failed: {e}")

    async def handle_mirror_channel(self, source_node, target_channel, text, msg_id, raw_msg=None, is_edit=False, is_upgrade=False):
        # 1. Update high-water mark
        if not is_edit:
            self.channel_watermarks[source_node] = max(self.channel_watermarks.get(source_node, 0), msg_id)

        # 2. Check persistent dedup guard before doing heavy AI work
        if not is_edit and not is_upgrade and self.is_source_posted(target_channel, source_node, msg_id):
            print(f"🛑 [DEDUP GUARD] {source_node} msg {msg_id} already posted to {target_channel}. Aborting send.")
            return

        # 3. Deduplication for Album / Media Groups (grouped_id)
        grouped_set = self.vahid_grouped_posted if source_node == 'VahidOnline' else self.ilia_grouped_posted
        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in grouped_set and not is_edit:
            return
            
        clean_text = (text or "").strip()
        # 4. Strict Filter: Never post media with empty or meaningless text
        if not clean_text or len(clean_text) < 5:
            return
            
        if grouped_id:
            grouped_set.add(grouped_id)
            
        # 5. Sequential processing per msg_id
        msg_locks = self.vahid_msg_locks if source_node == 'VahidOnline' else self.ilia_msg_locks
        if msg_id not in msg_locks:
            if len(msg_locks) > 200:
                msg_locks.clear()
            msg_locks[msg_id] = asyncio.Lock()
            
        posted_dict = self.vahid_ai_posted if source_node == 'VahidOnline' else self.ilia_ai_posted
        post_time = self.vahid_post_time if source_node == 'VahidOnline' else self.ilia_post_time
        channel_fa_name = "وحیدآنلاین" if source_node == 'VahidOnline' else "ایلیا"
        
        async with msg_locks[msg_id]:
            # Safeguard: if edit of ancient post (>2h old) not tracked, ignore
            if is_edit and not is_upgrade and msg_id not in posted_dict:
                orig_date = getattr(raw_msg, 'date', None)
                if orig_date:
                    now_utc = datetime.now(timezone.utc)
                    if (now_utc - orig_date).total_seconds() > 7200:
                        print(f"⏩ Skipped editing ancient post {msg_id} in {source_node} (>2h old and not tracked)")
                        return False

            if source_node == 'VahidOnline':
                res = await self.summarize_vahid_post(clean_text)
            else:
                res = await self.summarize_ilia_post(clean_text)
                
            title = res.get('title', 'چکیده خبر').strip()
            summary = res.get('summary', clean_text).strip()
            is_ai = res.get('is_ai', False)
            
            if is_upgrade and not is_ai and (title == "خلاصه خبر" or summary == clean_text):
                print(f"⏩ Upgrade deferred for {source_node} msg {msg_id}: no AI summary available.")
                return False
                
            link = f"https://t.me/{source_node}/{msg_id}"
            
            # Detect media type from raw_msg
            media_icon = ""
            link_action = f"مشاهده پست اصلی در کانال {channel_fa_name}"
            if raw_msg:
                if getattr(raw_msg, 'video', None):
                    media_icon = " 📹"
                    link_action = f"مشاهده ویدیو در کانال {channel_fa_name}"
                elif getattr(raw_msg, 'photo', None):
                    media_icon = " 📸"
                    link_action = f"مشاهده تصویر در کانال {channel_fa_name}"
                elif getattr(raw_msg, 'voice', None) or getattr(raw_msg, 'audio', None):
                    media_icon = " 🎙"
                    link_action = f"شنیدن فایل صوتی در کانال {channel_fa_name}"
                    
            if is_edit and not is_upgrade:
                time_fa = self.get_tehran_time_fa()
                post_content = (
                    f"⚡️ **{title}**{media_icon}\n\n"
                    f"{summary}\n\n"
                    f"🔗 [{link_action}]({link})\n\n"
                    f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و تکمیل متن خبر**\n\n"
                    f"📡 {target_channel}"
                )
            else:
                post_content = (
                    f"⚡️ **{title}**{media_icon}\n\n"
                    f"{summary}\n\n"
                    f"🔗 [{link_action}]({link})\n\n"
                    f"📡 {target_channel}"
                )
                
            try:
                if msg_id in posted_dict:
                    posted_id = posted_dict[msg_id]
                    try:
                        await self.bot.edit_message(target_channel, posted_id, post_content, link_preview=False)
                        print(f"✏️ Edited post {posted_id} in {target_channel} for {source_node} msg {msg_id}.")
                        return is_ai
                    except Exception as edit_err:
                        if "not modified" in str(edit_err).lower():
                            print(f"ℹ️ Post {posted_id} in {target_channel} is already up to date (no content change).")
                            return is_ai
                        else:
                            raise edit_err
                else:
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
                        posted_dict[msg_id] = sent.id
                        post_time[msg_id] = time.time()
                        self.channel_last_alert_id[target_channel] = sent.id
                        self.record_posted_message(target_channel, source_node, msg_id, sent.id)
                        self.save_persisted_state()
                        print(f"🚀 Published AI summary to {target_channel} for {source_node} msg {msg_id}")
            except Exception as e:
                print(f"❌ Error publishing to {target_channel} for {source_node} msg {msg_id}: {e}")

    async def handle_vahid_online_ai(self, text, msg_id, raw_msg=None, is_edit=False, is_upgrade=False):
        return await self.handle_mirror_channel("VahidOnline", "@VahidOnlineAI", text, msg_id, raw_msg=raw_msg, is_edit=is_edit, is_upgrade=is_upgrade)

    async def preload_ilia_ai_history(self, reader_client=None):
        target_channel = "@iliaenAI"
        client_to_use = reader_client or self.bot
        try:
            count = 0
            async for msg in client_to_use.iter_messages(target_channel, limit=100):
                txt = msg.text or msg.message or ""
                urls = self.extract_message_urls(msg)
                for u in urls:
                    match = re.search(r'iliaen/(\d+)', u, re.IGNORECASE)
                    if match:
                        ilia_id = int(match.group(1))
                        if ilia_id not in self.ilia_ai_posted:
                            self.ilia_ai_posted[ilia_id] = msg.id
                            count += 1
                        self.record_posted_message(target_channel, "iliaen", ilia_id, msg.id)
                        if "خلاصه خبر" in txt and "⚡️" in txt:
                            self.ilia_needs_refresh.add((ilia_id, msg.id))
                        break
            if count > 0:
                print(f"🔄 Preloaded {count} historical posts for @iliaenAI edit tracking.")
        except Exception as e:
            print(f"⚠️ Could not preload @iliaenAI history: {e}")

    async def catchup_unposted_ilia(self, reader_client=None):
        if not reader_client:
            return
        try:
            # Fast scan of last 10 messages in @iliaenAI to detect any fallback posts needing AI upgrade
            try:
                async for m in reader_client.iter_messages("@iliaenAI", limit=10):
                    txt = m.text or m.message or ""
                    if "خلاصه خبر" in txt and "⚡️" in txt:
                        urls = self.extract_message_urls(m)
                        for u in urls:
                            match = re.search(r'iliaen/(\d+)', u, re.IGNORECASE)
                            if match:
                                src_id = int(match.group(1))
                                self.ilia_ai_posted[src_id] = m.id
                                self.record_posted_message("@iliaenAI", "iliaen", src_id, m.id)
                                self.ilia_needs_refresh.add((src_id, m.id))
                                break
            except Exception as scan_err:
                print(f"⚠️ Quick scan @iliaenAI failed: {scan_err}")

            now_utc = datetime.now(timezone.utc)
            recent_ilia = []
            async for msg in reader_client.iter_messages("iliaen", limit=5):
                if msg.date and (now_utc - msg.date).total_seconds() > 300:
                    continue
                recent_ilia.append(msg)
            recent_ilia.reverse()
            
            for msg in recent_ilia:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 5:
                    if msg.id not in self.ilia_ai_posted and not self.is_source_posted("@iliaenAI", "iliaen", msg.id):
                        print(f"⚡ Catching up missed iliaen post {msg.id}...")
                        await self.handle_ilia_ai(clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
                    # Feed into process_message with skip_mirror_channels=True so it doesn't trigger handle_ilia_ai again
                    await self.process_message(clean_text, "iliaen", msg.id, msg_date=msg.date, raw_msg=msg, skip_mirror_channels=True)

            # Re-summarize any fallback posts
            for ilia_id, posted_id in list(self.ilia_needs_refresh):
                try:
                    orig_msgs = await reader_client.get_messages("iliaen", ids=[ilia_id])
                    if orig_msgs and orig_msgs[0]:
                        orig_m = orig_msgs[0]
                        clean_text = (orig_m.message or orig_m.text or "").strip()
                        if clean_text:
                            print(f"🔄 Upgrading fallback post {ilia_id} (msg {posted_id}) with full AI summary...")
                            upgraded_ok = await self.handle_ilia_ai(clean_text, ilia_id, raw_msg=orig_m, is_edit=False, is_upgrade=True)
                            if upgraded_ok:
                                self.ilia_needs_refresh.discard((ilia_id, posted_id))
                            await asyncio.sleep(4)
                except Exception as ref_err:
                    print(f"⚠️ Failed to upgrade fallback post {ilia_id}: {ref_err}")
        except Exception as e:
            print(f"⚠️ Catchup for iliaen failed: {e}")

    async def handle_ilia_ai(self, text, msg_id, raw_msg=None, is_edit=False, is_upgrade=False):
        return await self.handle_mirror_channel("iliaen", "@iliaenAI", text, msg_id, raw_msg=raw_msg, is_edit=is_edit, is_upgrade=is_upgrade)

    async def classify_and_translate_alibk(self, arabic_text):
        if not GEMINI_API_KEY:
            return None
            
        if not hasattr(self, 'current_gemini_model'):
            self.current_gemini_model = DEFAULT_GEMINI_MODEL
            
        prompt = (
            "تو یک تحلیلگر ارشد تحولات نظامی و امنیتی خاورمیانه و مترجم حرفه‌ای برای سامانه دیده‌بان جنگ (پایش فوری اخبار تلگرام) هستی.\n"
            "پست زیر به زبان عربی از کانال خبری 'Ali Bk' (پوشش‌دهنده رویدادهای نظامی و امنیتی منطقه مانند یمن، عربستان، خلیج فارس، دریای سرخ، اسرائیل، لبنان، عراق و سوریه) منتشر شده است.\n\n"
            "وظیفه تو تحلیل رویداد و انطباق با سیاست‌های خبری ما است:\n\n"
            "۱. دسته‌بندی و سیاست خبری (category):\n"
            "   - WAR: اخبار جنگ، حملات موشکی، پهپادی یا راکتی، بمباران هوایی، شلیک پدافند، درگیری‌های مسلحانه، توقف پروازها یا ناوبری دریایی، هلاکت یا زخمی شدن فرماندهان و نظامیان، آژیر خطر، انفجارها، آتش‌سوزی یا آسیب به تاسیسات نفتی/گازی و پالایشگاهی (نظیر آرامکو، مخازن نفت، لوله‌ها، کارخانه‌های گاز، نفتکش‌ها) و پیشروی‌های نظامی.\n"
            "   - OTHER: هرگونه خبر یا محتوای دیگری که به طور مستقیم یک حادثه جنگی، حمله، درگیری یا اصابت امنیتی/نظامی نیست (مانند نظرات و تحلیل‌های شخصی، اخبار عمومی/فرهنگی، احوال‌پرسی، شوخی/میم، اخبار عادی آب‌وهوا، گزارش‌های اقتصادی غیرنظامی).\n\n"
            "۲. تشخیص ارتباط مستقیم با ایران (is_iran_related):\n"
            "   - مقدار true: اگر خبر مستقیماً به ایران، خاک ایران، اهداف و پایگاه‌های نظامی ایران، نیروهای مسلح ایران (سپاه، ارتش، پدافند)، مقامات و رهبران ایران، یا درگیری مستقیم و پاسخ‌های موشکی/نظامی ایران مربوط است.\n"
            "   - مقدار false: اگر رویداد بین سایر طرف‌ها و کشورهای منطقه است و به طور مستقیم به ایران مربوط نمی‌شود (مانند درگیری نیروهای یمن با عربستان یا آمریکا، حملات به آرامکو، درگیری‌های داخلی سوریه یا عراق، درگیری‌های اسرائیل در غزه و لبنان بدون حمله مستقیم به ایران).\n\n"
            "۳. قوانین تولید تیتر و چکیده:\n"
            "   - اگر category برابر OTHER است، فیلدهای title و summary خالی باشند.\n"
            "   - اگر category برابر WAR است، با تکیه بر دانش ژورنالیستی و زبانی خودت دو فیلد زیر را به زبان فارسی تولید کن:\n"
            "     * title: عنوان و تیتر رسا، جذاب و صریح به زبان فارسی همراه با فعل کامل که اصل اتفاق را بگوید.\n"
            "     * summary: چکیده روان، گویا و منسجم به زبان فارسی با اولویت ایجاز و کوتاه‌سازی در کنار پوشش ابعاد کلیدی رویداد؛ در صورت وجود ابهام، نکات تکمیلی را شفاف کن.\n\n"
            f"متن پیام به زبان عربی:\n{arabic_text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب این شیء JSON ارسال کن و هیچ عبارت دیگری ننویس:\n"
            "{\n"
            '  "category": "WAR" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "title": "عنوان رسا و کامل فارسی",\n'
            '  "summary": "چکیده شیوا و ابهام‌زدایی‌شده به زبان فارسی"\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 400,
                "temperature": 0.2
            }
        }
        
        raw_out = await self.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
                cat = str(res.get("category", "")).upper().strip()
                is_iran = bool(res.get("is_iran_related", False))
                title = str(res.get("title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                if cat in ["WAR", "OTHER"]:
                    return {"category": cat, "is_iran_related": is_iran, "title": title, "summary": summary}
        return None

    async def classify_and_translate_alibk_thread(self, thread_text):
        if not GEMINI_API_KEY:
            return None
            
        prompt = (
            "تو یک تحلیلگر ارشد تحولات نظامی و امنیتی خاورمیانه و مترجم حرفه‌ای برای سامانه دیده‌بان جنگ هستی.\n"
            "در زیر یک خبر اصلی و یک یا چند گزارش تکمیلی پیوست‌شده که به آن ریپلای یا ویرایش شده‌اند قرار دارد.\n"
            "این پیام‌ها سیر تحولات یک رویداد خبری مشترک را نشان می‌دهند.\n\n"
            "وظایف الزامی تو:\n"
            "۱. تمام متن‌ها را به دقت تحلیل کن و با تلفیق خبر اصلی و گزارش‌های تکمیلی، آخرین وضعیت و جمع‌بندی کامل رویداد را استخراج کن.\n"
            "۲. category: دسته‌بندی را مشخص کن (تنها دسته‌های مجاز: WAR یا OTHER):\n"
            "   - WAR: اخبار جنگ، حملات موشکی/پهپادی، بمباران هوایی، شلیک پدافند، درگیری‌های مسلحانه، توقف پروازها یا ناوبری، تلفات و خسارات، آتش‌سوزی یا آسیب به تاسیسات نفتی/گازی و بندری/فرودگاهی و آرامکو.\n"
            "   - OTHER: نظرات شخصی، میم/شوخی، تحلیل‌های بدون رویداد نظامی جدید، اخبار آب‌وهوا.\n"
            "۳. is_iran_related: اگر رویداد مستقیماً به ایران، خاک ایران، سپاه یا ارتش ایران مربوط است true وگرنه false.\n"
            "۴. title: یک تیتر رسا، جذاب و صریح به زبان فارسی همراه با فعل کامل که جدیدترین وضعیت نهایی رویداد را بازتاب دهد.\n"
            "۵. summary: یک چکیده روان، منسجم و گویا به زبان فارسی با اولویت ایجاز و خلاصه‌سازی در کنار پوشش کامل نکات جدید اضافه شده؛ در صورت نیاز به توضیح تکمیلی یا رفع ابهام، با دانش خودت آن را شفاف کن.\n\n"
            f"متن رویداد و گزارش‌های تکمیلی:\n{thread_text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب این شیء JSON ارسال کن و هیچ عبارت دیگری ننویس:\n"
            "{\n"
            '  "category": "WAR" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "title": "عنوان رسا و دقیق فارسی",\n'
            '  "summary": "چکیده منسجم به زبان فارسی"\n'
            "}"
        )
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 450,
                "temperature": 0.2
            }
        }
        
        raw_out = await self.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
                cat = str(res.get("category", "")).upper().strip()
                is_iran = bool(res.get("is_iran_related", False))
                title = str(res.get("title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                if cat in ["WAR", "OTHER"]:
                    return {"category": cat, "is_iran_related": is_iran, "title": title, "summary": summary}
        return None


    async def preload_alibk_history(self, reader_client=None):
        if not reader_client:
            return
        channels_to_preload = [
            ('@DidebanJang', '#دیده‌بان_جنگ', 'WAR'),
            ('@DidehbanEghtesad', '#دیده‌بان_اقتصاد', 'ECONOMY'),
            ('@DidebanEterazat', '#دیده‌بان_اعتراضات', 'PROTEST_RIGHTS')
        ]
        try:
            total_loaded = 0
            for target_ch, default_tag, default_cat in channels_to_preload:
                try:
                    count = 0
                    async for msg in reader_client.iter_messages(target_ch, limit=80):
                        text = msg.text or msg.message or ""
                        if not text:
                            continue
                            
                        # Extract title
                        m_title = re.search(r'\*\*(?:🚨|🔕|📈|⚖️|🛑|📢)?\s*(?:هشدار فوری|هشدار منطقه‌ای|گزارش اقتصادی|گزارش حقوق بشری|گزارش مردمی):\s*([^*📹📸🎙\n]+)', text)
                        title = m_title.group(1).strip() if m_title else ""
                        
                        # Extract summary
                        m_sum = re.search(r'(?:📝|🤖)?\s*\*\*(?:چکیده رویداد|چکیده هوشمند):\*\*\n([^\n]+(?:\n[^\n]+)?)', text)
                        summary = m_sum.group(1).strip() if m_sum else ""
                        
                        # Extract sources: - [Channel](https://t.me/Channel/123)
                        src_matches = re.findall(r'- \[([^\]]+)\]\(https://t\.me/([^/]+)/(\d+)\)', text)
                        if src_matches:
                            first_label, first_node, first_id_str = src_matches[0]
                            root_id = int(first_id_str)
                            thread_key = f"{first_node}_{root_id}"
                            
                            is_iran = "🚨" in text or "هشدار فوری" in text
                            sources_list = []
                            for s_label, s_node, s_mid in src_matches:
                                mid_int = int(s_mid)
                                sources_list.append({
                                    'node': s_node,
                                    'msg_id': mid_int,
                                    'media_type': 'video' if '📹' in text else ('photo' if '📸' in text else 'text'),
                                    'link': f"https://t.me/{s_node}/{s_mid}",
                                    'timestamp': msg.date.timestamp() if msg.date else time.time()
                                })
                                if s_node == 'Alibk3':
                                    self.alibk_posted[mid_int] = {
                                        'channel': target_ch,
                                        'msg_id': msg.id,
                                        'timestamp': msg.date.timestamp() if msg.date else time.time()
                                    }
                                    if mid_int != root_id:
                                        self.reply_to_root_map[f"Alibk3_{mid_int}"] = root_id
                                self.record_posted_message(target_ch, s_node, mid_int, msg.id)

                            # Filter out dummy/radar nodes
                            sources_list = [s for s in sources_list if str(s.get('node')).lower() not in ('radar', 'unknown', 'sentinel')]
                            
                            # Auto-clean legacy [radar] line from channel message if present
                            if '- [radar](' in text:
                                clean_text_no_radar = re.sub(r'\n?- \[radar\]\([^\)]+\)\s*[^\n]*', '', text).strip()
                                if len(sources_list) <= 1:
                                    clean_text_no_radar = clean_text_no_radar.replace('🔗 **منابع خبر:**', '🔗 **منبع خبر:**')
                                try:
                                    await self.bot.edit_message(target_ch, msg.id, clean_text_no_radar, link_preview=False)
                                    print(f"🧹 Auto-cleaned legacy 'radar' artifact in {target_ch} msg {msg.id}")
                                except Exception as clean_err:
                                    print(f"⚠️ Could not auto-clean radar artifact: {clean_err}")

                            if thread_key not in self.thread_alerts:
                                self.thread_alerts[thread_key] = {
                                    'channel': target_ch,
                                    'alert_msg_id': msg.id,
                                    'node': first_node,
                                    'root_id': root_id,
                                    'root_text': '',
                                    'root_media_type': 'text',
                                    'timestamp': msg.date.timestamp() if msg.date else time.time(),
                                    'category': default_cat,
                                    'is_iran_related': is_iran,
                                    'title': title,
                                    'summary': summary,
                                    'sources': sources_list,
                                    'replies': [],
                                    'tag': default_tag
                                }
                                count += 1
                    if count > 0:
                        total_loaded += count
                        print(f"🔄 Preloaded {count} historical alerts and thread trees from {target_ch}.")
                except Exception as ch_err:
                    print(f"⚠️ Could not preload history from {target_ch}: {ch_err}")
                    
            if total_loaded > 0:
                print(f"✅ Total historical alerts preloaded: {total_loaded}.")
        except Exception as e:
            print(f"⚠️ Could not preload channel alert history: {e}")

    async def catchup_unposted_alibk(self, reader_client=None):
        if not reader_client:
            return
        try:
            now_utc = datetime.now(timezone.utc)
            recent_alibk = []
            async for msg in reader_client.iter_messages("Alibk3", limit=20):
                if msg.date and (now_utc - msg.date).total_seconds() > 7200:
                    continue
                recent_alibk.append(msg)
            recent_alibk.reverse()
            
            for msg in recent_alibk:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 10:
                    await self.handle_alibk_regional(clean_text, msg.id, raw_msg=msg, is_edit=False)
                    await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Catchup for Alibk3 failed: {e}")

    async def handle_alibk_regional(self, text, msg_id, raw_msg=None, is_edit=False):
        if not is_edit:
            self.channel_watermarks["Alibk3"] = max(self.channel_watermarks.get("Alibk3", 0), msg_id)
        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 10:
            return

        # 1. Deduplication for Album / Media Groups (grouped_id)
        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in self.alibk_grouped_posted and not is_edit:
            return
        if grouped_id:
            self.alibk_grouped_posted.add(grouped_id)

        # 2. Check if this is a reply (supplementary follow-up to an earlier root post)
        is_reply = bool(raw_msg and (getattr(raw_msg, 'is_reply', False) or getattr(raw_msg, 'reply_to_msg_id', None) or getattr(raw_msg, 'reply_to', None)))

        if is_reply:
            root_id, chain = await self.find_root_message("Alibk3", raw_msg)
            thread_key = f"Alibk3_{root_id}" if root_id else None

            # If root message was alerted in @DidebanJang (within 72h window)
            if thread_key and (thread_key in self.thread_alerts or root_id in self.alibk_posted):
                if thread_key not in self.thread_alerts and root_id in self.alibk_posted:
                    posted_info = self.alibk_posted[root_id]
                    self.thread_alerts[thread_key] = {
                        'channel': posted_info.get('channel', '@DidebanJang'),
                        'alert_msg_id': posted_info.get('msg_id'),
                        'node': 'Alibk3',
                        'root_id': root_id,
                        'root_text': '',
                        'root_media_type': 'text',
                        'timestamp': time.time(),
                        'replies': [],
                        'tag': '#دیده‌بان_جنگ'
                    }

                thread_data = self.thread_alerts[thread_key]
                media_type = self.detect_msg_media_type(raw_msg)

                existing_rep = next((r for r in thread_data.get('replies', []) if r.get('msg_id') == msg_id), None)
                if existing_rep:
                    existing_rep['text'] = clean_text
                    existing_rep['media_type'] = media_type
                else:
                    thread_data.setdefault('replies', []).append({
                        'msg_id': msg_id,
                        'text': clean_text,
                        'media_type': media_type,
                        'timestamp': time.time()
                    })

                # Also sync sources list
                sources = thread_data.setdefault('sources', [])
                if not sources:
                    sources.append({
                        'node': 'Alibk3',
                        'msg_id': root_id,
                        'media_type': thread_data.get('root_media_type', 'text'),
                        'link': f"https://t.me/Alibk3/{root_id}",
                        'timestamp': thread_data.get('timestamp', time.time())
                    })
                existing_src = next((s for s in sources if s.get('node') == 'Alibk3' and s.get('msg_id') == msg_id), None)
                if existing_src:
                    existing_src['media_type'] = media_type
                else:
                    sources.append({
                        'node': 'Alibk3',
                        'msg_id': msg_id,
                        'media_type': media_type,
                        'link': f"https://t.me/Alibk3/{msg_id}",
                        'timestamp': time.time()
                    })

                self.reply_to_root_map[f"Alibk3_{msg_id}"] = root_id
                for cid in chain:
                    self.reply_to_root_map[f"Alibk3_{cid}"] = root_id

                print(f"🔗 Attached reply {msg_id} (media: {media_type}) to Alibk3 root post {root_id}. Scheduled 5m debounce...")
                self.schedule_thread_debounce(thread_key)
                self.save_persisted_state()
                return
            else:
                print(f"⏩ Alibk3 reply {msg_id} refers to root {root_id} which was not alerted. Skipping.")
                return

        # 3. Check if this is an edit of an existing root or reply
        thread_key = f"Alibk3_{msg_id}"
        if is_edit and (thread_key in self.thread_alerts or msg_id in self.alibk_posted or str(msg_id) in self.alibk_posted):
            if thread_key not in self.thread_alerts:
                posted_info = self.alibk_posted.get(msg_id) or self.alibk_posted.get(str(msg_id), {})
                self.thread_alerts[thread_key] = {
                    'channel': posted_info.get('channel', '@DidebanJang'),
                    'alert_msg_id': posted_info.get('msg_id'),
                    'node': 'Alibk3',
                    'root_id': msg_id,
                    'root_text': clean_text,
                    'root_media_type': self.detect_msg_media_type(raw_msg),
                    'timestamp': posted_info.get('timestamp', time.time()),
                    'replies': [],
                    'tag': '#دیده‌بان_جنگ'
                }
            thread_data = self.thread_alerts[thread_key]
            thread_data['root_text'] = clean_text
            thread_data['root_media_type'] = self.detect_msg_media_type(raw_msg)
            print(f"✏️ Alibk3 root post {msg_id} was edited. Scheduling 5m debounce...")
            self.schedule_thread_debounce(thread_key)
            self.save_persisted_state()
            return

        if is_edit and f"Alibk3_{msg_id}" in self.reply_to_root_map:
            root_id = self.reply_to_root_map[f"Alibk3_{msg_id}"]
            rk = f"Alibk3_{root_id}"
            if rk in self.thread_alerts:
                thread_data = self.thread_alerts[rk]
                existing_rep = next((r for r in thread_data.get('replies', []) if r.get('msg_id') == msg_id), None)
                if existing_rep:
                    existing_rep['text'] = clean_text
                    existing_rep['media_type'] = self.detect_msg_media_type(raw_msg)
                print(f"✏️ Alibk3 reply {msg_id} was edited. Scheduling 5m debounce for root {root_id}...")
                self.schedule_thread_debounce(rk)
                self.save_persisted_state()
                return

        # 4. Sequential lock per msg_id for standalone root processing
        if msg_id not in self.alibk_msg_locks:
            if len(self.alibk_msg_locks) > 200:
                self.alibk_msg_locks.clear()
            self.alibk_msg_locks[msg_id] = asyncio.Lock()

        async with self.alibk_msg_locks[msg_id]:
            is_already_posted = msg_id in self.alibk_posted
            if is_already_posted and not is_edit:
                return

            # AI Classifier & Persian Title/Summary
            ai_res = await self.classify_and_translate_alibk(clean_text)
            if not ai_res:
                print(f"⚠️ Alibk3 AI processing skipped/failed for msg {msg_id}")
                return

            category = ai_res.get('category', 'OTHER')
            if category == 'OTHER':
                print(f"ℹ️ Alibk3 post {msg_id} filtered out by news policy (OTHER).")
                return

            title = ai_res.get('title', '').strip()
            summary = ai_res.get('summary', '').strip()
            if not title or not summary:
                return

            # Check if Iran-related (Sound vs Silent alert)
            is_iran_related = self.determine_iran_related(ai_res, clean_text, title)
            is_silent = not is_iran_related

            # Target channel routing (Alibk3 is exclusively military/war -> @DidebanJang)
            if category != 'WAR':
                print(f"ℹ️ Alibk3 post {msg_id} filtered out by news policy (category={category}).")
                return

            target_channel = "@DidebanJang"
            tag = "#دیده‌بان_جنگ"
            link = f"https://t.me/Alibk3/{msg_id}"

            root_media_type = self.detect_msg_media_type(raw_msg)
            root_icon = self.get_media_icon_from_type(root_media_type)

            icon_header = ""
            if root_media_type == 'video': icon_header = " 📹"
            elif root_media_type == 'photo': icon_header = " 📸"
            elif root_media_type == 'voice': icon_header = " 🎙"

            if is_iran_related:
                alert_header = f"🚨 **هشدار فوری: {title}**{icon_header}"
            else:
                alert_header = f"🔕 **هشدار منطقه‌ای: {title}**{icon_header}"

            sources_text = f"🔗 **منبع خبر:**\n- [Alibk3]({link}) {root_icon}"
            alert_text = (
                f"{alert_header}\n\n"
                f"📝 **چکیده رویداد:**\n{summary}\n\n"
                f"{sources_text}\n\n"
                f"{tag}\n{target_channel}"
            )

            try:
                if is_already_posted:
                    posted_info = self.alibk_posted[msg_id]
                    posted_ch = posted_info.get('channel', target_channel)
                    posted_mid = posted_info.get('msg_id')
                    
                    thread_key = f"Alibk3_{msg_id}"
                    if thread_key in self.thread_alerts:
                        thread_data = self.thread_alerts[thread_key]
                        thread_data['title'] = title
                        thread_data['summary'] = summary
                        thread_data['updated_at'] = time.time()
                        thread_data['update_reason'] = 'ویرایش و اطلاعات تکمیلی'
                        alert_text = self.build_alert_post_content(thread_data)
                    else:
                        time_fa = self.get_tehran_time_fa()
                        alert_text = (
                            f"{alert_header}\n\n"
                            f"📝 **چکیده رویداد:**\n{summary}\n\n"
                            f"{sources_text}\n\n"
                            f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و اطلاعات تکمیلی**\n\n"
                            f"{tag}\n{posted_ch}"
                        )
                        
                    try:
                        await self.bot.edit_message(posted_ch, posted_mid, alert_text, link_preview=False)
                        print(f"✏️ Updated Alibk3 alert in {posted_ch} (Alibk msg {msg_id}) with in-place status line.")
                    except Exception as edit_err:
                        if "not modified" in str(edit_err).lower():
                            print(f"ℹ️ Alibk3 alert {posted_mid} already up to date.")
                        else:
                            raise edit_err
                else:
                    merged = await self.check_and_merge_with_active_alert(
                        text=clean_text,
                        node="Alibk3",
                        msg_id=msg_id,
                        target_channel=target_channel,
                        candidate_title=title,
                        candidate_summary=summary,
                        raw_msg=raw_msg,
                        category=category,
                        is_iran_related=is_iran_related
                    )
                    if merged:
                        return

                    sent = await self.bot.send_message(target_channel, alert_text, link_preview=False, silent=is_silent)
                    if sent:
                        self.channel_last_alert_id[target_channel] = sent.id
                        self.alibk_posted[msg_id] = {'channel': target_channel, 'msg_id': sent.id, 'timestamp': time.time()}
                        self.record_posted_message(target_channel, "Alibk3", msg_id, sent.id)
                        self.thread_alerts[f"Alibk3_{msg_id}"] = {
                            'channel': target_channel,
                            'alert_msg_id': sent.id,
                            'node': 'Alibk3',
                            'root_id': msg_id,
                            'root_text': clean_text,
                            'root_media_type': root_media_type,
                            'timestamp': time.time(),
                            'category': category,
                            'is_iran_related': is_iran_related,
                            'title': title,
                            'summary': summary,
                            'sources': [{
                                'node': 'Alibk3',
                                'msg_id': msg_id,
                                'media_type': root_media_type,
                                'link': link,
                                'timestamp': time.time()
                            }],
                            'replies': [],
                            'tag': tag
                        }
                        self.save_persisted_state()
                        status_label = "Iran/Sound" if is_iran_related else "Regional/Silent"
                        print(f"🚨 Published Persian Alibk3 alert ({status_label}) to {target_channel} for msg {msg_id}: {title}")
            except Exception as e:
                print(f"❌ Error publishing Alibk3 alert to {target_channel} for msg {msg_id}: {e}")

    async def synthesize_withyashar_post(self, text):
        clean = (text or "").strip()
        clean_prompt_text = re.sub(r'@\w+\s*$', '', clean).strip()

        prompt = (
            "تو سردبیر ارشد و همکار هوشمند تحریریه دیده‌بان (دیده‌بان جنگ، اقتصاد، اعتراضات) هستی.\n"
            "پست زیر از کانال خبری و تحلیلی 'withyashar' منتشر شده است:\n\n"
            f"{clean_prompt_text}\n\n"
            "با اتکا به دانش ژورنالیستی و درک زبانی خودت، این خبر را برای مخاطب فارسی‌زبان بازنویسی و تنظیم کن:\n"
            "۱. title: یک تیتر رسا، جذاب، گویا و مستقل همراه با فعل کامل بنویس که اصل واقعه را شفاف بیان کند.\n"
            "۲. summary: چکیده‌ای شفاف و شیوا بنویس. اولویت اصلی با ایجاز و خلاصه‌سازی در کنار رساندن تمام ابعاد و ماهیت اصلی رویداد است. اگر خبر دارای ابهام است، گمراه‌کننده به نظر می‌رسد، یا نیاز به توضیح تکمیلی و پیش‌زمینه دارد، با دانش خودت آن را برای مخاطب شفاف کن.\n"
            "۳. category: تعیین دقیق حوزه خبر بین 'WAR' (جنگ و تحرکات نظامی منطقه‌ای)، 'ECONOMY' (ارز، طلا، معیشت، بازار)، 'PROTEST_RIGHTS' (اعتراضات، حقوق بشر، دادگاه، زندان) یا 'OTHER' (سایر موضوعات متفرقه و بی‌ارتباط).\n"
            "۴. is_iran_related: اگر خبر مستقیم یا غیرمستقیم به ایران، خاک ایران، مردم و مواضع یا رویارویی‌های ایران مربوط است مقدار true، در غیر این صورت false.\n\n"
            "پاسخ را منحصراً در قالب این شیء JSON ارسال کن:\n"
            "{\n"
            '  "category": "WAR" | "ECONOMY" | "PROTEST_RIGHTS" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "title": "تیتر رسا و حرفه‌ای فارسی",\n'
            '  "summary": "چکیده شیوا و ابهام‌زدایی‌شده"\n'
            "}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 450,
                "temperature": 0.2
            }
        }

        raw_text = await self.call_gemini_content(payload, timeout=15, max_retries=6)
        if raw_text:
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                try:
                    res = json.loads(match.group(0))
                    category = str(res.get("category", "WAR")).upper().strip()
                    title = str(res.get("title", "")).strip()
                    summary = str(res.get("summary", "")).strip()
                    is_iran_related = bool(res.get("is_iran_related", False))
                    if title and summary:
                        return {
                            "category": category,
                            "is_iran_related": is_iran_related,
                            "title": title,
                            "summary": summary,
                            "is_ai": True
                        }
                except Exception:
                    pass

        return self.generate_withyashar_heuristic_fallback(clean_prompt_text)

    def generate_withyashar_heuristic_fallback(self, text):
        clean = re.sub(r'@\w+\s*$', '', (text or "")).strip()
        lines = [l.strip() for l in clean.split('\n') if l.strip()]
        first_line = lines[0] if lines else clean
        
        is_economy = any(w in clean for w in ['دلار', 'تتر', 'ارز', 'طلا', 'سکه', 'بورس', 'تورم', 'قیمت'])
        is_protest = any(w in clean for w in ['اعتراض', 'اعتصاب', 'اعدام', 'تجمع', 'زندان', 'دادگاه', 'بازداشت', 'حقوق بشری'])
        category = 'ECONOMY' if is_economy else ('PROTEST_RIGHTS' if is_protest else 'WAR')

        clean_first = re.sub(r'^[🚨⚡️📢🛑🔕⚖️📈📸📹🎙🔴⚠️📌\s\*\-]+', '', first_line).strip()
        # Clean parentheticals (e.g. slogans or side notes like "(تهدید را بشناس)") which bloat length
        clean_first = re.sub(r'\s*\([^)]*\)', '', clean_first)
        clean_first = re.sub(r'\s+([,،])', r'\1', clean_first)
        clean_first = re.sub(r'\s{2,}', ' ', clean_first).strip()
        
        # Take the first complete sentence ending in [.؛!?\n]
        parts = re.split(r'[.؛!?\n]', clean_first)
        candidate_title = parts[0].strip() if parts else clean_first

        # IMPORTANT: NEVER split candidate_title on comma [,،]!
        # In Persian grammar, the main verb is placed at the end of the clause/sentence.
        # Splitting on commas invariably chops off the verb and leaves an incomplete sentence.
        if len(candidate_title) > 135:
            words = candidate_title.split()
            truncated = []
            curr_len = 0
            for w in words:
                if curr_len + len(w) + 1 > 130:
                    break
                truncated.append(w)
                curr_len += len(w) + 1
            if truncated:
                candidate_title = " ".join(truncated).rstrip("،,؛.:- ") + "..."
            
        title = candidate_title if candidate_title else clean_first[:90]
        title = title.replace("||FOREIGN||", "").replace("||ECONOMY||", "").strip()

        summary = clean
        is_iran = self.determine_iran_related(None, clean, title)

        return {
            "category": category,
            "is_iran_related": is_iran,
            "title": title,
            "summary": summary,
            "is_ai": False
        }

    async def catchup_unposted_withyashar(self, reader_client=None):
        if not reader_client:
            return
        try:
            now_utc = datetime.now(timezone.utc)
            recent_yashar = []
            async for msg in reader_client.iter_messages("withyashar", limit=5):
                if msg.date and (now_utc - msg.date).total_seconds() > 300:
                    continue
                recent_yashar.append(msg)
            recent_yashar.reverse()
            for msg in recent_yashar:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 10:
                    if not self.is_source_posted("@DidebanJang", "withyashar", msg.id):
                        await self.handle_withyashar_intelligence(clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Catchup for withyashar failed: {e}")

    async def handle_withyashar_intelligence(self, text, msg_id, raw_msg=None, is_edit=False):
        if not is_edit:
            self.channel_watermarks["withyashar"] = max(self.channel_watermarks.get("withyashar", 0), msg_id)
        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 10:
            return

        # 1. Deduplication for Album / Media Groups (grouped_id)
        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in self.vip_grouped_posted and not is_edit:
            return
        if grouped_id:
            self.vip_grouped_posted.add(grouped_id)

        target_channel = "@DidebanJang"
        # 2. Check persistent dedup guard
        if not is_edit and self.is_source_posted(target_channel, "withyashar", msg_id):
            print(f"🛑 [DEDUP GUARD] withyashar msg {msg_id} already posted to {target_channel}. Aborting.")
            return

        # 3. Sequential lock per msg_id
        if msg_id not in self.withyashar_msg_locks:
            if len(self.withyashar_msg_locks) > 200:
                self.withyashar_msg_locks.clear()
            self.withyashar_msg_locks[msg_id] = asyncio.Lock()

        async with self.withyashar_msg_locks[msg_id]:
            ai_res = await self.synthesize_withyashar_post(clean_text)
            if not ai_res:
                return

            category = ai_res.get('category', 'WAR')
            if category == 'OTHER':
                print(f"ℹ️ withyashar post {msg_id} filtered out by news policy (OTHER).")
                return

            title = ai_res.get('title', '').strip()
            summary = ai_res.get('summary', '').strip()
            if not title or not summary:
                return

            is_iran_related = self.determine_iran_related(ai_res, clean_text, title)
            is_silent = not is_iran_related

            if category == 'ECONOMY':
                target_channel = "@DidehbanEghtesad"
                tag = "#دیده‌بان_اقتصاد"
                icon = "📈"
                alert_type_name = "گزارش اقتصادی"
            elif category == 'PROTEST_RIGHTS':
                target_channel = "@DidebanEterazat"
                tag = "#دیده‌بان_اعتراضات"
                icon = "⚖️" if any(w in clean_text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت"]) else "📢"
                alert_type_name = "گزارش حقوق بشری" if icon == "⚖️" else "گزارش مردمی"
            else:
                target_channel = "@DidebanJang"
                tag = "#دیده‌بان_جنگ"
                icon = "🚨" if is_iran_related else "🔕"
                alert_type_name = "هشدار فوری" if is_iran_related else "هشدار منطقه‌ای"

            link = f"https://t.me/withyashar/{msg_id}"
            root_media_type = self.detect_msg_media_type(raw_msg)
            root_icon = self.get_media_icon_from_type(root_media_type)

            media_header = ""
            if root_media_type == 'video': media_header = " 📹"
            elif root_media_type == 'photo': media_header = " 📸"
            elif root_media_type == 'voice': media_header = " 🎙"

            alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"
            sources_text = f"🔗 **منبع خبر:**\n- [withyashar]({link}) {root_icon}"

            alert_text = (
                f"{alert_header}\n\n"
                f"📝 **چکیده رویداد:**\n{summary}\n\n"
                f"{sources_text}\n\n"
                f"{tag}\n{target_channel}"
            )

            msg_key = f"withyashar_{msg_id}"
            try:
                if msg_key in self.vip_alert_history and is_edit:
                    posted_mid = self.vip_alert_history[msg_key].get('sent_msg_id')
                    posted_ch = self.vip_alert_history[msg_key].get('target_channel', target_channel)
                    time_fa = self.get_tehran_time_fa()
                    updated_text = (
                        f"{alert_header}\n\n"
                        f"📝 **چکیده رویداد:**\n{summary}\n\n"
                        f"{sources_text}\n\n"
                        f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و تکمیل خبر**\n\n"
                        f"{tag}\n{posted_ch}"
                    )
                    try:
                        await self.bot.edit_message(posted_ch, posted_mid, updated_text, link_preview=False)
                        print(f"✏️ Edited withyashar post {posted_mid} in {posted_ch}.")
                    except Exception as edit_err:
                        if "not modified" in str(edit_err).lower():
                            pass
                        else:
                            raise edit_err
                else:
                    merged = await self.check_and_merge_with_active_alert(
                        text=clean_text,
                        node="withyashar",
                        msg_id=msg_id,
                        target_channel=target_channel,
                        candidate_title=title,
                        candidate_summary=summary,
                        raw_msg=raw_msg,
                        category=category,
                        is_iran_related=is_iran_related
                    )
                    if merged:
                        return

                    sent = await self.bot.send_message(target_channel, alert_text, link_preview=False, silent=is_silent)
                    if sent:
                        self.channel_last_alert_id[target_channel] = sent.id
                        self.vip_alert_history[msg_key] = {
                            'target_channel': target_channel,
                            'sent_msg_id': sent.id,
                            'time': time.time(),
                            'text': clean_text,
                            'pattern': 'withyashar_report'
                        }
                        self.record_posted_message(target_channel, "withyashar", msg_id, sent.id)
                        thread_key = f"withyashar_{msg_id}"
                        self.thread_alerts[thread_key] = {
                            'channel': target_channel,
                            'alert_msg_id': sent.id,
                            'node': 'withyashar',
                            'root_id': msg_id,
                            'root_text': clean_text,
                            'root_media_type': root_media_type,
                            'timestamp': time.time(),
                            'category': category,
                            'is_iran_related': is_iran_related,
                            'title': title,
                            'summary': summary,
                            'sources': [{
                                'node': 'withyashar',
                                'msg_id': msg_id,
                                'media_type': root_media_type,
                                'link': link,
                                'timestamp': time.time()
                            }],
                            'replies': [],
                            'tag': tag
                        }
                        self.save_persisted_state()
                        status_label = "Iran/Sound" if is_iran_related else "Regional/Silent"
                        print(f"🚨 Published intelligent withyashar alert ({status_label}) to {target_channel} for msg {msg_id}: {title}")
            except Exception as e:
                print(f"❌ Error publishing withyashar alert to {target_channel} for msg {msg_id}: {e}")

import signal
import sys

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING or not BOT_TOKEN:
        print("Error: Missing Telegram API credentials.")
        return
        
    print("👁️ Sentinel Eye (LIVE MODE): Initializing...")
    
    bot = TelegramClient(StringSession(), int(API_ID), API_HASH)
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    
    sentinel = LiveSentinel(bot, client=client)
    
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
            # Periodic fallback check for deleted messages (every 90s)
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
                            last_ids[node] = latest_id
                            # Startup Catch-up for messages from last 15 minutes during runner restart transitions
                            now_utc = datetime.now(timezone.utc)
                            recent = [m for m in messages if m.date and (now_utc - m.date).total_seconds() <= 900]
                            recent.reverse()
                            for m in recent:
                                await sentinel.process_message(m.message, node, m.id, msg_date=m.date, raw_msg=m, skip_mirror_channels=True)
                        else:
                            new_msgs = [m for m in messages if m.id > last_ids[node]]
                            new_msgs.reverse()
                            for m in new_msgs:
                                await sentinel.process_message(m.message, node, m.id, msg_date=m.date, raw_msg=m)
                            last_ids[node] = max(last_ids[node], latest_id)
                except Exception as e:
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
                
                # Proactive connection health check
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
        
        # 1. Start active poller and heartbeat monitor immediately so no messages are ever delayed
        poller_task = asyncio.create_task(active_poller())
        heartbeat_task = asyncio.create_task(heartbeat_monitor())
        
        # 2. Fast critical initialization: Preload entities, live Google models and channel alert IDs
        await sentinel.preload_node_entities(reader_client=client)
        try:
            await asyncio.wait_for(sentinel.fetch_latest_google_models(), timeout=5.0)
        except Exception as gm_err:
            print(f"⚠️ Startup model query timed out or failed: {gm_err}")
        await sentinel.init_channel_last_alert_ids(reader_client=client)
        
        # 3. Synchronously preload historical posts from destination channels into DB and memory before catch-up
        print("📚 Preloading recent post history from target channels...")
        await asyncio.gather(
            sentinel.preload_vahid_ai_history(reader_client=client),
            sentinel.preload_ilia_ai_history(reader_client=client),
            sentinel.preload_alibk_history(reader_client=client),
            return_exceptions=True
        )
        
        # 4. Safely catch up missed posts from the last 5m transition window
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
            except: pass
            
        try:
            await asyncio.wait_for(safe_disconnect(), timeout=10.0)
        except asyncio.TimeoutError:
            print("⚠️ Disconnect timed out, forcing exit.")
        
        # Save persistent state and generate session report
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


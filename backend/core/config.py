import os
import re
import time
from datetime import datetime, timezone, timedelta

# --- Paths & General Settings ---
CONFIG_FILE = 'backend/sentinel_config.json'
BASELINE_FILE = 'backend/trend_baselines.json'
STATE_FILE = 'backend/sentinel_state.json'
POSTED_DB_FILE = 'backend/posted_registry.db'
MAX_RUNTIME_SEC = 35 * 60  # 35 minutes graceful rotation

# --- Telegram Credentials ---
API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION_GENERAL")
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- Gemini API Configuration ---
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

# --- VIP Canonical Mapping ---
VIP_MAP = {
    'vahidonline': 'VahidOnline',
    'iliaen': 'iliaen',
    'alibk3': 'Alibk3',
    'withyashar': 'withyashar'
}

# --- Country Theatres & Geographic Entities ---
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

FOREIGN_KEYWORDS = [
    'اسرائیل', 'لبنان', 'غزه', 'فلسطین', 'سوریه', 'عراق', 'یمن', 'عربستان', 
    'کویت', 'قطر', 'امارات', 'عمان', 'بحرین', 'ترکیه', 'پاکستان', 'افغانستان', 
    'آمریکا', 'اردن', 'مصر', 'آذربایجان', 'ارمنستان', 'اوکراین', 'روسیه',
    'ریاض', 'جده', 'دمام', 'ینبع', 'جازان', 'بقیق', 'خریص', 'راس تنوره', 'شدقم', 
    'نجران', 'ابها', 'تبوک', 'ظهران', 'خمیس مشیط',
    'تل آویو', 'حیفا', 'قدس', 'اورشلیم', 'ایلات', 'صفد', 'طبریا', 'عکا', 'دیمونا', 
    'بئرشبع', 'عسقلان', 'اسدود', 'کریات شمونه', 'رمات گان', 'نتانیا', 'هرتزلیا', 
    'غلاف غزه', 'خان یونس', 'رفح', 'جنین', 'نابلس', 'طولکرم', 'رام الله',
    'بیروت', 'ضاحیه', 'بقاع', 'صور', 'صیدا', 'بعلبک', 'نبطیه', 'الناقوره', 'خیام', 'مرجعیون',
    'دمشق', 'حلب', 'حمص', 'حماه', 'لاذقیه', 'طرطوس', 'دیرالزور', 'بوکمال', 'تدمر', 'قنیطره', 'سویداء',
    'بغداد', 'اربیل', 'سلیمانیه', 'دهوک', 'بصره', 'نجف', 'کربلا', 'سامرا', 'کرکوک', 
    'موصل', 'الانبار', 'عین الاسد', 'التاجی', 'بلد', 'جرف الصخر', 'القائم',
    'صنعا', 'عدن', 'حدیده', 'صعده', 'مارب', 'تعز', 'حجه', 'شبوه', 'ابین',
    'دبی', 'ابوظبی', 'شارجه', 'فجیره', 'جبل علی', 'الرویس', 'عجمان', 'راس الخیمه',
    'دوحه', 'راس لفان', 'منامه', 'مسقط', 'صلاله', 'دقم',
    'آرامکو', 'بن گوریون', 'فرودگاه بن گوریون', 'فرودگاه بیروت', 'فرودگاه ریاض', 
    'فرودگاه دمشق', 'فرودگاه بغداد', 'پایگاه نواتیم', 'پایگاه هاتزریم', 'پایگاه رامات دیوید',
    'پایگاه الحریر', 'پایگاه التنف', 'نیروگاه براکه', 'رآکتور دیمونا'
]

IRAN_KEYWORDS = [
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

def to_fa_num(n):
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    return "".join(fa_digits[int(d)] if d.isdigit() else d for d in str(n))

def get_tehran_time_fa(ts=None):
    tz = timezone(timedelta(hours=3, minutes=30))
    dt = datetime.fromtimestamp(ts if ts else time.time(), tz=tz)
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    time_str = dt.strftime("%H:%M")
    return "".join(fa_digits[int(d)] if d.isdigit() else d for d in time_str)

def detect_msg_media_type(msg):
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
        if 'pdf' in mime: return 'file'
        return 'file'
    return 'text'

def get_media_icon_from_type(media_type):
    m = (media_type or 'text').lower()
    if m == 'video': return "📹"
    if m == 'photo': return "📸"
    if m == 'voice': return "🎙"
    if m == 'file': return "📄"
    return "📝"

KINETIC_WAR_KEYWORDS = [
    "بمباران", "بمباران هوایی", "حمله موشکی", "حملات موشکی", "شلیک موشک", "شلیک پهپاد", 
    "حملات پهپادی", "حمله پهپادی", "صدای انفجار", "انفجارهای شدید", "انفجار مهیب",
    "آژیر خطر", "آژیر قرمز", "وضعیت قرمز", "پدافند هوایی فعال", "شلیک پدافند", 
    "حمله هوایی", "تجاوز هوایی", "جنگنده‌ها حمله کردند", "اصابت موشک", "موشک‌های بالستیک",
    "حمله به خاک ایران", "حمله اسرائیل به ایران", "پاسخ موشکی ایران", "عملیات وعده صادق", 
    "آغاز عملیات", "اعلام وضعیت جنگی", "بستن حریم هوایی"
]

def classify_war_urgency(ai_urgency=None, text="", title="", is_iran_related=False):
    """
    3-Tier Classification for @DidebanJang:
    1. EMERGENCY (🚨 هشدار فوری): Kinetic active strikes/bombing/missiles/explosions involving Iran (Loud: is_silent=False).
    2. BREAKING (⚡️ خبر فوری): Important military/political/fleet/intelligence news about Iran, non-kinetic (Silent: is_silent=True).
    3. REGIONAL (🔕 هشدار منطقه‌ای): War news from other countries (Gaza, Lebanon, Yemen, Ukraine, etc.) not on Iran (Silent: is_silent=True).

    Returns: (tier, icon, alert_type_name, is_silent)
    """
    clean_all = f"{text or ''} {title or ''}".strip()

    # 1. If AI explicitly specified urgency_level
    if ai_urgency:
        u = str(ai_urgency).strip().upper()
        if u in ('EMERGENCY', 'KINETIC', 'URGENT', 'ALERT'):
            return ('EMERGENCY', '🚨', 'هشدار فوری', False)
        elif u in ('BREAKING', 'IMPORTANT', 'NEWS'):
            return ('BREAKING', '⚡️', 'خبر فوری', True)
        elif u in ('REGIONAL', 'SILENT'):
            return ('REGIONAL', '🔕', 'هشدار منطقه‌ای', True)

    # 2. Check if related to Iran
    if is_iran_related:
        if any(kw in clean_all for kw in KINETIC_WAR_KEYWORDS):
            return ('EMERGENCY', '🚨', 'هشدار فوری', False)
        else:
            return ('BREAKING', '⚡️', 'خبر فوری', True)

    # 3. Non-Iran regional war news
    return ('REGIONAL', '🔕', 'هشدار منطقه‌ای', True)


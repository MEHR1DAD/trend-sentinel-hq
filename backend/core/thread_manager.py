import re
import time
import json
import asyncio
from backend.core.config import (
    CHANNEL_CANONICAL_TAGS,
    to_fa_num,
    get_tehran_time_fa,
    get_tehran_datetime_fa,
    format_update_status_line,
    detect_msg_media_type,
    get_media_icon_from_type,
    classify_war_urgency,
    COUNTRY_THEATRES,
    FOREIGN_KEYWORDS,
    IRAN_KEYWORDS,
    safe_extract_json
)

class ThreadAlertManager:
    """
    Dedicated Microservice Engine for Managing Active Alert Threads,
    In-Place Message Debouncing, and Semantic Event Merging across:
    - @DidebanJang
    - @DidehbanEghtesad
    - @DidebanEterazat
    """
    def __init__(self, bot=None, gemini=None, db=None, safe_edit_func=None, save_state_func=None):
        self.bot = bot
        self.gemini = gemini
        self.db = db
        self.safe_edit_func = safe_edit_func
        self.save_state_func = save_state_func

        # In-Memory State
        self.thread_alerts = {}
        self.reply_to_root_map = {}
        self.debounce_tasks = {}
        self.thread_locks = {}
        self.vip_alert_history = {}

        # Keywords
        self.foreign_keywords = FOREIGN_KEYWORDS
        self.iran_keywords = IRAN_KEYWORDS

    def set_dependencies(self, bot=None, gemini=None, db=None, safe_edit_func=None, save_state_func=None):
        if bot: self.bot = bot
        if gemini: self.gemini = gemini
        if db: self.db = db
        if safe_edit_func: self.safe_edit_func = safe_edit_func
        if save_state_func: self.save_state_func = save_state_func

    def load_state(self, state_data):
        if not isinstance(state_data, dict):
            return
        self.thread_alerts = state_data.get('thread_alerts', {})
        self.reply_to_root_map = state_data.get('reply_to_root_map', {})
        self.vip_alert_history = state_data.get('vip_alert_history', {})

    def get_state(self):
        return {
            'thread_alerts': self.thread_alerts,
            'reply_to_root_map': self.reply_to_root_map,
            'vip_alert_history': self.vip_alert_history
        }

    # ==========================================
    # --- ANCHOR EXTRACTION & NORMALIZATION ---
    # ==========================================

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

    # ==========================================
    # --- SEMANTIC DEDUPLICATION & MERGING ---
    # ==========================================

    async def evaluate_semantic_relation(self, existing_title, existing_summary, new_text, is_same_source=False, has_country_theatre=False):
        if not self.gemini or not self.gemini.get_api_key():
            return None
            
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
            "- updated_title: یک عنوان جامع، رسا و خوش‌خوان فارسی همراه با فعل یا گزاره کامل که تمام ابعاد و اهداف رویداد را منعکس کند.\n"
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
        
        raw_out = await self.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            parsed = safe_extract_json(raw_out)
            if parsed and isinstance(parsed, dict):
                return parsed
        return None

    async def check_and_merge_with_active_alert(self, text, node, msg_id, target_channel, candidate_title="", candidate_summary="", raw_msg=None, category='WAR', is_iran_related=False, candidate_sources=None):
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
            if (now - td.get('timestamp', 0)) > 21600: # 6 hours
                continue
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
            
            is_same_source = (
                thread_data.get('node') == node or 
                any(s.get('node') == node for s in thread_data.get('sources', []))
            )
            is_recent_same_source = is_same_source and (time_diff <= 2700) # 45 minutes
            
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
            
            if relation != 'SAME_EVENT' and is_recent_same_source and has_country_theatre:
                print(f"🛡️ Guardrail triggered: Forcing SAME_EVENT merge for same source {node} in theatre within {int(time_diff)}s.")
                relation = 'SAME_EVENT'
                ai_eval['has_update'] = True
                if not ai_eval.get('updated_title'):
                    clean_cand = re.sub(r'^(?:🚨|🔕|⚡️)?\s*(?:هشدار فوری|خبر فوری|هشدار منطقه‌ای):\s*', '', candidate_title).strip()
                    clean_exist = re.sub(r'^(?:🚨|🔕|⚡️)?\s*(?:هشدار فوری|خبر فوری|هشدار منطقه‌ای):\s*', '', existing_title).strip()
                    ai_eval['updated_title'] = f"{clean_exist} و {clean_cand}" if clean_cand else clean_exist
                if not ai_eval.get('updated_summary'):
                    ai_eval['updated_summary'] = f"{existing_summary}\nهمچنین گزارش‌ها حاکی از دریافت اطلاعات تکمیلی و پوشش تحولات جدید در این زمینه است."
                    
            if relation == 'SAME_EVENT':
                print(f"🔀 Merging {node}_{msg_id} into existing alert {thread_data.get('alert_msg_id')} in {target_channel} (SAME_EVENT confirmed).")
                
                sources = thread_data.setdefault('sources', [])
                if candidate_sources:
                    for cs in candidate_sources:
                        if not any(s.get('node') == cs.get('node') and (s.get('msg_id') == cs.get('msg_id') or str(s.get('msg_id')) == str(cs.get('msg_id'))) for s in sources):
                            sources.append(cs)
                else:
                    new_src_entry = {
                        'node': node,
                        'msg_id': msg_id,
                        'media_type': detect_msg_media_type(raw_msg),
                        'link': f"https://t.me/{node}/{msg_id}",
                        'timestamp': time.time()
                    }
                    if not any(s.get('node') == node and (s.get('msg_id') == msg_id or str(s.get('msg_id')) == str(msg_id)) for s in sources):
                        sources.append(new_src_entry)
                        
                if ai_eval.get('has_update'):
                    if ai_eval.get('updated_title'):
                        thread_data['title'] = ai_eval['updated_title']
                    if ai_eval.get('updated_summary'):
                        thread_data['summary'] = ai_eval['updated_summary']
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
                
                if self.safe_edit_func:
                    try:
                        await self.safe_edit_func(target_channel, alert_msg_id, updated_alert_text)
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
                if self.save_state_func:
                    self.save_state_func()
                return True
                
        return False

    # ==========================================
    # --- THREAD DEBOUNCING & BUILDER ---
    # ==========================================

    def schedule_thread_debounce(self, thread_key):
        if thread_key in self.debounce_tasks and not self.debounce_tasks[thread_key].done():
            self.debounce_tasks[thread_key].cancel()
        self.debounce_tasks[thread_key] = asyncio.create_task(self.flush_thread_debounce(thread_key, delay=180))
        print(f"⏳ Scheduled/Extended 3-minute debounce update for thread {thread_key}.")

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
                    combined_sections.append(f"[گزارش تکمیلی {to_fa_num(idx)}]:\n{rep_text}")
                    
            combined_thread_text = "\n\n".join(combined_sections)
            
            # AI Editorial Analysis of the consolidated thread
            ai_res = await self.synthesize_thread_alert_content(combined_thread_text)
            if ai_res:
                thread_data['title'] = ai_res.get('title', thread_data.get('title', ''))
                thread_data['summary'] = ai_res.get('summary', thread_data.get('summary', ''))
                
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
            
            if self.safe_edit_func:
                try:
                    await self.safe_edit_func(target_channel, alert_msg_id, updated_alert_text)
                    print(f"✏️ Successfully updated alert {alert_msg_id} in {target_channel} ({len(replies)} replies attached). Status line updated in-place.")
                except Exception as e:
                    if "not modified" in str(e).lower():
                        print(f"ℹ️ Alert {alert_msg_id} in {target_channel} content unchanged.")
                    else:
                        print(f"❌ Error editing thread alert {alert_msg_id} in {target_channel}: {e}")
                        
            if self.save_state_func:
                self.save_state_func()

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

    async def synthesize_thread_alert_content(self, thread_text):
        if not self.gemini or not self.gemini.get_api_key():
            return None
            
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
        
        raw_out = await self.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            res = safe_extract_json(raw_out)
            if res and isinstance(res, dict):
                title = res.get("title", "").strip()
                summary = res.get("summary", "").strip()
                if title and summary:
                    return {"title": title, "summary": summary}
        return None

    def build_alert_post_content(self, thread_data):
        category = thread_data.get('category', 'WAR')
        is_iran_related = thread_data.get('is_iran_related', False)
        title = thread_data.get('title', '').strip()
        summary = thread_data.get('summary', '').strip()
        target_channel = thread_data.get('channel', '@DidebanJang')
        tag = CHANNEL_CANONICAL_TAGS.get(target_channel, thread_data.get('tag', '#دیده‌بان_جنگ'))
        
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

        all_media_types = [s.get('media_type', 'text') for s in sources]
        header_media_icon = ""
        if 'video' in all_media_types:
            header_media_icon = " 📹"
        elif 'photo' in all_media_types:
            header_media_icon = " 📸"
        elif 'voice' in all_media_types:
            header_media_icon = " 🎙"
        elif 'file' in all_media_types:
            header_media_icon = " 📄"

        if target_channel == '@DidebanJang':
            ai_urgency = thread_data.get('urgency_level')
            tier, icon, alert_type_name, _ = classify_war_urgency(
                ai_urgency=ai_urgency,
                text=summary,
                title=title,
                is_iran_related=is_iran_related
            )
            alert_header = f"{icon} **{alert_type_name}: {title}**{header_media_icon}"
        elif target_channel == '@DidehbanEghtesad':
            alert_header = f"📈 **گزارش اقتصادی: {title}**{header_media_icon}"
        elif target_channel == '@DidebanEterazat':
            is_legal = any(w in title for w in ['اعدام', 'دادگاه', 'حکم', 'حقوق بشری', 'زندان', 'بازداشت'])
            is_strike = 'اعتصاب' in title
            icon = "⚖️" if is_legal else ("🛑" if is_strike else "📢")
            type_name = "گزارش حقوق بشری" if is_legal else ("گزارش کارگری و اعتصاب" if is_strike else "گزارش مردمی")
            alert_header = f"{icon} **{type_name}: {title}**{header_media_icon}"
        else:
            alert_header = f"🚨 **هشدار فوری: {title}**{header_media_icon}"

        sources_lines = []
        for s in sources:
            s_node = s.get('node', 'منبع')
            s_link = s.get('link', '')
            s_icon = get_media_icon_from_type(s.get('media_type', 'text'))
            if s_link:
                sources_lines.append(f"- [{s_node}]({s_link}) {s_icon}")
            else:
                sources_lines.append(f"- {s_node} {s_icon}")
                
        sources_text = "🔗 **منابع خبر:**\n" + "\n".join(sources_lines) if sources_lines else ""
        
        status_line = ""
        update_reason = thread_data.get('update_reason', 'اطلاعات تکمیلی')
        if thread_data.get('updated_at'):
            status_line = f"{format_update_status_line(thread_data['updated_at'], update_reason)}\n\n"
            
        summary_section = f"📝 **چکیده رویداد:**\n{summary}\n\n" if summary else ""
        sources_section = f"{sources_text}\n\n" if sources_text else ""
        
        post_content = (
            f"{alert_header}\n\n"
            f"{summary_section}"
            f"{sources_section}"
            f"{status_line}"
            f"{tag}\n"
            f"{target_channel}"
        )
        return post_content.strip()

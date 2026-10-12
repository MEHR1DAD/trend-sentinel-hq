import os
import re
import time
import json
import asyncio
from datetime import datetime, timezone
from backend.core.config import (
    COUNTRY_THEATRES,
    FOREIGN_KEYWORDS,
    IRAN_KEYWORDS,
    BOT_TOKEN,
    get_tehran_time_fa,
    to_fa_num,
    detect_msg_media_type,
    get_media_icon_from_type,
    classify_war_urgency
)

class RadarPipeline:
    """
    Subsystem for General Radar & Multi-Source Consensus:
    - 3-channel consensus detection across standard monitored channels
    - Cross-channel semantic merging & deduplication
    - Anomaly detection and routing to @DidebanJang, @DidehbanEghtesad, @DidebanEterazat
    - Message deletion tracking & automated cleanup
    """
    def __init__(self, sentinel):
        self.sentinel = sentinel

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
                    
        for kw in self.sentinel.foreign_keywords:
            norm_kw = self.normalize_text_for_anchors(kw)
            if norm_kw in norm_combined:
                anchors.add(norm_kw)
                
        for kw in self.sentinel.iran_keywords:
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
        if not self.sentinel.gemini.get_api_key():
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
        
        raw_out = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
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
        for tk, td in self.sentinel.thread_alerts.items():
            if not isinstance(td, dict):
                continue
            if td.get('channel') != target_channel:
                continue
            if (now - td.get('timestamp', 0)) > 259200: # 72 hours
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
                    anchor in self.sentinel.foreign_keywords or anchor in self.sentinel.iran_keywords or 
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
                
                try:
                    await self.sentinel.safe_edit_post(target_channel, alert_msg_id, updated_alert_text)
                    print(f"🔀 Successfully merged msg {node}_{msg_id} into alert {alert_msg_id} in {target_channel} ({len(sources)} sources). Status line updated in-place.")
                except Exception as edit_err:
                    if "not modified" in str(edit_err).lower():
                        print(f"ℹ️ Merged alert {alert_msg_id} in {target_channel} content unchanged.")
                    else:
                        print(f"❌ Error editing merged alert {alert_msg_id}: {edit_err}")
                        
                msg_key = f"{node}_{msg_id}"
                self.sentinel.vip_alert_history[msg_key] = {
                    'sent_msg_id': alert_msg_id,
                    'target_channel': target_channel,
                    'time': time.time(),
                    'thread_key': thread_key
                }
                self.sentinel.save_persisted_state()
                return True
                
        return False

    # ==========================================
    # --- THREAD DEBOUNCING & BUILDER ---
    # ==========================================

    def schedule_thread_debounce(self, thread_key):
        if thread_key not in self.sentinel.debounce_tasks or self.sentinel.debounce_tasks[thread_key].done():
            self.sentinel.debounce_tasks[thread_key] = asyncio.create_task(self.flush_thread_debounce(thread_key, delay=300))
            print(f"⏳ Scheduled 5-minute debounce update for thread {thread_key}.")

    async def flush_thread_debounce(self, thread_key, delay=300):
        if delay > 0:
            await asyncio.sleep(delay)
            
        thread_data = self.sentinel.thread_alerts.get(thread_key)
        if not thread_data:
            return
            
        lock = self.sentinel.thread_locks.setdefault(thread_key, asyncio.Lock())
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
            
            try:
                await self.sentinel.safe_edit_post(target_channel, alert_msg_id, updated_alert_text)
                print(f"✏️ Successfully updated alert {alert_msg_id} in {target_channel} ({len(replies)} replies attached). Status line updated in-place.")
            except Exception as e:
                if "not modified" in str(e).lower():
                    print(f"ℹ️ Alert {alert_msg_id} in {target_channel} content unchanged.")
                else:
                    print(f"❌ Error editing thread alert {alert_msg_id} in {target_channel}: {e}")
                    
            self.sentinel.save_persisted_state()

    async def flush_all_pending_debounces(self):
        print("⏳ Flushing all pending debounced updates before exit...")
        active_keys = list(self.sentinel.debounce_tasks.keys())
        for k in active_keys:
            task = self.sentinel.debounce_tasks.pop(k, None)
            if task and not task.done():
                task.cancel()
            try:
                await self.flush_thread_debounce(k, delay=0)
            except Exception as e:
                print(f"⚠️ Error flushing thread {k}: {e}")

    async def synthesize_thread_alert_content(self, thread_text):
        if not self.sentinel.gemini.get_api_key():
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
        
        raw_out = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            match = re.search(r'\{.*\}', raw_out, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
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
        tag = thread_data.get('tag', '#دیده‌بان_جنگ')
        
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
            ai_urgency = thread_data.get('urgency_level')
            tier, icon, alert_type_name, _ = classify_war_urgency(
                ai_urgency=ai_urgency,
                text=thread_data.get('root_text', ''),
                title=title,
                is_iran_related=is_iran_related
            )
            alert_header = f"{icon} **{alert_type_name}: {title}**{header_media_icon}"

        node_counts = {}
        for s in sources:
            n = s.get('node', '')
            node_counts[n] = node_counts.get(n, 0) + 1

        node_seen = {}
        sources_lines = []
        for s in sources:
            n = s.get('node', '')
            link = s.get('link') or f"https://t.me/{n}/{s.get('msg_id')}"
            m_icon = get_media_icon_from_type(s.get('media_type'))
            if node_counts.get(n, 0) > 1:
                idx = node_seen.get(n, 0) + 1
                node_seen[n] = idx
                if idx == 1:
                    line = f"- [{n}]({link}) (خبر اصلی) {m_icon}".strip()
                else:
                    line = f"- [{n}]({link}) (تکمیلی {to_fa_num(idx - 1)}) {m_icon}".strip()
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
            time_fa = get_tehran_time_fa(update_time)
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

    async def find_root_message(self, node, raw_msg, max_depth=5):
        if not raw_msg:
            return None, []
            
        reply_to_id = getattr(raw_msg, 'reply_to_msg_id', None)
        if not reply_to_id and getattr(raw_msg, 'reply_to', None):
            reply_to_id = getattr(raw_msg.reply_to, 'reply_to_msg_id', None)
            
        if not reply_to_id:
            return raw_msg.id, [raw_msg.id]
            
        cache_key = f"{node}_{reply_to_id}"
        if cache_key in self.sentinel.reply_to_root_map:
            return self.sentinel.reply_to_root_map[cache_key], [raw_msg.id]

        thread_key = f"{node}_{reply_to_id}"
        if thread_key in self.sentinel.thread_alerts:
            return reply_to_id, [reply_to_id, raw_msg.id]

        curr_id = reply_to_id
        chain = [raw_msg.id]
        depth = 0
        while curr_id and depth < max_depth:
            chain.insert(0, curr_id)
            ck = f"{node}_{curr_id}"
            if ck in self.sentinel.reply_to_root_map:
                return self.sentinel.reply_to_root_map[ck], chain
            if ck in self.sentinel.thread_alerts:
                return curr_id, chain
                
            if not self.sentinel.client:
                break
                
            try:
                parent_msgs = await self.sentinel.client.get_messages(node, ids=[curr_id])
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

    # ==========================================
    # --- DELETION MANAGEMENT ---
    # ==========================================

    async def handle_message_deleted(self, node_username, deleted_ids):
        if not deleted_ids:
            return
            
        print(f"🗑️ Handling MessageDeleted event for node={node_username}: {deleted_ids}")
        for del_id in deleted_ids:
            del_int = int(del_id) if str(del_id).isdigit() else del_id
            del_str = str(del_id)
            
            for tk, thread_data in list(self.sentinel.thread_alerts.items()):
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
                        self.sentinel.thread_alerts.pop(tk, None)
                        task = self.sentinel.debounce_tasks.pop(tk, None)
                        if task and not task.done():
                            task.cancel()
                        if alert_mid:
                            to_delete = [alert_mid]
                            if thread_data.get('last_notified_msg_id'):
                                to_delete.append(thread_data['last_notified_msg_id'])
                            try:
                                await self.sentinel.bot.delete_messages(target_ch, to_delete)
                                print(f"🗑️ Successfully deleted alert {alert_mid} in {target_ch} (all source posts deleted by admin).")
                            except Exception as e:
                                print(f"⚠️ Error deleting alert {alert_mid} in {target_ch}: {e}")
                    else:
                        updated_text = self.build_alert_post_content(thread_data)
                        try:
                            await self.sentinel.safe_edit_post(target_ch, alert_mid, updated_text)
                            print(f"✏️ Refreshed alert {alert_mid} in {target_ch} after removing deleted source {del_id}.")
                        except Exception as e:
                            print(f"⚠️ Error editing alert after source deletion: {e}")
                    self.sentinel.save_persisted_state()

            # VIP alerts
            for msg_key in list(self.sentinel.vip_alert_history.keys()):
                if msg_key.endswith(f"_{del_int}") or msg_key.endswith(f"_{del_str}"):
                    if node_username and not msg_key.startswith(f"{node_username}_"):
                        continue
                    info = self.sentinel.vip_alert_history.pop(msg_key, None)
                    if info and info.get('sent_msg_id'):
                        sent_mid = info['sent_msg_id']
                        target_ch = info.get('target_channel', '@DidebanJang')
                        try:
                            await self.sentinel.bot.delete_messages(target_ch, [sent_mid])
                            print(f"🗑️ Successfully deleted VIP alert {sent_mid} in {target_ch} (source {msg_key} deleted).")
                        except Exception as e:
                            print(f"⚠️ Error deleting VIP alert {sent_mid} in {target_ch}: {e}")
                    self.sentinel.save_persisted_state()

    async def verify_recent_messages_existence(self):
        if not self.sentinel.client or not self.sentinel.client.is_connected():
            return
            
        from telethon.tl.types import MessageEmpty
        now = time.time()
        
        # Check active monitored VIP threads
        recent_ids_by_node = {}
        for k, v in self.sentinel.thread_alerts.items():
            if (now - v.get('timestamp', 0)) <= 7200:
                nd = v.get('node')
                root_id = v.get('root_id')
                if nd and root_id:
                    recent_ids_by_node.setdefault(nd, set()).add(int(root_id) if str(root_id).isdigit() else root_id)
                for r in v.get('replies', []):
                    rep_id = r.get('msg_id')
                    if nd and rep_id:
                        recent_ids_by_node.setdefault(nd, set()).add(int(rep_id) if str(rep_id).isdigit() else rep_id)

        for nd, ids in recent_ids_by_node.items():
            try:
                check_ids = list(ids)[:20]
                msgs = await self.sentinel.client.get_messages(nd, ids=check_ids)
                deleted_ids = []
                for cid, m in zip(check_ids, msgs):
                    if m is None or isinstance(m, MessageEmpty) or (not getattr(m, 'message', None) and not getattr(m, 'media', None)):
                        deleted_ids.append(cid)
                if deleted_ids:
                    print(f"🔍 Poller fallback detected deleted messages in {nd}: {deleted_ids}")
                    await self.handle_message_deleted(nd, deleted_ids)
            except Exception:
                pass

    # ==========================================
    # --- PATTERN MATCHING & CONSENSUS ---
    # ==========================================

    def is_old_news(self, text, node):
        if not text:
            return False
            
        canon = (node or '').lower()
        if canon in ['vahidonline', 'iliaen', 'withyashar', 'alibk3']:
            return False

        citizen_indicators = [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ]
        if any(ind in text for ind in citizen_indicators):
            return False
            
        # Only true historical archival / anniversary markers without breaking news context
        text_clean = text.lower()
        is_anniversary = any(w in text_clean for w in ["در چنین روزی در سال", "سالگرد درگذشت", "سالروز درگذشت", "خاطره‌بازی"])
        if is_anniversary and not any(k in text_clean for k in ["انفجار", "حمله", "موشک", "پهپاد", "بمباران", "آژیر", "فوری"]):
            return True
            
        return False

    def match_pattern(self, text, pattern):
        try:
            diacritics_regex = re.compile(r'[\u064B-\u065F\u0670]')
            clean_text = diacritics_regex.sub('', text).replace('\u200c', ' ')
            clean_pattern = diacritics_regex.sub('', pattern).replace('\u200c', ' ')
            
            if clean_pattern == 'حمله':
                if re.search(r'حمله\s+(قلبی|عصبی|تنفسی|پانیک)', clean_text):
                    non_medical = re.sub(r'حمله\s+(قلبی|عصبی|تنفسی|پانیک)', '', clean_text)
                    if not re.search(r'(?<![آ-یa-zA-Z0-9_])حمله(?![آ-یa-zA-Z0-9_])', non_medical):
                        return False

            esc_pattern = re.escape(clean_pattern)
            regex = r'(?<![آ-یa-zA-Z0-9_])' + esc_pattern + r'(?![آ-یa-zA-Z0-9_])'
            return bool(re.search(regex, clean_text))
        except:
            return pattern in text

    def calculate_jaccard(self, text1, text2):
        set1 = set(text1.split())
        set2 = set(text2.split())
        if not set1 or not set2:
            return 0.0
        return len(set1.intersection(set2)) / len(set1.union(set2))

    def purge_old_messages(self):
        now = time.time()
        while self.sentinel.recent_messages and (now - self.sentinel.recent_messages[0]['timestamp']) > 180:
            self.sentinel.recent_messages.popleft()

    def get_message_patterns(self, text, is_citizen=False):
        config_patterns = self.sentinel.config.get('patterns', {})
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

        specific_cities = [l for l in found_locations if l not in generic_locations]
        generic_locs = [l for l in found_locations if l in generic_locations]
        has_foreign = any(fk in text for fk in self.sentinel.foreign_keywords)
        
        if specific_cities:
            merged_cities = "، ".join(specific_cities)
            if generic_locs:
                merged_generics = " و ".join(generic_locs)
                final_loc_str = f"{merged_generics} {merged_cities}"
            else:
                final_loc_str = merged_cities
        elif generic_locs:
            final_loc_str = "، ".join(generic_locs) if is_citizen else ""
        else:
            final_loc_str = ""
            
        patterns = []
        if final_loc_str:
            if resolved_incidents:
                def inc_priority(inc):
                    sev = self.sentinel.incident_severities.get(inc, "IMPORTANT")
                    return 0 if sev == "URGENT" else 1
                sorted_incidents = sorted(list(resolved_incidents), key=inc_priority)
                
                if len(sorted_incidents) == 1:
                    inc_title = sorted_incidents[0]
                elif len(sorted_incidents) == 2:
                    inc_title = f"{sorted_incidents[0]} و {sorted_incidents[1]}"
                else:
                    inc_title = f"{sorted_incidents[0]}، {sorted_incidents[1]} و {sorted_incidents[2]}"
                    
                pat = f"{inc_title} در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in self.sentinel.foreign_keywords):
                    pat += "||FOREIGN||"
                patterns.append(pat)
            elif is_citizen:
                pat = f"گزارش شهروندی در {final_loc_str}"
                if has_foreign or any(fk in final_loc_str for fk in self.sentinel.foreign_keywords):
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

    async def classify_message(self, text):
        if not self.sentinel.gemini.get_api_key():
            return None
            
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
            "سطح فوریت خبر جنگی (urgency_level):\n"
            "- 'EMERGENCY': رویدادهای اضطراری و میدانی فوری (بمباران، شلیک موشک و پهپاد، انفجار، آژیر قرمز، درگیری مسلحانه مستقیم).\n"
            "- 'BREAKING': اخبار مهم و تحولات امنیتی/سیاسی/تسلیحاتی ایران که بمباران لحظه‌ای نیستند.\n"
            "- 'REGIONAL': جنگ‌های منطقه‌ای بدون درگیری مستقیم ایران.\n\n"
            f"متن خبر:\n{text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب یک شیء JSON با این فیلدها بنویس و هیچ کلمه یا توضیح دیگری قبل یا بعد از آن ننویس:\n"
            "{\n"
            '  "category": "WAR" | "ECONOMY" | "PROTEST_RIGHTS" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "urgency_level": "EMERGENCY" | "BREAKING" | "REGIONAL",\n'
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
        
        raw_text = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_text:
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                res = json.loads(match.group(0))
                category = str(res.get("category", "")).upper().strip()
                topic_title = str(res.get("topic_title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                is_iran_related = bool(res.get("is_iran_related", False))
                urgency_level = str(res.get("urgency_level", "BREAKING" if is_iran_related else "REGIONAL")).upper().strip()
                if category in ["WAR", "ECONOMY", "PROTEST_RIGHTS", "OTHER"]:
                    return {"category": category, "is_iran_related": is_iran_related, "urgency_level": urgency_level, "topic_title": topic_title, "summary": summary}
                    
        return None

    async def append_ai_summary(self, target_channel, message_id, original_text, raw_texts):
        if not self.sentinel.gemini.get_api_key():
            return
            
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
        
        summary = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if summary:
            delimiter = "🔗 **منابع خبر:**" if "🔗 **منابع خبر:**" in original_text else "🔗 **منبع خبر:**"
            parts = original_text.split(delimiter)
            if len(parts) == 2:
                clean_part0 = re.sub(r'⚡️\s*سرعت انتشار[^\n]*\n+', '', parts[0])
                clean_part0 = re.sub(r'📊\s*وضعیت عادی[^\n]*\n+', '', clean_part0)
                clean_part0 = clean_part0.rstrip() + "\n\n"
                new_text = f"{clean_part0}📝 **چکیده رویداد:**\n{summary}\n\n{delimiter}{parts[1]}"
                try:
                    await self.sentinel.safe_edit_post(target_channel, message_id, new_text)
                    for tk, td in self.sentinel.thread_alerts.items():
                        if td.get('alert_msg_id') == message_id and td.get('channel') == target_channel:
                            td['summary'] = summary
                            break
                    self.sentinel.save_persisted_state()
                    print(f"✅ AI Summary added to message {message_id} in {target_channel}.")
                except Exception as edit_err:
                    print(f"Failed to edit AI summary onto message {message_id}: {edit_err}")

    # ==========================================
    # --- ANOMALY CONSENSUS & SEND ALERT ---
    # ==========================================

    async def detect_anomalies(self):
        channel_pools = {}
        
        for msg in self.sentinel.recent_messages:
            text = msg['text']
            is_citizen_report = msg.get('is_citizen_report', False)
            patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
            
            for pat in patterns_in_msg:
                if pat not in channel_pools:
                    channel_pools[pat] = {}
                channel_pools[pat][msg['node']] = msg['link']
                
        now = time.time()
        while self.sentinel.recent_alert_sources and (now - self.sentinel.recent_alert_sources[0][0]) > 900:
            self.sentinel.recent_alert_sources.popleft()
            
        for pat, channels in channel_pools.items():
            is_foreign = "||FOREIGN||" in pat
            clean_pat = pat.replace("||FOREIGN||", "")
            
            distinct_channel_count = len(channels)
            if distinct_channel_count < 3:
                continue
            
            normal_rate = self.sentinel.baselines.get(clean_pat, 0.1)
            
            if clean_pat in self.sentinel.last_alert_time and (now - self.sentinel.last_alert_time[clean_pat]) < 1800:
                continue
                
            source_links = [f"- [{node}]({link})" for node, link in channels.items() if node and node != 'unknown' and re.match(r'^[A-Za-z0-9_]+$', str(node))]
            current_sources = set(channels.values())
            is_duplicate_story = False
            for prev_time, prev_sources in self.sentinel.recent_alert_sources:
                common_sources = current_sources.intersection(prev_sources)
                if len(common_sources) >= 2 or (len(current_sources) > 0 and len(common_sources) / len(current_sources) >= 0.5):
                    is_duplicate_story = True
                    break
                    
            if is_duplicate_story:
                continue
                
            self.sentinel.last_alert_time[clean_pat] = now
            self.sentinel.recent_alert_sources.append((now, current_sources))
            
            is_silent = True
            if not is_foreign:
                for inc, sev in self.sentinel.incident_severities.items():
                    if inc in clean_pat and sev == "URGENT":
                        is_silent = False
                        break
                    
            raw_texts = [msg['text'] for msg in self.sentinel.recent_messages if msg['node'] in channels and msg['link'] == channels[msg['node']]]
            combined_text = "\n---\n".join(raw_texts[:3])
            
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
                    is_iran = self.sentinel.determine_iran_related(ai_class, combined_text, topic_title)
                    ai_urgency = ai_class.get('urgency_level') if ai_class else None
                    tier, icon, alert_type_name, is_silent = classify_war_urgency(
                        ai_urgency=ai_urgency,
                        text=combined_text,
                        title=topic_title,
                        is_iran_related=is_iran
                    )
                    title = f"{alert_type_name}: {topic_title}"
                else:
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

                is_iran = self.sentinel.determine_iran_related(ai_class, combined_text, topic_title) if category == 'WAR' else False
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
                    self.sentinel.thread_alerts[thread_key] = {
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
                    self.sentinel.save_persisted_state()
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, raw_texts[:3]))
            else:
                sent_msg, target_channel, alert_text = await self.send_alert(clean_pat, distinct_channel_count, normal_rate, source_links[:3], is_silent=is_silent)
                if sent_msg:
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, raw_texts[:3]))

    async def send_alert(self, pattern, count, normal_rate, context_msgs, is_silent=False, target_channel=None, alert_title=None, custom_icon=None, summary=None):
        if not BOT_TOKEN:
            return None, None, ""
        
        is_economy = "||ECONOMY||" in str(pattern) or target_channel == "@DidehbanEghtesad"
        clean_pat_str = str(pattern).replace("||ECONOMY||", "").replace("||FOREIGN||", "").strip()
        is_protest = ("اعتراض" in clean_pat_str or "اعتصاب" in clean_pat_str or "اعدام" in clean_pat_str or "حقوق بشری" in clean_pat_str) or target_channel == "@DidebanEterazat"
        
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
            
        sent_msg = None
        try:
            sent_msg = await self.sentinel.bot.send_message(target_channel, alert_text, link_preview=False, silent=is_silent)
            if sent_msg:
                self.sentinel.channel_last_alert_id[target_channel] = sent_msg.id
        except Exception as e:
            print(f"Failed to send alert to {target_channel}: {e}")
            
        if sent_msg:
            self.sentinel.save_persisted_state()
            
        return sent_msg, target_channel, alert_text

    async def process_radar_consensus(self, text, node, msg_id, is_edit=False, msg_date=None, raw_msg=None):
        if msg_date:
            now_utc = datetime.now(timezone.utc)
            if (now_utc - msg_date).total_seconds() > 10800:
                return
                
        self.purge_old_messages()
        if not text:
            return
            
        self.sentinel.total_msgs_processed += 1
        self.sentinel.last_msg_text = text[:100] + "..." if len(text) > 100 else text
        self.sentinel.last_msg_time = time.strftime("%Y-%m-%d %H:%M:%S")
        
        text = text.replace('ي', 'ی').replace('ك', 'ک')
        
        is_citizen_report = any(ind in text for ind in [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ])
        
        patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
        has_economy = any("||ECONOMY||" in p for p in patterns_in_msg)
        
        if (node or '').lower() not in ['vahidonline', 'iliaen', 'withyashar', 'alibk3'] and not has_economy and self.is_old_news(text, node):
            return
        
        is_syndicated = False
        for rm in self.sentinel.recent_messages:
            if self.calculate_jaccard(text, rm['text']) > 0.75:
                is_syndicated = True
                break
                
        if is_syndicated:
            return
        
        msg_obj = {
            'text': text,
            'node': node,
            'timestamp': time.time(),
            'link': f"https://t.me/{node}/{msg_id}",
            'is_citizen_report': is_citizen_report
        }
        self.sentinel.recent_messages.append(msg_obj)
        await self.detect_anomalies()

    async def process_vip_channel(self, text, node, msg_id, raw_msg=None, is_edit=False):
        canonical_node = 'VahidOnline' if (node or '').lower() == 'vahidonline' else ('iliaen' if (node or '').lower() == 'iliaen' else node)
        msg_key = f"{canonical_node}_{msg_id}"
        now = time.time()
        link = f"https://t.me/{canonical_node}/{msg_id}"
        
        is_citizen_report = any(ind in text for ind in [
            "پیام دریافتی", "دریافتی:", "پیام‌های دریافتی", "پیامهای دریافتی", 
            "ارسالی:", "پیام:", "از پیام‌ها:", "از پیامها:"
        ])
        patterns_in_msg = self.get_message_patterns(text, is_citizen=is_citizen_report)
        
        # Check if previously alerted
        if msg_key in self.sentinel.vip_alert_history:
            prev = self.sentinel.vip_alert_history[msg_key]
            time_diff = now - prev.get('time', now)
            target_ch = prev.get('target_channel')
            orig_sent_id = prev.get('sent_msg_id')
            
            if time_diff <= 259200 and text != prev.get('text') and orig_sent_id and target_ch:
                self.sentinel.vip_alert_history[msg_key]['time'] = now
                self.sentinel.vip_alert_history[msg_key]['text'] = text
                
                ai_class = await self.classify_message(text)
                if ai_class:
                    topic_title = ai_class.get('topic_title', '').strip()
                    category = ai_class.get('category')
                    is_iran = self.sentinel.determine_iran_related(ai_class, text, topic_title)
                    ai_urgency = ai_class.get('urgency_level') if ai_class else None
                    tier, icon, alert_type_name, _ = classify_war_urgency(
                        ai_urgency=ai_urgency,
                        text=text,
                        title=topic_title,
                        is_iran_related=is_iran
                    )
                    alert_title = f"{alert_type_name}: {topic_title}"
                    if category == 'ECONOMY':
                        alert_title = f"گزارش اقتصادی: {topic_title}"
                        icon = "📈"
                    elif category == 'PROTEST_RIGHTS':
                        alert_title = f"گزارش حقوق بشری: {topic_title}"
                        icon = "⚖️"
                        
                    root_media = detect_msg_media_type(raw_msg)
                    root_icon = get_media_icon_from_type(root_media)
                    tag = "#دیده‌بان_جنگ" if target_ch == "@DidebanJang" else ("#دیده‌بان_اقتصاد" if target_ch == "@DidehbanEghtesad" else "#دیده‌بان_اعتراضات")
                    time_fa = get_tehran_time_fa()
                    updated_alert = (
                        f"{icon} **{alert_title}**\n\n"
                        f"🔗 **منبع خبر:**\n- [{canonical_node}]({link}) {root_icon}\n\n"
                        f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | دریافت اطلاعات تکمیلی**\n\n"
                        f"{tag}\n\n{target_ch}"
                    )
                    try:
                        await self.sentinel.safe_edit_post(target_ch, orig_sent_id, updated_alert)
                        asyncio.create_task(self.append_ai_summary(target_ch, orig_sent_id, updated_alert, [text]))
                        print(f"✏️ Updated VIP alert {orig_sent_id} in {target_ch} with in-place status line.")
                    except Exception as edit_err:
                        print(f"⚠️ Failed to edit VIP alert {orig_sent_id} in {target_ch}: {edit_err}")
                else:
                    time_fa = get_tehran_time_fa()
                    try:
                        curr_msg = await self.sentinel.bot.get_messages(target_ch, ids=orig_sent_id)
                        if curr_msg and curr_msg.text:
                            status_line = f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | دریافت اطلاعات تکمیلی**"
                            if '🔄 **به‌روزرسانی شده' in curr_msg.text:
                                new_t = re.sub(r'🔄\s*\*\*به‌روزرسانی شده[^\n]+', status_line, curr_msg.text)
                            else:
                                tag = "#دیده‌بان_جنگ" if target_ch == "@DidebanJang" else ("#دیده‌بان_اقتصاد" if target_ch == "@DidehbanEghtesad" else "#دیده‌بان_اعتراضات")
                                new_t = curr_msg.text.replace(tag, f"{status_line}\n\n{tag}")
                            await self.sentinel.safe_edit_post(target_ch, orig_sent_id, new_t)
                            print(f"✏️ Fallback updated VIP alert {orig_sent_id} in {target_ch}.")
                    except Exception as fb_err:
                        print(f"⚠️ Failed to fallback edit VIP alert {orig_sent_id} in {target_ch}: {fb_err}")
                self.sentinel.save_persisted_state()
            return
        
        # New message check
        if not patterns_in_msg:
            has_war_keyword = any(w in text for w in [
                'حمله', 'موشک', 'پهپاد', 'انفجار', 'بمباران', 'آژیر', 'پدافند', 'جنگنده', 
                'سپاه', 'ارتش', 'درگیری', 'نظامی', 'سقوط', 'کشته', 'مجروح', 'تلفات', 'تیراندازی', 'عملیات',
                'آرامکو', 'پایگاه', 'ناو', 'بمب‌افکن', 'سوخت‌رسان', 'رادار', 'سنتکام', 'جبهه', 'اصابت', 'شهید'
            ])
            is_economy = any(w in text for w in ['دلار', 'تتر', 'ارز', 'طلا', 'سکه', 'بورس', 'تورم', 'قیمت'])
            is_protest = any(w in text for w in ['اعتراض', 'اعتصاب', 'اعدام', 'تجمع', 'زندان', 'دادگاه', 'بازداشت', 'حقوق بشری'])
            if not has_war_keyword and not is_economy and not is_protest:
                return

        self.sentinel.vip_alert_history[msg_key] = {'time': now, 'pattern': patterns_in_msg[0] if patterns_in_msg else "vip_report", 'text': text}
        if msg_key not in self.sentinel.alerted_msg_patterns:
            self.sentinel.alerted_msg_patterns[msg_key] = set()
            
        clean_pats = [p.replace("||FOREIGN||", "") for p in patterns_in_msg]
        for p in clean_pats:
            self.sentinel.alerted_msg_patterns[msg_key].add(p)
            
        ai_class = await self.classify_message(text)
        if ai_class:
            category = ai_class.get('category')
            topic_title = ai_class.get('topic_title', '').strip()
            if category == 'OTHER':
                return
            if category == 'ECONOMY':
                target_channel = "@DidehbanEghtesad"
                alert_title = f"گزارش اقتصادی: {topic_title}"
                custom_icon = "📈"
                is_silent = False if not is_edit else True
            elif category == 'PROTEST_RIGHTS':
                target_channel = "@DidebanEterazat"
                is_rights = any(w in text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت", "محبوس", "قوه قضائیه", "فساد", "تخلف"])
                alert_title = f"گزارش حقوق بشری: {topic_title}" if is_rights else f"گزارش مردمی: {topic_title}"
                custom_icon = "⚖️" if any(w in text for w in ["اعدام", "حکم", "طناب دار", "دادگاه", "قوه قضائیه", "بازداشت"]) else "📢"
                is_silent = False if not is_edit else True
            elif category == 'WAR':
                target_channel = "@DidebanJang"
                is_iran = self.sentinel.determine_iran_related(ai_class, text, topic_title)
                ai_urgency = ai_class.get('urgency_level') if ai_class else None
                tier, custom_icon, alert_type_name, is_silent = classify_war_urgency(
                    ai_urgency=ai_urgency,
                    text=text,
                    title=topic_title,
                    is_iran_related=is_iran
                )
                alert_title = f"{alert_type_name}: {topic_title}"
                if is_edit:
                    is_silent = True
            else:
                return
                
            is_iran = self.sentinel.determine_iran_related(ai_class, text, topic_title) if category == 'WAR' else False
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
                
            self.sentinel.vip_alert_history[msg_key]['target_channel'] = target_channel
            baseline = self.sentinel.baselines.get(clean_pats[0], 0.1) if clean_pats else 0.1
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
                self.sentinel.vip_alert_history[msg_key]['sent_msg_id'] = sent_msg.id
                thread_key = f"{canonical_node}_{msg_id}"
                self.sentinel.thread_alerts[thread_key] = {
                    'channel': target_channel,
                    'alert_msg_id': sent_msg.id,
                    'node': canonical_node,
                    'root_id': msg_id,
                    'root_text': text,
                    'root_media_type': detect_msg_media_type(raw_msg),
                    'timestamp': time.time(),
                    'category': category,
                    'is_iran_related': is_iran,
                    'title': topic_title,
                    'summary': topic_summary,
                    'tag': '#دیده‌بان_اقتصاد' if target_channel == '@DidehbanEghtesad' else ('#دیده‌بان_اعتراضات' if target_channel == '@DidebanEterazat' else '#دیده‌بان_جنگ'),
                    'sources': [{
                        'node': canonical_node,
                        'msg_id': msg_id,
                        'media_type': detect_msg_media_type(raw_msg),
                        'link': link,
                        'timestamp': time.time()
                    }],
                    'replies': []
                }
                self.sentinel.save_persisted_state()
                if not topic_summary:
                    asyncio.create_task(self.append_ai_summary(target_channel, sent_msg.id, alert_text, [text]))


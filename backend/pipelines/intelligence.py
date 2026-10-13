import re
import time
import json
import asyncio
from datetime import datetime, timezone
from backend.core.config import (
    CHANNEL_CANONICAL_TAGS,
    get_tehran_time_fa,
    get_tehran_datetime_fa,
    format_update_status_line,
    detect_msg_media_type,
    get_media_icon_from_type,
    classify_war_urgency,
    calculate_source_latency,
    safe_extract_json
)

class IntelligencePipeline:
    """
    Subsystem for VIP Intelligence / Editorial Synthesis channels:
    - withyashar (breaking Persian analytical news)
    - Alibk / Alibk3 (regional military/security reporting in Arabic)
    Features AI editorial freedom, complete verbs, contextual explanation, and debounced thread tracking.
    """
    def __init__(self, sentinel, threads=None):
        self.sentinel = sentinel
        self.threads = threads or getattr(sentinel, 'threads', None)
        self.withyashar_msg_locks = {}
        self.alibk_msg_locks = {}
        self.alibk_posted = {}
        self.alibk_grouped_posted = set()

    # ==========================================================
    # --- WITHYASHAR PIPELINE (DELEGATED TO YASHAR AUTONOMOUS) ---
    # ==========================================================

    async def handle_withyashar(self, text, msg_id, raw_msg=None, is_edit=False):
        return await self.sentinel.yashar_pipeline.handle_message(text, msg_id, raw_msg=raw_msg, is_edit=is_edit)

    async def catchup_unposted_withyashar(self, reader_client=None):
        return await self.sentinel.yashar_pipeline.catchup_unposted(reader_client)

    # ==========================================
    # --- ALIBK / ALIBK3 PIPELINE ---
    # ==========================================

    async def classify_and_translate_alibk(self, arabic_text):
        if not self.sentinel.gemini.get_api_key():
            return None
            
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
            "۳. تعیین سطح فوریت و اضطرار رویداد (urgency_level):\n"
            "   - 'EMERGENCY': تنها برای حوادث مستقیم نظامی و حملات اضطراری مرتبط با ایران (بمباران، شلیک موشک و پهپاد، اصابت‌ها و انفجارها، آژیر قرمز، پدافند فعال).\n"
            "   - 'BREAKING': اخبار و تحولات مهم، راهبردی، سیاسی و نظامی ایران که بمباران لحظه‌ای نیستند (تحرکات ناوگان، رزمایش‌ها، مواضع مقامات، تحریم‌ها و گزارش‌های اطلاعاتی).\n"
            "   - 'REGIONAL': جنگ و درگیری‌های سایر کشورهای منطقه (یمن، عربستان، آرامکو، الشیبه، غزه، لبنان، سوریه، اوکراین و...) که حمله مستقیم به خاک ایران نیست.\n\n"
            "۴. قوانین تولید تیتر و چکیده:\n"
            "   - اگر category برابر OTHER است، فیلدهای title و summary خالی باشند.\n"
            "   - اگر category برابر WAR است، با تکیه بر دانش ژورنالیستی و زبانی خودت دو فیلد زیر را به زبان فارسی تولید کن:\n"
            "     * title: عنوان و تیتر رسا، جذاب و صریح به زبان فارسی همراه با فعل کامل که اصل اتفاق را بگوید.\n"
            "     * summary: چکیده روان، گویا و منسجم به زبان فارسی با اولویت ایجاز و کوتاه‌سازی در کنار پوشش ابعاد کلیدی رویداد؛ در صورت وجود ابهام، نکات تکمیلی را شفاف کن.\n\n"
            f"متن پیام به زبان عربی:\n{arabic_text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب این شیء JSON ارسال کن و هیچ عبارت دیگری ننویس:\n"
            "{\n"
            '  "category": "WAR" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "urgency_level": "EMERGENCY" | "BREAKING" | "REGIONAL",\n'
            '  "title": "عنوان رسا و کامل فارسی",\n'
            '  "summary": "چکیده شیوا و ابهام‌زدایی‌شده به زبان فارسی"\n'
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
            res = safe_extract_json(raw_out)
            if res and isinstance(res, dict):
                cat = str(res.get("category", "")).upper().strip()
                is_iran = bool(res.get("is_iran_related", False))
                urgency = str(res.get("urgency_level", "REGIONAL")).upper().strip()
                title = str(res.get("title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                if cat in ["WAR", "OTHER"]:
                    return {"category": cat, "is_iran_related": is_iran, "urgency_level": urgency, "title": title, "summary": summary}
        return None

    async def classify_and_translate_alibk_thread(self, thread_text):
        if not self.sentinel.gemini.get_api_key():
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
            "۴. urgency_level: سطح فوریت خبر در صورت جنگی بودن ('EMERGENCY' برای حملات مستقیم میدانی ایران، 'BREAKING' برای تحولات مهم ناوگان و سیاسی ایران، 'REGIONAL' برای جنگ‌های منطقه‌ای).\n"
            "۵. title: یک تیتر رسا، جذاب و صریح به زبان فارسی همراه با فعل کامل که جدیدترین وضعیت نهایی رویداد را بازتاب دهد.\n"
            "۶. summary: یک چکیده روان، منسجم و گویا به زبان فارسی با اولویت ایجاز و خلاصه‌سازی در کنار پوشش کامل نکات جدید اضافه شده؛ در صورت نیاز به توضیح تکمیلی یا رفع ابهام، با دانش خودت آن را شفاف کن.\n\n"
            f"متن رویداد و گزارش‌های تکمیلی:\n{thread_text}\n\n"
            "پاسخ را دقیقاً و فقط در قالب این شیء JSON ارسال کن و هیچ عبارت دیگری ننویس:\n"
            "{\n"
            '  "category": "WAR" | "OTHER",\n'
            '  "is_iran_related": true | false,\n'
            '  "urgency_level": "EMERGENCY" | "BREAKING" | "REGIONAL",\n'
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
        
        raw_out = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
        if raw_out:
            res = safe_extract_json(raw_out)
            if res and isinstance(res, dict):
                cat = str(res.get("category", "")).upper().strip()
                is_iran = bool(res.get("is_iran_related", False))
                urgency = str(res.get("urgency_level", "REGIONAL")).upper().strip()
                title = str(res.get("title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                if cat in ["WAR", "OTHER"]:
                    return {"category": cat, "is_iran_related": is_iran, "urgency_level": urgency, "title": title, "summary": summary}
        return None

    async def preload_alibk_history(self, reader_client=None):
        target_channel = "@DidebanJang"
        client_to_use = reader_client or self.sentinel.bot
        try:
            count = 0
            async for msg in client_to_use.iter_messages(target_channel, limit=100):
                urls = self.sentinel.extract_message_urls(msg)
                for u in urls:
                    match = re.search(r'Alibk3/(\d+)', u, re.IGNORECASE)
                    if match:
                        alibk_id = int(match.group(1))
                        if alibk_id not in self.alibk_posted:
                            self.alibk_posted[alibk_id] = {
                                'channel': target_channel,
                                'msg_id': msg.id,
                                'timestamp': msg.date.timestamp() if msg.date else time.time()
                            }
                            count += 1
                        self.sentinel.db.record_posted_message(target_channel, "Alibk3", alibk_id, msg.id)
                        break
            if count > 0:
                print(f"🔄 Preloaded {count} historical posts for Alibk3 into memory/DB.")
        except Exception as e:
            print(f"⚠️ Could not preload Alibk3 history: {e}")

    async def catchup_unposted_alibk(self, reader_client=None):
        if not reader_client:
            return
        try:
            watermark = self.sentinel.db.get_watermark("Alibk3")
            kwargs = {'limit': 50}
            if watermark > 0:
                kwargs['min_id'] = watermark
            recent_alibk = []
            async for msg in reader_client.iter_messages("Alibk3", **kwargs):
                recent_alibk.append(msg)
            recent_alibk.reverse()
            for msg in recent_alibk:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 5:
                    if msg.id not in self.alibk_posted and not self.sentinel.db.is_source_posted("@DidebanJang", "Alibk3", msg.id):
                        print(f"⚡ Catching up missed Alibk3 post {msg.id} (watermark was {watermark})...")
                        await self.handle_alibk(clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Catchup for Alibk3 failed: {e}")

    async def handle_alibk(self, arabic_text, msg_id, raw_msg=None, is_edit=False):
        if not is_edit:
            self.sentinel.db.advance_watermark("Alibk3", msg_id)

        clean_text = (arabic_text or "").strip()
        if not clean_text or len(clean_text) < 5:
            return

        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in self.alibk_grouped_posted and not is_edit:
            return
        if grouped_id:
            self.alibk_grouped_posted.add(grouped_id)

        thread_key = f"Alibk3_{msg_id}"
        if is_edit and thread_key in self.sentinel.thread_alerts:
            thread_data = self.sentinel.thread_alerts[thread_key]
            thread_data['root_text'] = clean_text
            thread_data['root_media_type'] = detect_msg_media_type(raw_msg)
            print(f"✏️ Alibk3 root post {msg_id} was edited. Scheduling 5m debounce...")
            self.sentinel.schedule_thread_debounce(thread_key)
            self.sentinel.save_persisted_state()
            return

        if is_edit and f"Alibk3_{msg_id}" in self.sentinel.reply_to_root_map:
            root_id = self.sentinel.reply_to_root_map[f"Alibk3_{msg_id}"]
            rk = f"Alibk3_{root_id}"
            if rk in self.sentinel.thread_alerts:
                thread_data = self.sentinel.thread_alerts[rk]
                existing_rep = next((r for r in thread_data.get('replies', []) if r.get('msg_id') == msg_id), None)
                if existing_rep:
                    existing_rep['text'] = clean_text
                    existing_rep['media_type'] = detect_msg_media_type(raw_msg)
                print(f"✏️ Alibk3 reply {msg_id} was edited. Scheduling 5m debounce for root {root_id}...")
                self.sentinel.schedule_thread_debounce(rk)
                self.sentinel.save_persisted_state()
                return

        if msg_id not in self.alibk_msg_locks:
            if len(self.alibk_msg_locks) > 200:
                self.alibk_msg_locks.clear()
            self.alibk_msg_locks[msg_id] = asyncio.Lock()

        async with self.alibk_msg_locks[msg_id]:
            is_already_posted = msg_id in self.alibk_posted
            if is_already_posted and not is_edit:
                return

            ai_res = await self.classify_and_translate_alibk(clean_text)
            if not ai_res:
                print(f"⏳ [ALIBK AI QUEUED] Msg #{msg_id} could not be processed by AI. Enqueueing into Smart Queue (45m TTL)...")
                if hasattr(self.sentinel, 'enqueue_unprocessed_message'):
                    await self.sentinel.enqueue_unprocessed_message(
                        pipeline="alibk",
                        node="Alibk3",
                        msg_id=msg_id,
                        text=clean_text,
                        raw_msg=raw_msg,
                        is_edit=is_edit
                    )
                return

            await self.process_classified_alibk(clean_text, msg_id, ai_res, raw_msg=raw_msg, is_edit=is_edit)

    async def process_classified_alibk(self, clean_text, msg_id, ai_res, raw_msg=None, is_edit=False):
        is_already_posted = msg_id in self.alibk_posted
        if is_already_posted and not is_edit:
            return

        category = ai_res.get('category', 'OTHER')
        if category != 'WAR':
            print(f"ℹ️ Alibk3 post {msg_id} filtered out by news policy (category={category}).")
            return

        title = ai_res.get('title', '').strip()
        summary = ai_res.get('summary', '').strip()
        if not title or not summary:
            return

        is_iran_related = self.sentinel.determine_iran_related(ai_res, clean_text, title)
        ai_urgency = ai_res.get('urgency_level') if ai_res else None
        tier, icon, alert_type_name, is_silent = classify_war_urgency(
            ai_urgency=ai_urgency,
            text=clean_text,
            title=title,
            is_iran_related=is_iran_related
        )

        target_channel = "@DidebanJang"
        tag = CHANNEL_CANONICAL_TAGS.get(target_channel, "#دیده‌بان_جنگ")
        link = f"https://t.me/Alibk3/{msg_id}"

        root_media_type = detect_msg_media_type(raw_msg)
        root_icon = get_media_icon_from_type(root_media_type)

        icon_header = ""
        if root_media_type == 'video': icon_header = " 📹"
        elif root_media_type == 'photo': icon_header = " 📸"
        elif root_media_type == 'voice': icon_header = " 🎙"

        alert_header = f"{icon} **{alert_type_name}: {title}**{icon_header}"

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
                posted_tag = CHANNEL_CANONICAL_TAGS.get(posted_ch, tag)

                thread_key = f"Alibk3_{msg_id}"
                if thread_key in self.sentinel.thread_alerts:
                    thread_data = self.sentinel.thread_alerts[thread_key]
                    thread_data['title'] = title
                    thread_data['summary'] = summary
                    thread_data['updated_at'] = time.time()
                    thread_data['update_reason'] = 'ویرایش و اطلاعات تکمیلی'
                    alert_text = self.sentinel.build_alert_post_content(thread_data)
                else:
                    status_line = format_update_status_line(reason="ویرایش و اطلاعات تکمیلی")
                    alert_text = (
                        f"{alert_header}\n\n"
                        f"📝 **چکیده رویداد:**\n{summary}\n\n"
                        f"{sources_text}\n\n"
                        f"{status_line}\n\n"
                        f"{posted_tag}\n{posted_ch}"
                    )

                try:
                    await self.sentinel.safe_edit_post(posted_ch, posted_mid, alert_text)
                    print(f"✏️ Updated Alibk3 alert in {posted_ch} (Alibk msg {msg_id}) with in-place status line.")
                except Exception as edit_err:
                    if "not modified" in str(edit_err).lower():
                        pass
                    else:
                        raise edit_err
            else:
                merged = await self.sentinel.check_and_merge_with_active_alert(
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

                target_peer = self.sentinel.get_node_input_peer(target_channel) or target_channel
                sent = await self.sentinel.bot.send_message(target_peer, alert_text, link_preview=False, silent=is_silent)
                if sent:
                    self.sentinel.channel_last_alert_id[target_channel] = sent.id
                    self.alibk_posted[msg_id] = {'channel': target_channel, 'msg_id': sent.id, 'timestamp': time.time()}
                    self.sentinel.db.record_posted_message(target_channel, "Alibk3", msg_id, sent.id)
                    ai_model = getattr(self.sentinel.gemini, 'last_used_model', 'unknown')
                    latency_sec, latency_str = calculate_source_latency(raw_msg)
                    self.sentinel.thread_alerts[f"Alibk3_{msg_id}"] = {
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
                        'ai_model': ai_model,
                        'latency_sec': round(latency_sec, 2) if latency_sec is not None else None,
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
                    self.sentinel.save_persisted_state()
                    status_label = "Iran/Sound" if is_iran_related else "Regional/Silent"
                    print(f"🚨 Published Persian Alibk3 alert ({status_label} | Model: {ai_model} | Latency: {latency_str}) to {target_channel} for msg {msg_id}: {title}")
        except Exception as e:
            print(f"❌ Error publishing Alibk3 alert to {target_channel} for msg {msg_id}: {e}")

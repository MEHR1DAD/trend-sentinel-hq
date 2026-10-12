import re
import time
import json
import asyncio
from datetime import datetime, timezone
from backend.core.config import (
    get_tehran_time_fa,
    detect_msg_media_type,
    get_media_icon_from_type
)

class IntelligencePipeline:
    """
    Subsystem for VIP Intelligence / Editorial Synthesis channels:
    - withyashar (breaking Persian analytical news)
    - Alibk / Alibk3 (regional military/security reporting in Arabic)
    Features AI editorial freedom, complete verbs, contextual explanation, and debounced thread tracking.
    """
    def __init__(self, sentinel):
        self.sentinel = sentinel
        self.withyashar_msg_locks = {}
        self.alibk_msg_locks = {}
        self.alibk_posted = {}
        self.alibk_grouped_posted = set()

    # ==========================================
    # --- WITHYASHAR PIPELINE ---
    # ==========================================

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

        raw_text = await self.sentinel.gemini.call_gemini_content(payload, timeout=15, max_retries=6)
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
        clean_first = re.sub(r'\s*\([^)]*\)', '', clean_first)
        clean_first = re.sub(r'\s+([,،])', r'\1', clean_first)
        clean_first = re.sub(r'\s{2,}', ' ', clean_first).strip()
        
        parts = re.split(r'[.؛!?\n]', clean_first)
        candidate_title = parts[0].strip() if parts else clean_first

        # If title starts with a reporting preamble (e.g., "روزنامه X گزارش داد: ..."), extract the core subject
        preamble_match = re.match(r'^(?:[^\n:]{5,50})\s+(?:گزارش داد|اعلام کرد|گفت|افزود|مدعی شد|نوشت)\s*:\s*(.+)$', candidate_title)
        if preamble_match and len(preamble_match.group(1).strip()) >= 20:
            candidate_title = preamble_match.group(1).strip()

        # Remove trailing colons, dashes, or broken punctuation
        candidate_title = candidate_title.rstrip("،,؛.:- ")
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

        # Heuristic Summarizer: Extract concise core factual sentence, never paste raw full text!
        cleaned_paras = [l.strip() for l in clean.split('\n') if l.strip() and not l.strip().startswith('@')]
        content_lines = [p for p in cleaned_paras if p != first_line]
        if not content_lines:
            content_lines = cleaned_paras

        summary_sentences = []
        for line in content_lines:
            sents = re.split(r'(?<=[.؛!?])\s+', line)
            for s in sents:
                s_clean = s.strip()
                if len(s_clean) >= 15 and s_clean not in summary_sentences:
                    summary_sentences.append(s_clean)
                if len(" ".join(summary_sentences)) >= 200:
                    break
            if len(" ".join(summary_sentences)) >= 200:
                break
                
        if summary_sentences:
            summary = " ".join(summary_sentences[:2]).strip()
        else:
            summary = clean[:200].rstrip("،,؛.:- ") + "..."

        is_iran = self.sentinel.determine_iran_related(None, clean, title)

        return {
            "category": category,
            "is_iran_related": is_iran,
            "title": title,
            "summary": summary,
            "is_ai": False
        }

    async def schedule_withyashar_ai_upgrade(self, target_channel, posted_id, src_msg_id, original_text, raw_msg=None, delay=20):
        """
        Background Auto-Upgrade Engine:
        When a breaking news alert was published via emergency heuristic fallback to ensure speed,
        this worker asynchronously rotates Gemini models and re-synthesizes the post,
        editing the live Telegram message with high-quality AI rewrite as soon as available.
        """
        print(f"⏳ [AI UPGRADE] Scheduled background AI enhancement for withyashar msg {src_msg_id} (posted {posted_id}) in {delay}s...")
        await asyncio.sleep(delay)
        for attempt in range(1, 5):
            try:
                await self.sentinel.gemini.rotate_gemini_model_dynamic()
                ai_res = await self.synthesize_withyashar_post(original_text)
                if ai_res and ai_res.get('is_ai'):
                    upgraded_title = ai_res.get('title', '').strip()
                    upgraded_summary = ai_res.get('summary', '').strip()
                    if not upgraded_title or not upgraded_summary:
                        await asyncio.sleep(15)
                        continue
                        
                    category = ai_res.get('category', 'WAR')
                    is_iran_related = self.sentinel.determine_iran_related(ai_res, original_text, upgraded_title)
                    icon = "🚨" if is_iran_related else "🔕"
                    alert_type_name = "هشدار فوری" if is_iran_related else "هشدار منطقه‌ای"
                    if category == 'ECONOMY':
                        icon = "📈"
                        alert_type_name = "گزارش اقتصادی"
                    elif category == 'PROTEST_RIGHTS':
                        icon = "⚖️" if any(w in original_text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت"]) else "📢"
                        alert_type_name = "گزارش حقوق بشری" if icon == "⚖️" else "گزارش مردمی"

                    root_media_type = detect_msg_media_type(raw_msg)
                    root_icon = get_media_icon_from_type(root_media_type)
                    media_header = f" {root_icon}" if root_icon != "📝" else ""

                    alert_header = f"{icon} **{alert_type_name}: {upgraded_title}**{media_header}"
                    link = f"https://t.me/withyashar/{src_msg_id}"
                    sources_text = f"🔗 **منبع خبر:**\n- [withyashar]({link}) {root_icon}"
                    time_fa = get_tehran_time_fa()
                    tag = "#دیده‌بان_اقتصاد" if target_channel == "@DidehbanEghtesad" else ("#دیده‌بان_اعتراضات" if target_channel == "@DidebanEterazat" else "#دیده‌بان_جنگ")

                    updated_text = (
                        f"{alert_header}\n\n"
                        f"📝 **چکیده رویداد:**\n{upgraded_summary}\n\n"
                        f"{sources_text}\n\n"
                        f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و بازنویسی تحلیلی**\n\n"
                        f"{tag}\n{target_channel}"
                    )

                    await self.sentinel.bot.edit_message(target_channel, posted_id, updated_text, link_preview=False)
                    print(f"✨ [AI UPGRADE SUCCESS] Successfully auto-upgraded post {posted_id} in {target_channel}: {upgraded_title}")

                    thread_key = f"withyashar_{src_msg_id}"
                    if thread_key in self.sentinel.thread_alerts:
                        self.sentinel.thread_alerts[thread_key]['title'] = upgraded_title
                        self.sentinel.thread_alerts[thread_key]['summary'] = upgraded_summary
                        self.sentinel.save_persisted_state()
                    return True
            except Exception as e:
                print(f"⚠️ [AI UPGRADE] Attempt {attempt} failed for withyashar post {posted_id}: {e}")
            await asyncio.sleep(25)
        return False

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
                    if not self.sentinel.db.is_source_posted("@DidebanJang", "withyashar", msg.id):
                        await self.handle_withyashar(clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Catchup for withyashar failed: {e}")

    async def handle_withyashar(self, text, msg_id, raw_msg=None, is_edit=False):
        if not is_edit:
            self.sentinel.db.advance_watermark("withyashar", msg_id)
        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 10:
            return

        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in self.sentinel.vip_grouped_posted and not is_edit:
            return
        if grouped_id:
            self.sentinel.vip_grouped_posted.add(grouped_id)

        target_channel = "@DidebanJang"
        if not is_edit and self.sentinel.db.is_source_posted(target_channel, "withyashar", msg_id):
            print(f"🛑 [DEDUP GUARD] withyashar msg {msg_id} already posted to {target_channel}. Aborting.")
            return

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

            is_iran_related = self.sentinel.determine_iran_related(ai_res, clean_text, title)
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
            root_media_type = detect_msg_media_type(raw_msg)
            root_icon = get_media_icon_from_type(root_media_type)

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
                if msg_key in self.sentinel.vip_alert_history and is_edit:
                    posted_mid = self.sentinel.vip_alert_history[msg_key].get('sent_msg_id')
                    posted_ch = self.sentinel.vip_alert_history[msg_key].get('target_channel', target_channel)
                    time_fa = get_tehran_time_fa()
                    updated_text = (
                        f"{alert_header}\n\n"
                        f"📝 **چکیده رویداد:**\n{summary}\n\n"
                        f"{sources_text}\n\n"
                        f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و تکمیل خبر**\n\n"
                        f"{tag}\n{posted_ch}"
                    )
                    try:
                        await self.sentinel.bot.edit_message(posted_ch, posted_mid, updated_text, link_preview=False)
                        print(f"✏️ Edited withyashar post {posted_mid} in {posted_ch}.")
                    except Exception as edit_err:
                        if "not modified" in str(edit_err).lower():
                            pass
                        else:
                            raise edit_err
                else:
                    merged = await self.sentinel.check_and_merge_with_active_alert(
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

                    sent = await self.sentinel.bot.send_message(target_channel, alert_text, link_preview=False, silent=is_silent)
                    if sent:
                        self.sentinel.channel_last_alert_id[target_channel] = sent.id
                        self.sentinel.vip_alert_history[msg_key] = {
                            'target_channel': target_channel,
                            'sent_msg_id': sent.id,
                            'time': time.time(),
                            'text': clean_text,
                            'pattern': 'withyashar_report'
                        }
                        self.sentinel.db.record_posted_message(target_channel, "withyashar", msg_id, sent.id)
                        thread_key = f"withyashar_{msg_id}"
                        self.sentinel.thread_alerts[thread_key] = {
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
                        self.sentinel.save_persisted_state()
                        status_label = "Iran/Sound" if is_iran_related else "Regional/Silent"
                        print(f"🚨 Published intelligent withyashar alert ({status_label}) to {target_channel} for msg {msg_id}: {title}")

                        # Trigger background AI enhancement if emergency fallback was used
                        if not ai_res.get('is_ai'):
                            asyncio.create_task(self.schedule_withyashar_ai_upgrade(target_channel, sent.id, msg_id, clean_text, raw_msg=raw_msg))
            except Exception as e:
                print(f"❌ Error publishing withyashar alert to {target_channel} for msg {msg_id}: {e}")

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
        
        raw_out = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
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
        
        raw_out = await self.sentinel.gemini.call_gemini_content(payload, timeout=14, max_retries=5)
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
            now_utc = datetime.now(timezone.utc)
            recent_alibk = []
            async for msg in reader_client.iter_messages("Alibk3", limit=5):
                if msg.date and (now_utc - msg.date).total_seconds() > 300:
                    continue
                recent_alibk.append(msg)
            recent_alibk.reverse()
            for msg in recent_alibk:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 5:
                    if msg.id not in self.alibk_posted and not self.sentinel.db.is_source_posted("@DidebanJang", "Alibk3", msg.id):
                        print(f"⚡ Catching up missed Alibk3 post {msg.id}...")
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
                print(f"⚠️ Alibk3 AI processing skipped/failed for msg {msg_id}")
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
            is_silent = not is_iran_related

            target_channel = "@DidebanJang"
            tag = "#دیده‌بان_جنگ"
            link = f"https://t.me/Alibk3/{msg_id}"

            root_media_type = detect_msg_media_type(raw_msg)
            root_icon = get_media_icon_from_type(root_media_type)

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
                    if thread_key in self.sentinel.thread_alerts:
                        thread_data = self.sentinel.thread_alerts[thread_key]
                        thread_data['title'] = title
                        thread_data['summary'] = summary
                        thread_data['updated_at'] = time.time()
                        thread_data['update_reason'] = 'ویرایش و اطلاعات تکمیلی'
                        alert_text = self.sentinel.build_alert_post_content(thread_data)
                    else:
                        time_fa = get_tehran_time_fa()
                        alert_text = (
                            f"{alert_header}\n\n"
                            f"📝 **چکیده رویداد:**\n{summary}\n\n"
                            f"{sources_text}\n\n"
                            f"🔄 **به‌روزرسانی شده (ساعت {time_fa}) | ویرایش و اطلاعات تکمیلی**\n\n"
                            f"{tag}\n{posted_ch}"
                        )
                        
                    try:
                        await self.sentinel.bot.edit_message(posted_ch, posted_mid, alert_text, link_preview=False)
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

                    sent = await self.sentinel.bot.send_message(target_channel, alert_text, link_preview=False, silent=is_silent)
                    if sent:
                        self.sentinel.channel_last_alert_id[target_channel] = sent.id
                        self.alibk_posted[msg_id] = {'channel': target_channel, 'msg_id': sent.id, 'timestamp': time.time()}
                        self.sentinel.db.record_posted_message(target_channel, "Alibk3", msg_id, sent.id)
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
                        print(f"🚨 Published Persian Alibk3 alert ({status_label}) to {target_channel} for msg {msg_id}: {title}")
            except Exception as e:
                print(f"❌ Error publishing Alibk3 alert to {target_channel} for msg {msg_id}: {e}")

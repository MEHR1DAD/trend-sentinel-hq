import re
import time
import json
import asyncio
from datetime import datetime, timezone
from backend.core.config import (
    get_tehran_datetime_fa,
    format_update_status_line,
    detect_msg_media_type,
    get_media_icon_from_type,
    classify_war_urgency
)

class YasharAutonomousPipeline:
    """
    Dedicated 100% Autonomous AI Editorial Pipeline for @withyashar.
    
    The AI acts as Senior Analytical Editor-in-Chief (سردبیر ارشد و تحلیلی دیده‌بان) with full authority to:
    1. Decide whether to publish (PUBLISH), merge (MERGE), or drop non-editorial posts (DROP).
    2. Route to the optimal target channel (@DidebanJang, @DidehbanEghtesad, @DidebanEterazat).
    3. Determine urgency level (EMERGENCY / BREAKING / REGIONAL).
    4. Provide deep analytical context and explain tactical/geopolitical implications.
    5. Write strong, standalone headlines with complete verbs.
    """
    def __init__(self, sentinel, threads=None):
        self.sentinel = sentinel
        self.threads = threads or getattr(sentinel, 'threads', None)
        self.msg_locks = {}
        self.grouped_posted = set()

    # ==========================================
    # --- AI EDITORIAL AGENT CORE ---
    # ==========================================

    async def analyze_and_synthesize(self, text, raw_msg=None):
        clean = (text or "").strip()
        clean_prompt_text = re.sub(r'@\w+\s*$', '', clean).strip()

        media_type = detect_msg_media_type(raw_msg)

        prompt = (
            "تو «سردبیر ارشد و تحلیلی هوشمند شبکه رسانه‌ای دیده‌بان» (دیده‌بان جنگ، اقتصاد، اعتراضات) هستی.\n"
            "پست زیر از کانال خبری و تحلیلی 'withyashar' (یکی از مهم‌ترین منابع مانیتورینگ منطقه‌ای) دریافت شده است:\n\n"
            f"--- متن پست خام ---\n{clean_prompt_text}\n-------------------\n\n"
            f"نوع پیوست چندرسانه‌ای: {media_type}\n\n"
            "تو دارای ۱۰۰٪ آزادی عمل و اختیارات کامل سردبیری برای تصمیم‌گیری و تنظیم خبر هستی. وظایف تو عبارتند از:\n\n"
            "۱. decision (تصمیم انتشار):\n"
            "   - 'PUBLISH': خبر یا تحلیل دارای ارزش ژورنالیستی و اطلاعاتی برای مخاطب فارسی‌زبان است و باید منتشر شود.\n"
            "   - 'DROP': پیام فاقد هرگونه ارزش خبری یا تحلیلی است (شامل: شوخی‌های توییتری، پیام‌های تبریک/تسلیت و احوالپرسی شخصی، تبلیغات، نظرسنجی‌های بی‌اهمیت، یا گپ‌وگفت غیرخبری). در این صورت drop_reason را بنویس.\n"
            "   - 'MERGE': خبر در ادامه و تکمیل مستقیم یک رویداد بحرانی لحظه‌ای اخیر است که دقایقی پیش رخ داده و بهتر است با پست فعال قبلی ادغام شود.\n\n"
            "۲. target_channel (انتخاب کانال مقصد بر اساس تخصص محتوا):\n"
            "   - '@DidebanJang': رویدادهای نظامی، امنیتی، تسلیحاتی، جنگ، تحرکات ناوها، پهپادها، انفجارها و بیانیه‌های استراتژیک.\n"
            "   - '@DidehbanEghtesad': تحولات اقتصادی، دلار، ارز، طلا، نفت، انرژی، بورس، تجارت و تحریم‌ها.\n"
            "   - '@DidebanEterazat': گزارش‌های کارگری، احکام قضایی، زندان، دادگاه، حقوق بشر و تجمعات مردمی.\n\n"
            "۳. urgency_level (سطح فوریت اعلان برای دیده‌بان جنگ):\n"
            "   - 'EMERGENCY': فقط و فقط برای رویدادهای میدانی زنده و دارای صدای آژیر (شلیک موشک و پهپاد، صدای انفجار، بمباران مستقیم، آژیر قرمز، پدافند فعال و نبرد میدانی زنده).\n"
            "   - 'BREAKING': تحولات مهم و فوری بدون آژیر (سخنان مقامات ارشد، تحرکات ناوها و بمب‌افکن‌ها، گزارش‌های اختصاصی، تصمیمات شورای امنیت).\n"
            "   - 'REGIONAL': سایر گزارش‌ها و درگیری‌های منطقه‌ای/بین‌المللی بدون تهدید و درگیری مستقیم ایران (مانند اوکراین، تحولات داخلی سایر کشورها).\n\n"
            "۴. title (تیتر ژورنالیستی):\n"
            "   - تیتری بسیار رسا، جذاب، معتبر، بدون کلمات کلیشه‌ای (مانند «یاشار نوشت» یا «روزنامه گزارش داد»)، و همراه با «فعل کامل» بنویس که اصل واقعه را مستقل و شفاف بیان کند.\n\n"
            "۵. summary (چکیده تحلیلی سردبیر):\n"
            "   - با نقش «سردبیر تحلیلی»، چکیده‌ای شیوا، خلاصه و دقیق بنویس. علاوه بر فکت اصلی، اگر مفاهیم، پایگاه‌های نظامی، نام موشک‌ها یا کانتکست ژئوپلیتیک نیاز به روشنگری دارد، با دانش عمومی خودت آن را برای مخاطب شفاف و ابهام‌زدایی کن (مثلاً توضیح کارکرد یک پایگاه نظامی، مفهوم یک پیمان، یا اهمیت یک تصمیم).\n\n"
            "۶. editorial_reasoning:\n"
            "   - استدلال کوتاه ۱ جمله‌ای خودت درباره علت این تصمیم و شیوه تنظیم.\n\n"
            "پاسخ را منحصراً در قالب این شیء JSON ارسال کن و هیچ متن اضافه‌ای بیرون آن ننویس:\n"
            "{\n"
            '  "decision": "PUBLISH" | "DROP" | "MERGE",\n'
            '  "drop_reason": "دلیل رد خبر در صورت تصمیم DROP یا رشته خالی",\n'
            '  "target_channel": "@DidebanJang" | "@DidehbanEghtesad" | "@DidebanEterazat",\n'
            '  "urgency_level": "EMERGENCY" | "BREAKING" | "REGIONAL",\n'
            '  "is_iran_related": true | false,\n'
            '  "title": "تیتر رسا با فعل کامل",\n'
            '  "summary": "چکیده تحلیلی و ابهام‌زدایی‌شده",\n'
            '  "editorial_reasoning": "استدلال سردبیر"\n'
            "}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 600,
                "temperature": 0.2
            }
        }

        raw_text = await self.sentinel.gemini.call_gemini_content(payload, timeout=20, max_retries=6)
        if raw_text:
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                try:
                    res = json.loads(match.group(0))
                    decision = str(res.get("decision", "PUBLISH")).upper().strip()
                    drop_reason = str(res.get("drop_reason", "")).strip()
                    target_channel = str(res.get("target_channel", "@DidebanJang")).strip()
                    if target_channel not in ("@DidebanJang", "@DidehbanEghtesad", "@DidebanEterazat"):
                        target_channel = "@DidebanJang"
                    urgency_level = str(res.get("urgency_level", "BREAKING")).upper().strip()
                    is_iran_related = bool(res.get("is_iran_related", False))
                    title = str(res.get("title", "")).strip()
                    summary = str(res.get("summary", "")).strip()
                    editorial_reasoning = str(res.get("editorial_reasoning", "")).strip()

                    return {
                        "decision": decision,
                        "drop_reason": drop_reason,
                        "target_channel": target_channel,
                        "urgency_level": urgency_level,
                        "is_iran_related": is_iran_related,
                        "title": title,
                        "summary": summary,
                        "editorial_reasoning": editorial_reasoning,
                        "is_ai": True
                    }
                except Exception as parse_err:
                    print(f"⚠️ Failed to parse Yashar AI JSON response: {parse_err}")

        # Emergency Fallback only if Gemini API is completely unreachable
        return self._generate_emergency_fallback(clean_prompt_text)

    def _generate_emergency_fallback(self, text):
        clean = re.sub(r'@\w+\s*$', '', (text or "")).strip()
        lines = [l.strip() for l in clean.split('\n') if l.strip()]
        first_line = lines[0] if lines else clean
        
        is_economy = any(w in clean for w in ['دلار', 'تتر', 'ارز', 'طلا', 'سکه', 'بورس', 'تورم', 'قیمت'])
        is_protest = any(w in clean for w in ['اعتراض', 'اعتصاب', 'اعدام', 'تجمع', 'زندان', 'دادگاه', 'بازداشت'])
        target_ch = "@DidehbanEghtesad" if is_economy else ("@DidebanEterazat" if is_protest else "@DidebanJang")
        
        clean_title = re.sub(r'^[🚨⚡️📢🛑🔕⚖️📈📸📹🎙🔴⚠️📌\s\*\-]+', '', first_line).strip()
        if len(clean_title) > 90:
            clean_title = clean_title[:87] + "..."

        return {
            "decision": "PUBLISH",
            "drop_reason": "",
            "target_channel": target_ch,
            "urgency_level": "BREAKING",
            "is_iran_related": True,
            "title": clean_title,
            "summary": clean,
            "editorial_reasoning": "Emergency fallback triggered due to AI connectivity issue.",
            "is_ai": False
        }

    # ==========================================
    # --- MESSAGE PROCESSING & ROUTING ---
    # ==========================================

    async def handle_message(self, text, msg_id, raw_msg=None, is_edit=False):
        if not is_edit:
            self.sentinel.db.advance_watermark("withyashar", msg_id)

        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 10:
            return

        # Deduplicate media group items
        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and grouped_id in self.grouped_posted and not is_edit:
            return
        if grouped_id:
            self.grouped_posted.add(grouped_id)

        msg_lock = self.msg_locks.setdefault(msg_id, asyncio.Lock())
        async with msg_lock:
            # 1. Ask Autonomous AI Editor
            ai_eval = await self.analyze_and_synthesize(clean_text, raw_msg=raw_msg)
            if not ai_eval:
                return

            decision = ai_eval.get('decision', 'PUBLISH')
            target_channel = ai_eval.get('target_channel', '@DidebanJang')
            title = ai_eval.get('title', '').strip()
            summary = ai_eval.get('summary', '').strip()
            editorial_reasoning = ai_eval.get('editorial_reasoning', '')

            # 2. Handle DROP Decision
            if decision == 'DROP':
                drop_reason = ai_eval.get('drop_reason', 'فاقد ارزش خبری یا غیرمرتبط')
                print(f"🛑 [YASHAR AI: DROPPED] Msg #{msg_id} was filtered out by AI Editor.")
                print(f"   Reason: {drop_reason} | Reasoning: {editorial_reasoning}")
                return

            # Dedup guard for publish
            if not is_edit and self.sentinel.db.is_source_posted(target_channel, "withyashar", msg_id):
                print(f"🛑 [DEDUP GUARD] withyashar msg {msg_id} already posted to {target_channel}. Aborting.")
                return

            if not title or not summary:
                return

            # 3. Determine presentation and tags
            is_iran_related = ai_eval.get('is_iran_related', True)
            ai_urgency = ai_eval.get('urgency_level', 'BREAKING')

            if target_channel == '@DidehbanEghtesad':
                tag = "#دیده‌بان_اقتصاد"
                icon = "📈"
                alert_type_name = "گزارش اقتصادی"
                is_silent = False
            elif target_channel == '@DidebanEterazat':
                tag = "#دیده‌بان_اعتراضات"
                is_legal = any(w in clean_text for w in ["اعدام", "حکم", "طناب دار", "زندان", "دادگاه", "بازداشت"])
                icon = "⚖️" if is_legal else "📢"
                alert_type_name = "گزارش حقوق بشری" if icon == "⚖️" else "گزارش مردمی"
                is_silent = False
            else:
                target_channel = "@DidebanJang"
                tag = "#دیده‌بان_جنگ"
                tier, icon, alert_type_name, is_silent = classify_war_urgency(
                    ai_urgency=ai_urgency,
                    text=clean_text,
                    title=title,
                    is_iran_related=is_iran_related
                )

            link = f"https://t.me/withyashar/{msg_id}"
            root_media_type = detect_msg_media_type(raw_msg)
            root_icon = get_media_icon_from_type(root_media_type)

            media_header = f" {root_icon}" if root_icon != "📝" else ""
            alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"
            sources_text = f"🔗 **منبع خبر:**\n- [withyashar]({link}) {root_icon}"

            alert_text = (
                f"{alert_header}\n\n"
                f"📝 **چکیده تحلیلی رویداد:**\n{summary}\n\n"
                f"{sources_text}\n\n"
                f"{tag}\n{target_channel}"
            )

            msg_key = f"withyashar_{msg_id}"

            # 4. Handle Edit / Update
            if is_edit and msg_key in self.sentinel.vip_alert_history:
                posted_mid = self.sentinel.vip_alert_history[msg_key].get('sent_msg_id')
                posted_ch = self.sentinel.vip_alert_history[msg_key].get('target_channel', target_channel)
                status_line = format_update_status_line(reason="ویرایش و تکمیل خبر")

                updated_text = (
                    f"{alert_header}\n\n"
                    f"📝 **چکیده تحلیلی رویداد:**\n{summary}\n\n"
                    f"{sources_text}\n\n"
                    f"{status_line}\n\n"
                    f"{tag}\n{posted_ch}"
                )
                try:
                    await self.sentinel.safe_edit_post(posted_ch, posted_mid, updated_text)
                    print(f"✏️ [YASHAR AI: EDITED] Post {posted_mid} in {posted_ch} updated with in-place analysis.")
                except Exception as edit_err:
                    if "not modified" in str(edit_err).lower():
                        pass
                    else:
                        print(f"⚠️ Error editing withyashar post: {edit_err}")
                return

            # 5. Handle MERGE Decision
            if decision == 'MERGE' and self.threads:
                merged = await self.threads.check_and_merge_with_active_alert(
                    text=clean_text,
                    node="withyashar",
                    msg_id=msg_id,
                    target_channel=target_channel,
                    candidate_title=title,
                    candidate_summary=summary,
                    raw_msg=raw_msg,
                    category='WAR' if target_channel == '@DidebanJang' else ('ECONOMY' if target_channel == '@DidehbanEghtesad' else 'PROTEST_RIGHTS'),
                    is_iran_related=is_iran_related
                )
                if merged:
                    print(f"🔀 [YASHAR AI: MERGED] Successfully merged withyashar msg {msg_id} into active alert in {target_channel}.")
                    return

            # 6. Publish to Target Channel
            try:
                sent = await self.sentinel.bot.send_message(target_channel, alert_text, link_preview=False, silent=is_silent)
                if sent:
                    self.sentinel.channel_last_alert_id[target_channel] = sent.id
                    self.sentinel.vip_alert_history[msg_key] = {
                        'target_channel': target_channel,
                        'sent_msg_id': sent.id,
                        'time': time.time(),
                        'text': clean_text,
                        'pattern': 'withyashar_autonomous'
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
                        'category': 'WAR' if target_channel == '@DidebanJang' else ('ECONOMY' if target_channel == '@DidehbanEghtesad' else 'PROTEST_RIGHTS'),
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

                    urg_label = "Alarm/Sound" if not is_silent else "Silent"
                    print(f"✨ [YASHAR AI: PUBLISHED] Posted to {target_channel} ({urg_label}) for msg #{msg_id}:")
                    print(f"   📌 Title: {title}")
                    print(f"   💡 Editorial Rationale: {editorial_reasoning}")
            except Exception as pub_err:
                print(f"❌ Error publishing autonomous withyashar alert to {target_channel}: {pub_err}")

    # ==========================================
    # --- CATCHUP SCANNER ---
    # ==========================================

    async def catchup_unposted(self, reader_client=None):
        if not reader_client:
            return
        try:
            watermark = self.sentinel.db.get_watermark("withyashar")
            kwargs = {'limit': 30}
            if watermark > 0:
                kwargs['min_id'] = watermark
            recent_msgs = []
            async for msg in reader_client.iter_messages("withyashar", **kwargs):
                recent_msgs.append(msg)
            recent_msgs.reverse()
            for msg in recent_msgs:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 10:
                    if not self.sentinel.db.is_source_posted("@DidebanJang", "withyashar", msg.id):
                        print(f"⚡ [YASHAR AUTONOMOUS] Catching up missed post #{msg.id} (watermark: {watermark})...")
                        await self.handle_message(clean_text, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Yashar catchup failed: {e}")

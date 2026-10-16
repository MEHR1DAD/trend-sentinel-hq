import re
import time
import json
import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
from backend.core.config import (
    CHANNEL_CANONICAL_TAGS,
    COUNTRY_THEATRES,
    FOREIGN_KEYWORDS,
    IRAN_KEYWORDS,
    get_tehran_time_fa,
    get_tehran_datetime_fa,
    format_update_status_line,
    to_fa_num,
    detect_msg_media_type,
    get_media_icon_from_type,
    classify_war_urgency,
    calculate_source_latency,
    detect_territory_scope,
    safe_extract_json
)

class VipMultiAgentEditorialPipeline:
    """
    سیستم تحریریه ۴ لایه تمام‌خودکار چندعامله (Multi-Agent Editorial Pipeline)
    برای تمامی کانال‌های VIP:
    - @withyashar
    - @Alibk3
    - @VahidOnline
    - @iliaen
    - @netblocks

    ساختار سربازها:
    - سرباز شماره ۰ (Soldier 0): مدیریت استراتژیک پشته مدل‌ها و ماتریس مدل × کلید (پیاده‌سازی شده در GeminiClient)
    - سرباز شماره ۱+۲ (Soldier 1+2): دروازه‌بان و مسیرساز تک‌ضرب فوق‌سریع دو زبانه (Flash-Lite)
    - سرباز شماره ۴ (Soldier 4): دیده‌بان رشته‌های فعال کانال در پنجره سخت‌گیرانه (Flash)
    - سرباز شماره ۳ (Soldier 3): سردبیر و تحلیلگر ارشد حوزه تخصصی (Flash) با ۳ پرسونا (دیده‌بان جنگ، اقتصاد، اعتراضات)
    - لایه انتشار نهایی: ارسال فوری، برچسب‌های کانونی قطعی، همگام‌سازی درجا و لاگ تفصیلی
    """
    def __init__(self, sentinel, threads=None):
        self.sentinel = sentinel
        self.threads = threads or getattr(sentinel, 'threads', None)
        self.msg_locks = {}
        self.grouped_posted = OrderedDict()
        self.media_group_buffers = {}
        self.thread_decision_lock = asyncio.Lock()
        self.in_flight_alerts = {}

    # ========================================================
    # --- قفل آهنین ضد تکرار و چتر نجات (Ironclad Dedup & Parachute) ---
    # ========================================================

    def find_existing_posted_alert(self, node, msg_id, target_channel=None):
        """
        بررسی چندلایه‌ای برای یافتن شناسه پست تلگرامی که قبلاً برای (node, msg_id) ارسال شده است.
        """
        msg_key = f"{node}_{msg_id}"

        # ۱. تاریخچه هشدارهای VIP در حافظه
        if msg_key in self.sentinel.vip_alert_history:
            entry = self.sentinel.vip_alert_history[msg_key]
            ch = entry.get('target_channel')
            sent_id = entry.get('sent_msg_id')
            if sent_id and (target_channel is None or ch == target_channel):
                return ch, sent_id

        # ۲. پایگاه داده پایدار SQLite (posted_sources)
        target_channels = [target_channel] if target_channel else ['@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat']
        for ch in target_channels:
            posted_id = self.sentinel.db.get_posted_message_id(ch, node, msg_id)
            if posted_id:
                return ch, posted_id

        # ۳. رشته‌های فعال ۶ ساعته (thread_alerts)
        for tk, td in self.sentinel.thread_alerts.items():
            if not isinstance(td, dict):
                continue
            ch = td.get('channel')
            alert_msg_id = td.get('alert_msg_id')
            if not alert_msg_id or (target_channel and ch != target_channel):
                continue
            if td.get('node') == node and (td.get('root_id') == msg_id or str(td.get('root_id')) == str(msg_id)):
                return ch, alert_msg_id
            for s in td.get('sources', []):
                if s.get('node') == node and (s.get('msg_id') == msg_id or str(s.get('msg_id')) == str(msg_id)):
                    return ch, alert_msg_id

        return None, None

    async def handle_existing_post_edit(self, node, msg_id, clean_text, target_channel, existing_post_id, raw_msg=None):
        """
        مدیریت ویرایش پیام‌های تلگرامی که قبلاً منتشر شده‌اند (In-Place Edit Only):
        تحت هیچ شرایطی پست نو ایجاد نمی‌شود. متن توسط سرباز ۳ بازنویسی شده و پست موجود درجا ویرایش می‌گردد.
        """
        print(f"✏️ [VIP EDIT HANDLER] Processing Telegram edit for {node}_{msg_id} on existing post #{existing_post_id} in {target_channel}...")

        # بررسی اینکه آیا این پست ریشه یا عضوی از یک رشته ۶ ساعته است
        target_thread_key = None
        target_thread_data = None
        for tk, td in self.sentinel.thread_alerts.items():
            if not isinstance(td, dict):
                continue
            if td.get('channel') == target_channel and td.get('alert_msg_id') == existing_post_id:
                target_thread_key = tk
                target_thread_data = td
                break

        urgency = "BREAKING"
        is_iran = False
        if target_thread_data:
            urgency = target_thread_data.get('urgency_level', 'BREAKING')
            is_iran = target_thread_data.get('is_iran_related', False)

        editor_info = await self.soldier_3_domain_editor(
            clean_text, node, target_channel,
            is_merge=True if target_thread_data else False,
            existing_thread_data=target_thread_data,
            urgency_level=urgency,
            is_iran_related=is_iran
        )
        if not editor_info:
            print(f"⏳ [VIP EDIT] Soldier 3 failed during edit handling. Enqueuing for retry...")
            if hasattr(self.sentinel, 'enqueue_unprocessed_message'):
                await self.sentinel.enqueue_unprocessed_message(
                    pipeline="vip_multi_agent", node=node, msg_id=msg_id, text=clean_text, raw_msg=raw_msg, is_edit=True
                )
            return

        title = editor_info['title']
        summary = editor_info['summary']
        ai_model = editor_info.get('model', 'unknown')

        tag = CHANNEL_CANONICAL_TAGS.get(target_channel, '#دیده‌بان_جنگ')
        root_media_type = detect_msg_media_type(raw_msg)
        root_icon = get_media_icon_from_type(root_media_type)
        media_header = f" {root_icon}" if root_icon != "📝" else ""
        link = f"https://t.me/{node}/{msg_id}"

        # تعیین هدر بر اساس کانال
        if target_channel == '@DidebanJang':
            tier, icon, alert_type_name, is_silent = classify_war_urgency(
                ai_urgency=urgency,
                text=summary,
                title=title,
                is_iran_related=is_iran
            )
            alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"
        elif target_channel == '@DidehbanEghtesad':
            icon = "📈"
            alert_type_name = "گزارش اقتصادی"
            alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"
        else:
            alert_header = f"📢 **گزارش دیده‌بان: {title}**{media_header}"

        if target_thread_data:
            target_thread_data['title'] = title
            target_thread_data['summary'] = summary
            target_thread_data['updated_at'] = time.time()
            target_thread_data['update_reason'] = 'ویرایش و اصلاح اطلاعات توسط منبع'
            updated_text = self.threads.build_alert_post_content(target_thread_data) if self.threads else ""
            if not updated_text:
                status_line = format_update_status_line(target_thread_data['updated_at'], target_thread_data['update_reason'])
                sources_lines = [f"- [{s.get('node', 'منبع')}]({s.get('link', '')}) {get_media_icon_from_type(s.get('media_type', 'text'))}" for s in target_thread_data.get('sources', [])]
                updated_text = (
                    f"{alert_header}\n\n"
                    f"📝 **چکیده رویداد:**\n{summary}\n\n"
                    f"🔗 **منابع خبر:**\n" + "\n".join(sources_lines) + "\n\n"
                    f"{status_line}\n\n"
                    f"{tag}\n{target_channel}"
                )
        else:
            status_line = format_update_status_line(time.time(), 'ویرایش و اصلاح متن توسط منبع')
            updated_text = (
                f"{alert_header}\n\n"
                f"📝 **چکیده تحلیلی رویداد:**\n{summary}\n\n"
                f"🔗 **منبع خبر:**\n- [{node}]({link}) {root_icon}\n\n"
                f"{status_line}\n\n"
                f"{tag}\n{target_channel}"
            )

        # ویرایش درجا در تلگرام بدون ارسال پست نو
        try:
            await self.sentinel.safe_edit_post(target_channel, existing_post_id, updated_text, msg_obj=raw_msg)
            print(f"✅ [VIP EDIT SUCCESS] Updated post #{existing_post_id} in {target_channel} for edited {node}_{msg_id}.")
        except Exception as edit_err:
            if "not modified" not in str(edit_err).lower():
                print(f"⚠️ Error updating edited post #{existing_post_id} in {target_channel}: {edit_err}")

        # به‌روزرسانی تاریخچه
        msg_key = f"{node}_{msg_id}"
        self.sentinel.vip_alert_history[msg_key] = {
            'sent_msg_id': existing_post_id,
            'target_channel': target_channel,
            'time': time.time(),
            'ai_model': ai_model,
            'pattern': 'vip_edit_update'
        }
        self.sentinel.db.record_posted_message(target_channel, node, msg_id, existing_post_id)
        self.sentinel.save_persisted_state()

    async def safe_delete_duplicate_post(self, target_channel, alert_msg_id):
        """
        چتر نجات: حذف پست اشتباه یا تکراری در تلگرام و پاکسازی آن از رجیستری
        """
        try:
            res = await self.sentinel.safe_delete_post(target_channel, alert_msg_id)
            if res:
                print(f"🗑️ [PARACHUTE] Successfully removed duplicate post #{alert_msg_id} from {target_channel}.")
            return res
        except Exception as e:
            print(f"⚠️ [PARACHUTE] Failed to delete duplicate post #{alert_msg_id} in {target_channel}: {e}")
            return False

    async def soldier_1_2_gatekeeper(self, text, node, raw_msg=None):
        """
        سرباز ۱+۲ (Flash-Lite):
        - متن را (فارسی یا عربی) در کمتر از ۱ ثانیه ارزیابی می‌کند.
        - شوخی‌ها، میم‌ها، چت‌های غیرخبری و تبلیغات را حذف (DROP) می‌کند.
        - کانال مقصد (@DidebanJang, @DidehbanEghtesad, @DidebanEterazat) و سطح فوریت را تعیین می‌کند.
        """
        clean_text = (text or "").strip()
        clean_prompt_text = re.sub(r'@\w+\s*$', '', clean_text).strip()
        media_type = detect_msg_media_type(raw_msg)

        prompt = (
            "تو «سرباز شماره ۱+۲ - دروازه‌بان و مسیرساز فوق‌سریع دیده‌بان» هستی.\n"
            f"متن زیر از کانال VIP '{node}' دریافت شده است (ممکن است به زبان فارسی، عربی یا انگلیسی باشد):\n\n"
            f"--- متن خام ---\n{clean_prompt_text}\n----------------\n\n"
            f"نوع پیوست چندرسانه‌ای: {media_type}\n\n"
            "وظیفه تو این است که سریعاً متن را بخوانید و تصمیم بگیرید:\n\n"
            "۱. decision (تصمیم):\n"
            "   - 'ROUTE': اگر پیام دارای ارزش خبری، نظامی، امنیتی، اقتصادی، سیاسی یا حقوق بشری مهم و ملموس است. همچنین اگر پیام از 'netblocks' پیرامون اختلال، قطعی یا افت اینترنت در ایران یا رویدادهای بحرانی جنگی است، حتماً 'ROUTE' شود.\n"
            "   - 'DROP': اگر پیام شوخی، طنز، میم، کل‌کل، احوالپرسی شخصی، تسلیت/تبریک عمومی، تبلیغات، نظرسنجی روزمره، یا محتوای غیرخبری کم‌ارزش است. اگر پیام از 'netblocks' درباره کشورهای دوردست و نامربوط به ایران و خاورمیانه است، 'DROP' شود.\n\n"
            "۲. drop_reason:\n"
            "   - در صورت تصمیم DROP، دلیل خلاصه فارسی را بنویس؛ در غیر این صورت خالی باشد.\n\n"
            "۳. target_channel (انتخاب کانال مناسب):\n"
            "   - '@DidebanJang': اخبار جنگ، نظامی، تحرکات موشکی/پهپادی، حملات هوایی، پدافند، انفجارها، درگیری‌های منطقه‌ای، و اختلالات اینترنت هم‌زمان با وضعیت جنگی.\n"
            "   - '@DidehbanEghtesad': نوسانات دلار، تتر، طلا، سکه، بورس، نفت، گاز، انرژی، تحریم‌ها و رویدادهای اقتصادی.\n"
            "   - '@DidebanEterazat': گزارش‌های کارگری، احکام قضایی، دادگاه، زندان، اعدام، حقوق بشر، اعتراضات مردمی، و قطعی اینترنت مرتبط با اعتراضات مدنی.\n\n"
            "۴. urgency_level (سطح فوریت خبر جنگی):\n"
            "   - 'EMERGENCY': فقط برای رویدادهای میدانی زنده و دارای صدای آژیر یا انفجار لحظه‌ای (شلیک موشک/پهپاد، بمباران مستقیم، آژیر قرمز، پدافند فعال).\n"
            "   - 'BREAKING': اخبار و تحولات مهم و فوری بدون آژیر (سخنان مقامات، تحرکات ناوها، تصمیمات استراتژیک، گزارش‌های اختصاصی).\n"
            "   - 'REGIONAL': سایر درگیری‌ها و نبردهای منطقه‌ای/جهانی بدون رویارویی مستقیم ایران (مانند اوکراین، تحولات داخلی سایر کشورها).\n\n"
            "۵. is_iran_related:\n"
            "   - آیا خبر مستقیماً به ایران، نیروهای مسلح ایران، جبهه جنگ با ایران یا منافع امنیتی مستقیم ایران مرتبط است؟ (true/false)\n\n"
            "۶. candidate_title:\n"
            "   - یک عنوان اولیه کوتاه به زبان فارسی برای موضوع خبر.\n\n"
            "پاسخ را دقیقاً و فقط در قالب شیء JSON زیر بدون هیچ متن اضافی ارسال کن:\n"
            "{\n"
            '  "decision": "ROUTE" | "DROP",\n'
            '  "drop_reason": "...",\n'
            '  "target_channel": "@DidebanJang" | "@DidehbanEghtesad" | "@DidebanEterazat",\n'
            '  "urgency_level": "EMERGENCY" | "BREAKING" | "REGIONAL",\n'
            '  "is_iran_related": true | false,\n'
            '  "candidate_title": "..."\n'
            "}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.15}
        }

        t0 = time.time()
        raw_text, used_model, elapsed = await self.sentinel.gemini.call_soldier_content(
            payload, tier="lite", timeout=12, soldier_name="Soldier_1_2_Gatekeeper"
        )
        if not raw_text:
            return None

        res = safe_extract_json(raw_text)
        if res and isinstance(res, dict):
            try:
                decision = str(res.get("decision", "ROUTE")).upper().strip()
                target_channel = str(res.get("target_channel", "@DidebanJang")).strip()
                if target_channel not in ("@DidebanJang", "@DidehbanEghtesad", "@DidebanEterazat"):
                    target_channel = "@DidebanJang"
                return {
                    "decision": decision,
                    "drop_reason": str(res.get("drop_reason", "")).strip(),
                    "target_channel": target_channel,
                    "urgency_level": str(res.get("urgency_level", "BREAKING")).upper().strip(),
                    "is_iran_related": bool(res.get("is_iran_related", False)),
                    "candidate_title": str(res.get("candidate_title", "")).strip(),
                    "model": used_model,
                    "elapsed": elapsed
                }
            except Exception as e:
                print(f"⚠️ [SOLDIER 1+2] JSON parse error: {e}")
        return None

    # ==========================================================
    # --- سرباز ۴: دیده‌بان رشته‌های فعال ۶ ساعته (Flash Stack) ---
    # ==========================================================

    async def soldier_4_thread_watcher(self, text, node, target_channel, candidate_title="", current_msg_key=None):
        """
        سرباز ۴ (Flash):
        - پست‌های منتشر شده در کانال مقصد طی ۶ ساعت گذشته (۲۱۶۰۰ ثانیه) را بررسی می‌کند.
        - تصمیم می‌گیرد آیا این خبر باید با یکی از پست‌های فعال قبلی ادغام (MERGE) شود
          یا یک پست جدید و مستقل (NEW_ALERT) منتشر شود.
        """
        # گام محافظتی: بررسی هشدارهای همزمان در حال پردازش (In-Flight Thread Guard)
        # جلوگیری از Race Condition زمانی که دو پیام متوالی به فاصله چند ثانیه می‌رسند
        if current_msg_key:
            for if_key, if_data in list(self.in_flight_alerts.items()):
                if if_key == current_msg_key:
                    continue
                if if_data.get('target_channel') != target_channel:
                    continue
                if (time.time() - if_data.get('started_at', 0)) > 35:
                    continue

                is_same_src = (if_data.get('node') == node)
                overlap = set()
                if self.threads:
                    anchors_if = self.threads.extract_message_anchors(if_data.get('text', ''), if_data.get('candidate_title', ''))
                    anchors_cur = self.threads.extract_message_anchors(text, candidate_title)
                    overlap = anchors_cur.intersection(anchors_if)

                if is_same_src or overlap:
                    print(f"⏳ [IN-FLIGHT THREAD GUARD] Detected concurrent alert {if_key} processing for {target_channel}. Waiting up to 12s for it to finish dispatch...")
                    try:
                        await asyncio.wait_for(if_data['event'].wait(), timeout=12.0)
                        print(f"✅ [IN-FLIGHT THREAD GUARD] Concurrent alert {if_key} dispatched. Resuming thread watcher with latest threads.")
                    except asyncio.TimeoutError:
                        print(f"⚠️ [IN-FLIGHT THREAD GUARD] Wait timed out for {if_key}. Proceeding with current state.")
                    break

        now = time.time()
        two_hours = 2 * 3600 # 7200 seconds (سقف پنجره پیوستگی اخبار)

        # استخراج حوزه جغرافیایی و مرزهای ادغام (Territory Scope)
        new_scope = detect_territory_scope(text, candidate_title)
        new_scope_str = new_scope.get('scope', 'UNKNOWN')

        # استخراج رشته‌های فعال در کانال مقصد طی ۲ ساعت گذشته با انطباق قطعی حوزه جغرافیایی
        candidate_threads = []
        for tk, td in self.sentinel.thread_alerts.items():
            if not isinstance(td, dict):
                continue
            if td.get('channel') != target_channel:
                continue
            thread_time = td.get('updated_at') or td.get('timestamp', 0)
            if (now - thread_time) > two_hours:
                continue

            # بررسی انطباق قطعی حوزه جغرافیایی (خارج از ایران به تفکیک کشور؛ داخل ایران به تفکیک شهر/منطقه)
            td_text = f"{td.get('title', '')} {td.get('summary', '')} {td.get('root_text', '')}"
            td_scope = td.get('territory_scope') or detect_territory_scope(td_text, td.get('title', ''))
            td_scope_str = td_scope.get('scope', 'UNKNOWN')

            if new_scope_str != td_scope_str:
                # حوزه‌های جغرافیایی متفاوت (مثلاً شهرهای مختلف ایران یا کشورهای مختلف) هرگز با هم ادغام یا ریپلای نمی‌شوند
                continue

            candidate_threads.append((tk, td, td_scope, thread_time))

        if not candidate_threads:
            print(f"ℹ️ [SOLDIER 4: THREAD WATCHER] No active threads in {target_channel} within 2h window for scope '{new_scope_str}'. Decision: NEW_ALERT")
            return {
                "relation": "NEW_ALERT",
                "target_thread_key": None,
                "target_thread_data": None,
                "merge_reason": f"No active threads within 2h for territory scope {new_scope_str}",
                "territory_scope": new_scope,
                "model": "rule_engine",
                "elapsed": 0.0
            }

        # مرتب‌سازی از تازه‌ترین به‌روزرسانی به قدیمی‌ترین
        candidate_threads.sort(key=lambda item: item[3], reverse=True)
        target_tk, target_td, target_scope, target_time = candidate_threads[0]

        # اندازه فاصله زمانی از زمان ایجاد پست ریشه (Root Creation Time) جهت جلوگیری از آلودگی زمانی ریپلای‌ها
        root_time = target_td.get('timestamp') or target_time
        time_since_root = now - root_time
        has_existing_replies = bool(target_td.get('replies'))
        existing_title = target_td.get('title', '')
        existing_summary = target_td.get('summary', '')
        is_same_source = (target_td.get('node') == node or any(s.get('node') == node for s in target_td.get('sources', [])))
        same_source_hint = "نکته کمکی: این خبر جدید از همان کانال منبع فرستاده شده است.\n" if is_same_source else ""

        # تعیین رژیم تصمیم‌گیری بر اساس زمان:
        # فاز ۱: زیر ۳۰ دقیقه از پیام ریشه و فاقد ریپلای قبلی -> امکان ادغام درجا (MERGE)
        # فاز ۲: بین ۳۰ دقیقه تا ۲ ساعت (یا زمانی که ریپلای قبلی در کانال وجود دارد تا متن زیر ریپلای ادیت نشود) -> امکان ریپلای (THREAD_REPLY)
        if time_since_root <= 1800 and not has_existing_replies:
            allowed_relations = '"MERGE" | "NEW_ALERT"'
            time_policy_text = (
                f"زمان دریافت: {int(time_since_root/60)} دقیقه پس از هشدار ریشه (زیر ۳۰ دقیقه و فاقد ریپلای قبلی).\n"
                "- تصمیم 'MERGE': اگر این خبر مستقیماً درباره همین واقعه است (تصاویر جدید، آمار خسارات، تأیید رسمی یا جزئیات فوری همان ماجرا) تا در همان پست ادغام و ویرایش شود.\n"
                "- تصمیم 'NEW_ALERT': اگر یک حادثه یا رویداد مستقل است (حتی با اینکه در همان حوزه رخ داده).\n"
            )
        else:
            allowed_relations = '"THREAD_REPLY" | "NEW_ALERT"'
            time_policy_text = (
                f"زمان دریافت: {int(time_since_root/60)} دقیقه پس از هشدار ریشه (بین ۳۰ دقیقه تا ۲ ساعت، یا دارای ریپلای قبلی در کانال).\n"
                "جهت جلوگیری از تولید پست‌های طوماری یا ادیت پست مادر در حضور ریپلای‌ها، ادغام مستقیم در پست قبلی مجاز نیست.\n"
                "- تصمیم 'THREAD_REPLY': اگر پیوستگی و امتداد این خبر به هشدار قبلی محرز است (واکنش‌های بعدی، تحولات تکمیلی، یا ابعاد جدید همان ماجرا) تا به صورت یک پست تازه و مجزا که روی خبر قبلی ریپلای شده منتشر شود (هم نوتیفیکیشن ایجاد می‌کند و هم به پست مادر متصل می‌شود).\n"
                "- تصمیم 'NEW_ALERT': اگر حادثه یا موضوعی مستقل است و نیازی به ریپلای ندارد.\n"
            )

        prompt = (
            "تو «سرباز شماره ۴ - دیده‌بان و ناظر پیوستگی رشته‌های خبری دیده‌بان» هستی.\n"
            f"یک هشدار فعال در کانال با حوزه جغرافیایی یکسان ({new_scope_str}) ثبت شده و اکنون خبر جدید دریافت شده است:\n\n"
            f"📌 [هشدار فعال قبلی]:\n"
            f"تیتر: {existing_title}\n"
            f"چکیده: {existing_summary}\n\n"
            f"📥 [خبر جدید دریافت شده از {node}]:\n"
            f"{text}\n\n"
            f"{same_source_hint}"
            f"قوانین تصمیم‌گیری:\n"
            f"{time_policy_text}\n"
            "پاسخ را دقیقاً در قالب شیء JSON زیر بدون هیچ متن اضافی ارسال کن:\n"
            "{\n"
            f'  "relation": {allowed_relations},\n'
            '  "merge_reason": "دلیل فنی و ژورنالیستی تصمیم"\n'
            "}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 300, "temperature": 0.15}
        }

        raw_text, used_model, elapsed = await self.sentinel.gemini.call_soldier_content(
            payload, tier="flash", timeout=15, soldier_name="Soldier_4_Thread_Watcher"
        )
        if not raw_text:
            return None

        res = safe_extract_json(raw_text)
        if res and isinstance(res, dict):
            try:
                rel = str(res.get("relation", "NEW_ALERT")).upper().strip()
                if time_since_root <= 1800 and not has_existing_replies:
                    if rel != "MERGE":
                        rel = "NEW_ALERT"
                else:
                    if rel != "THREAD_REPLY":
                        rel = "NEW_ALERT"

                return {
                    "relation": rel,
                    "target_thread_key": target_tk if rel in ("MERGE", "THREAD_REPLY") else None,
                    "target_thread_data": target_td if rel in ("MERGE", "THREAD_REPLY") else None,
                    "merge_reason": str(res.get("merge_reason", "")).strip(),
                    "territory_scope": new_scope,
                    "model": used_model,
                    "elapsed": elapsed
                }
            except Exception as e:
                print(f"⚠️ [SOLDIER 4] JSON parse error: {e}")

        return {
            "relation": "NEW_ALERT",
            "target_thread_key": None,
            "target_thread_data": None,
            "merge_reason": "Fallback to NEW_ALERT on parse error",
            "territory_scope": new_scope,
            "model": used_model or "unknown",
            "elapsed": elapsed
        }

    # ==============================================================
    # --- سرباز ۳: سردبیر و تحلیلگر ارشد حوزه تخصصی (Flash Stack) ---
    # ==============================================================

    async def soldier_3_domain_editor(self, text, node, target_channel, is_merge=False, is_reply=False, existing_thread_data=None, urgency_level="BREAKING", is_iran_related=False):
        """
        سرباز ۳ (Flash):
        - با اشراف کامل به حوزه تخصصی (دیده‌بان جنگ، اقتصاد، اعتراضات)، متن را با قلمی فاخر بازنویسی می‌کند.
        - اگر متن عربی است (مثل علی‌بک)، با نهایت تسلط و دقت نظامی آن را به فارسی روان ترجمه و کانتکست‌سازی می‌کند.
        - اگر متن انگلیسی است (گزارش‌های فنی نت‌بلاکس NetBlocks)، آن را با دقت ترجمه و ابعاد افت شبکه را ابهام‌زدایی می‌کند.
        - تیتر مستقل همراه با «فعل کامل» می‌سازد.
        - چکیده تحلیلی و ابهام‌زدایی‌شده تولید می‌کند.
        - در صورت MERGE، متن جدید و هشدار قبلی را تلفیق کرده و چکیده یکپارچه می‌سازد.
        - در صورت THREAD_REPLY، یک گزارش تکمیلی کوتاه و تازه بدون تکرار مکررات می‌نویسد.
        """
        # تنظیم پرسونای تخصصی سردبیر
        if target_channel == "@DidebanJang":
            persona_title = "تحلیلگر و سردبیر ارشد نظامی، دفاعی و امنیتی «دیده‌بان جنگ»"
            domain_instructions = (
                "- اشراف کامل به معادلات جنگ، جبهه‌های نبرد، نام دقیق موشک‌ها، پهپادها، جنگنده‌ها، پدافندها، پایگاه‌های نظامی و ابعاد ژئوپلیتیک داری.\n"
                "- اگر زبان متن اصلی عربی است (گزارش‌های علی‌بک)، با کمال فصاحت آن را به زبان فارسی روان، شیوا و با اصطلاحات نظامی استاندارد ترجمه کن.\n"
                "- اگر اصطلاح نظامی، پایگاه یا نام منطقه‌ای نیاز به توضیح دارد، آن را در چکیده برای مخاطب ابهام‌زدایی کن.\n"
            )
        elif target_channel == "@DidehbanEghtesad":
            persona_title = "تحلیلگر و سردبیر ارشد مسائل اقتصادی و بازارهای مالی «دیده‌بان اقتصاد»"
            domain_instructions = (
                "- اشراف کامل به نبض بازار ارز، طلا، سکه، بورس، نوسانات ریال، نفت، تحریم‌ها و شاخص‌های کلان اقتصادی داری.\n"
                "- متن را با ادبیات دقیق اقتصادی، اثرات بازار و ابهام‌زدایی از تصمیمات مالی بنویس.\n"
            )
        else: # @DidebanEterazat
            persona_title = "حقوق‌دان و سردبیر ارشد مسائل اجتماعی و حقوق بشری «دیده‌بان اعتراضات»"
            domain_instructions = (
                "- اشراف کامل به مفاهیم حقوقی، روندهای قضایی، دادگاه‌ها، احکام صادره، وضعیت زندانیان و مطالبات کارگری و اعتصابات داری.\n"
                "- با لحنی کاملاً دقیق، مستند، بی‌طرفانه و شفاف بنویس.\n"
            )

        english_instructions = ""
        if (node or '').lower() == 'netblocks' or any(ord(c) < 128 and c.isalpha() for c in (text or '')[:100]):
            english_instructions = (
                "- متن اصلی به زبان انگلیسی است (به ویژه گزارش‌ها و شاخص‌های آماری نت‌بلاکس NetBlocks درباره اختلال و قطعی اینترنت).\n"
                "- متن را با تسلط کامل به فارسی روان و رسمی ترجمه کن. اگر گزارش درباره افت ترافیک یا قطعی اینترنت در ایران یا منطقه است، درصد دقیق کاهش، گستره جغرافیایی، اپراتورهای متأثر و اهمیت امنیتی را در چکیده بیان کن.\n"
            )

        merge_context = ""
        if is_merge and existing_thread_data:
            merge_context = (
                "⚠️ این خبر باید با هشدار فعال قبلی ادغام (MERGE) شود:\n"
                f"[تیتر قبلی]: {existing_thread_data.get('title', '')}\n"
                f"[چکیده قبلی]: {existing_thread_data.get('summary', '')}\n\n"
                "دستور ادغام: تیتر جدید باید تلفیق کامل هر دو رویداد باشد و چکیده تحلیلی باید حاصل تجمیع منسجم اطلاعات قبلی و خبر جدید بدون تکرار باشد.\n\n"
            )
        elif is_reply and existing_thread_data:
            merge_context = (
                "⚠️ این خبر یک تحول جدید و گزارش تکمیلی است که به صورت ریپلای روی هشدار قبلی منتشر خواهد شد:\n"
                f"[تیتر قبلی]: {existing_thread_data.get('title', '')}\n\n"
                "دستور نگارش ریپلای: نیازی به تکرار متن خبر قبلی نیست. تیتری جذاب با فعل کامل بنویس که این تحول و گزارش تازه را اعلام کند و چکیده‌ای کوتاه و شفاف بنویس که ابعاد تازه، واکنش‌ها، یا تصاویر و شواهد جدید را پوشش دهد.\n\n"
            )

        prompt = (
            f"تو «سرباز شماره ۳ - {persona_title}» هستی.\n"
            f"{domain_instructions}\n"
            f"{english_instructions}\n"
            f"{merge_context}"
            f"متن خبر ارسالی از منبع '{node}':\n"
            f"{text}\n\n"
            "الزامات قطعی نگارش ژورنالیستی:\n"
            "۱. title (تیتر):\n"
            "   - باید یک تیتر مستقل، جذاب، مقتدر و حتماً دارای «فعل یا گزاره کامل» باشد (از تیترهای ناقص، تیترهای حاوی «یاشار نوشت» یا «به گزارش علی‌بک» اکیداً خودداری کن).\n"
            "۲. summary (چکیده تحلیلی رویداد):\n"
            "   - چکیده‌ای روان، گویا و منسجم با اولویت ایجاز در کنار شفاف‌سازی زوایای پنهان و ابهام‌زدایی از رویداد بنویس.\n\n"
            "پاسخ را دقیقاً و فقط در قالب شیء JSON زیر بدون هیچ متن اضافی ارسال کن:\n"
            "{\n"
            '  "title": "تیتر رسا با فعل کامل به زبان فارسی",\n'
            '  "summary": "چکیده تحلیلی و ابهام‌زدایی‌شده به زبان فارسی"\n'
            "}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 600, "temperature": 0.2}
        }

        raw_text, used_model, elapsed = await self.sentinel.gemini.call_soldier_content(
            payload, tier="flash", timeout=20, soldier_name=f"Soldier_3_Editor_{target_channel.replace('@','')}"
        )
        if not raw_text:
            return None

        res = safe_extract_json(raw_text)
        if res and isinstance(res, dict):
            try:
                title = str(res.get("title", "")).strip()
                summary = str(res.get("summary", "")).strip()
                if title and summary:
                    return {
                        "title": title,
                        "summary": summary,
                        "model": used_model,
                        "elapsed": elapsed
                    }
            except Exception as e:
                print(f"⚠️ [SOLDIER 3] JSON parse error: {e}")
        return None

    # ========================================================
    # --- لایه اجرایی و انتشار نهایی (Dispatch & Execute) ---
    # ========================================================

    async def execute_dispatch(self, node, msg_id, route_info, thread_info, editor_info, raw_msg=None, is_edit=False, custom_sources=None):
        """
        اجرای نهایی عملیات (تنها درگاه رسمی انتشار سامانه):
        - در صورت MERGE: ویرایش درجا در تلگرام و به‌روزرسانی تاریخچه و وضعیت.
        - در صورت NEW_ALERT: انتشار پست جدید با برچسب‌های کانونی قطعی و ثبت در حافظه دیده‌بان.
        پشتیبانی کامل از پیام‌های تک‌منبعی VIP و رویدادهای اجماع چندمنبعی رادار عمومی.
        """
        target_channel = route_info['target_channel']
        urgency_level = route_info['urgency_level']
        is_iran_related = route_info['is_iran_related']
        relation = thread_info['relation']

        title = editor_info['title']
        summary = editor_info['summary']
        ai_model = editor_info.get('model', 'unknown')

        tag = CHANNEL_CANONICAL_TAGS.get(target_channel, '#دیده‌بان_جنگ')
        latency_sec, latency_str = calculate_source_latency(raw_msg)
        root_media_type = detect_msg_media_type(raw_msg)
        root_icon = get_media_icon_from_type(root_media_type)
        media_header = f" {root_icon}" if root_icon != "📝" else ""
        link = f"https://t.me/{node}/{msg_id}"

        # استخراج حوزه جغرافیایی و خط‌مشی صوت/سایلنت
        territory_scope = thread_info.get('territory_scope') or detect_territory_scope(summary, title)
        is_silent = territory_scope.get('is_silent', True)

        # تعیین آیکون و نوع هشدار
        if target_channel == '@DidebanJang':
            if relation == "THREAD_REPLY":
                alert_header = f"⚡️ **گزارش تکمیلی: {title}**{media_header}"
            else:
                tier, icon, alert_type_name, _ = classify_war_urgency(
                    ai_urgency=urgency_level,
                    text=summary,
                    title=title,
                    is_iran_related=is_iran_related
                )
                alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"
        elif target_channel == '@DidehbanEghtesad':
            icon = "📈"
            alert_type_name = "گزارش اقتصادی (تکمیلی)" if relation == "THREAD_REPLY" else "گزارش اقتصادی"
            alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"
        else: # @DidebanEterazat
            is_legal = any(w in title for w in ['اعدام', 'دادگاه', 'حکم', 'حقوق بشری', 'زندان', 'بازداشت'])
            is_strike = 'اعتصاب' in title
            icon = "⚖️" if is_legal else ("🛑" if is_strike else "📢")
            alert_type_name = "گزارش تکمیلی" if relation == "THREAD_REPLY" else ("گزارش حقوق بشری" if is_legal else ("گزارش کارگری و اعتصاب" if is_strike else "گزارش مردمی"))
            alert_header = f"{icon} **{alert_type_name}: {title}**{media_header}"

        # ساختار بلوک منابع و متن کامل پست برای ارسال نو یا ادیت بازهدایت‌شده
        if custom_sources and len(custom_sources) > 1:
            sources_lines = [f"- [{s.get('node', 'منبع')}]({s.get('link', '')}) {get_media_icon_from_type(s.get('media_type', 'text'))}" for s in custom_sources]
            source_block = "🔗 **منابع خبر:**\n" + "\n".join(sources_lines)
        else:
            source_block = f"🔗 **منبع خبر:**\n- [{node}]({link}) {root_icon}"

        alert_text = (
            f"{alert_header}\n\n"
            f"📝 **چکیده تحلیلی رویداد:**\n{summary}\n\n"
            f"{source_block}\n\n"
            f"{tag}\n{target_channel}"
        )

        # ----------------------------------------------------
        # حالت ۱: ادغام درجا با پست فعال قبلی (MERGE - کمتر از ۳۰ دقیقه)
        # ----------------------------------------------------
        if relation == "MERGE" and thread_info.get('target_thread_key'):
            thread_key = thread_info['target_thread_key']
            thread_data = self.sentinel.thread_alerts.get(thread_key)
            if thread_data:
                alert_msg_id = thread_data.get('alert_msg_id')
                sources = thread_data.setdefault('sources', [])

                # افزودن منبع یا منابع جدید به لیست منابع رشته
                new_additions = custom_sources if custom_sources else [{
                    'node': node,
                    'msg_id': msg_id,
                    'media_type': root_media_type,
                    'link': link,
                    'timestamp': time.time()
                }]
                for item in new_additions:
                    if not any(s.get('node') == item.get('node') and str(s.get('msg_id')) == str(item.get('msg_id')) for s in sources):
                        sources.append(item)

                thread_data['title'] = title
                thread_data['summary'] = summary
                thread_data['updated_at'] = time.time()
                thread_data['territory_scope'] = territory_scope

                updated_text = self.threads.build_alert_post_content(thread_data) if self.threads else ""
                if not updated_text:
                    status_line = format_update_status_line(thread_data['updated_at'])
                    sources_lines = [f"- [{s.get('node', 'منبع')}]({s.get('link', '')}) {get_media_icon_from_type(s.get('media_type', 'text'))}" for s in sources]
                    updated_text = (
                        f"{alert_header}\n\n"
                        f"📝 **چکیده رویداد:**\n{summary}\n\n"
                        f"🔗 **منابع خبر:**\n" + "\n".join(sources_lines) + "\n\n"
                        f"{status_line}\n\n"
                        f"{tag}\n{target_channel}"
                    )

                try:
                    await self.sentinel.safe_edit_post(target_channel, alert_msg_id, updated_text)
                    print(f"🔀 [MULTI-AGENT: MERGED (<=30m) | Model: {ai_model} | Latency: {latency_str} | Scope: {territory_scope.get('scope')}] Merged {node}_{msg_id} into alert #{alert_msg_id} in {target_channel}.")
                except Exception as edit_err:
                    if "not modified" not in str(edit_err).lower():
                        print(f"⚠️ Error editing merged alert #{alert_msg_id} in {target_channel}: {edit_err}")

                msg_key = f"{node}_{msg_id}"
                self.sentinel.vip_alert_history[msg_key] = {
                    'sent_msg_id': alert_msg_id,
                    'target_channel': target_channel,
                    'time': time.time(),
                    'thread_key': thread_key,
                    'ai_model': ai_model,
                    'pattern': 'vip_edit_update'
                }
                if custom_sources:
                    for s in custom_sources:
                        sn = s.get('node')
                        sm = s.get('msg_id')
                        if sn and sm:
                            self.sentinel.db.record_posted_message(target_channel, sn, sm, alert_msg_id)
                else:
                    self.sentinel.db.record_posted_message(target_channel, node, msg_id, alert_msg_id)
                self.sentinel.save_persisted_state()
                return

        # ----------------------------------------------------
        # حالت ۲: انتشار پست جدید با ریپلای روی پست قبلی (THREAD_REPLY - بین ۳۰ دقیقه تا ۲ ساعت)
        # ----------------------------------------------------
        elif relation == "THREAD_REPLY" and thread_info.get('target_thread_data'):
            target_td = thread_info['target_thread_data']
            reply_to_post_id = target_td.get('alert_msg_id')

            existing_ch, existing_post_id = self.find_existing_posted_alert(node, msg_id, target_channel=target_channel)
            if existing_post_id:
                print(f"🛑 [IRONCLAD GUARD] Refusing duplicate reply! {node}_{msg_id} already exists as #{existing_post_id}. Redirecting to safe_edit_post.")
                try:
                    await self.sentinel.safe_edit_post(existing_ch, existing_post_id, alert_text)
                except Exception as e:
                    print(f"⚠️ Error editing existing reply #{existing_post_id}: {e}")
                return

            sent = None
            is_fallback = False
            try:
                target_peer = self.sentinel.get_node_input_peer(target_channel) or target_channel
                sent = await self.sentinel.bot.send_message(
                    target_peer,
                    alert_text,
                    link_preview=False,
                    silent=is_silent,
                    reply_to=reply_to_post_id
                )
            except Exception as reply_err:
                print(f"⚠️ [REPLY FALLBACK] Could not reply to #{reply_to_post_id} in {target_channel} ({reply_err}). Falling back to standalone post...")
                try:
                    sent = await self.sentinel.bot.send_message(
                        target_peer,
                        alert_text,
                        link_preview=False,
                        silent=is_silent
                    )
                    is_fallback = True
                except Exception as fallback_err:
                    print(f"❌ [DISPATCH ERROR] Both reply and standalone fallback failed for {target_channel}: {fallback_err}")
                    return

            if sent:
                target_td['updated_at'] = time.time()
                target_td['last_reply_at'] = time.time()
                target_td.setdefault('replies', []).append(sent.id)
                self.sentinel.channel_last_alert_id[target_channel] = sent.id

                msg_key = f"{node}_{msg_id}"
                self.sentinel.vip_alert_history[msg_key] = {
                    'target_channel': target_channel,
                    'sent_msg_id': sent.id,
                    'time': time.time(),
                    'text': summary,
                    'pattern': 'thread_reply' if not is_fallback else 'thread_reply_fallback',
                    'reply_to': reply_to_post_id if not is_fallback else None,
                    'ai_model': ai_model,
                    'latency_sec': round(latency_sec, 2) if latency_sec is not None else None
                }
                if custom_sources:
                    for s in custom_sources:
                        sn = s.get('node')
                        sm = s.get('msg_id')
                        if sn and sm:
                            self.sentinel.db.record_posted_message(target_channel, sn, sm, sent.id)
                else:
                    self.sentinel.db.record_posted_message(target_channel, node, msg_id, sent.id)

                self.sentinel.save_persisted_state()
                urg_label = "Alarm/Sound" if not is_silent else "Silent"
                disp_type = "THREAD REPLY" if not is_fallback else "THREAD REPLY (STANDALONE FALLBACK)"
                print(f"💬 [MULTI-AGENT: {disp_type} | Model: {ai_model} | Latency: {latency_str} | Scope: {territory_scope.get('scope')} | {urg_label}] Dispatched alert #{sent.id} to {target_channel} for {node} #{msg_id}:")
                print(f"   {title}")
                return

        # ----------------------------------------------------
        # حالت ۳: انتشار پست جدید و مستقل (NEW_ALERT)
        # ----------------------------------------------------
        # قفل آهنین سطح نهایی (Last-Mile Ironclad Guard):
        # بررسی قطعی قبل از هرگونه ارسال پست نو به تلگرام
        existing_ch, existing_post_id = self.find_existing_posted_alert(node, msg_id, target_channel=target_channel)
        if existing_post_id:
            print(f"🛑 [IRONCLAD LAST-MILE GUARD] Refusing NEW_ALERT! {node}_{msg_id} already exists in {existing_ch} as #{existing_post_id}. Redirecting to safe_edit_post!")
            try:
                await self.sentinel.safe_edit_post(existing_ch, existing_post_id, alert_text)
                print(f"✅ [IRONCLAD LAST-MILE GUARD] In-place edit redirected successfully to #{existing_post_id} in {existing_ch}.")
            except Exception as e:
                print(f"⚠️ Error in last-mile edit redirect: {e}")
            return

        try:
            target_peer = self.sentinel.get_node_input_peer(target_channel) or target_channel
            sent = await self.sentinel.bot.send_message(target_peer, alert_text, link_preview=False, silent=is_silent)
            if sent:
                # چتر نجات: اطمینان مضاعف از عدم ارسال دابل‌پست همزمان
                existing_ch, prev_id = self.find_existing_posted_alert(node, msg_id, target_channel=target_channel)
                if prev_id and prev_id != sent.id:
                    print(f"⚠️ [PARACHUTE ALERT] Detected duplicate post for {node}_{msg_id}: Sent #{sent.id} but #{prev_id} existed! Deleting newer duplicate...")
                    await self.safe_delete_duplicate_post(target_channel, sent.id)
                    return
                self.sentinel.channel_last_alert_id[target_channel] = sent.id
                msg_key = f"{node}_{msg_id}"
                self.sentinel.vip_alert_history[msg_key] = {
                    'target_channel': target_channel,
                    'sent_msg_id': sent.id,
                    'time': time.time(),
                    'text': summary,
                    'pattern': 'multi_agent_editorial',
                    'ai_model': ai_model,
                    'latency_sec': round(latency_sec, 2) if latency_sec is not None else None
                }
                if custom_sources:
                    for s in custom_sources:
                        sn = s.get('node')
                        sm = s.get('msg_id')
                        if sn and sm:
                            self.sentinel.db.record_posted_message(target_channel, sn, sm, sent.id)
                else:
                    self.sentinel.db.record_posted_message(target_channel, node, msg_id, sent.id)

                # ثبت رشته در حافظه سیستم (با افق ۲ ساعته پیوستگی و متادیتا حوزه جغرافیایی)
                thread_key = f"{node}_{msg_id}"
                self.sentinel.thread_alerts[thread_key] = {
                    'channel': target_channel,
                    'alert_msg_id': sent.id,
                    'node': node,
                    'root_id': msg_id,
                    'root_text': summary,
                    'root_media_type': root_media_type,
                    'timestamp': time.time(),
                    'updated_at': time.time(),
                    'category': 'WAR' if target_channel == '@DidebanJang' else ('ECONOMY' if target_channel == '@DidehbanEghtesad' else 'PROTEST_RIGHTS'),
                    'is_iran_related': is_iran_related,
                    'urgency_level': urgency_level,
                    'territory_scope': territory_scope,
                    'title': title,
                    'summary': summary,
                    'ai_model': ai_model,
                    'latency_sec': round(latency_sec, 2) if latency_sec is not None else None,
                    'tag': tag,
                    'sources': custom_sources if custom_sources else [{
                        'node': node,
                        'msg_id': msg_id,
                        'media_type': root_media_type,
                        'link': link,
                        'timestamp': time.time()
                    }],
                    'replies': []
                }
                self.sentinel.save_persisted_state()

                urg_label = "Alarm/Sound" if not is_silent else "Silent"
                print(f"✨ [MULTI-AGENT: PUBLISHED | Model: {ai_model} | Latency: {latency_str}] Posted to {target_channel} ({urg_label}) for {node} #{msg_id}:")
                print(f"   📌 Title: {title}")
        except Exception as pub_err:
            print(f"❌ Error publishing alert to {target_channel}: {pub_err}")

    # ========================================================
    # --- موتور اصلی و هدایت پیام (Master Message Handler) ---
    # ========================================================

    async def handle_message(self, text, node, msg_id, raw_msg=None, is_edit=False, from_queue=False):
        """
        نقطه ورود پیام‌های کانال‌های VIP:
        1. ضد تکرار آلبوم‌ها (media groups)
        2. قفل پیام
        3. فراخوانی سرباز ۱+۲ (Lite Stack)
        4. در صورت DROP: ثبت در recent_messages (در صورت وحید یا ایلیا برای تغذیه رادار)
        5. فراخوانی سرباز ۴ (Flash Stack) برای پایش رشته‌های ۶ ساعته
        6. فراخوانی سرباز ۳ (Flash Stack) برای بازنویسی تخصصی حوزه
        7. انتشار یا ادغام امن درجا
        8. در صورت قطعی هر مرحله: ارسال مستقیم به صف هوشمند ۴۵ دقیقه‌ای (بدون انتشار خام)
        """
        # جلوگیری از رشد نامحدود حافظه در قفل‌ها
        if len(self.msg_locks) > 500:
            for old_k in list(self.msg_locks.keys())[:100]:
                self.msg_locks.pop(old_k, None)

        # ----------------------------------------------------
        # مدیریت هوشمند آلبوم‌ها و مدیاگروپ‌ها (Telegram Media Groups Buffer)
        # ----------------------------------------------------
        grouped_id = getattr(raw_msg, 'grouped_id', None) if raw_msg else None
        if grouped_id and not is_edit:
            str_gid = str(grouped_id)
            # اگر این مدیاگروپ قبلاً پردازش شده است، پیام‌های پس‌آیند نادیده گرفته شوند
            if str_gid in self.grouped_posted:
                if not is_edit:
                    self.sentinel.db.advance_watermark(node, msg_id)
                return True

            group_key = f"{node}_{str_gid}"
            if group_key not in self.media_group_buffers:
                # پیام اول آلبوم: ایجاد بافر و پنجره انتظار ۱.۵ ثانیه‌ای
                self.media_group_buffers[group_key] = {
                    'messages': [(text, msg_id, raw_msg)],
                    'event': asyncio.Event()
                }
                await asyncio.sleep(1.5)
                buf_entry = self.media_group_buffers.pop(group_key, None)
                if not buf_entry or not buf_entry['messages']:
                    return True

                # انتخاب پیامی که طولانی‌ترین و کامل‌ترین کپشن را دارد
                best_text, best_msg_id, best_raw_msg = max(
                    buf_entry['messages'],
                    key=lambda m: len(((m[0] or "")).strip())
                )
                self.grouped_posted[str_gid] = time.time()
                if len(self.grouped_posted) > 1000:
                    self.grouped_posted.popitem(last=False)  # هرس واقعی FIFO

                buf_entry['event'].set()
                text, msg_id, raw_msg = best_text, best_msg_id, best_raw_msg
            else:
                # پیام‌های بعدی همان آلبوم در طول پنجره ۱.۵ ثانیه‌ای
                self.media_group_buffers[group_key]['messages'].append((text, msg_id, raw_msg))
                try:
                    await asyncio.wait_for(self.media_group_buffers[group_key]['event'].wait(), timeout=3.5)
                except Exception:
                    pass
                if not is_edit:
                    self.sentinel.db.advance_watermark(node, msg_id)
                return True

        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 10:
            if not is_edit:
                self.sentinel.db.advance_watermark(node, msg_id)
            return True

        msg_lock = self.msg_locks.setdefault(f"{node}_{msg_id}", asyncio.Lock())
        async with msg_lock:
            # ----------------------------------------------------
            # گام ۰: قفل آهنین ضد تکرار (Ironclad Deduplication Guard)
            # ----------------------------------------------------
            existing_target_ch, existing_post_id = self.find_existing_posted_alert(node, msg_id)

            if existing_post_id and not is_edit:
                print(f"🛑 [VIP IRONCLAD DEDUP] Msg {node}_{msg_id} was ALREADY posted to {existing_target_ch} as #{existing_post_id}. Discarding duplicate invocation.")
                self.sentinel.db.advance_watermark(node, msg_id)
                return True

            if existing_post_id and is_edit:
                # پیام قبلاً منتشر شده و اکنون نویسنده آن را در تلگرام ادیت کرده است.
                # مستقیماً همان پست قبلی بدون ایجاد پست جدید درجا ویرایش می‌شود.
                await self.handle_existing_post_edit(
                    node=node,
                    msg_id=msg_id,
                    clean_text=clean_text,
                    target_channel=existing_target_ch,
                    existing_post_id=existing_post_id,
                    raw_msg=raw_msg
                )
                return True

            # ----------------------------------------------------
            # گام ۱: سرباز شماره ۱+۲ (دروازه‌بان و مسیرساز تک‌ضرب - Lite)
            # ----------------------------------------------------
            route_info = await self.soldier_1_2_gatekeeper(clean_text, node, raw_msg=raw_msg)
            if not route_info:
                print(f"⏳ [VIP QUEUE: SOLDIER 1+2] Msg {node}_{msg_id} failed AI triage. Enqueuing into Smart Queue (45m TTL)...")
                if not from_queue and hasattr(self.sentinel, 'enqueue_unprocessed_message'):
                    await self.sentinel.enqueue_unprocessed_message(
                        pipeline="vip_multi_agent", node=node, msg_id=msg_id, text=clean_text, raw_msg=raw_msg, is_edit=is_edit
                    )
                return False

            decision = route_info['decision']
            if decision == 'DROP':
                drop_reason = route_info.get('drop_reason', 'فاقد ارزش خبری یا غیرمرتبط')
                model_used = route_info.get('model', 'unknown')
                elapsed = route_info.get('elapsed', 0.0)
                print(f"🛑 [VIP SOLDIER 1+2: DROPPED | Model: {model_used} | {elapsed:.2f}s] Msg {node}_{msg_id} dropped: {drop_reason}")
                if not is_edit:
                    self.sentinel.db.advance_watermark(node, msg_id)
                return True

            # بررسی تکراری نبودن انتشار
            target_channel = route_info['target_channel']
            existing_ch_routed, existing_id_routed = self.find_existing_posted_alert(node, msg_id, target_channel=target_channel)
            if existing_id_routed:
                if not is_edit:
                    print(f"🛑 [VIP DEDUP] Msg {node}_{msg_id} already posted to {target_channel} as #{existing_id_routed}. Aborting.")
                    self.sentinel.db.advance_watermark(node, msg_id)
                    return True
                else:
                    await self.handle_existing_post_edit(
                        node=node,
                        msg_id=msg_id,
                        clean_text=clean_text,
                        target_channel=target_channel,
                        existing_post_id=existing_id_routed,
                        raw_msg=raw_msg
                    )
                    return True

            # ثبت در in_flight_alerts برای هماهنگی و حل مسابقه زمانی (Race Condition)
            in_flight_key = f"{node}_{msg_id}"
            in_flight_entry = {
                'key': in_flight_key,
                'node': node,
                'msg_id': msg_id,
                'target_channel': target_channel,
                'candidate_title': route_info.get('candidate_title', ''),
                'text': clean_text,
                'started_at': time.time(),
                'event': asyncio.Event()
            }
            self.in_flight_alerts[in_flight_key] = in_flight_entry

            try:
                # ----------------------------------------------------
                # گام ۲: سرباز شماره ۴ (دیده‌بان رشته‌ها - Flash) تحت قفل هماهنگی
                # ----------------------------------------------------
                async with self.thread_decision_lock:
                    thread_info = await self.soldier_4_thread_watcher(
                        clean_text, node, target_channel,
                        candidate_title=route_info.get('candidate_title', ''),
                        current_msg_key=in_flight_key
                    )
                if not thread_info:
                    print(f"⏳ [VIP QUEUE: SOLDIER 4] Msg {node}_{msg_id} failed thread watcher. Enqueuing into Smart Queue (45m TTL)...")
                    if not from_queue and hasattr(self.sentinel, 'enqueue_unprocessed_message'):
                        await self.sentinel.enqueue_unprocessed_message(
                            pipeline="vip_multi_agent", node=node, msg_id=msg_id, text=clean_text, raw_msg=raw_msg, is_edit=is_edit
                        )
                    return False

                is_merge = (thread_info.get('relation') == 'MERGE')
                is_reply = (thread_info.get('relation') == 'THREAD_REPLY')
                existing_td = thread_info.get('target_thread_data')

                # ----------------------------------------------------
                # گام ۳: سرباز شماره ۳ (سردبیر حوزه تخصصی - Flash)
                # ----------------------------------------------------
                editor_info = await self.soldier_3_domain_editor(
                    clean_text, node, target_channel,
                    is_merge=is_merge,
                    is_reply=is_reply,
                    existing_thread_data=existing_td,
                    urgency_level=route_info.get('urgency_level', 'BREAKING'),
                    is_iran_related=route_info.get('is_iran_related', False)
                )
                if not editor_info:
                    print(f"⏳ [VIP QUEUE: SOLDIER 3] Msg {node}_{msg_id} failed domain editor. Enqueuing into Smart Queue (45m TTL)...")
                    if not from_queue and hasattr(self.sentinel, 'enqueue_unprocessed_message'):
                        await self.sentinel.enqueue_unprocessed_message(
                            pipeline="vip_multi_agent", node=node, msg_id=msg_id, text=clean_text, raw_msg=raw_msg, is_edit=is_edit
                        )
                    return False

                # ----------------------------------------------------
                # گام ۴: انتشار یا ادغام نهایی
                # ----------------------------------------------------
                await self.execute_dispatch(
                    node=node,
                    msg_id=msg_id,
                    route_info=route_info,
                    thread_info=thread_info,
                    editor_info=editor_info,
                    raw_msg=raw_msg,
                    is_edit=is_edit
                )
                return True
            finally:
                in_flight_entry['event'].set()
                self.in_flight_alerts.pop(in_flight_key, None)

    async def process_queue_item(self, item, raw_msg=None):
        """
        پردازش مجدد پیام‌های معلق در صف هوشمند پس از بازیابی سهمیه یا اینترنت
        """
        node = item.get('node', 'unknown')
        msg_id = item.get('msg_id')
        text = item.get('text', '')
        is_edit = item.get('is_edit', False)
        print(f"🔄 [VIP MULTI-AGENT] Retrying queued msg {node}_{msg_id} through 4-soldier pipeline...")
        success = await self.handle_message(text, node, msg_id, raw_msg=raw_msg, is_edit=is_edit, from_queue=True)
        return bool(success)

    async def catchup_unposted(self, reader_client=None, node="withyashar"):
        """
        اسکن گذشته‌نگر پیام‌های پردازش‌نشده برای دوره‌های ری‌استارت یا قطعی موقت
        """
        if not reader_client:
            return
        try:
            watermark = self.sentinel.db.get_watermark(node)
            kwargs = {'limit': 25}
            if watermark > 0:
                kwargs['min_id'] = watermark
            recent_msgs = []
            node_peer = self.sentinel.get_node_input_peer(node) or node
            async for msg in reader_client.iter_messages(node_peer, **kwargs):
                recent_msgs.append(msg)
            recent_msgs.reverse()
            now_ts = time.time()
            for msg in recent_msgs:
                clean_text = (msg.message or msg.text or "").strip()
                if clean_text and len(clean_text) >= 10:
                    msg_date = getattr(msg, 'date', None)
                    if msg_date:
                        msg_age_sec = now_ts - msg_date.timestamp()
                        if msg_age_sec > 7200:
                            # نادیده گرفتن اخبار بیات با قدمت بیش از ۲ ساعت و پیشروی واتر‌مارک
                            self.sentinel.db.advance_watermark(node, msg.id)
                            continue

                    # بررسی عدم ارسال قبلی به کانال‌های دیده‌بان
                    already_posted = any(
                        self.sentinel.db.is_source_posted(ch, node, msg.id)
                        for ch in ('@DidebanJang', '@DidehbanEghtesad', '@DidebanEterazat')
                    )
                    if not already_posted:
                        print(f"⚡ [VIP MULTI-AGENT] Catching up missed post #{msg.id} from {node} (watermark: {watermark})...")
                        await self.handle_message(clean_text, node, msg.id, raw_msg=msg, is_edit=False)
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ {node} catchup failed: {e}")

